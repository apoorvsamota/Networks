#!/usr/bin/env python3
"""
Part 1 Server: Reliable UDP File Transfer (IMPROVED)
Implements sliding window with SACK support and per-packet timeout tracking
"""

import socket
import sys
import time
import struct
import os

# Constants
MSS = 1180  # Maximum segment size for data (1200 - 20 header)
HEADER_SIZE = 20
MAX_PAYLOAD = 1200
INITIAL_RTO = 0.3  # Balanced initial RTO (not too aggressive)
ALPHA = 0.125  # For RTT estimation
BETA = 0.25   # For RTT deviation estimation
MIN_RTO = 0.15  # Balanced minimum (was too aggressive at 0.1)
MAX_RTO = 2.0  # Allow higher max for very lossy networks

class ReliableUDPServer:
    def __init__(self, ip, port, sws):
        self.ip = ip
        self.port = port
        self.sws = sws  # Sender window size in bytes
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        self.sock.settimeout(0.05)  # Very short timeout for faster loop
        
        # Sliding window state
        self.base = 0  # Sequence number of oldest unacked byte
        self.next_seq_num = 0  # Next sequence number to send
        self.packets = []  # List of (seq_num, packet_data, data_len) tuples
        
        # Per-packet timeout tracking (KEY IMPROVEMENT)
        self.packet_timers = {}  # seq_num -> send_time
        self.acked_packets = set()  # Track individually acked packets via SACK
        
        # RTT and timeout management
        self.estimated_rtt = INITIAL_RTO
        self.dev_rtt = 0
        self.RTO = INITIAL_RTO
        
        # Fast retransmit
        self.dup_ack_count = 0
        self.last_ack = 0
        
        # Statistics
        self.packets_sent = 0
        self.retransmissions = 0
        self.fast_retransmits = 0
        
    def make_packet(self, seq_num, data):
        """Create a packet with header (20 bytes) and data"""
        header = struct.pack('!I', seq_num)
        header += b'\x00' * 16  # Reserved bytes
        return header + data
    
    def parse_ack_with_sack(self, ack_packet):
        """Parse ACK packet and extract SACK blocks"""
        if len(ack_packet) < 4:
            return None, []
        
        # Get cumulative ACK
        ack_num = struct.unpack('!I', ack_packet[:4])[0]
        
        # Parse SACK blocks from reserved 16 bytes
        sack_blocks = []
        if len(ack_packet) >= 20:
            try:
                # Up to 2 SACK blocks (each is 8 bytes: left_edge, right_edge)
                for i in range(2):
                    offset = 4 + i * 8
                    if offset + 8 <= len(ack_packet):
                        left = struct.unpack('!I', ack_packet[offset:offset+4])[0]
                        right = struct.unpack('!I', ack_packet[offset+4:offset+8])[0]
                        if left > 0 and right > left:
                            sack_blocks.append((left, right))
            except:
                pass
        
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
        self.RTO = max(MIN_RTO, min(MAX_RTO, self.RTO))
    
    def get_next_timeout(self):
        """Get time until the earliest packet timeout"""
        if not self.packet_timers:
            return 0.05  # Default short timeout
        
        current_time = time.time()
        min_timeout_time = min(self.packet_timers.values())
        timeout = max(0.01, min_timeout_time + self.RTO - current_time)
        return min(timeout, 0.1)  # Cap at 100ms for responsiveness
    
    def check_and_retransmit_timeouts(self, client_addr):
        """Check for timed out packets and retransmit them"""
        current_time = time.time()
        timed_out_seqs = []
        
        # Find all packets that have timed out
        for seq_num, send_time in list(self.packet_timers.items()):
            if current_time - send_time >= self.RTO:
                # Find packet index
                for i, (pkt_seq, packet, data_len) in enumerate(self.packets):
                    if pkt_seq == seq_num and i >= self.base:
                        timed_out_seqs.append((i, seq_num, packet))
                        break
        
        if timed_out_seqs:
            # Limit burst size at high loss to prevent overwhelming network
            max_retransmit_burst = 5
            timed_out_seqs = timed_out_seqs[:max_retransmit_burst]
            
            print(f"[SERVER] TIMEOUT! Retransmitting {len(timed_out_seqs)} packet(s)")
            for idx, seq_num, packet in timed_out_seqs:
                self.sock.sendto(packet, client_addr)
                self.packet_timers[seq_num] = time.time()
                self.retransmissions += 1
                # Small delay between retransmissions to avoid burst
                time.sleep(0.001)
            
            # Conservative backoff on timeout (important for high loss)
            self.RTO = min(self.RTO * 2.0, MAX_RTO)
    
    def send_file(self, client_addr, filename):
        """Send file using reliable UDP with SACK and per-packet timeouts"""
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
        print(f"[SERVER] Starting transmission with SACK and per-packet timeouts...")
        
        # Initialize state
        self.base = 0
        self.next_seq_num = 0
        self.packet_timers = {}
        self.acked_packets = set()
        self.dup_ack_count = 0
        self.last_ack = 0
        self.packets_sent = 0
        self.retransmissions = 0
        self.fast_retransmits = 0
        
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
                
                # Send packet and track its timer
                seq_num, packet, data_len = self.packets[self.next_seq_num]
                self.sock.sendto(packet, client_addr)
                self.packet_timers[seq_num] = time.time()
                self.packets_sent += 1
                self.next_seq_num += 1
            
            # Try to receive ACK with adaptive timeout
            timeout = self.get_next_timeout()
            self.sock.settimeout(timeout)
            
            try:
                ack_packet, addr = self.sock.recvfrom(1024)
                receive_time = time.time()
                ack_num, sack_blocks = self.parse_ack_with_sack(ack_packet)
                
                if ack_num is None:
                    continue
                
                # Calculate RTT sample for base packet if it's being ACKed
                base_seq = self.packets[self.base][0]
                if ack_num > base_seq and base_seq in self.packet_timers:
                    sample_rtt = receive_time - self.packet_timers[base_seq]
                    self.update_rto(sample_rtt)
                
                # Process cumulative ACK
                if ack_num > self.packets[self.base][0]:
                    # New ACK - move window forward
                    old_base = self.base
                    while self.base < len(self.packets) and \
                          (self.packets[self.base][0] + self.packets[self.base][2]) <= ack_num:
                        # Remove timer for acked packet
                        acked_seq = self.packets[self.base][0]
                        if acked_seq in self.packet_timers:
                            del self.packet_timers[acked_seq]
                        if acked_seq in self.acked_packets:
                            self.acked_packets.remove(acked_seq)
                        self.base += 1
                    
                    # Reset duplicate ACK counter
                    self.dup_ack_count = 0
                    self.last_ack = ack_num
                    
                    # Progress update
                    if time.time() - last_print > 1.0:
                        progress = (self.base / len(self.packets)) * 100
                        print(f"[SERVER] Progress: {progress:.1f}% | "
                              f"Packets sent: {self.packets_sent} | "
                              f"Retransmissions: {self.retransmissions} | "
                              f"Fast retrans: {self.fast_retransmits} | "
                              f"RTO: {self.RTO:.3f}s")
                        last_print = time.time()
                
                # Process SACK blocks (KEY IMPROVEMENT)
                for left, right in sack_blocks:
                    # Mark packets in SACK range as acknowledged
                    for i in range(self.base, len(self.packets)):
                        pkt_seq = self.packets[i][0]
                        pkt_len = self.packets[i][2]
                        if left <= pkt_seq < right:
                            if pkt_seq not in self.acked_packets:
                                self.acked_packets.add(pkt_seq)
                                # Remove timer for selectively acked packet
                                if pkt_seq in self.packet_timers:
                                    del self.packet_timers[pkt_seq]
                
                # Duplicate ACK handling for fast retransmit
                if ack_num == self.last_ack:
                    self.dup_ack_count += 1
                    
                    if self.dup_ack_count == 3:
                        base_seq = self.packets[self.base][0]
                        if base_seq not in self.acked_packets:
                            print(f"[SERVER] Fast retransmit: seq {base_seq}")
                            seq_num, packet, data_len = self.packets[self.base]
                            self.sock.sendto(packet, client_addr)
                            self.packet_timers[seq_num] = time.time()
                            self.retransmissions += 1
                            self.fast_retransmits += 1
                            self.dup_ack_count = 0
            
            except socket.timeout:
                # Check for timed out packets and retransmit them
                self.check_and_retransmit_timeouts(client_addr)
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"\n[SERVER] Transfer complete!")
        print(f"[SERVER] Time: {duration:.2f}s")
        print(f"[SERVER] Total packets sent: {self.packets_sent}")
        print(f"[SERVER] Retransmissions: {self.retransmissions}")
        print(f"[SERVER] Fast retransmits: {self.fast_retransmits}")
        print(f"[SERVER] Efficiency: {((self.packets_sent - self.retransmissions) / self.packets_sent * 100):.1f}%")
        print(f"[SERVER] Final RTO: {self.RTO:.3f}s")
    
    def run(self):
        """Main server loop"""
        print(f"[SERVER] Listening on {self.ip}:{self.port}")
        print(f"[SERVER] Sender window size: {self.sws} bytes")
        
        try:
            # Wait for client request
            print(f"[SERVER] Waiting for client request...")
            self.sock.settimeout(None)  # Block until we get a request
            data, client_addr = self.sock.recvfrom(1024)
            print(f"[SERVER] Received request from {client_addr}")
            
            # Now set short timeout for ACK reception during transfer
            self.sock.settimeout(0.05)
            
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
