#!/usr/bin/env python3
"""
Part 1 Server: Reliable UDP File Transfer (FIXED)
Implements sliding window with SACK support and proper dynamic timeout management
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
INITIAL_RTO = 0.25  # Initial RTO
ALPHA = 0.125  # For RTT estimation
BETA = 0.25   # For RTT deviation estimation
MIN_RTO = 0.1  # Minimum RTO
MAX_RTO = 2.0  # Maximum RTO

class ReliableUDPServer:
    def __init__(self, ip, port, sws):
        self.ip = ip
        self.port = port
        self.sws = sws  # Sender window size in bytes
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        
        # Sliding window state
        self.base = 0  # Index of oldest unacked packet
        self.next_seq_num = 0  # Next packet index to send
        self.packets = []  # List of (seq_num, packet_data, data_len) tuples
        
        # Per-packet timeout tracking (CRITICAL)
        self.packet_send_times = {}  # seq_num -> send_time
        self.packet_timeouts = {}  # seq_num -> timeout_time
        self.acked_packets = set()  # Track individually acked packets via SACK
        
        # RTT and timeout management
        self.estimated_rtt = None
        self.dev_rtt = None
        self.RTO = INITIAL_RTO
        
        # Fast retransmit
        self.dup_ack_count = {}
        
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
        if self.estimated_rtt is None:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
        else:
            self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + ALPHA * sample_rtt
        
        self.RTO = self.estimated_rtt + 3 * self.dev_rtt
        self.RTO = max(MIN_RTO, min(MAX_RTO, self.RTO))
    
    def get_next_timeout(self):
        """Get time until the earliest packet timeout (CRITICAL FIX)"""
        if not self.packet_timeouts:
            return self.RTO  # Default to RTO if no packets waiting
        
        current_time = time.time()
        min_timeout_time = min(self.packet_timeouts.values())
        timeout = max(0.01, min_timeout_time - current_time)
        return timeout  # Return actual time to wait
    
    def check_and_retransmit_timeouts(self, client_addr):
        """Check for timed out packets and retransmit them"""
        current_time = time.time()
        timed_out_packets = []
        
        # Find all packets that have timed out
        for seq_num, timeout_time in list(self.packet_timeouts.items()):
            if seq_num not in self.acked_packets and current_time >= timeout_time:
                # Find the packet
                for i in range(self.base, len(self.packets)):
                    pkt_seq, packet, data_len = self.packets[i]
                    if pkt_seq == seq_num:
                        timed_out_packets.append((seq_num, packet))
                        break
        
        if timed_out_packets:
            print(f"[SERVER] TIMEOUT! Retransmitting {len(timed_out_packets)} packet(s)")
            for seq_num, packet in timed_out_packets:
                self.sock.sendto(packet, client_addr)
                current_time = time.time()
                self.packet_send_times[seq_num] = current_time
                self.packet_timeouts[seq_num] = current_time + self.RTO
                self.retransmissions += 1
            
            # Backoff on timeout
            self.RTO = min(self.RTO * 1.5, MAX_RTO)
    
    def slide_window(self):
        """Slide the window forward based on acknowledged packets"""
        # Move base to the first unacknowledged packet
        while self.base < len(self.packets):
            pkt_seq = self.packets[self.base][0]
            if pkt_seq in self.acked_packets:
                # Clean up acknowledged packet
                self.acked_packets.remove(pkt_seq)
                if pkt_seq in self.packet_send_times:
                    del self.packet_send_times[pkt_seq]
                if pkt_seq in self.packet_timeouts:
                    del self.packet_timeouts[pkt_seq]
                self.base += 1
            else:
                break
    
    def send_file(self, client_addr, filename):
        """Send file using reliable UDP with SACK and dynamic timeouts"""
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
        self.packet_send_times = {}
        self.packet_timeouts = {}
        self.acked_packets = set()
        self.dup_ack_count = {}
        self.packets_sent = 0
        self.retransmissions = 0
        self.fast_retransmits = 0
        
        start_time = time.time()
        last_print = start_time
        
        # Main sending loop
        while self.base < len(self.packets):
            # SEND PHASE: Send new packets within window
            while self.next_seq_num < len(self.packets):
                # Calculate bytes in flight
                if self.next_seq_num > 0 and self.base < len(self.packets):
                    bytes_in_flight = (self.packets[self.next_seq_num - 1][0] + 
                                     self.packets[self.next_seq_num - 1][2] - 
                                     self.packets[self.base][0])
                else:
                    bytes_in_flight = 0
                
                # Check if we can send more
                if bytes_in_flight >= self.sws:
                    break
                
                # Send packet and track its timer
                seq_num, packet, data_len = self.packets[self.next_seq_num]
                if seq_num not in self.acked_packets:
                    self.sock.sendto(packet, client_addr)
                    current_time = time.time()
                    self.packet_send_times[seq_num] = current_time
                    self.packet_timeouts[seq_num] = current_time + self.RTO
                    self.packets_sent += 1
                
                self.next_seq_num += 1
            
            # RECEIVE PHASE: Wait for ACKs with DYNAMIC timeout (CRITICAL FIX)
            timeout = self.get_next_timeout()
            self.sock.settimeout(timeout)
            
            try:
                ack_packet, addr = self.sock.recvfrom(1024)
                receive_time = time.time()
                ack_num, sack_blocks = self.parse_ack_with_sack(ack_packet)
                
                if ack_num is None:
                    continue
                
                # Calculate RTT sample for base packet if it's being ACKed
                if self.base < len(self.packets):
                    base_seq = self.packets[self.base][0]
                    if ack_num > base_seq and base_seq in self.packet_send_times:
                        sample_rtt = receive_time - self.packet_send_times[base_seq]
                        self.update_rto(sample_rtt)
                
                # Process cumulative ACK
                if self.base < len(self.packets):
                    base_seq = self.packets[self.base][0]
                    if ack_num > base_seq:
                        # Mark all packets up to ack_num as acknowledged
                        for i in range(self.base, len(self.packets)):
                            pkt_seq, _, pkt_len = self.packets[i]
                            if pkt_seq + pkt_len <= ack_num:
                                if pkt_seq not in self.acked_packets:
                                    self.acked_packets.add(pkt_seq)
                            else:
                                break
                        
                        # Slide window
                        self.slide_window()
                        
                        # Reset duplicate ACK counter
                        self.dup_ack_count.clear()
                        
                        # Progress update
                        if time.time() - last_print > 1.0:
                            progress = (self.base / len(self.packets)) * 100
                            print(f"[SERVER] Progress: {progress:.1f}% | "
                                  f"Sent: {self.packets_sent} | "
                                  f"Retrans: {self.retransmissions} | "
                                  f"Fast: {self.fast_retransmits} | "
                                  f"RTO: {self.RTO:.3f}s")
                            last_print = time.time()
                
                # Process SACK blocks (mark packets as acked)
                for left, right in sack_blocks:
                    for i in range(self.base, len(self.packets)):
                        pkt_seq, _, pkt_len = self.packets[i]
                        if left <= pkt_seq < right:
                            if pkt_seq not in self.acked_packets:
                                self.acked_packets.add(pkt_seq)
                                # Remove timers for SACKed packets
                                if pkt_seq in self.packet_timeouts:
                                    del self.packet_timeouts[pkt_seq]
                
                # Duplicate ACK handling for fast retransmit
                if self.base < len(self.packets):
                    base_seq = self.packets[self.base][0]
                    if ack_num == base_seq:
                        if base_seq not in self.dup_ack_count:
                            self.dup_ack_count[base_seq] = 0
                        self.dup_ack_count[base_seq] += 1
                        
                        if self.dup_ack_count[base_seq] == 3:
                            if base_seq not in self.acked_packets:
                                print(f"[SERVER] Fast retransmit: seq {base_seq}")
                                _, packet, _ = self.packets[self.base]
                                self.sock.sendto(packet, client_addr)
                                current_time = time.time()
                                self.packet_send_times[base_seq] = current_time
                                self.packet_timeouts[base_seq] = current_time + self.RTO
                                self.retransmissions += 1
                                self.fast_retransmits += 1
            
            except socket.timeout:
                # Check for timed out packets and retransmit them
                self.check_and_retransmit_timeouts(client_addr)
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"\n[SERVER] Transfer complete!")
        print(f"[SERVER] Time: {duration:.2f}s")
        print(f"[SERVER] Throughput: {(len(file_data) * 8 / duration / 1_000_000):.2f} Mbps")
        print(f"[SERVER] Total packets sent: {self.packets_sent}")
        print(f"[SERVER] Retransmissions: {self.retransmissions}")
        print(f"[SERVER] Fast retransmits: {self.fast_retransmits}")
        print(f"[SERVER] Efficiency: {((self.packets_sent - self.retransmissions) / max(1, self.packets_sent) * 100):.1f}%")
        print(f"[SERVER] Final RTO: {self.RTO:.3f}s")
        
        # Send EOF multiple times to ensure delivery
        print(f"[SERVER] Sending EOF...")
        eof_seq = self.packets[-1][0]
        eof_packet = self.make_packet(eof_seq, b'EOF')
        for _ in range(5):
            self.sock.sendto(eof_packet, client_addr)
            time.sleep(0.05)
    
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
