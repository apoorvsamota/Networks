#!/usr/bin/env python3
"""
Part 1 Server: Reliable UDP File Transfer
Optimized for speed with SACK support
"""

import socket
import sys
import time
import struct
import os

# Constants - optimized for speed like bihari's code
MSS = 1180  # Maximum segment size for data (1200 - 20 header)
HEADER_SIZE = 20
MAX_PAYLOAD = 1200
INITIAL_RTO = 0.25  # Reduced from 0.3 for faster start
ALPHA = 0.125  # For RTT estimation
BETA = 0.25   # For RTT deviation estimation
MIN_RTO = 0.1  # Reduced from 0.15 for faster retransmission
MAX_RTO = 2.0  # Increased from 1.0 for better stability

class ReliableUDPServer:
    def __init__(self, ip, port, sws):
        self.ip = ip
        self.port = port
        self.sws = sws  # Sender window size in bytes
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        self.sock.settimeout(0.1)  # Short timeout for non-blocking receive
        
        # Sliding window state
        self.base = 0  # Sequence number of oldest unacked byte
        self.next_seq_num = 0  # Next sequence number to send
        self.packets = []  # List of (seq_num, packet_data, data_len) tuples
        
        # Per-packet tracking for selective repeat
        self.acked_seqs = set()  # Track individually acknowledged sequence numbers
        self.packet_send_times = {}  # seq_num -> send_time for RTT calculation
        self.packet_timeouts = {}  # seq_num -> timeout_time for per-packet timeouts
        
        # Timeout management
        self.timer_start = None
        self.estimated_rtt = INITIAL_RTO
        self.dev_rtt = 0
        self.RTO = INITIAL_RTO
        
        # Fast retransmit
        self.dup_ack_count = {}  # Track duplicate ACKs per ack_num
        self.last_ack = 0
        
        # Statistics
        self.packets_sent = 0
        self.retransmissions = 0
        self.fast_retransmits = 0
        
    def make_packet(self, seq_num, data):
        """Create a packet with header (20 bytes) and data"""
        # 4 bytes: sequence number
        # 16 bytes: reserved (can be used for SACK, timestamps, etc.)
        header = struct.pack('!I', seq_num)
        header += b'\x00' * 16  # Reserved bytes
        return header + data
    
    def parse_ack_packet(self, ack_packet):
        """Parse ACK packet including SACK blocks"""
        if len(ack_packet) < 4:
            return None, []
        
        # Extract cumulative ACK
        ack_num = struct.unpack('!I', ack_packet[:4])[0]
        
        # Extract SACK blocks if present
        sack_blocks = []
        if len(ack_packet) >= 20:
            try:
                # Parse up to 2 SACK blocks (8 bytes each)
                for i in range(2):
                    offset = 4 + i * 8
                    if offset + 8 <= len(ack_packet):
                        start = struct.unpack('!I', ack_packet[offset:offset+4])[0]
                        end = struct.unpack('!I', ack_packet[offset+4:offset+8])[0]
                        if start > 0 and end > start:
                            sack_blocks.append((start, end))
            except:
                pass  # Ignore malformed SACK blocks
        
        return ack_num, sack_blocks
    
    def update_rto(self, sample_rtt):
        """Update RTO using exponential weighted moving average"""
        if self.estimated_rtt == INITIAL_RTO:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
        else:
            self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + ALPHA * sample_rtt
        
        self.RTO = self.estimated_rtt + 3 * self.dev_rtt
        self.RTO = max(MIN_RTO, min(MAX_RTO, self.RTO))  # Clamp
    
    def get_dynamic_timeout(self):
        """Get earliest packet timeout for dynamic waiting"""
        if not self.packet_timeouts:
            return self.RTO
        
        current_time = time.time()
        min_timeout = min(self.packet_timeouts.values())
        return max(0.01, min_timeout - current_time)
    
    def check_packet_timeouts(self, client_addr):
        """Check for timed out packets and retransmit them"""
        current_time = time.time()
        timed_out_indices = []
        
        # Find timed out packets
        for idx, (seq_num, packet, data_len) in enumerate(self.packets):
            if idx < self.base:
                continue  # Skip already acknowledged
            if seq_num in self.acked_seqs:
                continue  # Skip SACK'd packets
            if seq_num in self.packet_timeouts and current_time >= self.packet_timeouts[seq_num]:
                timed_out_indices.append(idx)
        
        if timed_out_indices:
            print(f"[SERVER] Timeout! Retransmitting {len(timed_out_indices)} packet(s)")
            for idx in timed_out_indices:
                seq_num, packet, data_len = self.packets[idx]
                self.sock.sendto(packet, client_addr)
                self.retransmissions += 1
                # Update timeout
                self.packet_timeouts[seq_num] = time.time() + self.RTO
                self.packet_send_times[seq_num] = time.time()
            
            # Backoff RTO (less aggressive than before)
            self.RTO = min(self.RTO * 1.5, MAX_RTO)
    
    def send_file(self, client_addr, filename):
        """Send file using reliable UDP with sliding window protocol"""
        print(f"[SERVER] Sending file '{filename}' to {client_addr}")
        print(f"[SERVER] Sender window size: {self.sws} bytes")
        
        # Read file
        try:
            with open(filename, 'rb') as f:
                file_data = f.read()
        except FileNotFoundError:
            print(f"[ERROR] File '{filename}' not found")
            return
        
        print(f"[SERVER] File size: {len(file_data)} bytes")
        
        # Split data into packets
        self.packets = []
        seq_num = 0
        packet_count = 0
        
        for i in range(0, len(file_data), MSS):
            chunk = file_data[i:i+MSS]
            packet = self.make_packet(seq_num, chunk)
            self.packets.append((seq_num, packet, len(chunk)))
            seq_num += len(chunk)
            packet_count += 1
        
        # Add EOF packet
        eof_packet = self.make_packet(seq_num, b'EOF')
        self.packets.append((seq_num, eof_packet, 3))
        
        print(f"[SERVER] Split into {packet_count} data packets + 1 EOF packet")
        print(f"[SERVER] Starting transmission...")
        
        # Initialize state
        self.base = 0
        self.next_seq_num = 0
        self.timer_start = None
        self.dup_ack_count = {}
        self.last_ack = 0
        self.packets_sent = 0
        self.retransmissions = 0
        self.fast_retransmits = 0
        self.acked_seqs = set()
        self.packet_send_times = {}
        self.packet_timeouts = {}
        
        start_time = time.time()
        last_print = start_time
        
        # Main sending loop
        while self.base < len(self.packets):
            # Send new packets within window
            while self.next_seq_num < len(self.packets):
                # Calculate bytes in flight
                if self.next_seq_num > 0:
                    bytes_in_flight = self.packets[self.next_seq_num - 1][0] + \
                                    self.packets[self.next_seq_num - 1][2] - \
                                    self.packets[self.base][0]
                else:
                    bytes_in_flight = 0
                
                # Check if we can send more
                if bytes_in_flight >= self.sws:
                    break
                
                # Send packet
                seq_num, packet, data_len = self.packets[self.next_seq_num]
                
                # Only send if not already SACK'd
                if seq_num not in self.acked_seqs:
                    self.sock.sendto(packet, client_addr)
                    self.packets_sent += 1
                    
                    # Track send time and timeout
                    current_time = time.time()
                    self.packet_send_times[seq_num] = current_time
                    self.packet_timeouts[seq_num] = current_time + self.RTO
                
                # Start timer for first unacked packet
                if self.base == self.next_seq_num:
                    self.timer_start = time.time()
                
                self.next_seq_num += 1
            
            # Try to receive ACK with dynamic timeout
            timeout = self.get_dynamic_timeout()
            self.sock.settimeout(timeout)
            
            try:
                ack_packet, addr = self.sock.recvfrom(1024)
                receive_time = time.time()
                ack_num, sack_blocks = self.parse_ack_packet(ack_packet)
                
                if ack_num is None:
                    continue
                
                # Calculate RTT sample if this ACKs our base
                if self.timer_start and ack_num > self.packets[self.base][0]:
                    sample_rtt = receive_time - self.timer_start
                    self.update_rto(sample_rtt)
                
                # Process cumulative ACK
                if ack_num > self.packets[self.base][0]:
                    # New ACK - move window forward
                    old_base = self.base
                    while self.base < len(self.packets) and \
                          (self.packets[self.base][0] + self.packets[self.base][2]) <= ack_num:
                        # Clean up acknowledged packet
                        seq = self.packets[self.base][0]
                        self.acked_seqs.discard(seq)
                        self.packet_send_times.pop(seq, None)
                        self.packet_timeouts.pop(seq, None)
                        self.base += 1
                    
                    # Reset duplicate ACK counter
                    self.dup_ack_count.clear()
                    self.last_ack = ack_num
                    
                    # Restart timer for new base
                    if self.base < len(self.packets):
                        self.timer_start = time.time()
                    else:
                        self.timer_start = None
                    
                    # Progress update
                    if time.time() - last_print > 1.0:
                        progress = (self.base / len(self.packets)) * 100
                        print(f"[SERVER] Progress: {progress:.1f}% | "
                              f"Sent: {self.packets_sent} | "
                              f"Retrans: {self.retransmissions} | "
                              f"RTO: {self.RTO:.3f}s")
                        last_print = time.time()
                
                # Process SACK blocks - mark selectively acknowledged packets
                for sack_start, sack_end in sack_blocks:
                    for idx in range(self.base, len(self.packets)):
                        seq_num, _, data_len = self.packets[idx]
                        seq_end = seq_num + data_len
                        
                        # Check if packet is within SACK range
                        if seq_num >= sack_start and seq_end <= sack_end:
                            if seq_num not in self.acked_seqs:
                                self.acked_seqs.add(seq_num)
                
                # Handle duplicate ACK
                if ack_num == self.last_ack:
                    if ack_num not in self.dup_ack_count:
                        self.dup_ack_count[ack_num] = 0
                    self.dup_ack_count[ack_num] += 1
                    
                    # Fast retransmit after 3 duplicate ACKs
                    if self.dup_ack_count[ack_num] == 3:
                        base_seq = self.packets[self.base][0]
                        if base_seq not in self.acked_seqs:
                            print(f"[SERVER] Fast retransmit: seq {base_seq}")
                            seq_num, packet, data_len = self.packets[self.base]
                            self.sock.sendto(packet, client_addr)
                            self.retransmissions += 1
                            self.fast_retransmits += 1
                            self.timer_start = time.time()
                            self.packet_timeouts[seq_num] = time.time() + self.RTO
            
            except socket.timeout:
                # Check for per-packet timeouts
                self.check_packet_timeouts(client_addr)
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"\n[SERVER] Transfer complete!")
        print(f"[SERVER] Time: {duration:.2f}s")
        print(f"[SERVER] Throughput: {len(file_data) * 8 / duration / 1_000_000:.2f} Mbps")
        print(f"[SERVER] Total packets sent: {self.packets_sent}")
        print(f"[SERVER] Retransmissions: {self.retransmissions} "
              f"({100*self.retransmissions/max(1,self.packets_sent):.1f}%)")
        print(f"[SERVER] Fast retransmits: {self.fast_retransmits}")
        print(f"[SERVER] Final RTO: {self.RTO:.4f}s")
    
    def run(self):
        """Main server loop"""
        print(f"[SERVER] Listening on {self.ip}:{self.port}")
        print(f"[SERVER] Sender window size: {self.sws} bytes")
        
        try:
            # Wait for client request (with no timeout)
            print(f"[SERVER] Waiting for client request...")
            self.sock.settimeout(None)  # Block until we get a request
            data, client_addr = self.sock.recvfrom(1024)
            print(f"[SERVER] Received request from {client_addr}")
            
            # Now set short timeout for ACK reception during transfer
            self.sock.settimeout(0.1)
            
            # Send file
            self.send_file(client_addr, 'data.txt')
            
        except KeyboardInterrupt:
            print("\n[SERVER] Shutting down...")
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.sock.close()

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>")
        sys.exit(1)
    
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    sws = int(sys.argv[3])
    
    server = ReliableUDPServer(server_ip, server_port, sws)
    server.run()
