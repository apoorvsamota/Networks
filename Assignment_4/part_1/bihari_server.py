#!/usr/bin/env python3
"""
Part 1 Server: Reliable File Transfer over UDP
Implements Selective Repeat protocol with optimized performance
"""

import socket
import sys
import time
import struct
import os

# Constants
MAX_PACKET_SIZE = 1200
HEADER_SIZE = 20
MAX_DATA_SIZE = MAX_PACKET_SIZE - HEADER_SIZE # 1180 bytes
INITIAL_RTO = 0.25 # Optimized initial RTO
ALPHA = 0.125
BETA = 0.25
EOF_MARKER = b"EOF"
MIN_RTO = 0.1 # Minimum RTO
MAX_RTO = 2.0 # Maximum RTO

class SelectiveRepeatServer:
    def __init__(self, server_ip, server_port, sws):
    self.server_ip = server_ip
    self.server_port = server_port
    self.sws = sws
    self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.socket.bind((self.server_ip, self.server_port))

    # RTT estimation
    self.estimated_rtt = None
    self.dev_rtt = None
    self.rto = INITIAL_RTO

    # Sliding window
    self.base = 0
    self.next_seq = 0

    # Selective Repeat specific: track individual packet acknowledgments
    self.acked_packets = set() # Set of acknowledged sequence numbers
    self.send_times = {} # seq_num -> send_time (for RTT calculation)
    self.packets = {} # seq_num -> packet_data
    self.packet_timeouts = {} # seq_num -> timeout_time
    self.dup_ack_count = {}

    # Statistics
    self.total_packets_sent = 0
    self.total_retransmissions = 0
    self.total_acks_received = 0
    self.fast_retransmits = 0

    print(f"[SERVER] Initialized (Selective Repeat)")
    print(f"[SERVER] Server: {server_ip}:{server_port}")
    print(f"[SERVER] SWS: {sws} bytes")

    def create_packet(self, seq_num, data):
    """Create packet with header + data"""
    header = struct.pack('!I', seq_num) + b'\x00' * 16
    return header + data

    def parse_ack(self, packet):
    """Parse ACK packet - supports both cumulative ACK and SACK"""
    if len(packet) < 4:
    return None, []

    ack_num = struct.unpack('!I', packet[:4])[0]

    # Parse SACK blocks if present (16 bytes reserved space)
    sack_blocks = []
    if len(packet) >= 20:
    try:
    # Up to 2 SACK blocks: (left_edge, right_edge) pairs
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
    """Update RTO using TCP-style estimation"""
    if self.estimated_rtt is None:
    self.estimated_rtt = sample_rtt
    self.dev_rtt = sample_rtt / 2
    else:
    self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample_rtt - self.estimated_rtt)
    self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + ALPHA * sample_rtt

    # Less conservative multiplier for better performance
    self.rto = self.estimated_rtt + 3 * self.dev_rtt
    self.rto = max(MIN_RTO, min(self.rto, MAX_RTO))

    def send_packet(self, seq_num, data, client_addr):
    """Send a packet and track it"""
    packet = self.create_packet(seq_num, data)
    self.socket.sendto(packet, client_addr)

    current_time = time.time()
    self.send_times[seq_num] = current_time
    self.packets[seq_num] = packet
    self.packet_timeouts[seq_num] = current_time + self.rto
    self.total_packets_sent += 1

    def retransmit_packet(self, seq_num, client_addr, reason="timeout"):
    """Retransmit a specific packet (Selective Repeat)"""
    if seq_num in self.packets and seq_num not in self.acked_packets:
    self.socket.sendto(self.packets[seq_num], client_addr)
    current_time = time.time()
    self.send_times[seq_num] = current_time
    self.packet_timeouts[seq_num] = current_time + self.rto
    self.total_retransmissions += 1
    if reason == "fast_retransmit":
    self.fast_retransmits += 1

    def get_next_timeout(self):
    """Get the earliest timeout among unacked packets"""
    if not self.packet_timeouts:
    return self.rto

    current_time = time.time()
    min_timeout = min(self.packet_timeouts.values())
    timeout = max(0.01, min_timeout - current_time)
    return timeout

    def check_timeouts(self, client_addr):
    """Check for timed out packets and retransmit them"""
    current_time = time.time()
    timed_out = []

    for seq_num, timeout_time in list(self.packet_timeouts.items()):
    if seq_num not in self.acked_packets and current_time >= timeout_time:
    timed_out.append(seq_num)

    if timed_out:
    print(f"[SERVER] TIMEOUT! Retransmitting {len(timed_out)} packets")
    for seq_num in timed_out:
    self.retransmit_packet(seq_num, client_addr, "timeout")

    # Mild backoff on timeout
    self.rto = min(self.rto * 1.5, MAX_RTO)

    def slide_window(self):
    """Slide the window forward based on acknowledged packets"""
    # Move base to the first unacknowledged packet
    while self.base in self.acked_packets:
    # Clean up acknowledged packet
    self.acked_packets.remove(self.base)
    if self.base in self.send_times:
    del self.send_times[self.base]
    if self.base in self.packets:
    del self.packets[self.base]
    if self.base in self.packet_timeouts:
    del self.packet_timeouts[self.base]

    self.base += MAX_DATA_SIZE

    def send_file(self, file_data, client_addr):
    """Send file using Selective Repeat protocol"""
    file_size = len(file_data)
    print(f"\n[SERVER] Starting transfer: {file_size} bytes")
    print(f"[SERVER] Packets needed: {(file_size + MAX_DATA_SIZE - 1) // MAX_DATA_SIZE}")

    start_time = time.time()
    last_progress_time = start_time

    while self.base < file_size:
    # SEND PHASE: Send new packets within window
    while self.next_seq < self.base + self.sws and self.next_seq < file_size:
    if self.next_seq not in self.acked_packets:
    end_pos = min(self.next_seq + MAX_DATA_SIZE, file_size)
    data = file_data[self.next_seq:end_pos]
    self.send_packet(self.next_seq, data, client_addr)

    self.next_seq += MAX_DATA_SIZE

    # Progress indicator
    current_time = time.time()
    if current_time - last_progress_time > 1.0:
    progress = (self.base / file_size) * 100
    print(f"[SERVER] Progress: {progress:.1f}% (base={self.base}/{file_size})")
    last_progress_time = current_time

    # RECEIVE PHASE: Wait for ACKs with dynamic timeout
    timeout = self.get_next_timeout()
    self.socket.settimeout(timeout)

    try:
    ack_packet, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
    receive_time = time.time()
    ack_num, sack_blocks = self.parse_ack(ack_packet)

    if ack_num is None:
    continue

    self.total_acks_received += 1

    # Process cumulative ACK - mark all packets before ack_num as acknowledged
    if ack_num > self.base:
    seq = self.base
    while seq < ack_num:
    if seq not in self.acked_packets:
    self.acked_packets.add(seq)
    # Update RTT for first acked packet in this ACK
    if seq in self.send_times and self.estimated_rtt is None or seq == self.base:
    sample_rtt = receive_time - self.send_times[seq]
    self.update_rto(sample_rtt)
    seq += MAX_DATA_SIZE

    self.slide_window()
    self.dup_ack_count.clear()

    # Process SACK blocks - mark selectively acknowledged packets
    for left, right in sack_blocks:
    seq = left
    while seq < right and seq < file_size:
    if seq >= self.base and seq not in self.acked_packets:
    self.acked_packets.add(seq)
    seq += MAX_DATA_SIZE

    # Duplicate ACK handling for fast retransmit
    if ack_num == self.base:
    if ack_num not in self.dup_ack_count:
    self.dup_ack_count[ack_num] = 0
    self.dup_ack_count[ack_num] += 1

    if self.dup_ack_count[ack_num] == 3:
    if self.base not in self.acked_packets:
    print(f"[SERVER] FAST RETRANSMIT seq={self.base}")
    self.retransmit_packet(self.base, client_addr, "fast_retransmit")

    except socket.timeout:
    # Check which specific packets timed out
    self.check_timeouts(client_addr)

    elapsed = time.time() - start_time
    print(f"\n[SERVER] Transfer complete!")
    print(f"[SERVER] Time: {elapsed:.2f}s")
    print(f"[SERVER] Throughput: {(file_size * 8 / elapsed / 1_000_000):.2f} Mbps")
    print(f"[SERVER] Packets sent: {self.total_packets_sent}")
    print(f"[SERVER] Retransmissions: {self.total_retransmissions} ({100*self.total_retransmissions/max(1,self.total_packets_sent):.1f}%)")
    print(f"[SERVER] Fast retransmits: {self.fast_retransmits}")
    print(f"[SERVER] Final RTO: {self.rto:.4f}s")

    # Send EOF
    print(f"[SERVER] Sending EOF...")
    eof_packet = self.create_packet(file_size, EOF_MARKER)
    for _ in range(5):
    self.socket.sendto(eof_packet, client_addr)
    time.sleep(0.05)

    def run(self):
    """Main server loop"""
    print(f"\n[SERVER] Waiting for client...")

    request, client_addr = self.socket.recvfrom(MAX_PACKET_SIZE)
    print(f"[SERVER] Request from {client_addr}")

    filename = "data.txt"
    if not os.path.exists(filename):
    print(f"[SERVER] ERROR: {filename} not found!")
    return

    with open(filename, 'rb') as f:
    file_data = f.read()

    print(f"[SERVER] Loaded {filename}: {len(file_data)} bytes")

    self.send_file(file_data, client_addr)
    self.socket.close()
    print(f"[SERVER] Closed")

    def main():
    if len(sys.argv) != 4:
    print("Usage: python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>")
    sys.exit(1)

    server = SelectiveRepeatServer(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
    server.run()

    if __name__ == "__main__":
    main()