#!/usr/bin/env python3
"""
Part 2 Client: High-Performance CUBIC Receiver
A refactored implementation of a reliable UDP client with SACK support.
Optimized for efficient buffering and fast ACK generation.
"""

import socket
import sys
import struct
import time

# --- Protocol Definitions ---
HEADER_OVERHEAD = 20
PACKET_LIMIT_BYTES = 1200
PAYLOAD_CAPACITY = PACKET_LIMIT_BYTES - HEADER_OVERHEAD
FIN_MARKER = b"EOF"

# --- Connection Tunables ---
HANDSHAKE_TIMEOUT_S = 2.0
HANDSHAKE_MAX_ATTEMPTS = 5

# --- Transfer Tunables ---
DATA_TIMEOUT_S = 8.0
MAX_STALL_COUNT = 8 # Max timeouts before aborting


class DataReceiverClient:
    """Client for reliable UDP file reception with SACK."""
    
    def __init__(self, target_ip, target_port, file_prefix):
        self.server_address = (target_ip, target_port)
        self.file_prefix = file_prefix
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Reception state
        self.cumulative_ack_point = 0   # Next in-order byte seq expected
        self.packet_cache = {}          # Buffers out-of-order packets
        self.file_data_buffer = bytearray() # Final assembled file data
        
        # Statistics
        self.total_packets_rcvd = 0
        self.total_acks_sent = 0
        self.ooo_packets_rcvd = 0
        self.dup_packets_rcvd = 0
        
        print(f"[Client] Ready to connect to {target_ip}:{target_port}")
        print(f"[Client] Output prefix: {self.file_prefix}")
    
    def _build_sack_options(self):
        """Generates SACK (Selective ACK) blocks from the packet cache."""
        if not self.packet_cache:
            return []
        
        sorted_sequences = sorted(self.packet_cache.keys())
        sack_blocks = []
        
        # Create contiguous ranges
        current_range_start = sorted_sequences[0]
        current_range_end = current_range_start + len(self.packet_cache[current_range_start])
        
        for seq in sorted_sequences[1:]:
            if seq == current_range_end:
                # Extend the current range
                current_range_end = seq + len(self.packet_cache[seq])
            else:
                # Gap found, start a new range
                if len(sack_blocks) < 2: # Max 2 SACK blocks
                    sack_blocks.append((current_range_start, current_range_end))
                current_range_start = seq
                current_range_end = seq + len(self.packet_cache[seq])
        
        # Add the last range
        if len(sack_blocks) < 2:
            sack_blocks.append((current_range_start, current_range_end))
        
        return sack_blocks
    
    def _construct_ack(self):
        """Builds a complete ACK packet with SACK options."""
        # Add cumulative ACK
        ack_payload = struct.pack('!I', self.cumulative_ack_point)
        
        # Add SACK blocks
        sack_options = self._build_sack_options()
        for left, right in sack_options:
            ack_payload += struct.pack('!II', left, right)
        
        # Pad to full header size
        ack_payload = ack_payload.ljust(HEADER_OVERHEAD, b'\x00')
        
        return ack_payload
    
    def _dispatch_ack(self):
        """Sends the current ACK packet to the server."""
        ack_packet = self._construct_ack()
        self.client_socket.sendto(ack_packet, self.server_address)
        self.total_acks_sent += 1
    
    def _parse_data_packet(self, packet):
        """Extracts sequence number and payload from a received packet."""
        if len(packet) < HEADER_OVERHEAD:
            return None, None
        
        seq_num = struct.unpack('!I', packet[:4])[0]
        payload = packet[HEADER_OVERHEAD:]
        return seq_num, payload
    
    def _request_file_from_server(self):
        """Sends the initial file request to the server, with retries."""
        request_payload = b'REQ'
        
        for attempt in range(HANDSHAKE_MAX_ATTEMPTS):
            print(f"[Client] Sending file request (Attempt {attempt + 1})...")
            self.client_socket.sendto(request_payload, self.server_address)
            self.client_socket.settimeout(HANDSHAKE_TIMEOUT_S)
            
            try:
                first_packet, addr = self.client_socket.recvfrom(PACKET_LIMIT_BYTES)
                print("[Client] Server ACK. Receiving data...")
                return first_packet, addr
            except socket.timeout:
                if attempt == HANDSHAKE_MAX_ATTEMPTS - 1:
                    print("[Client] ERROR: Server is not responding.")
                    sys.exit(1)
                continue
        
        return None, None
    
    def _assemble_from_cache(self):
        """Moves in-order packets from the cache to the final data buffer."""
        while self.cumulative_ack_point in self.packet_cache:
            # This packet is now in-order
            data_chunk = self.packet_cache.pop(self.cumulative_ack_point)
            chunk_len = len(data_chunk)
            
            self.file_data_buffer.extend(data_chunk)
            self.cumulative_ack_point += chunk_len
    
    def _on_packet_received(self, seq_num, payload):
        """Processes a single received data packet."""
        if seq_num == self.cumulative_ack_point:
            # Packet is in-order. Add to cache and try to assemble.
            self.packet_cache[seq_num] = payload
            self._assemble_from_cache()
        elif seq_num < self.cumulative_ack_point:
            # This is an old duplicate packet.
            self.dup_packets_rcvd += 1
        else:
            # This is an out-of-order packet.
            if seq_num not in self.packet_cache:
                self.ooo_packets_rcvd += 1
                self.packet_cache[seq_num] = payload
            else:
                # This is a duplicate of a cached packet.
                self.dup_packets_rcvd += 1
    
    def execute_transfer(self, output_filepath):
        """Main loop for receiving the file."""
        start_transfer_time = time.time()
        last_status_print_time = start_transfer_time
        
        # Initiate connection
        first_packet, addr = self._request_file_from_server()
        self.client_socket.settimeout(DATA_TIMEOUT_S)
        
        consecutive_timeout_count = 0
        packets_in_batch = [first_packet]
        
        while True:
            # Process all packets received in the last batch
            for packet in packets_in_batch:
                seq_num, payload = self._parse_data_packet(packet)
                
                if seq_num is None:
                    continue # Skip malformed
                
                self.total_packets_rcvd += 1
                consecutive_timeout_count = 0 # Reset stall counter
                
                # Check for FIN marker
                if payload == FIN_MARKER:
                    transfer_duration = time.time() - start_transfer_time
                    print(f"\n[Client] FIN received. Transfer complete.")
                    
                    # Final statistics
                    print(f"[Client] Total Time: {transfer_duration:.2f}s")
                    total_bytes = len(self.file_data_buffer)
                    print(f"[Client] Total Bytes: {total_bytes}")
                    print(f"[Client] Packets Rcvd: {self.total_packets_rcvd}")
                    print(f"[Client] OOO Packets: {self.ooo_packets_rcvd}")
                    print(f"[Client] Dup Packets: {self.dup_packets_rcvd}")
                    
                    if transfer_duration > 0:
                        throughput_mbps = (total_bytes * 8 / transfer_duration / 1_000_000)
                        print(f"[Client] Avg Throughput: {throughput_mbps:.2f} Mbps")
                    
                    # Write to file
                    with open(output_filepath, 'wb') as f:
                        f.write(self.file_data_buffer)
                    
                    return True # Success
                
                # Process the data
                self._on_packet_received(seq_num, payload)
                
                # Acknowledge the packet
                self._dispatch_ack()
                
                # Print periodic status
                current_time = time.time()
                if current_time - last_status_print_time > 1.0:
                    print(f"[Client] Progress: {len(self.file_data_buffer)} bytes received, " +
                          f"Cache size: {len(self.packet_cache)} packets")
                    last_status_print_time = current_time
            
            # Wait for the next packet
            packets_in_batch = []
            try:
                new_packet, addr = self.client_socket.recvfrom(PACKET_LIMIT_BYTES)
                packets_in_batch.append(new_packet)
            except socket.timeout:
                consecutive_timeout_count += 1
                
                # Send another ACK to re-request data
                self._dispatch_ack()
                
                # Check if the transfer is stalled
                if consecutive_timeout_count >= MAX_STALL_COUNT:
                    print(f"\n[Client] ERROR: Transfer stalled after {MAX_STALL_COUNT} timeouts.")
                    
                    if len(self.file_data_buffer) > 0:
                        print("[Client] Saving partial data received...")
                        with open(output_filepath, 'wb') as f:
                            f.write(self.file_data_buffer)
                    
                    return False # Failure
                
                continue
            except Exception as e:
                print(f"[Client] ERROR: {e}")
                return False
    
    def start(self):
        """Runs the client's main logic."""
        output_filepath = f"{self.file_prefix}received_data.txt"
        print(f"[Client] Final output will be saved to: {output_filepath}")
        
        try:
            success = self.execute_transfer(output_filepath)
            
            if success:
                print(f"\n[Client] File saved successfully to '{output_filepath}'")
            else:
                print(f"\n[Client] Transfer failed or was incomplete.")
            
            return success
        
        except KeyboardInterrupt:
            print(f"\n[Client] User interrupted transfer. Exiting.")
            return False
        except Exception as e:
            print(f"[Client] An unexpected error occurred: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.client_socket.close()


def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p2_client_refactored.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>")
        sys.exit(1)
    
    client = DataReceiverClient(
        sys.argv[1],
        int(sys.argv[2]),
        sys.argv[3]
    )
    
    success = client.start()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()