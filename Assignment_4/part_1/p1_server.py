#!/usr/bin/env python3
"""
Part 1 Server: Reliable UDP File Transfer
Optimized for high-jitter and lossy environments
"""

import socket
import sys
import time
import struct
import os
import heapq
import select

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
        # KEY OPTIMIZATION 1: Non-blocking socket for better responsiveness
        self.sock.setblocking(False)
        
        # Window state
        self.base = 0
        self.next_seq = 0
        
        # Dictionary-based storage (O(1) access)
        self.packets = {}
        self.pkt_lens = {}
        self.send_times = {}
        self.timeouts = {}
        self.acked = set()
        
        # KEY OPTIMIZATION 2: Heap-based timeout tracking for efficiency
        self.timeout_heap = []
        
        # RTO
        self.est_rtt = None
        self.dev_rtt = 0
        self.rto = INITIAL_RTO
        
        # Fast retransmit
        self.dup_acks = {}
        
        # KEY OPTIMIZATION 3: Track retransmitted packets for Karn's algorithm
        self.retransmitted_pkts = set()
        
        # KEY OPTIMIZATION 4: Track maximum SACK'd sequence for gap detection
        self.max_sack_seq = 0
        
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
        """Update RTO using exponential weighted moving average"""
        if self.est_rtt is None:
            self.est_rtt = sample
            self.dev_rtt = sample / 2
        else:
            self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample - self.est_rtt)
            self.est_rtt = (1 - ALPHA) * self.est_rtt + ALPHA * sample
        
        self.rto = self.est_rtt + 2.5 * self.dev_rtt
        self.rto = max(MIN_RTO, min(MAX_RTO, self.rto))
    
    def get_next_timeout(self):
        """Calculate timeout for select() call"""
        if not self.timeout_heap:
            return 0.1  # Default timeout
        
        now = time.time()
        # Peek at earliest timeout
        while self.timeout_heap:
            exp_time, seq = self.timeout_heap[0]
            # Verify this timeout is still valid
            if seq in self.timeouts and self.timeouts[seq] == exp_time:
                time_remaining = exp_time - now
                return max(0.001, min(time_remaining, 0.1))
            else:
                # Stale entry, remove it
                heapq.heappop(self.timeout_heap)
        
        return 0.1
    
    def retransmit_packet(self, seq, addr, now):
        """Retransmit a single packet"""
        if seq in self.packets and seq not in self.acked:
            self.sock.sendto(self.packets[seq], addr)
            self.retrans += 1
            self.send_times[seq] = now
            exp_time = now + self.rto
            self.timeouts[seq] = exp_time
            heapq.heappush(self.timeout_heap, (exp_time, seq))
            # Mark as retransmitted for Karn's algorithm
            self.retransmitted_pkts.add(seq)
    
    def check_timeouts(self, addr):
        """Check and handle all timed-out packets"""
        now = time.time()
        timed_out = []
        
        # Process timeout heap
        while self.timeout_heap:
            exp_time, seq = self.timeout_heap[0]
            
            # Check if this is still a valid timeout
            if seq not in self.timeouts or self.timeouts[seq] != exp_time:
                heapq.heappop(self.timeout_heap)
                continue
            
            # Check if actually timed out
            if exp_time <= now:
                heapq.heappop(self.timeout_heap)
                if seq not in self.acked:
                    timed_out.append(seq)
            else:
                break  # No more timeouts yet
        
        if timed_out:
            for seq in timed_out:
                self.retransmit_packet(seq, addr, now)
            # Conservative RTO increase on timeout
            self.rto = min(self.rto * 1.3, MAX_RTO)
    
    def process_all_acks(self, addr, all_lens, file_size):
        """KEY OPTIMIZATION 5: Process all available ACKs in batch"""
        acks_processed = 0
        last_ack_num = self.base
        
        try:
            while True:
                # Non-blocking receive
                ack_pkt, _ = self.sock.recvfrom(1024)
                recv_time = time.time()
                ack_num, sacks = self.parse_ack(ack_pkt)
                
                if ack_num is None:
                    continue
                
                acks_processed += 1
                
                # Update max SACK sequence
                if sacks:
                    for l, r in sacks:
                        self.max_sack_seq = max(self.max_sack_seq, r)
                
                # Handle cumulative ACK
                if ack_num > self.base:
                    # KEY OPTIMIZATION 6: Karn's algorithm - only sample RTT for non-retransmitted packets
                    if (self.base in self.send_times and 
                        self.base not in self.acked and 
                        self.base not in self.retransmitted_pkts):
                        sample = recv_time - self.send_times[self.base]
                        self.update_rto(sample)
                    
                    # Mark all packets up to ack_num as acked
                    s = self.base
                    while s < ack_num and s < file_size:
                        if s in all_lens:
                            self.acked.add(s)
                            self.retransmitted_pkts.discard(s)
                            s += all_lens[s]
                        else:
                            s += MSS
                    
                    self.slide_window(all_lens)
                    self.dup_acks.clear()
                    last_ack_num = ack_num
                
                # Process SACKs
                for l, r in sacks:
                    s = l
                    while s < r and s < file_size:
                        if s in all_lens and s >= self.base:
                            self.acked.add(s)
                            s += all_lens[s]
                        else:
                            s += MSS
                
                # KEY OPTIMIZATION 7: Aggressive fast retransmit (2 dup ACKs)
                # with gap-based retransmission
                if ack_num == last_ack_num and ack_num == self.base:
                    cnt = self.dup_acks.get(ack_num, 0) + 1
                    self.dup_acks[ack_num] = cnt
                    
                    # Trigger on 2 dup ACKs instead of 3
                    if cnt == 2 and self.max_sack_seq > self.base:
                        # Retransmit all gaps between base and max SACK
                        now = time.time()
                        s = self.base
                        while s < self.max_sack_seq:
                            if s in all_lens and s not in self.acked:
                                self.retransmit_packet(s, addr, now)
                                self.fast_retrans += 1
                            if s in all_lens:
                                s += all_lens[s]
                            else:
                                s += MSS
                        
                        self.dup_acks[ack_num] = 0  # Reset counter
                
        except BlockingIOError:
            # No more ACKs available
            pass
        
        return acks_processed
    
    def slide_window(self, all_lens):
        """Slide window forward based on acknowledged packets"""
        while self.base in self.acked and self.base in all_lens:
            self.acked.remove(self.base)
            self.packets.pop(self.base, None)
            self.send_times.pop(self.base, None)
            self.timeouts.pop(self.base, None)
            pkt_len = all_lens[self.base]
            self.base += pkt_len
    
    def send_packets(self, addr, all_pkts, all_lens, file_size):
        """Send new packets within window"""
        while self.next_seq < file_size:
            bytes_in_flight = self.next_seq - self.base
            if bytes_in_flight >= self.sws:
                break
            
            if self.next_seq in all_pkts and self.next_seq not in self.acked:
                pkt = all_pkts[self.next_seq]
                self.sock.sendto(pkt, addr)
                self.packets[self.next_seq] = pkt
                self.sent += 1
                
                now = time.time()
                self.send_times[self.next_seq] = now
                exp_time = now + self.rto
                self.timeouts[self.next_seq] = exp_time
                heapq.heappush(self.timeout_heap, (exp_time, self.next_seq))
            
            if self.next_seq in all_lens:
                self.next_seq += all_lens[self.next_seq]
            else:
                break
    
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
        
        # KEY OPTIMIZATION 8: Pre-cache all packets
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
        print(f"[SERVER] Optimizations: Non-blocking I/O, Batch ACK processing, Gap-based fast retransmit")
        
        # Reset state
        self.base = 0
        self.next_seq = 0
        self.packets = {}
        self.send_times = {}
        self.timeouts = {}
        self.timeout_heap = []
        self.acked = set()
        self.dup_acks = {}
        self.retransmitted_pkts = set()
        self.max_sack_seq = 0
        self.sent = 0
        self.retrans = 0
        self.fast_retrans = 0
        
        start = time.time()
        last_print = start
        
        # Main loop with select()
        while self.base < file_size:
            # Send new packets
            self.send_packets(addr, all_pkts, all_lens, file_size)
            
            # Calculate timeout for select
            timeout = self.get_next_timeout()
            
            # KEY OPTIMIZATION 9: Use select() for responsive I/O
            readable, _, _ = select.select([self.sock], [], [], timeout)
            
            if readable:
                # Process all available ACKs
                acks = self.process_all_acks(addr, all_lens, file_size)
            
            # Check for timeouts
            self.check_timeouts(addr)
            
            # Progress update
            if time.time() - last_print > 1.0:
                prog = (self.base / file_size) * 100
                print(f"[SERVER] {prog:.1f}% | Sent: {self.sent} | "
                      f"Retrans: {self.retrans} | RTO: {self.rto:.3f}s")
                last_print = time.time()
        
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
        # Switch back to blocking for EOF
        self.sock.setblocking(True)
        self.sock.settimeout(2.0)  # Add timeout for safety
        eof = self.make_pkt(file_size, b'EOF')
        for _ in range(5):
            self.sock.sendto(eof, addr)
            time.sleep(0.02)
        
        # Try to receive final ACK (optional, don't wait forever)
        try:
            self.sock.settimeout(0.5)
            ack_pkt, _ = self.sock.recvfrom(1024)
        except socket.timeout:
            pass  # Client may have already closed
    
    def run(self):
        print(f"[SERVER] Listening on {self.ip}:{self.port}")
        
        try:
            # Blocking wait for initial request
            self.sock.setblocking(True)
            _, addr = self.sock.recvfrom(1024)
            print(f"[SERVER] Client: {addr}")
            
            # Switch to non-blocking for transfer
            self.sock.setblocking(False)
            
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
