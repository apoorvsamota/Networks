#!/usr/bin/env python3
"""
Part 1 Server: Reliable UDP File Transfer
High-performance dictionary-based implementation
"""

import socket
import sys
import time
import struct
import os

# Constants - Aggressive for speed
MSS = 1180
HEADER_SIZE = 20
MAX_PAYLOAD = 1200
INITIAL_RTO = 0.2
ALPHA = 0.125
BETA = 0.25
MIN_RTO = 0.08
MAX_RTO = 1.5

class ReliableUDPServer:
    def __init__(self, ip, port, sws):
        self.ip = ip
        self.port = port
        self.sws = sws
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        
        # Window state
        self.base = 0
        self.next_seq = 0
        
        # Dictionary-based storage (O(1) access)
        self.packets = {}
        self.pkt_lens = {}
        self.send_times = {}
        self.timeouts = {}
        self.acked = set()
        
        # RTO
        self.est_rtt = None
        self.dev_rtt = 0
        self.rto = INITIAL_RTO
        
        # Fast retransmit
        self.dup_acks = {}
        
        # Stats
        self.sent = 0
        self.retrans = 0
        self.fast_retrans = 0
        
    def make_pkt(self, seq, data):
        hdr = struct.pack('!I', seq) + b'\x00' * 16
        return hdr + data
    
    def parse_ack(self, pkt):
        if len(pkt) < 4:
            return None, []
        
        ack = struct.unpack('!I', pkt[:4])[0]
        sacks = []
        
        if len(pkt) >= 20:
            try:
                for i in range(2):
                    off = 4 + i * 8
                    if off + 8 <= len(pkt):
                        l = struct.unpack('!I', pkt[off:off+4])[0]
                        r = struct.unpack('!I', pkt[off+4:off+8])[0]
                        if l > 0 and r > l:
                            sacks.append((l, r))
            except:
                pass
        
        return ack, sacks
    
    def update_rto(self, sample):
        if self.est_rtt is None:
            self.est_rtt = sample
            self.dev_rtt = sample / 2
        else:
            self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample - self.est_rtt)
            self.est_rtt = (1 - ALPHA) * self.est_rtt + ALPHA * sample
        
        self.rto = self.est_rtt + 2.5 * self.dev_rtt
        self.rto = max(MIN_RTO, min(MAX_RTO, self.rto))
    
    def get_timeout(self):
        if not self.timeouts:
            return self.rto
        
        now = time.time()
        earliest = min(self.timeouts.values())
        return max(0.005, earliest - now)
    
    def check_timeouts(self, addr):
        now = time.time()
        timed_out = [seq for seq, t in self.timeouts.items() 
                     if seq not in self.acked and now >= t]
        
        if timed_out:
            for seq in timed_out:
                if seq in self.packets:
                    self.sock.sendto(self.packets[seq], addr)
                    self.retrans += 1
                    self.send_times[seq] = now
                    self.timeouts[seq] = now + self.rto
            
            self.rto = min(self.rto * 1.3, MAX_RTO)
    
    def slide_window(self, all_lens):
        while self.base in self.acked and self.base in all_lens:
            self.acked.remove(self.base)
            self.packets.pop(self.base, None)
            self.send_times.pop(self.base, None)
            self.timeouts.pop(self.base, None)
            pkt_len = all_lens[self.base]
            self.base += pkt_len
    
    def send_file(self, addr, fname):
        print(f"[SERVER] Sending '{fname}' to {addr}")
        print(f"[SERVER] Window: {self.sws} bytes")
        
        try:
            with open(fname, 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            print(f"[ERROR] File not found")
            return
        
        file_size = len(data)
        print(f"[SERVER] Size: {file_size} bytes")
        
        # Prepare packets
        all_pkts = {}
        all_lens = {}
        seq = 0
        
        for i in range(0, file_size, MSS):
            chunk = data[i:i+MSS]
            pkt = self.make_pkt(seq, chunk)
            all_pkts[seq] = pkt
            all_lens[seq] = len(chunk)
            seq += len(chunk)
        
        print(f"[SERVER] Packets: {len(all_pkts)}")
        
        # Reset
        self.base = 0
        self.next_seq = 0
        self.packets = {}
        self.send_times = {}
        self.timeouts = {}
        self.acked = set()
        self.dup_acks = {}
        self.sent = 0
        self.retrans = 0
        self.fast_retrans = 0
        
        start = time.time()
        last_print = start
        last_ack_base = 0
        
        # Main loop
        while self.base < file_size:
            # Safety check: base should always be a valid packet boundary
            if self.base not in all_lens:
                print(f"[ERROR] Invalid base position: {self.base}")
                print(f"[ERROR] This indicates a bug in window management")
                break
            
            # Send packets
            while self.next_seq < file_size:
                bytes_in_flight = self.next_seq - self.base
                if bytes_in_flight >= self.sws:
                    break
                
                # Only send if this is a valid packet boundary
                if self.next_seq in all_pkts:
                    if self.next_seq not in self.acked:
                        pkt = all_pkts[self.next_seq]
                        self.sock.sendto(pkt, addr)
                        self.packets[self.next_seq] = pkt
                        self.sent += 1
                        
                        now = time.time()
                        self.send_times[self.next_seq] = now
                        self.timeouts[self.next_seq] = now + self.rto
                    
                    # Advance by actual packet length
                    self.next_seq += all_lens[self.next_seq]
                else:
                    # Should never happen, but safety check
                    print(f"[ERROR] Invalid next_seq: {self.next_seq}")
                    break
            
            # Wait for ACK
            timeout = self.get_timeout()
            self.sock.settimeout(timeout)
            
            try:
                ack_pkt, _ = self.sock.recvfrom(1024)
                recv_time = time.time()
                ack_num, sacks = self.parse_ack(ack_pkt)
                
                if ack_num is None:
                    continue
                
                # Cumulative ACK - only mark actual packet boundaries as acked
                if ack_num > self.base:
                    s = self.base
                    while s < ack_num and s < file_size:
                        if s in all_lens:  # Only process if valid packet boundary
                            if s not in self.acked:
                                self.acked.add(s)
                                if s == self.base and s in self.send_times:
                                    sample = recv_time - self.send_times[s]
                                    self.update_rto(sample)
                            s += all_lens[s]
                        else:
                            # Skip to next possible packet boundary
                            s += MSS
                    
                    self.slide_window(all_lens)
                    self.dup_acks.clear()
                    last_ack_base = ack_num
                    
                    if time.time() - last_print > 1.0:
                        prog = (self.base / file_size) * 100
                        print(f"[SERVER] {prog:.1f}% | Sent: {self.sent} | "
                              f"Retrans: {self.retrans} | RTO: {self.rto:.3f}s")
                        last_print = time.time()
                
                # SACKs - must check if s is actually a valid packet sequence
                for l, r in sacks:
                    s = l
                    while s < r and s < file_size:
                        # Only mark as acked if this is actually a packet boundary
                        if s in all_lens and s >= self.base and s not in self.acked:
                            self.acked.add(s)
                            s += all_lens[s]
                        else:
                            # Not a valid packet boundary, skip ahead
                            s += MSS
                
                # Fast retransmit
                if ack_num == last_ack_base:
                    cnt = self.dup_acks.get(ack_num, 0) + 1
                    self.dup_acks[ack_num] = cnt
                    
                    if cnt == 3 and self.base not in self.acked and self.base in self.packets:
                        self.sock.sendto(self.packets[self.base], addr)
                        self.retrans += 1
                        self.fast_retrans += 1
                        self.timeouts[self.base] = time.time() + self.rto
            
            except socket.timeout:
                self.check_timeouts(addr)
        
        elapsed = time.time() - start
        
        print(f"\n[SERVER] Complete!")
        print(f"[SERVER] Time: {elapsed:.2f}s")
        print(f"[SERVER] Throughput: {file_size * 8 / elapsed / 1_000_000:.2f} Mbps")
        print(f"[SERVER] Sent: {self.sent}")
        print(f"[SERVER] Retrans: {self.retrans} ({100*self.retrans/max(1,self.sent):.1f}%)")
        print(f"[SERVER] Fast retrans: {self.fast_retrans}")
        print(f"[SERVER] Final RTO: {self.rto:.4f}s")
        
        # EOF
        print(f"[SERVER] Sending EOF...")
        eof = self.make_pkt(file_size, b'EOF')
        for _ in range(5):
            self.sock.sendto(eof, addr)
            time.sleep(0.02)
    
    def run(self):
        print(f"[SERVER] Listening on {self.ip}:{self.port}")
        
        try:
            self.sock.settimeout(None)
            _, addr = self.sock.recvfrom(1024)
            print(f"[SERVER] Client: {addr}")
            
            self.send_file(addr, 'data.txt')
            
        except KeyboardInterrupt:
            print("\n[SERVER] Stopped")
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.sock.close()

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 p1_server.py <IP> <PORT> <SWS>")
        sys.exit(1)
    
    srv = ReliableUDPServer(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
    srv.run()
