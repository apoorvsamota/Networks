import socket
import struct
import time
import sys
import os
import heapq
import select

class SelectiveRepeatServer:
    def __init__(self, server_ip, server_port, sws):
        self.server_ip = server_ip
        self.server_port = server_port
        self.sws = sws
        self.mss = 1180
        self.header_size = 20
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.server_ip, self.server_port))

        self.socket.setblocking(False)

        self.estimated_rtt = 0.1
        self.dev_rtt = 0.05
        self.rto = 0.25
        self.alpha = 0.125
        self.beta = 0.25
        self.is_first_rtt_sample = True

        self.timeout_heap = []
        self.retransmitted_packets = set()
        self.max_sacked_ever = 0

        self.base = 0
        self.next_seq = 0
        self.total_bytes = 0
        self.packets = {} # [MODIFIED] This will now be a pre-filled cache
        self.client_addr = None
        self.file_data = None # [MODIFIED] We will clear this after caching

        print(f"Server listening on {self.server_ip}:{self.server_port}")
        print(f"Sender Window Size: {self.sws} bytes")
        print(f"ULTIMATE OPTIMIZATION v4:")
        print(f"  - Non-Blocking 'select()' loop")
        print(f"  - Smart 'Gap-Based' SACK Fast Retransmit")
        print(f"  - [NEW] All Packets Pre-Cached (Memory-for-Speed)")
        print(f"  - Karn's Algorithm (Fixes RTT Pollution)")

    def calculate_rto(self, sample_rtt):
        if self.is_first_rtt_sample:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
            self.is_first_rtt_sample = False
        else:
            self.dev_rtt = (1 - self.beta) * self.dev_rtt + self.beta * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - self.alpha) * self.estimated_rtt + self.alpha * sample_rtt

        self.rto = self.estimated_rtt + 4 * self.dev_rtt
        self.rto = max(0.02, min(self.rto, 1.0)) # 20ms min, 1s max

    def create_packet(self, seq_num, data):
        header = struct.pack('!I', seq_num) + b'\x00' * 16
        return header + data

    def parse_ack(self, packet):
        if len(packet) >= 20:
            cum_ack = struct.unpack('!I', packet[:4])[0]
            sack_blocks = []
            for i in range(2):
                offset = 4 + i * 8
                if offset + 8 <= len(packet):
                    left_edge = struct.unpack('!I', packet[offset:offset+4])[0]
                    right_edge = struct.unpack('!I', packet[offset+4:offset+8])[0]
                    if right_edge > left_edge:
                        sack_blocks.append((left_edge, right_edge))
            return cum_ack, sack_blocks
        return None, []

    def update_sack_scoreboard(self, acked_packets, sack_blocks):
        for left_edge, right_edge in sack_blocks:
            byte_offset = left_edge
            while byte_offset < right_edge:
                acked_packets.add(byte_offset)
                byte_offset += self.mss

    def _fill_window(self, send_times, packet_timeouts):
        """Sends new packets as long as the window has space."""
        while self.next_seq < self.total_bytes and self.next_seq < self.base + self.sws:
            if self.next_seq not in send_times:

                # --- [MODIFIED] ---
                # Removed on-demand packet creation. We just send from the cache.
                self.socket.sendto(self.packets[self.next_seq], self.client_addr)
                # --- [END MODIFIED] ---

                current_time = time.time()
                expiration_time = current_time + self.rto
                send_times[self.next_seq] = current_time
                packet_timeouts[self.next_seq] = expiration_time
                heapq.heappush(self.timeout_heap, (expiration_time, self.next_seq))

            self.next_seq += self.mss

    def _process_acks(self, send_times, packet_timeouts, acked_packets, dup_ack_count, last_ack_num_ref):
        """Processes all ACKs currently in the socket's receive buffer."""
        last_ack_num = last_ack_num_ref[0]
        try:
            while True:
                ack_packet, _ = self.socket.recvfrom(1024)
                ack_num, sack_blocks = self.parse_ack(ack_packet)

                if ack_num is None:
                    continue

                if sack_blocks:
                    self.update_sack_scoreboard(acked_packets, sack_blocks)
                    current_max_sack = max(block[1] for block in sack_blocks)
                    self.max_sacked_ever = max(self.max_sacked_ever, current_max_sack)

                if ack_num == last_ack_num and ack_num == self.base:
                    dup_ack_count[ack_num] = dup_ack_count.get(ack_num, 0) + 1

                    if dup_ack_count[ack_num] == 2: # 2-Dup-ACK
                        print(f"FAST RETRANSMIT TRIGGER (2 dup ACKs) at byte {self.base}")

                        byte_offset = self.base
                        while byte_offset < self.max_sacked_ever:
                            if byte_offset in packet_timeouts and byte_offset not in acked_packets:
                                print(f"  -> Gap-FR: Retransmitting byte {byte_offset}")
                                self.socket.sendto(self.packets[byte_offset], self.client_addr)
                                current_time = time.time()
                                expiration_time = current_time + self.rto
                                send_times[byte_offset] = current_time
                                packet_timeouts[byte_offset] = expiration_time
                                heapq.heappush(self.timeout_heap, (expiration_time, byte_offset))
                                self.retransmitted_packets.add(byte_offset)

                            byte_offset += self.mss

                        dup_ack_count[ack_num] = 0

                last_ack_num = ack_num

                if ack_num > self.base:
                    if (self.base in send_times and
                        self.base not in acked_packets and
                        self.base not in self.retransmitted_packets):

                        sample_rtt = time.time() - send_times[self.base]
                        self.calculate_rto(sample_rtt)

                    byte_offset = self.base
                    while byte_offset < ack_num:
                        if byte_offset in send_times: del send_times[byte_offset]
                        if byte_offset in packet_timeouts: del packet_timeouts[byte_offset]
                        acked_packets.discard(byte_offset)
                        self.retransmitted_packets.discard(byte_offset)
                        byte_offset += self.mss

                    self.base = ack_num
                    dup_ack_count.clear()

                    if self.next_seq < self.base:
                        self.next_seq = self.base

                    self._fill_window(send_times, packet_timeouts)

        except BlockingIOError:
            pass # Socket buffer is empty

        last_ack_num_ref[0] = last_ack_num

    def _process_timeouts(self, send_times, packet_timeouts, acked_packets):
        current_time = time.time()
        packets_timed_out = False

        while self.timeout_heap and self.timeout_heap[0][0] <= current_time:
            exp_time, byte_offset = heapq.heappop(self.timeout_heap)
            if byte_offset not in packet_timeouts or packet_timeouts[byte_offset] != exp_time:
                continue
            if byte_offset not in acked_packets:
                print(f"TIMEOUT: Retransmitting byte {byte_offset}")
                if byte_offset in self.packets:
                    self.socket.sendto(self.packets[byte_offset], self.client_addr)
                    packets_timed_out = True
                    new_expiration = current_time + self.rto
                    send_times[byte_offset] = current_time
                    packet_timeouts[byte_offset] = new_expiration
                    heapq.heappush(self.timeout_heap, (new_expiration, byte_offset))
                    self.retransmitted_packets.add(byte_offset)
                else:
                    print(f"ERROR: Timed out packet {byte_offset} not in cache!")

        if packets_timed_out:
            print(f"Timeout occurred, RTO remains {self.rto:.4f}s")

    # --- [MODIFIED] ---
    # No arguments needed, all setup is done in prepare_file
    def send_file(self):
        # [REMOVED] Redundant setup, now done in prepare_file
        # self.total_bytes = len(file_data)
        # self.client_addr = client_addr
        # self.file_data = file_data

        # [MODIFIED] These inits are still per-transfer state
        self.base = 0
        self.next_seq = 0
        self.max_sacked_ever = 0

        print(f"Starting transfer for {self.total_bytes} bytes")

        acked_packets = set()
        send_times = {}
        packet_timeouts = {}
        dup_ack_count = {}
        last_ack_num_ref = [-1]

        while self.base < self.total_bytes:
            self._fill_window(send_times, packet_timeouts)

            min_timeout = 0.1
            current_time = time.time()
            while self.timeout_heap:
                exp_time, byte_offset = self.timeout_heap[0]
                if byte_offset not in packet_timeouts or packet_timeouts[byte_offset] != exp_time:
                    heapq.heappop(self.timeout_heap)
                    continue
                time_remaining = exp_time - current_time
                if time_remaining > 0:
                    min_timeout = max(0.001, min(time_remaining, 0.1))
                else:
                    min_timeout = 0.001
                break

            readable, _, _ = select.select([self.socket], [], [], min_timeout)

            if readable:
                self._process_acks(send_times, packet_timeouts, acked_packets, dup_ack_count, last_ack_num_ref)

            self._process_timeouts(send_times, packet_timeouts, acked_packets)

        print("File transfer complete, sending EOF")
        self.socket.setblocking(True)
        # Create EOF on the fly (it's not in the cache)
        eof_packet = self.create_packet(self.total_bytes, b'EOF')
        for _ in range(5):
            self.socket.sendto(eof_packet, self.client_addr)
            time.sleep(0.1)

        self.socket.settimeout(1.0)
        try:
            ack_packet, _ = self.socket.recvfrom(1024)
            print("Received final ACK from client")
        except socket.timeout:
            print("Timeout waiting for final ACK (normal)")

    def run(self):
        print("Waiting for client request...")

        self.socket.setblocking(True)
        request, client_addr = self.socket.recvfrom(1024)
        print(f"Received request from {client_addr}")
        self.socket.setblocking(False)

        try:
            with open('data.txt', 'rb') as f:
                file_data = f.read()
        except FileNotFoundError:
            print("Error: data.txt not found")
            return

        # --- [MODIFIED] ---
        # Call prepare_file first to build the packet cache
        self.prepare_file(client_addr, file_data)

        # Then call send_file, which now uses the cache
        self.send_file()
        # --- [END MODIFIED] ---

        print("Server finished")

    # --- [NEW] ---
    # Your requested method to pre-cache all packets
    def prepare_file(self, client_addr, file_data):
        """Pre-create all packets for faster transmission"""
        self.total_bytes = len(file_data)
        self.client_addr = client_addr
        # self.file_data = file_data # We don't need to store the raw data
        self.packets = {}

        num_packets = (self.total_bytes + self.mss - 1) // self.mss
        print(f"Pre-caching {num_packets} packets...")

        byte_offset = 0
        while byte_offset < self.total_bytes:
            chunk = file_data[byte_offset:byte_offset + self.mss]
            self.packets[byte_offset] = self.create_packet(byte_offset, chunk)
            byte_offset += self.mss

        # We can now free the memory from the raw file data
        self.file_data = None
        print(f"Packet cache ready! ({len(self.packets)} packets)")
    # --- [END NEW] ---

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>")
        sys.exit(1)

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    sws = int(sys.argv[3])

    server = SelectiveRepeatServer(server_ip, server_port, sws)
    server.run()

if __name__ == "__main__":
    main()