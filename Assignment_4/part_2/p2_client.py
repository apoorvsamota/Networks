#!/usr/bin/env python3
"""
Part 2 Client: Improved Reliable UDP File Transfer Client
Enhanced timeout handling and efficient SACK support
"""

import socket
import sys
import struct
import time

# Protocol Constants
HEADER_SIZE = 20
MAX_PACKET_SIZE = 1200
MAX_DATA_SIZE = MAX_PACKET_SIZE - HEADER_SIZE
EOF_MARKER = b"EOF"

# Request Parameters
REQUEST_TIMEOUT = 2.0
MAX_REQUEST_RETRIES = 5

# Transfer Parameters
TRANSFER_TIMEOUT = 8.0
MAX_CONSECUTIVE_TIMEOUTS = 8


class ImprovedReliableClient:
    """Client for reliable UDP file transfer with SACK"""
    
    def __init__(self, server_ip, server_port, prefix_filename):
        self.server_ip = server_ip
        self.server_port = server_port
        self.prefix_filename = prefix_filename
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Reception state
        self.expected_seq = 0
        self.out_of_order_buffer = {}
        self.received_data = bytearray()
        
        # Statistics
        self.packets_received = 0
        self.acks_sent = 0
        self.out_of_order_packets = 0
        self.duplicate_packets = 0
        
        print(f"[CLIENT] Connecting to {server_ip}:{server_port}")
        print(f"[CLIENT] Prefix: {prefix_filename}")
    
    def create_sack_blocks(self):
        """Generate SACK blocks from buffered packets"""
        if not self.out_of_order_buffer:
            return []
        
        sorted_seqs = sorted(self.out_of_order_buffer.keys())
        sack_blocks = []
        
        # Build contiguous ranges
        range_start = sorted_seqs[0]
        range_end = range_start + len(self.out_of_order_buffer[range_start])
        
        for seq in sorted_seqs[1:]:
            if seq == range_end:
                # Extend current range
                range_end = seq + len(self.out_of_order_buffer[seq])
            else:
                # Save current range and start new one
                if len(sack_blocks) < 2:
                    sack_blocks.append((range_start, range_end))
                range_start = seq
                range_end = seq + len(self.out_of_order_buffer[seq])
        
        # Add final range
        if len(sack_blocks) < 2:
            sack_blocks.append((range_start, range_end))
        
        return sack_blocks[:2]
    
    def build_ack_packet(self, ack_num):
        """Build ACK packet with SACK information"""
        # Start with cumulative ACK
        ack_packet = struct.pack('!I', ack_num)
        
        # Add SACK blocks
        sack_blocks = self.create_sack_blocks()
        for left_edge, right_edge in sack_blocks:
            ack_packet += struct.pack('!II', left_edge, right_edge)
        
        # Pad to header size
        while len(ack_packet) < HEADER_SIZE:
            ack_packet += b'\x00'
        
        return ack_packet
    
    def transmit_ack(self, ack_num):
        """Send ACK to server"""
        ack_packet = self.build_ack_packet(ack_num)
        self.socket.sendto(ack_packet, (self.server_ip, self.server_port))
        self.acks_sent += 1
    
    def parse_packet(self, packet):
        """Extract sequence number and data from packet"""
        if len(packet) < HEADER_SIZE:
            return None, None
        
        seq_num = struct.unpack('!I', packet[:4])[0]
        data = packet[HEADER_SIZE:]
        return seq_num, data
    
    def send_initial_request(self):
        """Send file request with retries"""
        request_data = b'R'
        
        for attempt in range(MAX_REQUEST_RETRIES):
            print(f"[CLIENT] Sending request (attempt {attempt + 1}/{MAX_REQUEST_RETRIES})")
            self.socket.sendto(request_data, (self.server_ip, self.server_port))
            self.socket.settimeout(REQUEST_TIMEOUT)
            
            try:
                first_packet, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
                print(f"[CLIENT] Received response from server")
                return first_packet, addr
            except socket.timeout:
                if attempt == MAX_REQUEST_RETRIES - 1:
                    print(f"[CLIENT] ERROR: No response from server")
                    sys.exit(1)
                continue
        
        return None, None
    
    def process_buffered_packets(self):
        """Deliver buffered in-order packets"""
        while self.expected_seq in self.out_of_order_buffer:
            data = self.out_of_order_buffer[self.expected_seq]
            data_length = len(data)
            
            self.received_data.extend(data)
            del self.out_of_order_buffer[self.expected_seq]
            self.expected_seq += data_length
    
    def handle_data_packet(self, seq_num, data):
        """Process received data packet"""
        if seq_num == self.expected_seq:
            # In-order packet - deliver immediately
            self.out_of_order_buffer[seq_num] = data
            self.process_buffered_packets()
        elif seq_num < self.expected_seq:
            # Duplicate packet
            self.duplicate_packets += 1
        else:
            # Out-of-order packet - buffer it
            if seq_num not in self.out_of_order_buffer:
                self.out_of_order_packets += 1
                self.out_of_order_buffer[seq_num] = data
            else:
                self.duplicate_packets += 1
    
    def receive_file_transfer(self, output_filename):
        """Main file reception loop"""
        start_time = time.time()
        last_status_time = start_time
        
        # Get first packet
        first_packet, addr = self.send_initial_request()
        self.socket.settimeout(TRANSFER_TIMEOUT)
        
        consecutive_timeouts = 0
        packets_to_handle = [first_packet]
        
        while True:
            # Process all pending packets
            for packet in packets_to_handle:
                seq_num, data = self.parse_packet(packet)
                
                if seq_num is None:
                    continue
                
                self.packets_received += 1
                consecutive_timeouts = 0
                
                # Check for EOF marker
                if data == EOF_MARKER:
                    elapsed = time.time() - start_time
                    print(f"\n[CLIENT] Transfer complete!")
                    print(f"[CLIENT] Time: {elapsed:.2f}s")
                    print(f"[CLIENT] Bytes received: {len(self.received_data)}")
                    print(f"[CLIENT] Packets: {self.packets_received}")
                    print(f"[CLIENT] Out-of-order: {self.out_of_order_packets}")
                    print(f"[CLIENT] Duplicates: {self.duplicate_packets}")
                    
                    throughput = (len(self.received_data) * 8 / elapsed / 1_000_000)
                    print(f"[CLIENT] Throughput: {throughput:.2f} Mbps")
                    
                    # Write received data to file
                    with open(output_filename, 'wb') as f:
                        f.write(self.received_data)
                    
                    return True
                
                # Process data packet
                self.handle_data_packet(seq_num, data)
                
                # Send ACK
                self.transmit_ack(self.expected_seq)
                
                # Periodic status update
                current_time = time.time()
                if current_time - last_status_time > 1.0:
                    print(f"[CLIENT] Progress: {len(self.received_data)} bytes, " +
                          f"Buffered: {len(self.out_of_order_buffer)} packets")
                    last_status_time = current_time
            
            # Wait for next packet
            packets_to_handle = []
            try:
                packet, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
                packets_to_handle.append(packet)
            except socket.timeout:
                consecutive_timeouts += 1
                
                # Send ACK to prompt retransmission
                self.transmit_ack(self.expected_seq)
                
                # Check if transfer is stuck
                if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    print(f"\n[CLIENT] Warning: Multiple timeouts, saving partial data")
                    
                    # Save whatever we received
                    if len(self.received_data) > 0:
                        with open(output_filename, 'wb') as f:
                            f.write(self.received_data)
                    
                    return False
                
                continue
            except Exception as e:
                print(f"[CLIENT] ERROR: {e}")
                return False
    
    def run(self):
        """Execute client operations"""
        output_filename = f"{self.prefix_filename}received_data.txt"
        print(f"[CLIENT] Output file: {output_filename}")
        
        try:
            success = self.receive_file_transfer(output_filename)
            
            if success:
                print(f"\n[CLIENT] File saved successfully to '{output_filename}'")
            else:
                print(f"\n[CLIENT] Transfer incomplete, partial data saved")
            
            return success
        
        except KeyboardInterrupt:
            print(f"\n[CLIENT] Interrupted by user")
            return False
        except Exception as e:
            print(f"[CLIENT] ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.socket.close()


def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>")
        sys.exit(1)
    
    client = ImprovedReliableClient(
        sys.argv[1],
        int(sys.argv[2]),
        sys.argv[3]
    )
    
    success = client.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
