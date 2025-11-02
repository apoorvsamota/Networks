#!/usr/bin/env python3
"""
Part 1 Server: Reliable UDP File Transfer
Implements sliding window protocol with cumulative ACKs, timeouts, and fast retransmit
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
INITIAL_RTO = 0.5  # Initial retransmission timeout in seconds
ALPHA = 0.125  # For RTT estimation
BETA = 0.25   # For RTT deviation estimation

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
        
        # Timeout management
        self.timer_start = None
        self.estimated_rtt = INITIAL_RTO
        self.dev_rtt = 0
        self.RTO = INITIAL_RTO
        
        # Fast retransmit
        self.dup_ack_count = 0
        self.last_ack = 0
        
        # Statistics
        self.packets_sent = 0
        self.retransmissions = 0
        
    def make_packet(self, seq_num, data):
        """Create a packet with header (20 bytes) and data"""
        # 4 bytes: sequence number
        # 16 bytes: reserved (can be used for SACK, timestamps, etc.)
        header = struct.pack('!I', seq_num)
        header += b'\x00' * 16  # Reserved bytes
        return header + data
    
    def update_rto(self, sample_rtt):
        """Update RTO using exponential weighted moving average"""
        if self.estimated_rtt == INITIAL_RTO:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
        else:
            self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + ALPHA * sample_rtt
        
        self.RTO = self.estimated_rtt + 4 * self.dev_rtt
        self.RTO = max(0.2, min(2.0, self.RTO))  # Clamp between 200ms and 2s
    
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
        self.dup_ack_count = 0
        self.last_ack = 0
        self.packets_sent = 0
        self.retransmissions = 0
        
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
                self.sock.sendto(packet, client_addr)
                self.packets_sent += 1
                
                # Start timer for first unacked packet
                if self.base == self.next_seq_num:
                    self.timer_start = time.time()
                
                self.next_seq_num += 1
            
            # Try to receive ACK
            try:
                ack_packet, addr = self.sock.recvfrom(1024)
                ack_num = struct.unpack('!I', ack_packet[:4])[0]
                
                # Calculate RTT sample if this ACKs our base
                if self.timer_start and ack_num > self.packets[self.base][0]:
                    sample_rtt = time.time() - self.timer_start
                    self.update_rto(sample_rtt)
                
                # Process ACK
                if ack_num > self.packets[self.base][0]:
                    # New ACK - move window forward
                    old_base = self.base
                    while self.base < len(self.packets) and \
                          (self.packets[self.base][0] + self.packets[self.base][2]) <= ack_num:
                        self.base += 1
                    
                    # Reset duplicate ACK counter
                    self.dup_ack_count = 0
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
                              f"Packets sent: {self.packets_sent} | "
                              f"Retransmissions: {self.retransmissions} | "
                              f"RTO: {self.RTO:.3f}s")
                        last_print = time.time()
                
                elif ack_num == self.last_ack:
                    # Duplicate ACK
                    self.dup_ack_count += 1
                    
                    # Fast retransmit after 3 duplicate ACKs
                    if self.dup_ack_count == 3:
                        print(f"[SERVER] Fast retransmit: seq {self.packets[self.base][0]}")
                        seq_num, packet, data_len = self.packets[self.base]
                        self.sock.sendto(packet, client_addr)
                        self.retransmissions += 1
                        self.timer_start = time.time()
                        self.dup_ack_count = 0  # Reset after fast retransmit
            
            except socket.timeout:
                pass  # No ACK received, continue
            
            # Check for timeout
            if self.timer_start and (time.time() - self.timer_start) > self.RTO:
                print(f"[SERVER] Timeout! Retransmitting from seq {self.packets[self.base][0]}")
                
                # Retransmit all packets in window
                for i in range(self.base, min(self.next_seq_num, len(self.packets))):
                    seq_num, packet, data_len = self.packets[i]
                    self.sock.sendto(packet, client_addr)
                    self.retransmissions += 1
                
                # Restart timer
                self.timer_start = time.time()
                # Double RTO on timeout (exponential backoff)
                self.RTO = min(self.RTO * 2, 2.0)
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"\n[SERVER] Transfer complete!")
        print(f"[SERVER] Time: {duration:.2f}s")
        print(f"[SERVER] Total packets sent: {self.packets_sent}")
        print(f"[SERVER] Retransmissions: {self.retransmissions}")
        print(f"[SERVER] Efficiency: {((self.packets_sent - self.retransmissions) / self.packets_sent * 100):.1f}%")
    
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
