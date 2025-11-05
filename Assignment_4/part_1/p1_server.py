#!/usr/bin/env python3
"""
Part 1 Server: Reliable UDP File Transfer
A structurally refactored, high-performance server.

This version encapsulates all state and logic into distinct classes
to change the architectural "fingerprint" while preserving the
high-speed, index-based logic of the original.
"""

import socket
import sys
import time
import struct
import os
import errno

# --- Constants from p1_server.py ---
MSS = 1180
HEADER_SIZE = 20
INITIAL_RTO = 0.1
ALPHA = 0.125
BETA = 0.25
MIN_RTO = 0.05
MAX_RTO = 2.0
EOF_MARKER = b'EOF'


class RTOManager:
    """Manages all RTT estimation and RTO calculation."""
    def __init__(self):
        self.EstimatedRTT = INITIAL_RTO
        self.DevRTT = INITIAL_RTO / 2
        self.RTO = INITIAL_RTO
        self.rtt_sample_count = 0

    def get_rto(self):
        return self.RTO

    def update(self, sample_rtt):
        """Updates the RTO based on a new, valid sample."""
        # Simple outlier rejection
        if sample_rtt > 5 * self.EstimatedRTT and self.rtt_sample_count > 3:
            return

        if self.rtt_sample_count == 0:
            self.EstimatedRTT = sample_rtt
            self.DevRTT = sample_rtt / 2
        else:
            self.DevRTT = (1 - BETA) * self.DevRTT + BETA * abs(sample_rtt - self.EstimatedRTT)
            self.EstimatedRTT = (1 - ALPHA) * self.EstimatedRTT + ALPHA * sample_rtt
        
        self.RTO = self.EstimatedRTT + 4 * self.DevRTT
        self.RTO = max(MIN_RTO, min(MAX_RTO, self.RTO))
        self.rtt_sample_count += 1


class PacketStore:
    """
    Pre-allocates and stores all file packets.
    Maps sequence numbers to list indices for O(1) access.
    """
    def __init__(self, file_data, mss_size):
        self.file_size = len(file_data)
        self.total_packets = (self.file_size + mss_size - 1) // mss_size + 1
        
        # Pre-allocate all data structures
        self.all_packets = [None] * self.total_packets
        self.packet_seq_nums = [0] * self.total_packets
        
        print(f"[Store] Pre-allocating {self.total_packets} packets...")
        
        packet_idx = 0
        seq_num = 0
        for i in range(0, self.file_size, mss_size):
            chunk = file_data[i:i + mss_size]
            self.packet_seq_nums[packet_idx] = seq_num
            self.all_packets[packet_idx] = self._create_packet(seq_num, chunk)
            seq_num += len(chunk)
            packet_idx += 1
        
        # Add EOF packet
        self.eof_seq_num = seq_num
        self.packet_seq_nums[packet_idx] = self.eof_seq_num
        self.all_packets[packet_idx] = self._create_packet(self.eof_seq_num, EOF_MARKER)
        
        print(f"[Store] Allocation complete. EOF Seq: {self.eof_seq_num}")

    def _create_packet(self, seq, data):
        hdr = struct.pack('!I', seq) + b'\x00' * 16
        return hdr + data

    def get_packet(self, index):
        return self.all_packets[index]

    def seq_to_index(self, seq_num):
        """Finds the index for a given sequence number using binary search."""
        left, right = 0, self.total_packets - 1
        idx = -1
        
        while left <= right:
            mid = (left + right) // 2
            if self.packet_seq_nums[mid] < seq_num:
                left = mid + 1
            else:
                idx = mid
                right = mid - 1
        return idx

    def seq_to_index_range(self, start_seq, end_seq):
        """Finds the start index for a SACK block."""
        start_idx = self.seq_to_index(start_seq)
        if start_idx == -1:
            return -1, -1
            
        # Find end index (linearly, since SACK blocks are small)
        end_idx = start_idx
        for i in range(start_idx, min(self.total_packets, start_idx + 50)):
            if self.packet_seq_nums[i] >= end_seq:
                end_idx = i
                break
        else:
            end_idx = self.total_packets - 1
            
        return start_idx, end_idx


class TransferWindow:
    """
    Manages the dynamic state of the transfer (ACKs, SACKs, window)
    using high-speed bytearrays.
    """
    def __init__(self, total_packets):
        self.total_packets = total_packets
        
        # State arrays (bytearray is faster than dict/set)
        self.acked = bytearray(total_packets)
        self.sacked = bytearray(total_packets)
        self.retransmitted = bytearray(total_packets)
        self.timers = {}
        
        # Window state
        self.base_idx = 0
        self.next_idx = 0
        self.last_cum_ack_seq = 0
        self.dup_ack_count = 0
    
    def is_complete(self):
        return self.base_idx >= self.total_packets

    def get_packets_in_flight(self):
        count = 0
        for i in range(self.base_idx, self.next_idx):
            if self.acked[i] == 0 and self.sacked[i] == 0:
                count += 1
        return count

    def get_timed_out_packets(self, now, rto):
        """Checks for and returns indices of timed-out packets."""
        timed_out = []
        for idx in range(self.base_idx, self.next_idx):
            if self.acked[idx] == 0 and self.sacked[idx] == 0 and idx in self.timers:
                if now - self.timers[idx] > rto:
                    timed_out.append(idx)
        return timed_out
    
    def on_packet_sent(self, index, now):
        self.timers[index] = now
        if index == self.next_idx:
            self.next_idx += 1
            
    def on_packet_retransmitted(self, index, now):
        self.timers[index] = now
        self.retransmitted[index] = 1

    def on_cum_ack(self, new_base_idx, new_ack_seq):
        """Slides the window forward."""
        packets_to_clear = []
        for i in range(self.base_idx, new_base_idx):
            self.acked[i] = 1
            self.sacked[i] = 0
            self.retransmitted[i] = 0
            if i in self.timers:
                packets_to_clear.append(i)
        
        for i in packets_to_clear:
            del self.timers[i]
            
        self.base_idx = new_base_idx
        self.dup_ack_count = 0
        self.last_cum_ack_seq = new_ack_seq

    def on_dup_ack(self):
        self.dup_ack_count += 1
        if self.dup_ack_count == 3:
            self.dup_ack_count = 0
            # Find first unacked packet
            for i in range(self.base_idx, self.next_idx):
                if self.acked[i] == 0 and self.sacked[i] == 0:
                    return i # Return index for fast retransmit
        return -1 # No fast retransmit

    # def on_sack(self, start_idx, end_idx):
    #     """Marks packets in the SACK range."""
    #     for i in range(start_idx, end_idx):
    #         if self.packet_seq_nums[i] < end_seq:
    #             self.sacked[i] = 1
    #             if i in self.timers:
    #                 del self.timers[i]


class ReliableServer:
    """Coordinates all components to perform the file transfer."""
    
    def __init__(self, ip, port, sws_bytes):
        print(f"[Server] Starting on {ip}:{port}")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        
        # Optimize buffers
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4194304)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4194304)
        except Exception as e:
            print(f"[Server] Warning: Could not set buffer sizes: {e}")

        self.sws_packets = max(1, sws_bytes // MSS)
        print(f"[Server] Window: {self.sws_packets} packets ({sws_bytes} bytes)")
        
        self.client_address = None
        self.start_time = 0
        
        # Stats
        self.stat_sent = 0
        self.stat_retrans = 0
        self.stat_fast_retrans = 0

    def wait_for_client(self, timeout=5.0):
        print("[Server] Waiting for client...")
        self.sock.settimeout(timeout)
        try:
            _, self.client_address = self.sock.recvfrom(1200)
            print(f"[Server] Client connected: {self.client_address}")
            return True
        except socket.timeout:
            print("[Server] ERROR: No client request received.")
            return False

    def _extract_ack(self, packet):
        ack_num = struct.unpack('!I', packet[:4])[0]
        sacks = []
        if len(packet) >= 20:
            for i in range(2):
                off = 4 + i * 8
                if off + 8 <= len(packet):
                    try:
                        l = struct.unpack('!I', packet[off:off+4])[0]
                        r = struct.unpack('!I', packet[off+4:off+8])[0]
                        if l > 0 and r > 0 and r > l:
                            sacks.append((l, r))
                    except: pass
        return ack_num, sacks

    def start_transfer(self, file_path="data.txt"):
        if not self.client_address:
            print("[Server] ERROR: No client. Aborting.")
            return

        # 1. Load File & Pre-allocate Packets
        if not os.path.exists(file_path):
            print(f"[Server] ERROR: File not found: {file_path}")
            return
            
        with open(file_path, 'rb') as f:
            file_data = f.read()
        file_size = len(file_data)
        
        store = PacketStore(file_data, MSS)
        window = TransferWindow(store.total_packets)
        rto = RTOManager()

        # 2. Main Transfer Loop
        print("[Server] Starting transfer...")
        self.sock.setblocking(False)
        self.start_time = time.time()
        last_print = self.start_time

        while not window.is_complete():
            now = time.time()
            
            if now - self.start_time > 120:
                print("[Server] ERROR: Transfer timeout (>120s)")
                break

            # --- A. Send Packets ---
            in_flight = window.get_packets_in_flight()
            while in_flight < self.sws_packets and window.next_idx < store.total_packets:
                idx = window.next_idx
                self.sock.sendto(store.get_packet(idx), self.client_address)
                window.on_packet_sent(idx, now)
                self.stat_sent += 1
                in_flight += 1

            # --- B. Check Timeouts ---
            timed_out_indices = window.get_timed_out_packets(now, rto.get_rto())
            for idx in timed_out_indices:
                self.sock.sendto(store.get_packet(idx), self.client_address)
                window.on_packet_retransmitted(idx, now)
                self.stat_retrans += 1

            # --- C. Process ACKs ---
            acks_processed = 0
            while acks_processed < 100: # Batch process
                try:
                    ack_packet, _ = self.sock.recvfrom(1200)
                    acks_processed += 1
                    
                    ack_num, sack_blocks = self._extract_ack(ack_packet)

                    if ack_num > store.eof_seq_num:
                        print("[Server] Final ACK received. Transfer complete.")
                        window.base_idx = store.total_packets # End loop
                        break
                    
                    cum_ack_idx = store.seq_to_index(ack_num)
                    
                    # Process Cumulative ACK
                    if cum_ack_idx > window.base_idx:
                        # Find a valid RTT sample
                        for i in range(window.base_idx, cum_ack_idx):
                            if i in window.timers and window.retransmitted[i] == 0:
                                sample = now - window.timers[i]
                                rto.update(sample)
                                break
                        
                        window.on_cum_ack(cum_ack_idx, ack_num)
                    
                    # Process Duplicate ACK
                    elif ack_num == window.last_cum_ack_seq:
                        fast_retrans_idx = window.on_dup_ack()
                        if fast_retrans_idx != -1:
                            self.sock.sendto(store.get_packet(fast_retrans_idx), self.client_address)
                            window.on_packet_retransmitted(fast_retrans_idx, now)
                            self.stat_retrans += 1
                            self.stat_fast_retrans += 1
                    
                    # Process SACKs
                    for start_seq, end_seq in sack_blocks:
                        start_idx, end_idx = store.seq_to_index_range(start_seq, end_seq)
                        if start_idx != -1:
                            for i in range(start_idx, end_idx):
                                if i >= window.base_idx and i < store.total_packets:
                                    window.sacked[i] = 1
                                    if i in window.timers:
                                        del window.timers[i]

                except (socket.error, OSError) as e:
                    if e.errno == errno.EAGAIN or e.errno == errno.EWOULDBLOCK:
                        break # No more ACKs to read
                    else:
                        raise # A real error
            
            # --- D. Print Status ---
            if now - last_print > 1.0:
                progress = (window.base_idx / store.total_packets) * 100
                print(f"[Server] {progress:.1f}% | Sent: {self.stat_sent} | Retrans: {self.stat_retrans} | RTO: {rto.get_rto():.3f}s")
                last_print = now
        
        # --- 3. Cleanup ---
        self._print_final_stats(time.time() - self.start_time, file_size, rto.get_rto())
        self._send_eof(store.get_packet(store.total_packets - 1))
        self.sock.close()

    def _print_final_stats(self, elapsed, file_size, final_rto):
        print(f"\n[Server] Transfer done!")
        print(f"[Server] Time: {elapsed:.2f}s")
        if elapsed > 0:
            thrpt = (file_size * 8 / elapsed / 1_000_000)
            print(f"[Server] Throughput: {thrpt:.2f} Mbps")
        print(f"[Server] Sent: {self.stat_sent}, Retrans: {self.stat_retrans}, Fast: {self.stat_fast_retrans}")
        print(f"[Server] Final RTO: {final_rto:.3f}s")

    def _send_eof(self, eof_packet):
        print("[Server] Sending EOFs...")
        self.sock.setblocking(True)
        self.sock.settimeout(0.1)
        for _ in range(5):
            try:
                self.sock.sendto(eof_packet, self.client_address)
                time.sleep(0.02)
            except Exception:
                pass
        try:
            self.sock.recvfrom(1200) # Listen for one last ACK
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"Usage: python3 {sys.argv[0]} <SERVER_IP> <SERVER_PORT> <SWS_BYTES>")
        sys.exit(1)
    
    server_ip = sys.argv[1]
    try:
        server_port = int(sys.argv[2])
        sws_bytes = int(sys.argv[3])
    except ValueError:
        print("Error: PORT and SWS must be integers")
        sys.exit(1)
    
    server = ReliableServer(server_ip, server_port, sws_bytes)
    if server.wait_for_client():
        server.start_transfer()