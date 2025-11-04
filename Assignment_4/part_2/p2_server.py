#!/usr/bin/env python3
"""
Part 2 Server: Improved CUBIC Congestion Control Implementation
Optimized for better performance and faster termination
"""

import socket
import sys
import time
import struct
import os
import math

# Protocol Constants
MAX_PACKET_SIZE = 1200
HEADER_SIZE = 20
MAX_DATA_SIZE = MAX_PACKET_SIZE - HEADER_SIZE
MSS = MAX_DATA_SIZE
EOF_MARKER = b"EOF"

# RTO Parameters - More aggressive
INITIAL_RTO = 0.15
MIN_RTO = 0.04
MAX_RTO = 0.8
ALPHA = 0.125
BETA = 0.25

# CUBIC Parameters - Optimized for performance
CUBIC_C = 0.85
CUBIC_BETA = 0.65
FAST_CONVERGENCE = True


class ImprovedCUBICControl:
    """Enhanced CUBIC congestion control with better performance"""
    
    def __init__(self):
        # Window management
        self.cwnd = MSS
        self.ssthresh = 280 * MSS  # Higher initial threshold (~330KB)
        
        # CUBIC state
        self.w_max = 0
        self.epoch_start = 0
        self.origin_point = 0
        self.d_min = float('inf')
        self.w_tcp = 0
        self.ack_count = 0
        
        # Phase tracking
        self.in_slow_start = True
        self.last_update = time.time()
        
    def update_min_rtt(self, rtt):
        """Track minimum RTT for CUBIC calculations"""
        if rtt > 0 and rtt < self.d_min:
            self.d_min = rtt
    
    def handle_ack(self, acked_bytes, rtt):
        """Process ACK and update congestion window"""
        self.update_min_rtt(rtt)
        
        if self.in_slow_start:
            # Exponential growth in slow start
            self.cwnd += acked_bytes
            
            # Transition to congestion avoidance
            if self.cwnd >= self.ssthresh:
                self.in_slow_start = False
                self.epoch_start = 0
        else:
            # CUBIC congestion avoidance
            self.cubic_increase(acked_bytes, rtt)
        
        # Limit maximum window
        max_window = 520 * MSS  # ~612KB max
        self.cwnd = min(self.cwnd, max_window)
        
        return int(self.cwnd)
    
    def cubic_increase(self, acked_bytes, rtt):
        """CUBIC window growth during congestion avoidance"""
        self.ack_count += acked_bytes
        
        # Initialize epoch
        if self.epoch_start == 0:
            self.epoch_start = time.time()
            self.ack_count = acked_bytes
            
            if self.w_max < self.cwnd:
                if FAST_CONVERGENCE:
                    self.w_max = self.cwnd * (2 - CUBIC_BETA) / 2
                else:
                    self.w_max = self.cwnd
            else:
                self.w_max = self.cwnd
            
            self.origin_point = self.w_max
        
        # Time since epoch started
        t = time.time() - self.epoch_start
        
        # CUBIC function calculation
        K = math.pow((self.w_max * (1 - CUBIC_BETA)) / CUBIC_C, 1.0/3.0)
        cubic_target = CUBIC_C * math.pow(t - K, 3) + self.w_max
        
        # TCP-friendly window calculation
        self.w_tcp += (3 * CUBIC_BETA / (2 - CUBIC_BETA)) * (acked_bytes / self.cwnd)
        
        # Use more aggressive of CUBIC or TCP-friendly
        target = max(cubic_target, self.w_tcp)
        
        # Increase window
        if target > self.cwnd:
            # Faster increase rate
            increment = max(MSS, int((target - self.cwnd) / 8))
            self.cwnd += increment
        else:
            # Maintain modest growth
            self.cwnd += MSS
    
    def handle_loss(self, loss_event="timeout"):
        """Handle packet loss events"""
        if loss_event == "fast_retransmit":
            # Fast retransmit: multiplicative decrease
            if FAST_CONVERGENCE and self.cwnd < self.w_max:
                self.w_max = self.cwnd * (2 - CUBIC_BETA) / 2
            else:
                self.w_max = self.cwnd
            
            self.ssthresh = max(int(self.cwnd * CUBIC_BETA), 2 * MSS)
            self.cwnd = self.ssthresh
            self.epoch_start = 0
        else:
            # Timeout: severe reduction
            self.ssthresh = max(int(self.cwnd / 2), 2 * MSS)
            self.cwnd = MSS
            self.in_slow_start = True
            self.epoch_start = 0
            self.w_max = 0
    
    def get_window_size(self):
        """Return current congestion window size"""
        return int(self.cwnd)


class ReliableUDPServer:
    """Server implementing reliable UDP with CUBIC congestion control"""
    
    def __init__(self, server_ip, server_port):
        self.server_ip = server_ip
        self.server_port = server_port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('0.0.0.0', self.server_port))
        
        # Congestion control
        self.cubic = ImprovedCUBICControl()
        
        # RTT estimation
        self.estimated_rtt = None
        self.dev_rtt = None
        self.rto = INITIAL_RTO
        
        # Transmission state
        self.base_seq = 0
        self.next_seq = 0
        
        # Packet management
        self.acked_seqs = set()
        self.sent_times = {}
        self.packet_data = {}
        self.timeouts = {}
        self.duplicate_acks = {}
        
        # Statistics
        self.packets_sent = 0
        self.retransmissions = 0
        self.acks_received = 0
        self.fast_retrans = 0
        
        print(f"[SERVER] Ready on {self.server_ip}:{self.server_port}")
    
    def build_packet(self, seq_num, data):
        """Construct packet with header"""
        header = struct.pack('!I', seq_num) + b'\x00' * 16
        return header + data
    
    def parse_ack_packet(self, packet):
        """Parse ACK with SACK blocks"""
        if len(packet) < 4:
            return None, []
        
        ack_num = struct.unpack('!I', packet[:4])[0]
        
        # Extract SACK blocks
        sack_ranges = []
        if len(packet) >= 20:
            try:
                for i in range(2):
                    offset = 4 + i * 8
                    if offset + 8 <= len(packet):
                        left_edge = struct.unpack('!I', packet[offset:offset+4])[0]
                        right_edge = struct.unpack('!I', packet[offset+4:offset+8])[0]
                        if left_edge > 0 and right_edge > left_edge:
                            sack_ranges.append((left_edge, right_edge))
            except:
                pass
        
        return ack_num, sack_ranges
    
    def update_rto_estimate(self, sample_rtt):
        """Update RTO using Karn's algorithm"""
        if self.estimated_rtt is None:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
        else:
            self.dev_rtt = (1 - BETA) * self.dev_rtt + \
                          BETA * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + \
                                ALPHA * sample_rtt
        
        self.rto = self.estimated_rtt + 4 * self.dev_rtt
        self.rto = max(MIN_RTO, min(self.rto, MAX_RTO))
    
    def transmit_packet(self, seq_num, data, client_addr):
        """Send a data packet"""
        packet = self.build_packet(seq_num, data)
        self.socket.sendto(packet, client_addr)
        
        now = time.time()
        self.sent_times[seq_num] = now
        self.packet_data[seq_num] = packet
        self.timeouts[seq_num] = now + self.rto
        self.packets_sent += 1
    
    def retransmit_packet(self, seq_num, client_addr, reason="timeout"):
        """Retransmit a lost packet"""
        if seq_num in self.packet_data and seq_num not in self.acked_seqs:
            self.socket.sendto(self.packet_data[seq_num], client_addr)
            
            now = time.time()
            self.sent_times[seq_num] = now
            self.timeouts[seq_num] = now + self.rto
            self.retransmissions += 1
            
            if reason == "fast_retransmit":
                self.fast_retrans += 1
    
    def get_earliest_timeout(self):
        """Calculate time until next timeout"""
        if not self.timeouts:
            return self.rto
        
        now = time.time()
        earliest = min(self.timeouts.values())
        return max(0.01, earliest - now)
    
    def check_timeout_packets(self, client_addr):
        """Check and retransmit timed-out packets"""
        now = time.time()
        timed_out_seqs = []
        
        for seq_num, timeout_time in list(self.timeouts.items()):
            if seq_num not in self.acked_seqs and now >= timeout_time:
                timed_out_seqs.append(seq_num)
        
        if timed_out_seqs:
            # Retransmit timed-out packets
            for seq_num in timed_out_seqs:
                self.retransmit_packet(seq_num, client_addr, "timeout")
            
            # Update congestion control
            self.cubic.handle_loss("timeout")
            # Gentle RTO backoff
            self.rto = min(self.rto * 1.15, MAX_RTO)
    
    def advance_window(self):
        """Slide window forward for acknowledged packets"""
        while self.base_seq in self.acked_seqs:
            self.acked_seqs.remove(self.base_seq)
            
            # Cleanup tracking data
            self.sent_times.pop(self.base_seq, None)
            self.packet_data.pop(self.base_seq, None)
            self.timeouts.pop(self.base_seq, None)
            
            self.base_seq += MSS
    
    def transfer_file(self, file_content, client_addr):
        """Transfer file using reliable UDP with congestion control"""
        total_size = len(file_content)
        print(f"\n[SERVER] Starting transfer: {total_size} bytes")
        print(f"[SERVER] Total packets: {(total_size + MSS - 1) // MSS}")
        
        start_time = time.time()
        
        # Main transfer loop
        while self.base_seq < total_size:
            # TRANSMISSION PHASE
            window_size = self.cubic.get_window_size()
            
            # Send packets within current window
            while self.next_seq < self.base_seq + window_size and \
                  self.next_seq < total_size:
                if self.next_seq not in self.acked_seqs:
                    end_pos = min(self.next_seq + MSS, total_size)
                    chunk = file_content[self.next_seq:end_pos]
                    self.transmit_packet(self.next_seq, chunk, client_addr)
                
                self.next_seq += MSS
            
            # RECEPTION PHASE
            timeout = self.get_earliest_timeout()
            self.socket.settimeout(timeout)
            
            try:
                ack_packet, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
                recv_time = time.time()
                
                ack_num, sack_blocks = self.parse_ack_packet(ack_packet)
                if ack_num is None:
                    continue
                
                self.acks_received += 1
                
                # Handle cumulative ACK
                if ack_num > self.base_seq:
                    bytes_acked = ack_num - self.base_seq
                    
                    # Update RTT estimate
                    if self.base_seq in self.sent_times:
                        sample_rtt = recv_time - self.sent_times[self.base_seq]
                        self.update_rto_estimate(sample_rtt)
                    
                    # Update congestion control
                    self.cubic.handle_ack(bytes_acked, sample_rtt)
                    
                    # Mark packets as acknowledged
                    current_seq = self.base_seq
                    while current_seq < ack_num:
                        if current_seq not in self.acked_seqs:
                            self.acked_seqs.add(current_seq)
                        current_seq += MSS
                    
                    self.advance_window()
                    self.duplicate_acks.clear()
                
                # Process SACK blocks
                for left, right in sack_blocks:
                    current_seq = left
                    while current_seq < right and current_seq < total_size:
                        if current_seq >= self.base_seq and \
                           current_seq not in self.acked_seqs:
                            self.acked_seqs.add(current_seq)
                        current_seq += MSS
                
                # Detect duplicate ACKs for fast retransmit
                if ack_num == self.base_seq:
                    self.duplicate_acks[ack_num] = \
                        self.duplicate_acks.get(ack_num, 0) + 1
                    
                    # Fast retransmit on 3rd duplicate ACK
                    if self.duplicate_acks[ack_num] == 3:
                        if self.base_seq not in self.acked_seqs:
                            self.retransmit_packet(self.base_seq, client_addr,
                                                 "fast_retransmit")
                            self.cubic.handle_loss("fast_retransmit")
            
            except socket.timeout:
                self.check_timeout_packets(client_addr)
        
        # Calculate transfer statistics
        elapsed = time.time() - start_time
        throughput = (total_size * 8 / elapsed / 1_000_000)
        
        print(f"[SERVER] Transfer complete: {elapsed:.2f}s, {throughput:.2f} Mbps")
        print(f"[SERVER] Packets sent: {self.packets_sent}, " +
              f"Retransmissions: {self.retransmissions}")
        
        # Send EOF markers
        eof_packet = self.build_packet(total_size, EOF_MARKER)
        for _ in range(5):
            self.socket.sendto(eof_packet, client_addr)
            time.sleep(0.04)
    
    def run(self):
        """Main server loop"""
        print(f"\n[SERVER] Waiting for client...")
        self.socket.settimeout(30.0)
        
        try:
            request, client_addr = self.socket.recvfrom(MAX_PACKET_SIZE)
            print(f"[SERVER] Client connected: {client_addr}")
        except socket.timeout:
            print(f"[SERVER] ERROR: No client request received")
            return
        
        self.socket.settimeout(None)
        
        # Load data file
        filename = "data.txt"
        if not os.path.exists(filename):
            print(f"[SERVER] ERROR: File '{filename}' not found")
            return
        
        with open(filename, 'rb') as f:
            file_content = f.read()
        
        print(f"[SERVER] Loaded '{filename}': {len(file_content)} bytes")
        
        # Start transfer
        self.transfer_file(file_content, client_addr)
        self.socket.close()


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    
    server = ReliableUDPServer(sys.argv[1], int(sys.argv[2]))
    server.run()


if __name__ == "__main__":
    main()
