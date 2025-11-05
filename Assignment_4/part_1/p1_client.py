#!/usr/bin/env python3
"""
Part 1 Client: Reliable UDP File Transfer
Optimized for speed
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
        
        # State
        self.expected_seq = 0
        self.buffer = {}
        self.file_data = bytearray()
        
        # Stats
        self.pkts_recv = 0
        self.acks_sent = 0
        self.ooo_count = 0
        self.dup_count = 0
        
    def parse_pkt(self, pkt):
        if len(pkt) < HEADER_SIZE:
            return None, None
        
        seq = struct.unpack('!I', pkt[:4])[0]
        data = pkt[HEADER_SIZE:]
        return seq, data
    
    def build_sacks(self):
        if not self.buffer:
            return []
        
        sorted_seqs = sorted(self.buffer.keys())
        ranges = []
        
        start = sorted_seqs[0]
        end = start + len(self.buffer[start])
        
        for seq in sorted_seqs[1:]:
            if seq == end:
                end = seq + len(self.buffer[seq])
            else:
                if len(ranges) < 2:
                    ranges.append((start, end))
                start = seq
                end = seq + len(self.buffer[seq])
        
        if len(ranges) < 2:
            ranges.append((start, end))
        
        return ranges[:2]
    
    def send_ack(self, ack_num):
        ack_pkt = struct.pack('!I', ack_num)
        
        sacks = self.build_sacks()
        for s, e in sacks:
            ack_pkt += struct.pack('!II', s, e)
        
        while len(ack_pkt) < 20:
            ack_pkt += b'\x00'
        
        self.sock.sendto(ack_pkt, (self.server_ip, self.server_port))
        self.acks_sent += 1
        
        if self.acks_sent % 50 == 0 or sacks:
            if sacks:
                sack_str = ", ".join([f"[{s}-{e})" for s, e in sacks])
                print(f"[CLIENT] ACK={ack_num} SACK: {sack_str}")
    
    def request_file(self):
        req = b'1'
        
        for attempt in range(MAX_REQUEST_RETRIES):
            print(f"[CLIENT] Request attempt {attempt + 1}/{MAX_REQUEST_RETRIES}")
            self.sock.sendto(req, (self.server_ip, self.server_port))
            self.sock.settimeout(REQUEST_TIMEOUT)
            
            try:
                pkt, _ = self.sock.recvfrom(MAX_PAYLOAD + 100)
                print(f"[CLIENT] Got first packet")
                return pkt
            except socket.timeout:
                if attempt == MAX_REQUEST_RETRIES - 1:
                    print("[ERROR] No response from server")
                    return None
        
        return None
    
    def deliver_buffered(self):
        while self.expected_seq in self.buffer:
            data = self.buffer.pop(self.expected_seq)
            self.file_data.extend(data)
            self.expected_seq += len(data)
    
    def receive_file(self, output='received_data.txt'):
        print(f"[CLIENT] Connecting to {self.server_ip}:{self.server_port}")
        
        first_pkt = self.request_file()
        if not first_pkt:
            return False
        
        print(f"[CLIENT] Transfer starting...")
        start = time.time()
        last_print = start
        
        seq, data = self.parse_pkt(first_pkt)
        if seq is None:
            print("[ERROR] Invalid first packet")
            return False
        
        if data == b'EOF':
            print("[CLIENT] Empty file")
            with open(output, 'wb') as f:
                f.write(b'')
            return True
        
        # CRITICAL: First packet MUST be seq=0, otherwise buffer it
        if seq == 0:
            self.file_data.extend(data)
            self.expected_seq = len(data)
            self.pkts_recv += 1
            self.send_ack(self.expected_seq)
        else:
            # First packet is out of order! Buffer it and wait for seq=0
            print(f"[CLIENT] First packet out of order (seq={seq}), buffering")
            self.buffer[seq] = data
            self.ooo_count += 1
            self.pkts_recv += 1
            self.send_ack(0)  # Still expecting seq=0
        
        # After processing first packet, try to deliver any buffered data
        self.deliver_buffered()
        
        # NO timeout - blocking receive
        self.sock.settimeout(None)
        
        eof_received = False
        eof_seq = None  # Track EOF sequence number
        
        while not eof_received:
            try:
                pkt, _ = self.sock.recvfrom(MAX_PAYLOAD + 100)
                
                seq, data = self.parse_pkt(pkt)
                if seq is None:
                    continue
                
                self.pkts_recv += 1
                
                if data == b'EOF':
                    print(f"[CLIENT] EOF received at seq={seq}")
                    eof_seq = seq
                    eof_received = True
                    # Don't break immediately - continue receiving for a bit to get late packets
                    continue
                
                if seq == self.expected_seq:
                    self.file_data.extend(data)
                    self.expected_seq += len(data)
                    self.deliver_buffered()
                    self.send_ack(self.expected_seq)
                    
                elif seq > self.expected_seq:
                    if seq not in self.buffer:
                        self.buffer[seq] = data
                        self.ooo_count += 1
                    else:
                        self.dup_count += 1
                    self.send_ack(self.expected_seq)
                    
                else:
                    self.dup_count += 1
                    self.send_ack(self.expected_seq)
                
                if time.time() - last_print > 1.0:
                    print(f"[CLIENT] Received: {len(self.file_data)} bytes | "
                          f"Buffered: {len(self.buffer)} | Packets: {self.pkts_recv}")
                    last_print = time.time()
                    
            except Exception as e:
                print(f"[ERROR] {e}")
                break
        
        # After EOF is received, wait a bit more for any late packets
        if eof_received and eof_seq is not None and self.expected_seq < eof_seq:
            print(f"[CLIENT] Waiting for remaining packets (expected: {self.expected_seq}, EOF at: {eof_seq})...")
            self.sock.settimeout(2.0)  # Wait up to 2 seconds for late packets
            wait_start = time.time()
            
            while self.expected_seq < eof_seq and time.time() - wait_start < 3.0:
                try:
                    pkt, _ = self.sock.recvfrom(MAX_PAYLOAD + 100)
                    seq, data = self.parse_pkt(pkt)
                    
                    if seq is None or data == b'EOF':
                        continue
                    
                    self.pkts_recv += 1
                    
                    if seq == self.expected_seq:
                        self.file_data.extend(data)
                        self.expected_seq += len(data)
                        self.deliver_buffered()
                        self.send_ack(self.expected_seq)
                    elif seq > self.expected_seq:
                        if seq not in self.buffer:
                            self.buffer[seq] = data
                            self.ooo_count += 1
                        self.send_ack(self.expected_seq)
                    else:
                        self.dup_count += 1
                        self.send_ack(self.expected_seq)
                        
                    # Check if we're done
                    if self.expected_seq >= eof_seq:
                        print(f"[CLIENT] All packets received!")
                        break
                        
                except socket.timeout:
                    print(f"[CLIENT] Timeout waiting for packets")
                    break
                except Exception as e:
                    print(f"[ERROR] in late packet reception: {e}")
                    break
            
            if self.expected_seq < eof_seq:
                print(f"[CLIENT] WARNING: Missing data! Expected {self.expected_seq}, EOF at {eof_seq}")
                print(f"[CLIENT] Missing {eof_seq - self.expected_seq} bytes")
        
        elapsed = time.time() - start
        
        print(f"\n[CLIENT] Writing {len(self.file_data)} bytes to '{output}'")
        try:
            with open(output, 'wb') as f:
                f.write(self.file_data)
        except Exception as e:
            print(f"[ERROR] Write failed: {e}")
            return False
        
        print(f"[CLIENT] Complete!")
        print(f"[CLIENT] Time: {elapsed:.2f}s")
        print(f"[CLIENT] Bytes: {len(self.file_data)}")
        print(f"[CLIENT] Packets: {self.pkts_recv}")
        print(f"[CLIENT] ACKs sent: {self.acks_sent}")
        print(f"[CLIENT] Out-of-order: {self.ooo_count}")
        print(f"[CLIENT] Duplicates: {self.dup_count}")
        print(f"[CLIENT] Throughput: {len(self.file_data) * 8 / elapsed / 1_000_000:.2f} Mbps")
        
        return True
    
    def run(self, output='received_data.txt'):
        try:
            success = self.receive_file(output)
            if success:
                print(f"\n[SUCCESS] File saved to '{output}'")
            else:
                print(f"\n[FAILURE] Transfer failed")
            return success
        except KeyboardInterrupt:
            print("\n[CLIENT] Interrupted")
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
    
    client = ReliableUDPClient(sys.argv[1], int(sys.argv[2]))
    success = client.run()
    sys.exit(0 if success else 1)
