#!/usr/bin/env python3
"""
Part 2 Client: Modular SACK Receiver (Corrected)
A structurally re-architected reliable UDP client.
Packet buffering and SACK generation are isolated into a
helper class, simplifying the main client's logic.
"""

import socket
import sys
import struct
import time

# --- Constants ---
HEADER_LEN = 20
MAX_PACKET = 1200
MAX_PAYLOAD = MAX_PACKET - HEADER_LEN
EOF_FLAG = b"EOF"

# --- Config ---
REQUEST_TIMEOUT = 2.0
MAX_REQUEST_RETRIES = 5
TRANSFER_TIMEOUT = 8.0
MAX_CONSECUTIVE_TIMEOUTS = 8


#
# 🔴======= THIS CLASS HAS BEEN FIXED =======🔴
#
class PacketBuffer:
    """Manages buffering, assembly, and SACK generation."""
    
    def __init__(self):
        self.expected_seq = 0
        self.buffer = {}
        self.data_store = bytearray()
    
    def get_expected_seq(self):
        """Returns the current cumulative ACK point."""
        return self.expected_seq
    
    def get_final_data(self):
        """Returns the fully assembled data."""
        return self.data_store
    
    def get_buffer_size(self):
        """Returns the number of out-of-order packets."""
        return len(self.buffer)
        
    def add_packet(self, seq_num, data):
        """
        Adds a packet to the buffer.
        Returns (is_new_packet, is_duplicate)
        """
        
        # --- THIS IS THE CORRECTED LOGIC ---
        
        # 1. Check if it's an old, already-processed duplicate
        if seq_num < self.expected_seq:
            return False, True # Is duplicate
        
        # 2. Check if it's a duplicate of a packet already in the OOO buffer
        if seq_num in self.buffer:
            return False, True # Is duplicate
        
        # 3. It's a new packet (either in-order or OOO)
        self.buffer[seq_num] = data
        
        # 4. If it's in-order, process the contiguous block
        if seq_num == self.expected_seq:
            self._assemble_contiguous()
            
        return True, False # It's a new packet

    def _assemble_contiguous(self):
        """Internal: Assembles all in-order packets from the buffer."""
        while self.expected_seq in self.buffer:
            data = self.buffer.pop(self.expected_seq)
            self.data_store.extend(data)
            self.expected_seq += len(data)

    def generate_ack_packet(self):
        """Builds the full ACK packet with SACK blocks."""
        # 1. Start with cumulative ACK
        ack_pkt = struct.pack('!I', self.expected_seq)
        
        # 2. Generate and add SACK blocks
        if not self.buffer:
            return ack_pkt.ljust(HEADER_LEN, b'\x00')
            
        sorted_seqs = sorted(self.buffer.keys())
        sack_blocks = []
        
        start = sorted_seqs[0]
        end = start + len(self.buffer[start])
        
        for seq in sorted_seqs[1:]:
            if seq == end:
                end = seq + len(self.buffer[seq])
            else:
                if len(sack_blocks) < 2:
                    sack_blocks.append((start, end))
                start = seq
                end = seq + len(self.buffer[seq])
        
        if len(sack_blocks) < 2:
            sack_blocks.append((start, end))
        
        for left, right in sack_blocks[:2]:
            ack_pkt += struct.pack('!II', left, right)
            
        # 3. Pad to header size
        return ack_pkt.ljust(HEADER_LEN, b'\x00')

#
# 🔴======= END OF FIXED CLASS =======🔴
#


class FileReceiver:
    """Main client class. Manages the socket and coordinates."""
    
    def __init__(self, server_ip, server_port, prefix):
        self.server_addr = (server_ip, server_port)
        self.output_file = f"{prefix}received_data.txt"
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # The buffer manager is now its own object
        self.buffer = PacketBuffer()
        
        # Stats
        self.stat_packets = 0
        self.stat_acks = 0
        self.stat_ooo = 0
        self.stat_dups = 0
        
        print(f"[Client] Connecting to {server_ip}:{server_port}")

    def _send_ack(self):
        """Constructs and sends an ACK."""
        ack_packet = self.buffer.generate_ack_packet()
        self.sock.sendto(ack_packet, self.server_addr)
        self.stat_acks += 1

    def _parse_packet(self, packet):
        if len(packet) < HEADER_LEN: return None, None
        seq_num = struct.unpack('!I', packet[:4])[0]
        data = packet[HEADER_LEN:]
        return seq_num, data

    def _initial_request(self):
        """Sends the first request to the server."""
        for attempt in range(MAX_REQUEST_RETRIES):
            print(f"[Client] Sending request (Attempt {attempt + 1})...")
            self.sock.sendto(b'R', self.server_addr)
            self.sock.settimeout(REQUEST_TIMEOUT)
            
            try:
                packet, addr = self.sock.recvfrom(MAX_PACKET)
                print("[Client] Server response received.")
                return packet
            except socket.timeout:
                continue
        
        print("[Client] ERROR: No response from server.")
        return None

    def start(self):
        """Runs the main client loop."""
        first_packet = self._initial_request()
        if not first_packet:
            return False

        self.sock.settimeout(TRANSFER_TIMEOUT)
        start_time = time.time()
        last_status_time = start_time
        
        packets_to_process = [first_packet]
        timeouts = 0
        
        while True:
            # --- Process Batch ---
            for packet in packets_to_process:
                seq_num, data = self._parse_packet(packet)
                if seq_num is None: continue
                
                self.stat_packets += 1
                timeouts = 0 # Reset timeout count
                
                # Check for EOF
                if data == EOF_FLAG:
                    self._write_to_file()
                    self._print_stats(time.time() - start_time)
                    return True
                
                # Add packet to buffer
                is_new, is_dup = self.buffer.add_packet(seq_num, data)
                
                if is_new:
                    self.stat_ooo += 1
                elif is_dup:
                    self.stat_dups += 1
                
                # Always send ACK
                self._send_ack()
                
                # Status print
                now = time.time()
                if now - last_status_time > 1.0:
                    print(f"[Client] Progress: {len(self.buffer.get_final_data())} bytes, " +
                          f"Buffered: {self.buffer.get_buffer_size()} packets")
                    last_status_time = now
            
            # --- Wait for Next Packet ---
            packets_to_process = []
            try:
                packet, addr = self.sock.recvfrom(MAX_PACKET)
                packets_to_process.append(packet)
            except socket.timeout:
                timeouts += 1
                print(f"[Client] Timeout {timeouts}/{MAX_CONSECUTIVE_TIMEOUTS}")
                self._send_ack() # Re-send ACK to prompt server
                
                if timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    print("[Client] Transfer stalled. Saving partial file.")
                    self._write_to_file()
                    self._print_stats(time.time() - start_time)
                    return False
            except Exception as e:
                print(f"[Client] Socket ERROR: {e}")
                return False

    def _write_to_file(self):
        """Saves the assembled data."""
        data = self.buffer.get_final_data()
        if not data:
            print("[Client] No data received.")
            return

        print(f"[Client] Writing {len(data)} bytes to {self.output_file}")
        with open(self.output_file, 'wb') as f:
            f.write(data)

    def _print_stats(self, elapsed):
        print("\n--- Transfer Stats ---")
        print(f"Time:       {elapsed:.2f}s")
        data_len = len(self.buffer.get_final_data())
        print(f"Bytes Rcvd: {data_len}")
        if elapsed > 0:
            thrpt = (data_len * 8 / elapsed / 1_000_000)
            print(f"Throughput: {thrpt:.2f} Mbps")
        print(f"Packets:    {self.stat_packets}")
        print(f"OOO:        {self.stat_ooo}")
        print(f"Duplicates: {self.stat_dups}")
        print(f"ACKs Sent:  {self.stat_acks}")

    def run(self):
        try:
            success = self.start()
            if success:
                print("\n[Client] File received successfully.")
            else:
                print("\n[Client] File transfer failed or was incomplete.")
            return success
        except KeyboardInterrupt:
            print("\n[Client] User interrupt. Exiting.")
            return False
        finally:
            self.sock.close()

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p2_client_refactored.py <IP> <PORT> <PREFIX>")
        sys.exit(1)
    
    client = FileReceiver(sys.argv[1], int(sys.argv[2]), sys.argv[3])
    success = client.run()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()