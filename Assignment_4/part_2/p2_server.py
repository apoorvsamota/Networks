#!/usr/bin/env python3
import socket
import sys
import struct
import time
import os
from collections import deque

# Protocol constants
PACKET_MAX = 1200
HEADER_BYTES = 20
PAYLOAD_BYTES = PACKET_MAX - HEADER_BYTES
TERMINATOR = b"EOF"

# RTO configuration
BASE_RTO = 0.1
FLOOR_RTO = 0.03
CEILING_RTO = 0.5
SMOOTH_COEFF = 0.125
VARIANCE_COEFF = 0.25

class WindowController:
    """BIC-based window management with RTT fairness"""
    
    def __init__(self):
        # Window state
        self.send_window = 2 * PAYLOAD_BYTES
        self.ss_threshold = 220 * PAYLOAD_BYTES
        self.peak_before_cut = 0
        self.prev_win_size = 0
        self.epoch_ts = 0
        self.growth_target = 1
        
        # BIC tuning for RTT fairness
        self.cut_factor = 0.7  # RTT-fair reduction
        self.fast_converge = True
        self.min_util_win = 14 * PAYLOAD_BYTES
        self.max_jump = 16 * PAYLOAD_BYTES  # Conservative
        self.smooth_param = 20
        self.binary_divisor = 4
        
        # Phase tracking
        self.in_slowstart = True
        self.ack_weight = 1
        
        # RTT fairness tracking
        self.rtt_samples = deque(maxlen=15)
        self.min_observed_rtt = float('inf')
        self.loss_history = deque(maxlen=10)
    
    def window_size(self):
        return int(self.send_window)
    
    def track_rtt_sample(self, rtt_val):
        """Track RTT for fairness adjustments"""
        if rtt_val > 0:
            self.rtt_samples.append(rtt_val)
            self.min_observed_rtt = min(self.min_observed_rtt, rtt_val)
    
    def _compute_rtt_penalty(self):
        """Calculate RTT-based growth penalty for fairness"""
        if len(self.rtt_samples) < 3:
            return 1.0
        
        recent_rtt = sum(list(self.rtt_samples)[-3:]) / 3
        if self.min_observed_rtt == 0:
            return 1.0
        
        rtt_ratio = recent_rtt / self.min_observed_rtt
        
        # Penalize if experiencing high RTT (congestion indicator)
        if rtt_ratio > 1.3:
            return 0.8
        elif rtt_ratio > 1.15:
            return 0.9
        return 1.0
    
    def on_new_ack(self, acked_bytes, rtt_measurement):
        """Process ACK and update window"""
        self.track_rtt_sample(rtt_measurement)
        
        if self.in_slowstart:
            # Slow start with RTT awareness
            growth = acked_bytes
            penalty = self._compute_rtt_penalty()
            growth = int(growth * penalty)
            self.send_window += growth
            
            if self.send_window >= self.ss_threshold:
                self.in_slowstart = False
                self.epoch_ts = 0
        else:
            # Congestion avoidance with BIC
            self._bic_increase()
            increment = acked_bytes / max(self.growth_target, 1)
            penalty = self._compute_rtt_penalty()
            increment *= penalty
            self.send_window += increment
        
        # Conservative cap for RTT fairness
        self.send_window = min(self.send_window, 520 * PAYLOAD_BYTES)
    
    def _bic_increase(self):
        """Binary Increase logic"""
        curr = int(self.send_window)
        
        if self.prev_win_size == curr:
            return
        
        self.prev_win_size = curr
        
        if self.epoch_ts == 0:
            self.epoch_ts = time.time()
        
        # Linear region
        if self.send_window <= self.min_util_win:
            self.growth_target = self.send_window / PAYLOAD_BYTES
            return
        
        # Binary search
        if self.send_window < self.peak_before_cut:
            distance = (self.peak_before_cut - self.send_window) / self.binary_divisor
            
            if distance > self.max_jump:
                self.growth_target = self.send_window / self.max_jump
            elif distance <= PAYLOAD_BYTES:
                self.growth_target = (self.send_window * self.smooth_param) / (self.binary_divisor * PAYLOAD_BYTES)
            else:
                self.growth_target = self.send_window / distance
        else:
            # Probing beyond peak
            probe_limit = self.peak_before_cut + self.binary_divisor * PAYLOAD_BYTES
            if self.send_window < probe_limit:
                self.growth_target = (self.send_window * self.smooth_param) / (self.binary_divisor * PAYLOAD_BYTES)
            elif self.send_window < self.peak_before_cut + self.max_jump * (self.binary_divisor - 1):
                denom = max(self.send_window - self.peak_before_cut, 1)
                self.growth_target = (self.send_window * (self.binary_divisor - 1)) / denom
            else:
                self.growth_target = self.send_window / self.max_jump
        
        # Initial conditions
        if self.peak_before_cut == 0:
            self.growth_target = min(self.growth_target, 16 * PAYLOAD_BYTES)
        
        self.growth_target = max(self.growth_target / self.ack_weight, 1)
    
    def on_congestion_event(self, event_kind="timeout"):
        """Handle loss events"""
        self.epoch_ts = 0
        self.loss_history.append(time.time())
        
        if event_kind == "dup_ack":
            # Fast retransmit - symmetric reduction for RTT fairness
            if self.fast_converge and self.send_window < self.peak_before_cut:
                self.peak_before_cut = self.send_window * (1 + self.cut_factor) / 2
            else:
                self.peak_before_cut = self.send_window
            
            # Symmetric reduction
            self.ss_threshold = max(int(self.send_window * self.cut_factor), 2 * PAYLOAD_BYTES)
            self.send_window = self.ss_threshold
            self.in_slowstart = False
        else:
            # Timeout
            self.ss_threshold = max(int(self.send_window * 0.6), 2 * PAYLOAD_BYTES)
            self.send_window = 3 * PAYLOAD_BYTES
            self.in_slowstart = True
            self.peak_before_cut = 0

class RTTEstimator:
    """RTT estimation and timeout calculation"""
    
    def __init__(self):
        self.smoothed = None
        self.variance = None
        self.rto_value = BASE_RTO
        self.sample_history = deque(maxlen=8)
    
    def rto(self):
        return self.rto_value
    
    def add_measurement(self, sample):
        """Process RTT sample"""
        self.sample_history.append(sample)
        
        if self.smoothed is None:
            self.smoothed = sample
            self.variance = sample / 2
        else:
            diff = abs(sample - self.smoothed)
            self.variance = (1 - VARIANCE_COEFF) * self.variance + VARIANCE_COEFF * diff
            self.smoothed = (1 - SMOOTH_COEFF) * self.smoothed + SMOOTH_COEFF * sample
        
        self.rto_value = self.smoothed + 4 * self.variance
        self.rto_value = max(FLOOR_RTO, min(self.rto_value, CEILING_RTO))
    
    def backoff(self):
        """RTO backoff"""
        self.rto_value = min(self.rto_value * 1.15, CEILING_RTO)

class FlightTracker:
    """Track packets in flight and manage retransmissions"""
    
    def __init__(self):
        self.left_edge = 0
        self.right_edge = 0
        self.acked = set()
        self.send_log = {}
        self.packet_store = {}
        self.expiry_times = {}
        self.dup_tracker = {}
    
    def acked_already(self, seq):
        return seq in self.acked
    
    def log_send(self, seq, payload, ts, timeout):
        """Record packet transmission"""
        pkt = self._make_packet(seq, payload)
        self.send_log[seq] = ts
        self.packet_store[seq] = pkt
        self.expiry_times[seq] = ts + timeout
    
    def update_send_time(self, seq, ts, timeout):
        """Update timing for retransmission"""
        self.send_log[seq] = ts
        self.expiry_times[seq] = ts + timeout
    
    def get_packet(self, seq):
        return self.packet_store.get(seq)
    
    def mark_ack(self, seq):
        self.acked.add(seq)
    
    def send_timestamp(self, seq):
        return self.send_log.get(seq)
    
    def advance_window(self):
        """Slide window forward"""
        while self.left_edge in self.acked:
            self.acked.remove(self.left_edge)
            self.send_log.pop(self.left_edge, None)
            self.packet_store.pop(self.left_edge, None)
            self.expiry_times.pop(self.left_edge, None)
            self.left_edge += PAYLOAD_BYTES
    
    def next_timeout(self, default):
        """Calculate next socket timeout"""
        if not self.expiry_times:
            return 0.01
        now = time.time()
        next_exp = min(self.expiry_times.values())
        return max(0.002, next_exp - now)
    
    def find_expired(self):
        """Identify timed out packets"""
        now = time.time()
        expired = []
        for seq, exp_time in list(self.expiry_times.items()):
            if seq not in self.acked and now >= exp_time:
                expired.append(seq)
        return sorted(expired)
    
    def count_duplicate(self, ack_val):
        """Track duplicate ACKs"""
        self.dup_tracker[ack_val] = self.dup_tracker.get(ack_val, 0) + 1
        return self.dup_tracker[ack_val]
    
    def reset_dup_tracking(self):
        self.dup_tracker.clear()
    
    def _make_packet(self, seq, payload):
        hdr = struct.pack('!I', seq) + b'\x00' * 16
        return hdr + payload

class TransferEngine:
    """Main transfer orchestration"""
    
    def __init__(self, listen_ip, listen_port):
        self.addr = (listen_ip, listen_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        self.sock.bind(('0.0.0.0', listen_port))
        
        # Core components
        self.window_ctrl = WindowController()
        self.rtt_est = RTTEstimator()
        self.flight = FlightTracker()
        
        # Transfer state
        self.peer = None
        self.file_data = None
        self.data_len = 0
        
        # Metrics
        self.pkts_sent = 0
        self.retransmits = 0
        self.fast_retx = 0
        self.acks_recv = 0
        
        print(f"[SRV] Listening on {listen_ip}:{listen_port}")
    
    def accept_client(self):
        """Wait for client"""
        print("[SRV] Waiting for client...")
        self.sock.settimeout(30.0)
        try:
            req, addr = self.sock.recvfrom(PACKET_MAX)
            self.peer = addr
            print(f"[SRV] Client: {addr}")
            self.sock.settimeout(None)
            return True
        except socket.timeout:
            print("[SRV] No client")
            return False
    
    def load_content(self, path="data.txt"):
        """Load file"""
        if not os.path.exists(path):
            print(f"[SRV] Not found: {path}")
            return False
        
        with open(path, 'rb') as f:
            self.file_data = f.read()
        self.data_len = len(self.file_data)
        print(f"[SRV] Loaded {path}: {self.data_len} bytes")
        return True
    
    def _decode_ack_packet(self, pkt):
        """Parse ACK"""
        if len(pkt) < 4:
            return None, []
        
        cum_ack = struct.unpack('!I', pkt[:4])[0]
        sack_list = []
        
        if len(pkt) >= 20:
            try:
                for i in range(2):
                    pos = 4 + i * 8
                    if pos + 8 <= len(pkt):
                        l = struct.unpack('!I', pkt[pos:pos+4])[0]
                        r = struct.unpack('!I', pkt[pos+4:pos+8])[0]
                        if l > 0 and r > l and l >= self.flight.left_edge:
                            sack_list.append((l, r))
            except:
                pass
        
        return cum_ack, sack_list
    
    def _dispatch_packets(self):
        """Send packets within window"""
        quota = self.window_ctrl.window_size()
        limit = self.flight.left_edge + quota
        
        while self.flight.right_edge < limit and self.flight.right_edge < self.data_len:
            seq = self.flight.right_edge
            
            if not self.flight.acked_already(seq):
                end = min(seq + PAYLOAD_BYTES, self.data_len)
                chunk = self.file_data[seq:end]
                
                self.flight.log_send(seq, chunk, time.time(), self.rtt_est.rto())
                self.sock.sendto(self.flight.get_packet(seq), self.peer)
                self.pkts_sent += 1
            
            self.flight.right_edge += PAYLOAD_BYTES
    
    def _process_incoming_ack(self, pkt, recv_ts):
        """Handle ACK"""
        cum_ack, sacks = self._decode_ack_packet(pkt)
        if cum_ack is None:
            return
        
        self.acks_recv += 1
        fresh_ack = False
        
        # Cumulative ACK
        if cum_ack > self.flight.left_edge:
            fresh_ack = True
            bytes_acked = cum_ack - self.flight.left_edge
            
            # RTT update
            ts = self.flight.send_timestamp(self.flight.left_edge)
            if ts:
                rtt = recv_ts - ts
                self.rtt_est.add_measurement(rtt)
                self.window_ctrl.on_new_ack(bytes_acked, rtt)
            
            # Mark packets
            seq = self.flight.left_edge
            while seq < cum_ack:
                self.flight.mark_ack(seq)
                seq += PAYLOAD_BYTES
            
            self.flight.advance_window()
            self.flight.reset_dup_tracking()
        
        # SACK processing
        for left, right in sacks:
            seq = left
            while seq < right and seq < self.data_len:
                if seq >= self.flight.left_edge:
                    self.flight.mark_ack(seq)
                seq += PAYLOAD_BYTES
        
        # Duplicate ACK detection
        if cum_ack == self.flight.left_edge and not fresh_ack:
            dup_cnt = self.flight.count_duplicate(cum_ack)
            if dup_cnt == 3:
                if not self.flight.acked_already(self.flight.left_edge):
                    self._resend(self.flight.left_edge, "dup_ack")
                    self.window_ctrl.on_congestion_event("dup_ack")
    
    def _resend(self, seq, reason="timeout"):
        """Retransmit packet"""
        pkt = self.flight.get_packet(seq)
        if pkt:
            self.sock.sendto(pkt, self.peer)
            self.flight.update_send_time(seq, time.time(), self.rtt_est.rto())
            self.retransmits += 1
            if reason == "dup_ack":
                self.fast_retx += 1
    
    def _check_expirations(self):
        """Handle timeouts"""
        expired = self.flight.find_expired()
        if not expired:
            return
        
        # Retransmit first expired
        self._resend(expired[0], "timeout")
        self.window_ctrl.on_congestion_event("timeout")
        self.rtt_est.backoff()
    
    def execute_transfer(self):
        """Main loop"""
        if not self.file_data:
            print("[SRV] No data")
            return
        
        print(f"[SRV] Starting transfer: {self.data_len} bytes")
        start = time.time()
        
        while self.flight.left_edge < self.data_len:
            # Send phase
            self._dispatch_packets()
            
            # Receive phase
            timeout = self.flight.next_timeout(self.rtt_est.rto())
            self.sock.settimeout(timeout)
            
            try:
                ack_pkt, addr = self.sock.recvfrom(PACKET_MAX)
                self._process_incoming_ack(ack_pkt, time.time())
            except socket.timeout:
                self._check_expirations()
        
        # Complete
        elapsed = time.time() - start
        tput = (self.data_len * 8 / elapsed / 1_000_000)
        
        print(f"[SRV] Complete: {elapsed:.2f}s @ {tput:.2f} Mbps")
        print(f"[SRV] Sent={self.pkts_sent} Retx={self.retransmits} FastRetx={self.fast_retx} ACKs={self.acks_recv}")
        
        # EOF
        eof = self.flight._make_packet(self.data_len, TERMINATOR)
        for _ in range(5):
            self.sock.sendto(eof, self.peer)
            time.sleep(0.02)
        
        self.sock.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <IP> <PORT>")
        sys.exit(1)
    
    engine = TransferEngine(sys.argv[1], int(sys.argv[2]))
    if engine.accept_client() and engine.load_content():
        engine.execute_transfer()

if __name__ == "__main__":
    main()
