#!/usr/bin/env python3
"""
Part 1 Client: Reliable UDP File Transfer
Receives file from server, sends cumulative ACKs, handles out-of-order packets
"""

import socket
import sys
import struct
import time

# Constants
HEADER_SIZE = 20
MAX_PAYLOAD = 1200
REQUEST_TIMEOUT = 2.0
MAX_REQUEST_RETRIES = 5

class ReliableUDPClient:
    def __init__(self, server_ip, server_port):
        self.server_ip = server_ip
        self.server_port = server_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(REQUEST_TIMEOUT)
        
        # State variables
        self.expected_seq = 0  # Next expected sequence number
        self.buffer = {}  # Out-of-order buffer: {seq_num: data}
        self.file_data = bytearray()  # Accumulated file data
        
        # Statistics
        self.packets_received = 0
        self.acks_sent = 0
        
    def parse_packet(self, packet):
        """Parse packet header and extract data"""
        if len(packet) < HEADER_SIZE:
            return None, None
        
        seq_num = struct.unpack('!I', packet[:4])[0]
        data = packet[HEADER_SIZE:]  # Skip 20-byte header
        return seq_num, data
    
    def send_ack(self, ack_num):
        """Send cumulative ACK packet"""
        # ACK packet: 4 bytes for ACK number + 16 bytes reserved
        ack_packet = struct.pack('!I', ack_num) + b'\x00' * 16
        self.sock.sendto(ack_packet, (self.server_ip, self.server_port))
        self.acks_sent += 1
    
    def request_file(self):
        """Request file from server with retries"""
        request = b'1'  # Single byte request
        
        for attempt in range(MAX_REQUEST_RETRIES):
            print(f"[CLIENT] Sending file request (attempt {attempt + 1}/{MAX_REQUEST_RETRIES})")
            self.sock.sendto(request, (self.server_ip, self.server_port))
            
            try:
                # Wait for first packet
                packet, addr = self.sock.recvfrom(MAX_PAYLOAD + 100)
                print(f"[CLIENT] Received first packet from server")
                return packet
            except socket.timeout:
                print(f"[CLIENT] Timeout on attempt {attempt + 1}")
                if attempt == MAX_REQUEST_RETRIES - 1:
                    print("[ERROR] Failed to connect to server after 5 attempts")
                    return None
        
        return None
    
    def receive_file(self, output_filename='received_data.txt'):
        """Receive file from server using reliable UDP"""
        print(f"[CLIENT] Connecting to server {self.server_ip}:{self.server_port}")
        
        # Request file and get first packet
        first_packet = self.request_file()
        if not first_packet:
            return False
        
        print(f"[CLIENT] Starting file transfer...")
        start_time = time.time()
        last_print = start_time
        
        # Process first packet
        seq_num, data = self.parse_packet(first_packet)
        if seq_num is None:
            print("[ERROR] Invalid first packet")
            return False
        
        # Check for immediate EOF (empty file)
        if data == b'EOF':
            print("[CLIENT] Received EOF immediately - empty file")
            with open(output_filename, 'wb') as f:
                f.write(b'')
            self.send_ack(seq_num + 3)
            return True
        
        # Add first packet data
        self.file_data.extend(data)
        self.expected_seq = len(data)
        self.packets_received += 1
        self.send_ack(self.expected_seq)
        
        # Set shorter timeout for data reception
        self.sock.settimeout(1.0)
        
        # Receive remaining packets
        eof_received = False
        consecutive_timeouts = 0
        
        while not eof_received:
            try:
                packet, addr = self.sock.recvfrom(MAX_PAYLOAD + 100)
                consecutive_timeouts = 0  # Reset timeout counter
                
                seq_num, data = self.parse_packet(packet)
                if seq_num is None:
                    continue
                
                self.packets_received += 1
                
                # Check for EOF
                if data == b'EOF':
                    print(f"[CLIENT] Received EOF packet (seq {seq_num})")
                    self.send_ack(seq_num + 3)  # ACK the EOF
                    eof_received = True
                    break
                
                if seq_num == self.expected_seq:
                    # In-order packet
                    self.file_data.extend(data)
                    self.expected_seq += len(data)
                    
                    # Check buffer for consecutive packets
                    while self.expected_seq in self.buffer:
                        buffered_data = self.buffer.pop(self.expected_seq)
                        self.file_data.extend(buffered_data)
                        self.expected_seq += len(buffered_data)
                    
                    # Send cumulative ACK
                    self.send_ack(self.expected_seq)
                    
                elif seq_num > self.expected_seq:
                    # Out-of-order packet - buffer it
                    if seq_num not in self.buffer:
                        self.buffer[seq_num] = data
                    
                    # Send duplicate ACK for expected sequence
                    self.send_ack(self.expected_seq)
                    
                else:
                    # Old packet (already received) - send ACK again
                    self.send_ack(self.expected_seq)
                
                # Progress update
                if time.time() - last_print > 1.0:
                    print(f"[CLIENT] Received: {len(self.file_data)} bytes | "
                          f"Packets: {self.packets_received} | "
                          f"ACKs sent: {self.acks_sent} | "
                          f"Buffered: {len(self.buffer)}")
                    last_print = time.time()
                    
            except socket.timeout:
                consecutive_timeouts += 1
                print(f"[CLIENT] Timeout waiting for packet (expected seq: {self.expected_seq})")
                
                # Resend ACK in case it was lost
                self.send_ack(self.expected_seq)
                
                # If we've had too many consecutive timeouts, something is wrong
                if consecutive_timeouts > 10:
                    print("[ERROR] Too many consecutive timeouts - transfer may have failed")
                    # But continue anyway, server might just be slow
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Write to file
        print(f"\n[CLIENT] Writing {len(self.file_data)} bytes to '{output_filename}'")
        try:
            with open(output_filename, 'wb') as f:
                f.write(self.file_data)
        except Exception as e:
            print(f"[ERROR] Failed to write file: {e}")
            return False
        
        print(f"[CLIENT] Transfer complete!")
        print(f"[CLIENT] Time: {duration:.2f}s")
        print(f"[CLIENT] Bytes received: {len(self.file_data)}")
        print(f"[CLIENT] Packets received: {self.packets_received}")
        print(f"[CLIENT] ACKs sent: {self.acks_sent}")
        print(f"[CLIENT] Average throughput: {len(self.file_data) / duration / 1024:.2f} KB/s")
        
        return True
    
    def run(self, output_filename='received_data.txt'):
        """Main client function"""
        try:
            success = self.receive_file(output_filename)
            if success:
                print(f"\n[SUCCESS] File saved to '{output_filename}'")
            else:
                print(f"\n[FAILURE] Transfer failed!")
            return success
        except KeyboardInterrupt:
            print("\n[CLIENT] Interrupted by user")
            return False
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.sock.close()

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 p1_client.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    
    client = ReliableUDPClient(server_ip, server_port)
    success = client.run()
    sys.exit(0 if success else 1)
