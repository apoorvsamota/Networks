#!/usr/bin/env python3
"""
Part 2 Server: Reliable UDP File Transfer with Congestion Control
TCP Reno-style congestion control with optimizations
"""

import socket
import sys
import time
import struct
import os

# Constants
MSS = 1180
HEADER_SIZE = 20
MAX_PAYLOAD = 1200
INITIAL_RTO = 0.3
ALPHA = 0.125
BETA = 0.25
MIN_RTO = 0.1
MAX_RTO = 2.0

# Congestion control states
SLOW_START = 0
CONGESTION_AVOIDANCE = 1
FAST_RECOVERY = 2

class CongestionControlServer:
    def __init__(self, ip, port):
        self.ip = ip
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        
        # Window state
        self.base = 0
        self.next_seq = 0
        
        # Congestion control
        self.cwnd = MSS  # Start with 1 MSS
        self.ssthresh = 64 * MSS  # Initial threshold
        self.state = SLOW_START
        self.bytes_acked_ca = 0  # For CA mode increment
        
        # Dictionary-based storage (O(1) access)
        self.packets = {}
        self.pkt_lens = {}
        self.send_times = {}
        self.timeouts = {}
        self.acked = set()
        
        # RTO estimation
        self.est_rtt = None
        self.dev_rtt = 0
        self.rto = INITIAL_RTO
        
        # Fast retransmit/recovery
        self.dup_acks = {}
        self.last_ack = 0
        self.recover = 0  # Recovery sequence number
        
        # Stats
        self.sent = 0
        self.retrans = 0
        self.fast_retrans = 0
        self.timeouts_count = 0
        
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
        
        self.rto = self.est_rtt + 4 * self.dev_rtt
        self.rto = max(MIN_RTO, min(MAX_RTO, self.rto))
    
    def on_new_ack(self, bytes_acked):
        """Update cwnd based on current state and bytes ACKed"""
        if self.state == SLOW_START:
            # Slow start: increase cwnd by bytes_acked (exponential growth)
            self.cwnd += bytes_acked
            
            # Transition to CA if we hit ssthresh
            if self.cwnd >= self.ssthresh:
                self.state = CONGESTION_AVOIDANCE
                self.bytes_acked_ca = 0
                
        elif self.state == CONGESTION_AVOIDANCE:
            # Congestion avoidance: increase cwnd by MSS per RTT
            # Approximate: increase by (MSS * MSS / cwnd) per ACK
            self.bytes_acked_ca += bytes_acked
            if self.bytes_acked_ca >= self.cwnd:
                self.cwnd += MSS
                self.bytes_acked_ca = 0
                
        elif self.state == FAST_RECOVERY:
            # Fast recovery: inflate cwnd by bytes_acked
            self.cwnd += bytes_acked
    
    def on_dup_ack(self):
        """Handle duplicate ACK"""
        if self.state == FAST_RECOVERY:
            # Already in fast recovery, inflate window
            self.cwnd += MSS
            
    def on_fast_retransmit(self):
        """Enter fast recovery after 3 dup ACKs"""
        if self.state != FAST_RECOVERY:
            # Save recovery point
            self.recover = self.next_seq
            
            # Reduce threshold and cwnd
            self.ssthresh = max(self.cwnd // 2, 2 * MSS)
            self.cwnd = self.ssthresh + 3 * MSS
            self.state = FAST_RECOVERY
            self.bytes_acked_ca = 0
            
    def exit_fast_recovery(self):
        """Exit fast recovery on new ACK"""
        self.cwnd = self.ssthresh
        self.state = CONGESTION_AVOIDANCE
        self.bytes_acked_ca = 0
        
    def on_timeout(self):
        """Handle timeout event"""
        self.timeouts_count += 1
        
        # Reduce threshold
        self.ssthresh = max(self.cwnd // 2, 2 * MSS)
        
        # Reset cwnd to 1 MSS
        self.cwnd = MSS
        
        # Enter slow start
        self.state = SLOW_START
        self.bytes_acked_ca = 0
        
        # Exponential backoff
        self.rto = min(self.rto * 2, MAX_RTO)
        
    def get_timeout(self):
        if not self.timeouts:
            return self.rto
        
        now = time.time()
        earliest = min(self.timeouts.values())
        return max(0.001, earliest - now)
    
    def check_timeouts(self, addr, all_pkts):
        now = time.time()
        timed_out = [seq for seq, t in self.timeouts.items() 
                     if seq not in self.acked and now >= t]
        
        if timed_out:
            # Timeout event - handle congestion
            self.on_timeout()
            
            # Retransmit base packet
            if self.base in all_pkts and self.base not in self.acked:
                self.sock.sendto(all_pkts[self.base], addr)
                self.retrans += 1
                self.send_times[self.base] = now
                self.timeouts[self.base] = now + self.rto
            
            # Reset dup ack counter
            self.dup_acks.clear()
            self.last_ack = self.base
    
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
        print(f"[SERVER] Initial cwnd: {self.cwnd} bytes ({self.cwnd/MSS:.1f} MSS)")
        print(f"[SERVER] Initial ssthresh: {self.ssthresh} bytes ({self.ssthresh/MSS:.1f} MSS)")
        
        try:
            with open(fname, 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            print(f"[ERROR] File not found: {fname}")
            return
        
        file_size = len(data)
        print(f"[SERVER] File size: {file_size} bytes")
        
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
        
        print(f"[SERVER] Total packets: {len(all_pkts)}")
        
        # Reset state
        self.base = 0
        self.next_seq = 0
        self.cwnd = MSS
        self.ssthresh = 64 * MSS
        self.state = SLOW_START
        self.bytes_acked_ca = 0
        self.packets = {}
        self.send_times = {}
        self.timeouts = {}
        self.acked = set()
        self.dup_acks = {}
        self.last_ack = 0
        self.recover = 0
        self.sent = 0
        self.retrans = 0
        self.fast_retrans = 0
        self.timeouts_count = 0
        
        start = time.time()
        last_print = start
        
        # Main loop
        while self.base < file_size:
            # Send packets within congestion window
            while self.next_seq < file_size:
                bytes_in_flight = self.next_seq - self.base
                if bytes_in_flight >= self.cwnd:
                    break
                
                if self.next_seq in all_pkts:
                    if self.next_seq not in self.acked:
                        pkt = all_pkts[self.next_seq]
                        self.sock.sendto(pkt, addr)
                        self.packets[self.next_seq] = pkt
                        self.sent += 1
                        
                        now = time.time()
                        self.send_times[self.next_seq] = now
                        self.timeouts[self.next_seq] = now + self.rto
                    
                    self.next_seq += all_lens[self.next_seq]
                else:
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
                
                # Check if this is a new ACK (advances window)
                if ack_num > self.base:
                    # New ACK - calculate bytes acked
                    bytes_acked = 0
                    s = self.base
                    while s < ack_num and s < file_size:
                        if s in all_lens:
                            if s not in self.acked:
                                self.acked.add(s)
                                bytes_acked += all_lens[s]
                                
                                # Update RTT if this was the base packet
                                if s == self.base and s in self.send_times:
                                    sample = recv_time - self.send_times[s]
                                    self.update_rto(sample)
                            s += all_lens[s]
                        else:
                            s += MSS
                    
                    # Update congestion window
                    if bytes_acked > 0:
                        # Check if exiting fast recovery
                        if self.state == FAST_RECOVERY and ack_num >= self.recover:
                            self.exit_fast_recovery()
                        else:
                            self.on_new_ack(bytes_acked)
                    
                    # Slide window
                    self.slide_window(all_lens)
                    
                    # Reset dup ack counter
                    self.dup_acks.clear()
                    self.last_ack = ack_num
                    
                    # Progress update
                    if time.time() - last_print > 1.0:
                        prog = (self.base / file_size) * 100
                        state_str = ["SS", "CA", "FR"][self.state]
                        print(f"[SERVER] {prog:.1f}% | cwnd: {self.cwnd/MSS:.1f} MSS | "
                              f"ssthresh: {self.ssthresh/MSS:.1f} MSS | state: {state_str} | "
                              f"RTO: {self.rto:.3f}s | sent: {self.sent} | retrans: {self.retrans}")
                        last_print = time.time()
                
                # Handle SACKs
                for l, r in sacks:
                    s = l
                    while s < r and s < file_size:
                        if s in all_lens and s >= self.base and s not in self.acked:
                            self.acked.add(s)
                            s += all_lens[s]
                        else:
                            s += MSS
                
                # Check for duplicate ACK
                if ack_num == self.last_ack and ack_num == self.base:
                    cnt = self.dup_acks.get(ack_num, 0) + 1
                    self.dup_acks[ack_num] = cnt
                    
                    if cnt == 3:
                        # Fast retransmit
                        if self.base in all_pkts and self.base not in self.acked:
                            self.on_fast_retransmit()
                            self.sock.sendto(all_pkts[self.base], addr)
                            self.retrans += 1
                            self.fast_retrans += 1
                            self.timeouts[self.base] = time.time() + self.rto
                    
                    elif cnt > 3 and self.state == FAST_RECOVERY:
                        # Additional dup ACK in fast recovery
                        self.on_dup_ack()
            
            except socket.timeout:
                self.check_timeouts(addr, all_pkts)
        
        elapsed = time.time() - start
        
        print(f"\n[SERVER] Transfer complete!")
        print(f"[SERVER] Time: {elapsed:.2f}s")
        print(f"[SERVER] Throughput: {file_size * 8 / elapsed / 1_000_000:.2f} Mbps")
        print(f"[SERVER] Packets sent: {self.sent}")
        print(f"[SERVER] Retransmissions: {self.retrans} ({100*self.retrans/max(1,self.sent):.1f}%)")
        print(f"[SERVER] Fast retransmits: {self.fast_retrans}")
        print(f"[SERVER] Timeouts: {self.timeouts_count}")
        print(f"[SERVER] Final cwnd: {self.cwnd/MSS:.1f} MSS")
        print(f"[SERVER] Final ssthresh: {self.ssthresh/MSS:.1f} MSS")
        print(f"[SERVER] Final RTO: {self.rto:.4f}s")
        
        # Send EOF
        print(f"[SERVER] Sending EOF...")
        eof = self.make_pkt(file_size, b'EOF')
        for _ in range(5):
            self.sock.sendto(eof, addr)
            time.sleep(0.02)
    
    def run(self):
        print(f"[SERVER] Listening on {self.ip}:{self.port}")
        print(f"[SERVER] MSS: {MSS} bytes")
        
        try:
            self.sock.settimeout(None)
            _, addr = self.sock.recvfrom(1024)
            print(f"[SERVER] Client connected: {addr}")
            
            self.send_file(addr, 'data.txt')
            
        except KeyboardInterrupt:
            print("\n[SERVER] Interrupted")
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.sock.close()

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <IP> <PORT>")
        sys.exit(1)
    
    srv = CongestionControlServer(sys.argv[1], int(sys.argv[2]))
    srv.run()
