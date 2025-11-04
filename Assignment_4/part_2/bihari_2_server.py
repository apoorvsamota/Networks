#!/usr/bin/env python3
"""
Part 2 Server: TCP CUBIC Congestion Control
Based on RFC 8312
"""

import socket
import sys
import time
import struct
import os
import math

# Constants
MAX_PACKET_SIZE = 1200
HEADER_SIZE = 20
MAX_DATA_SIZE = MAX_PACKET_SIZE - HEADER_SIZE
MSS = MAX_DATA_SIZE  # Maximum Segment Size
INITIAL_RTO = 0.2
ALPHA = 0.125
BETA = 0.25
EOF_MARKER = b"EOF"
MIN_RTO = 0.05  # More aggressive
MAX_RTO = 1.0  # Lower max

# CUBIC parameters (RFC 8312)
CUBIC_C = 0.8  # CUBIC scaling constant (more aggressive)
CUBIC_BETA = 0.7  # Window reduction factor on loss (70%)
FAST_CONVERGENCE = True


class CUBICCongestionControl:
    def __init__(self):
        # Congestion window (in bytes)
        self.cwnd = MSS  # Start with 1 MSS
        self.ssthresh = 256 * MSS  # Higher threshold (~300KB) for better performance

        # CUBIC specific
        self.w_max = 0  # Window size before last reduction
        self.epoch_start = 0  # Beginning of epoch
        self.origin_point = 0  # Origin point of cubic function
        self.d_min = float('inf')  # Minimum RTT observed
        self.w_tcp = 0  # TCP-friendly window
        self.ack_count = 0  # ACKs received in current RTT

        # State
        self.in_slow_start = True
        self.last_cwnd_update = time.time()

    def update_rtt(self, rtt):
        """Update minimum RTT"""
        if rtt < self.d_min:
            self.d_min = rtt

    def on_ack(self, acked_bytes, rtt):
        """Called when ACK received"""
        self.update_rtt(rtt)

        if self.in_slow_start:
            # Slow start: exponential growth
            # Standard TCP: increase by acked_bytes (1 MSS per ACK typically)
            self.cwnd += acked_bytes

            # Exit slow start if cwnd >= ssthresh
            if self.cwnd >= self.ssthresh:
                self.in_slow_start = False
        else:
            # Congestion avoidance: use CUBIC
            self.cubic_update(acked_bytes, rtt)

        # Cap cwnd to prevent excessive bursting (but higher limit)
        max_cwnd = 500 * MSS  # ~590KB max window (increased from 200)
        self.cwnd = min(self.cwnd, max_cwnd)

        return int(self.cwnd)

    def cubic_update(self, acked_bytes, rtt):
        """CUBIC congestion avoidance"""
        self.ack_count += acked_bytes

        # Calculate time since epoch start
        if self.epoch_start == 0:
            self.epoch_start = time.time()
            self.ack_count = acked_bytes

        if self.w_max < self.cwnd:
            # Fast convergence
            if FAST_CONVERGENCE:
                self.w_max = self.cwnd * (2 - CUBIC_BETA) / 2
            else:
                self.w_max = self.cwnd
        else:
            self.w_max = self.cwnd

        # Calculate origin point
        self.origin_point = self.w_max

        t = time.time() - self.epoch_start

        # CUBIC window calculation
        K = math.pow((self.w_max * (1 - CUBIC_BETA)) / CUBIC_C, 1/3)
        target = CUBIC_C * math.pow(t - K, 3) + self.w_max

        # TCP-friendly window
        self.w_tcp += (3 * CUBIC_BETA / (2 - CUBIC_BETA)) * (acked_bytes / self.cwnd)

        # Use max of CUBIC and TCP-friendly (more aggressive)
        target = max(target, self.w_tcp)

        # More aggressive increase
        if target > self.cwnd:
            # Increase faster
            increase = max(MSS, int((target - self.cwnd) / 10))
            self.cwnd += increase
        else:
            # Still increase slowly
            self.cwnd += MSS

    def on_loss(self, loss_type="timeout"):
        """Called on packet loss"""
        if loss_type == "fast_retransmit":
            # Multiplicative decrease
            if FAST_CONVERGENCE and self.cwnd < self.w_max:
                self.w_max = self.cwnd * (2 - CUBIC_BETA) / 2
            else:
                self.w_max = self.cwnd

            self.ssthresh = max(int(self.cwnd * CUBIC_BETA), 2 * MSS)
            self.cwnd = self.ssthresh
            self.epoch_start = 0  # Reset epoch
        else:
            # Timeout: more severe
            self.ssthresh = max(int(self.cwnd / 2), 2 * MSS)
            self.cwnd = MSS  # Back to 1 MSS
            self.in_slow_start = True
            self.epoch_start = 0
            self.w_max = 0

    def get_cwnd(self):
        """Get current congestion window"""
        return int(self.cwnd)


class CUBICServer:
    def __init__(self, server_ip, server_port):
        self.server_ip = server_ip
        self.server_port = server_port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('0.0.0.0', self.server_port))

        # Congestion control
        self.cc = CUBICCongestionControl()

        # RTT estimation
        self.estimated_rtt = None
        self.dev_rtt = None
        self.rto = INITIAL_RTO

        # Sliding window
        self.base = 0
        self.next_seq = 0

        # Packet tracking
        self.acked_packets = set()
        self.send_times = {}
        self.packets = {}
        self.packet_timeouts = {}
        self.dup_ack_count = {}

        # Statistics
        self.total_packets_sent = 0
        self.total_retransmissions = 0
        self.total_acks_received = 0
        self.fast_retransmits = 0

        print(f"[SERVER] CUBIC Server on port {self.server_port}")

    def create_packet(self, seq_num, data):
        """Create packet with header + data"""
        header = struct.pack('!I', seq_num) + b'\x00' * 16
        return header + data

    def parse_ack(self, packet):
        """Parse ACK packet"""
        if len(packet) < 4:
            return None, []

        ack_num = struct.unpack('!I', packet[:4])[0]

        # Parse SACK blocks
        sack_blocks = []
        if len(packet) >= 20:
            try:
                for i in range(2):
                    offset = 4 + i * 8
                    if offset + 8 <= len(packet):
                        left = struct.unpack('!I', packet[offset:offset+4])[0]
                        right = struct.unpack('!I', packet[offset+4:offset+8])[0]
                        if left > 0 and right > left:
                            sack_blocks.append((left, right))
            except:
                pass

        return ack_num, sack_blocks

    def update_rto(self, sample_rtt):
        """Update RTO"""
        if self.estimated_rtt is None:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
        else:
            self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + ALPHA * sample_rtt

        self.rto = self.estimated_rtt + 4 * self.dev_rtt
        self.rto = max(MIN_RTO, min(self.rto, MAX_RTO))

    def send_packet(self, seq_num, data, client_addr):
        """Send packet"""
        packet = self.create_packet(seq_num, data)
        self.socket.sendto(packet, client_addr)

        current_time = time.time()
        self.send_times[seq_num] = current_time
        self.packets[seq_num] = packet
        self.packet_timeouts[seq_num] = current_time + self.rto
        self.total_packets_sent += 1

    def retransmit_packet(self, seq_num, client_addr, reason="timeout"):
        """Retransmit packet"""
        if seq_num in self.packets and seq_num not in self.acked_packets:
            self.socket.sendto(self.packets[seq_num], client_addr)
            current_time = time.time()
            self.send_times[seq_num] = current_time
            self.packet_timeouts[seq_num] = current_time + self.rto
            self.total_retransmissions += 1
            if reason == "fast_retransmit":
                self.fast_retransmits += 1

    def get_next_timeout(self):
        """Get earliest timeout"""
        if not self.packet_timeouts:
            return self.rto

        current_time = time.time()
        min_timeout = min(self.packet_timeouts.values())
        return max(0.01, min_timeout - current_time)

    def check_timeouts(self, client_addr):
        """Check for timed out packets"""
        current_time = time.time()
        timed_out = []

        for seq_num, timeout_time in list(self.packet_timeouts.items()):
            if seq_num not in self.acked_packets and current_time >= timeout_time:
                timed_out.append(seq_num)

        if timed_out:
            for seq_num in timed_out:
                self.retransmit_packet(seq_num, client_addr, "timeout")

            # Update congestion control
            self.cc.on_loss("timeout")
            self.rto = min(self.rto * 1.2, MAX_RTO)  # Gentler backoff

    def slide_window(self):
        """Slide window forward"""
        while self.base in self.acked_packets:
            self.acked_packets.remove(self.base)
            if self.base in self.send_times:
                del self.send_times[self.base]
            if self.base in self.packets:
                del self.packets[self.base]
            if self.base in self.packet_timeouts:
                del self.packet_timeouts[self.base]

            self.base += MSS

    def send_file(self, file_data, client_addr):
        """Send file with CUBIC congestion control"""
        file_size = len(file_data)
        print(f"\n[SERVER] Starting transfer: {file_size} bytes")
        print(f"[SERVER] Packets needed: {(file_size + MSS - 1) // MSS}")

        start_time = time.time()

        while self.base < file_size:
            # SEND PHASE: Send packets within cwnd
            cwnd = self.cc.get_cwnd()

            while self.next_seq < self.base + cwnd and self.next_seq < file_size:
                if self.next_seq not in self.acked_packets:
                    end_pos = min(self.next_seq + MSS, file_size)
                    data = file_data[self.next_seq:end_pos]
                    self.send_packet(self.next_seq, data, client_addr)

                self.next_seq += MSS

            # RECEIVE PHASE
            timeout = self.get_next_timeout()
            self.socket.settimeout(timeout)

            try:
                ack_packet, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
                receive_time = time.time()
                ack_num, sack_blocks = self.parse_ack(ack_packet)

                if ack_num is None:
                    continue

                self.total_acks_received += 1

                # Process cumulative ACK
                if ack_num > self.base:
                    acked_bytes = ack_num - self.base

                    # Update RTT
                    if self.base in self.send_times:
                        sample_rtt = receive_time - self.send_times[self.base]
                        self.update_rto(sample_rtt)

                    # Update congestion control
                    self.cc.on_ack(acked_bytes, sample_rtt)

                    # Mark packets as acked
                    seq = self.base
                    while seq < ack_num:
                        if seq not in self.acked_packets:
                            self.acked_packets.add(seq)
                        seq += MSS

                    self.slide_window()
                    self.dup_ack_count.clear()

                # Process SACK blocks
                for left, right in sack_blocks:
                    seq = left
                    while seq < right and seq < file_size:
                        if seq >= self.base and seq not in self.acked_packets:
                            self.acked_packets.add(seq)
                        seq += MSS

                # Duplicate ACK - fast retransmit
                if ack_num == self.base:
                    if ack_num not in self.dup_ack_count:
                        self.dup_ack_count[ack_num] = 0
                    self.dup_ack_count[ack_num] += 1

                    if self.dup_ack_count[ack_num] == 3:
                        if self.base not in self.acked_packets:
                            self.retransmit_packet(self.base, client_addr, "fast_retransmit")
                            self.cc.on_loss("fast_retransmit")

            except socket.timeout:
                self.check_timeouts(client_addr)

        elapsed = time.time() - start_time
        throughput = (file_size * 8 / elapsed / 1_000_000)

        print(f"[SERVER] Done: {elapsed:.2f}s, {throughput:.2f}Mbps")

        # Send EOF
        eof_packet = self.create_packet(file_size, EOF_MARKER)
        for _ in range(5):
            self.socket.sendto(eof_packet, client_addr)
            time.sleep(0.05)

    def run(self):
        """Main server loop"""
        print(f"\n[SERVER] Waiting for client request...")

        self.socket.settimeout(30.0)

        try:
            request, client_addr = self.socket.recvfrom(MAX_PACKET_SIZE)
            print(f"[SERVER] Request from {client_addr}")
        except socket.timeout:
            print(f"[SERVER] ERROR: No client request")
            return

        self.socket.settimeout(None)

        filename = "data.txt"
        if not os.path.exists(filename):
            print(f"[SERVER] ERROR: {filename} not found!")
            return

        with open(filename, 'rb') as f:
            file_data = f.read()

        print(f"[SERVER] Loaded {filename}: {len(file_data)} bytes")

        self.send_file(file_data, client_addr)
        self.socket.close()


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)

    server = CUBICServer(sys.argv[1], int(sys.argv[2]))
    server.run()


if __name__ == "__main__":
    main()