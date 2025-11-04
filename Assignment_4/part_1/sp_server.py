import socket
import sys
import os
import time
import errno
import bisect

# --- Configuration Constants ---
PACKET_SIZE = 1200
HEADER_SIZE = 4 + 16  # 4 bytes Seq Num + 16 bytes Reserved (for SACK)
DATA_SIZE = PACKET_SIZE - HEADER_SIZE
EOF_MARKER = b"EOF"
ReTrCo = 0.001  # Retransmission cooldown to avoid immediate re-retransmits

# --- JITTER-RESISTANT RTO PARAMS ---
INITIAL_RTO = 0.05  # More conservative initial RTO for jittery networks
alpha = 0.125  # Standard TCP value for EWMA (less reactive)
beta = 0.25  # Standard TCP value for DevRTT (less reactive)
MAX_DUPLICATE_ACKS = 3
ACK_TIMEOUT = 0.001  # Check for ACKs as fast as possible
FINAL_ACK_WAIT = 1.0
MIN_RTO = 0.04  # Higher min RTO to handle jitter (50ms)
MAX_RTO = 2.0  # Higher max RTO for very jittery conditions (2s)
RTT_SAMPLES_FOR_TRUST = 5  # Number of samples before trusting RTT estimate

# --- Helper Functions for Packet Handling ---

def create_packet(seq_num, data):
    """Creates a packet with the 4-byte sequence number header."""
    # CPU OPT: Use bytearray for slightly faster concatenation
    result = bytearray(HEADER_SIZE + len(data))
    result[0:4] = seq_num.to_bytes(4, byteorder='big')
    # Reserved bytes already initialized as zeros
    result[HEADER_SIZE:] = data
    return bytes(result)

def extract_ack_num(packet):
    """Extracts the 4-byte ACK number (next expected sequence number)."""
    return int.from_bytes(packet[:4], byteorder='big')

def extract_sack_blocks(packet):
    """
    Extracts SACK blocks from the reserved field.
    Each SACK block is 8 bytes (4 bytes start + 4 bytes end).
    """
    sack_blocks = []
    reserved_field = packet[4:20]

    # CPU OPT: Unroll loop for 2 blocks max
    block_start = int.from_bytes(reserved_field[0:4], byteorder='big')
    block_end = int.from_bytes(reserved_field[4:8], byteorder='big')
    if block_start > 0 and block_end > 0:
        sack_blocks.append((block_start, block_end))

    block_start = int.from_bytes(reserved_field[8:12], byteorder='big')
    block_end = int.from_bytes(reserved_field[12:16], byteorder='big')
    if block_start > 0 and block_end > 0:
        sack_blocks.append((block_start, block_end))

    return sack_blocks

def run_server(server_ip, server_port, sws_bytes):
    # Initialize RTT estimation variables with jitter resistance
    EstimatedRTT = INITIAL_RTO
    DevRTT = INITIAL_RTO / 2
    RTO = INITIAL_RTO
    rtt_sample_count = 0  # Track number of RTT samples
    rtt_samples = []  # Store initial samples for outlier detection

    # Calculate SWS in terms of packets
    SWS_PACKETS = max(1, sws_bytes // DATA_SIZE)

    print(f"Server starting on {server_ip}:{server_port} with SWS of {SWS_PACKETS} packets ({sws_bytes} bytes).")

    # Setup UDP Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # --- OPTIMIZATION: Increase socket buffers ---
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
    server_address = (server_ip, server_port)
    sock.bind(server_address)
    sock.settimeout(2.0)  # Initial timeout for client request

    # Wait for Client Request
    print("Waiting for file request from client...")
    try:
        request_data, client_address = sock.recvfrom(PACKET_SIZE)
        print(f"Received request from {client_address}.")
    except socket.timeout:
        print("Timeout waiting for client request. Server exiting.")
        sock.close()
        return

    file_to_send = "data.txt"
    if not os.path.exists(file_to_send):
        file_to_send = "/mnt/user-data/uploads/data.txt"  # Adjust path if needed
        if not os.path.exists(file_to_send):
            print(f"Error: File 'data.txt' not found.")
            sock.close()
            return

    # Read File Data
    with open(file_to_send, 'rb') as f:
        file_data = f.read()

    # Prepare all packets
    all_packets = []
    packet_seq_nums = []
    seq_num = 0

    # CPU OPT: Pre-allocate lists with known size
    estimated_packets = (len(file_data) + DATA_SIZE - 1) // DATA_SIZE + 1
    all_packets = [None] * estimated_packets
    packet_seq_nums = [0] * estimated_packets

    packet_idx = 0
    for i in range(0, len(file_data), DATA_SIZE):
        chunk = file_data[i:i + DATA_SIZE]
        packet_seq_nums[packet_idx] = seq_num
        all_packets[packet_idx] = create_packet(seq_num, chunk)
        seq_num += len(chunk)
        packet_idx += 1

    # Add EOF packet
    eof_seq_num = seq_num
    packet_seq_nums[packet_idx] = eof_seq_num
    all_packets[packet_idx] = create_packet(eof_seq_num, EOF_MARKER)
    total_packets = packet_idx + 1

    # Trim lists if needed
    all_packets = all_packets[:total_packets]
    packet_seq_nums = packet_seq_nums[:total_packets]

    print(f"File prepared: {total_packets} packets")

    # --- Selective Repeat State ---
    send_base_idx = 0  # Index of first unACKed packet
    next_seq_idx = 0  # Index of next packet to send
    # CPU OPT: Use bytearray instead of list for simple flags
    acked_packets = bytearray(total_packets)
    sacked_packets = bytearray(total_packets)
    retransmitted_packets = bytearray(total_packets)
    packet_timers = {}
    last_cum_ack = 0
    dup_ack_count = 0

    sock.settimeout(ACK_TIMEOUT)

    print("Starting Selective Repeat transfer...")
    start_time = time.time()
    last_progress_time = time.time()

    all_packets_sent_once = False
    final_ack_wait_start = None

    last_window_update_time = current_time = time.time()
    packets_in_flight = 0

    # CPU OPT: Cache method lookups
    sock_sendto = sock.sendto
    sock_recvfrom = sock.recvfrom
    time_time = time.time

    # Keep socket in non-blocking mode continuously
    sock.setblocking(0)

    while send_base_idx < total_packets:
        current_time = time_time()

        if current_time - start_time > 120:
            print("Transfer taking too long (>120s). Exiting.")
            break

        # 1. Calculate packets in flight
        packets_in_flight = 0
        for idx in range(send_base_idx, next_seq_idx):
            if acked_packets[idx] == 0 and sacked_packets[idx] == 0:
                packets_in_flight += 1

        # 2. Send new packets if window allows
        while next_seq_idx < total_packets and packets_in_flight < SWS_PACKETS:
            if acked_packets[next_seq_idx] == 0:
                print("og send", 1180 * next_seq_idx)
                sock_sendto(all_packets[next_seq_idx], client_address)
                packet_timers[next_seq_idx] = current_time

            next_seq_idx += 1
            packets_in_flight += 1

            if next_seq_idx >= total_packets:
                all_packets_sent_once = True

        # 3. Check for timeouts and retransmit - JITTER RESISTANT
        # Use adaptive timeout multiplier based on DevRTT
        timeout_multiplier = 1.5 + min(2.0, DevRTT / EstimatedRTT)  # Scale with jitter
        adaptive_rto = min(timeout_multiplier * RTO, MAX_RTO)

        for idx in range(send_base_idx, next_seq_idx):
            if acked_packets[idx] == 0 and sacked_packets[idx] == 0 and idx in packet_timers:
                if current_time - packet_timers[idx] > adaptive_rto:
                    print("Timeout retransmit", 1180 * idx)
                    sock_sendto(all_packets[idx], client_address)
                    packet_timers[idx] = current_time
                    retransmitted_packets[idx] = 1

        # Process ACKs in batches
        try:
            while True:
                try:
                    ack_packet, _ = sock_recvfrom(PACKET_SIZE)

                    ack_num = extract_ack_num(ack_packet)
                    sack_blocks = extract_sack_blocks(ack_packet)
                    print("Received ACK for", ack_num, "with SACK blocks", sack_blocks, "at", current_time - start_time)
                    if ack_num > eof_seq_num:
                        print(f"Received final ACK. Transfer complete!")
                        print(f"Final RTT: {EstimatedRTT:.3f}s, DevRTT: {DevRTT:.3f}s")
                        print(f"File transfer complete. Final RTO: {RTO:.3f}s")
                        sock.close()
                        return

                    # CPU OPT: Binary search for cum_ack_idx
                    cum_ack_idx = -1
                    left, right = 0, len(packet_seq_nums) - 1
                    while left <= right:
                        mid = (left + right) // 2
                        if packet_seq_nums[mid] < ack_num:
                            left = mid + 1
                        else:
                            cum_ack_idx = mid
                            right = mid - 1

                    if cum_ack_idx == -1 and ack_num > eof_seq_num:
                        cum_ack_idx = total_packets

                    if cum_ack_idx > send_base_idx:
                        # JITTER-RESISTANT RTT Update
                        for i in range(send_base_idx, cum_ack_idx):
                            if i in packet_timers and retransmitted_packets[i] == 0:
                                SampleRTT = current_time - packet_timers[i]

                                # Outlier detection for initial samples
                                if rtt_sample_count < RTT_SAMPLES_FOR_TRUST:
                                    rtt_samples.append(SampleRTT)
                                    rtt_sample_count += 1

                                # Use median of initial samples for better jitter resistance
                                if rtt_sample_count == RTT_SAMPLES_FOR_TRUST:
                                    sorted_samples = sorted(rtt_samples)
                                    median_rtt = sorted_samples[len(sorted_samples) // 2]
                                    EstimatedRTT = median_rtt
                                    # Calculate initial DevRTT from samples
                                    deviations = [abs(s - EstimatedRTT) for s in rtt_samples]
                                    DevRTT = sum(deviations) / len(deviations)
                                    print(f"Initial RTT calibration: EstimatedRTT={EstimatedRTT:.3f}s, DevRTT={DevRTT:.3f}s")
                                else:
                                    # Standard EWMA update, but ignore extreme outliers
                                    # Outlier: more than 3x the current estimate
                                    if SampleRTT < 3 * EstimatedRTT:
                                        EstimatedRTT = (1 - alpha) * EstimatedRTT + alpha * SampleRTT
                                        DevRTT = (1 - beta) * DevRTT + beta * abs(SampleRTT - EstimatedRTT)

                                # Calculate RTO with higher weight on DevRTT for jitter
                                RTO = EstimatedRTT + 6 * DevRTT  # Increased from 4 to 6
                                RTO = max(MIN_RTO, min(MAX_RTO, RTO))
                                break

                        # CPU OPT: Batch operations
                        for i in range(send_base_idx, cum_ack_idx):
                            acked_packets[i] = 1
                            sacked_packets[i] = 0
                            retransmitted_packets[i] = 0
                            if i in packet_timers:
                                del packet_timers[i]

                        send_base_idx = cum_ack_idx
                        dup_ack_count = 0
                        last_cum_ack = ack_num
                        last_progress_time = current_time
                        last_window_update_time = current_time

                    elif ack_num == last_cum_ack:
                        dup_ack_count += 1

                    if dup_ack_count == MAX_DUPLICATE_ACKS:
                        # Fast retransmit with cooldown
                        # Find and retransmit gaps
                        gaps_found = []
                        temp = []
                        # received_later = False
                        for idx in range(send_base_idx, min(next_seq_idx, send_base_idx + SWS_PACKETS)):
                            if acked_packets[idx] == 0 and sacked_packets[idx] == 0:
                                temp.append(idx)
                            else:
                                gaps_found.extend(temp)
                                temp = []
                                continue
                        # if received_later:
                        for gap_idx in gaps_found:
                            if gap_idx in packet_timers and (current_time - packet_timers[gap_idx] < min(RTO / 50, ReTrCo)):
                                continue
                            print("Fast retransmit", 1180 * gap_idx)
                            sock_sendto(all_packets[gap_idx], client_address)
                            packet_timers[gap_idx] = current_time
                            retransmitted_packets[gap_idx] = 1
                        dup_ack_count = 0

                    # SACK Recovery
                    if sack_blocks:
                        for sack_start, sack_end in sack_blocks:
                            start_idx = bisect.bisect_left(packet_seq_nums, sack_start)

                            for idx in range(start_idx, min(total_packets, start_idx + 100)):
                                if packet_seq_nums[idx] >= sack_end:
                                    break

                                if packet_seq_nums[idx] >= sack_start:
                                    if sacked_packets[idx] == 0:
                                        sacked_packets[idx] = 1
                                        if idx in packet_timers:
                                            del packet_timers[idx]

                except socket.error as e:
                    if e.errno == errno.EAGAIN or e.errno == errno.EWOULDBLOCK:
                        break
                    else:
                        print(f"Socket error: {e}")
                        break
                finally:
                    pass

        # Fallback retransmission with adaptive timeout
        # if current_time - last_window_update_time > RTO:
        # for idx in range(send_base_idx, min(next_seq_idx, send_base_idx + SWS_PACKETS)):
        # if acked_packets[idx]==0 and sacked_packets[idx]==0:
        # if current_time - packet_timers[idx] < min(RTO/50, ReTrCo):
        # continue
        # sock_sendto(all_packets[idx], client_address)
        # packet_timers[idx] = current_time
        # retransmitted_packets[idx]=1
        # last_window_update_time = current_time
        # break

        # Handle final ACK wait
        if send_base_idx >= total_packets - 1 and all_packets_sent_once:
            if final_ack_wait_start is None:
                final_ack_wait_start = current_time
                print("Waiting for final ACK...")
            elif current_time - final_ack_wait_start > FINAL_ACK_WAIT:
                print("Final ACK wait timeout. Assuming transfer complete.")
                break

    print(f"File transfer complete. Final RTO: {RTO:.3f}s")
    sock.close()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"Usage: python3 {sys.argv[0]} <SERVER_IP> <SERVER_PORT> <SWS>")
        sys.exit(1)

    server_ip = sys.argv[1]
    try:
        server_port = int(sys.argv[2])
        sws_bytes = int(sys.argv[3])
    except ValueError:
        print("Error: SERVER_PORT and SWS must be integers.")
        sys.exit(1)

    run_server(server_ip, server_port, sws_bytes)