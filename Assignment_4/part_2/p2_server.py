#!/usr/bin/env python3
import socket
import sys
import struct
import time
import os
from collections import deque

# Configuration
PACKET_MAX = 1200
HEADER_BYTES = 20
PAYLOAD_BYTES = PACKET_MAX - HEADER_BYTES
TERMINATOR = b"EOF"

# RTO constants
BASE_RTO = 0.12
FLOOR_RTO = 0.035
CEILING_RTO = 0.55
SMOOTH_FACTOR = 0.125
VARIANCE_FACTOR = 0.25

class BICEngine:
    """BIC congestion control engine - fairness optimized"""
    
    def __init__(self):
        # Core state
        self.congestion_window = 3 * PAYLOAD_BYTES  # Start conservative
        self.threshold = 240 * PAYLOAD_BYTES
        self.prior_peak = 0
        self.last_size = 0
        self.epoch_marker = 0
        self.increase_target = 1
        
        # BIC configuration for fairness
        self.reduction_factor = 0.72  # Symmetric reduction
        self.convergence_enabled = True
        self.baseline_window = 14 * PAYLOAD_BYTES
        self.jump_limit = 18 * PAYLOAD_BYTES  # Conservative jumps
        self.smooth_divisor = 20
        self.search_base = 4
        
        # State flags
        self.exponential_phase = True
        self.delay_ack_factor = 1
        
        # Fairness tracking
        self.rtt_history = deque(maxlen=20)
        self.loss_events = 0
        
    def get_send_quota(self):
        """Returns bytes allowed to send"""
        return int(self.congestion_window)
    
    def register_rtt(self, rtt_value):
        """Track RTT for fairness"""
        if rtt_value > 0:
            self.rtt_history.append(rtt_value)
    
    def update_on_ack(self, acked_amount, rtt_measurement):
        """Process acknowledgment - main window growth logic"""
        self.register_rtt(rtt_measurement)
        
        if self.exponential_phase:
            # Slow start with fairness consideration
            growth = acked_amount
            if len(self.rtt_history) > 5:
                avg_rtt = sum(self.rtt_history) / len(self.rtt_history)
                min_rtt = min(self.rtt_history)
                if avg_rtt > min_rtt * 1.5:  # Detect congestion early
                    growth = int(growth * 0.7)
            self.congestion_window += growth
            
            if self.congestion_window >= self.threshold:
                self.exponential_phase = False
                self.epoch_marker = 0
        else:
            # BIC increase
            self._apply_binary_increase()
            increment = acked_amount / max(self.increase_target, 1)
            self.congestion_window += increment
            
        # Conservative cap for fairness
        self.congestion_window = min(self.congestion_window, 550 * PAYLOAD_BYTES)
    
    def _apply_binary_increase(self):
        """Binary search window adjustment"""
        current = int(self.congestion_window)
        
        if self.last_size == current:
            return
        
        self.last_size = current
        
        if self.epoch_marker == 0:
            self.epoch_marker = time.time()
        
        # Below threshold - linear
        if self.congestion_window <= self.baseline_window:
            self.increase_target = self.congestion_window / PAYLOAD_BYTES
            return
        
        # Binary search logic with fairness bounds
        if self.congestion_window < self.prior_peak:
            gap = (self.prior_peak - self.congestion_window) / self.search_base
            
            if gap > self.jump_limit:
                self.increase_target = self.congestion_window / self.jump_limit
            elif gap <= PAYLOAD_BYTES:
                self.increase_target = (self.congestion_window * self.smooth_divisor) / (self.search_base * PAYLOAD_BYTES)
            else:
                self.increase_target = self.congestion_window / gap
        else:
            # Probing phase - very conservative for fairness
            probe_range = self.prior_peak + self.search_base * PAYLOAD_BYTES
            if self.congestion_window < probe_range:
                self.increase_target = (self.congestion_window * self.smooth_divisor) / (self.search_base * PAYLOAD_BYTES)
            elif self.congestion_window < self.prior_peak + self.jump_limit * (self.search_base - 1):
                denominator = max(self.congestion_window - self.prior_peak, 1)
                self.increase_target = (self.congestion_window * (self.search_base - 1)) / denominator
            else:
                self.increase_target = self.congestion_window / self.jump_limit
        
        # Initial phase handling
        if self.prior_peak == 0:
            self.increase_target = min(self.increase_target, 18 * PAYLOAD_BYTES)
        
        self.increase_target = max(self.increase_target / self.delay_ack_factor, 1)
    
    def react_to_loss(self, loss_category="timeout"):
        """Handle packet loss events"""
        self.epoch_marker = 0
        self.loss_events += 1
        
        if loss_category == "fast_recovery":
            # Fast retransmit case - symmetric for fairness
            if self.convergence_enabled and self.congestion_window < self.prior_peak:
                self.prior_peak = self.congestion_window * (1 + self.reduction_factor) / 2
            else:
                self.prior_peak = self.congestion_window
            
            # Uniform reduction for fairness
            self.threshold = max(int(self.congestion_window * self.reduction_factor), 2 * PAYLOAD_BYTES)
            self.congestion_window = self.threshold
            self.exponential_phase = False
        else:
            # Timeout - moderate reset
            self.threshold = max(int(self.congestion_window * 0.65), 2 * PAYLOAD_BYTES)
            self.congestion_window = 3 * PAYLOAD_BYTES
            self.exponential_phase = True
            self.prior_peak = 0

class TimeoutCalculator:
    """RTO estimation with smoothing"""
    
    def __init__(self):
        self.mean_rtt = None
        self.deviation = None
        self.current_rto = BASE_RTO
        self.samples = deque(maxlen=10)
    
    def get_timeout(self):
        return self.current_rto
    
    def incorporate_sample(self, sample):
        """Add RTT sample and recalculate"""
        self.samples.append(sample)
        
        if self.mean_rtt is None:
            self.mean_rtt = sample
            self.deviation = sample / 2
        else:
            error = abs(sample - self.mean_rtt)
            self.deviation = (1 - VARIANCE_FACTOR) * self.deviation + VARIANCE_FACTOR * error
            self.mean_rtt = (1 - SMOOTH_FACTOR) * self.mean_rtt + SMOOTH_FACTOR * sample
        
        self.current_rto = self.mean_rtt + 4 * self.deviation
        self.current_rto = max(FLOOR_RTO, min(self.current_rto, CEILING_RTO))
    
    def increase_timeout(self):
        """Backoff on timeout"""
        self.current_rto = min(self.current_rto * 1.18, CEILING_RTO)

class PacketRegistry:
    """Manages packet lifecycle and retransmission tracking"""
    
    def __init__(self):
        self.base_pointer = 0
        self.send_pointer = 0
        self.confirmed = set()
        self.timestamp_map = {}
        self.data_cache = {}
        self.deadline_map = {}
        self.dup_counter = {}
        self.inflight_count = 0
    
    def is_confirmed(self, seq):
        return seq in self.confirmed
    
    def record_transmission(self, seq, payload, send_time, timeout_val):
        """Log sent packet"""
        packet = self._build_header(seq) + payload
        self.timestamp_map[seq] = send_time
        self.data_cache[seq] = packet
        self.deadline_map[seq] = send_time + timeout_val
        self.inflight_count += 1
    
    def update_retransmission(self, seq, send_time, timeout_val):
        """Update packet timing on retransmit"""
        self.timestamp_map[seq] = send_time
        self.deadline_map[seq] = send_time + timeout_val
    
    def fetch_packet(self, seq):
        return self.data_cache.get(seq)
    
    def mark_confirmed(self, seq):
        """Mark packet as acknowledged"""
        if seq not in self.confirmed:
            self.confirmed.add(seq)
            self.inflight_count = max(0, self.inflight_count - 1)
    
    def get_timestamp(self, seq):
        return self.timestamp_map.get(seq)
    
    def shift_base_forward(self):
        """Advance window base"""
        while self.base_pointer in self.confirmed:
            self.confirmed.remove(self.base_pointer)
            self.timestamp_map.pop(self.base_pointer, None)
            self.data_cache.pop(self.base_pointer, None)
            self.deadline_map.pop(self.base_pointer, None)
            self.base_pointer += PAYLOAD_BYTES
    
    def calculate_wait_time(self, default_rto):
        """Compute socket timeout"""
        if not self.deadline_map:
            return 0.015
        now = time.time()
        earliest = min(self.deadline_map.values())
        return max(0.003, earliest - now)
    
    def identify_expired(self):
        """Find timed-out packets"""
        now = time.time()
        expired = []
        for seq, deadline in list(self.deadline_map.items()):
            if seq not in self.confirmed and now >= deadline:
                expired.append(seq)
        return sorted(expired)  # Retransmit in order
    
    def increment_dup_count(self, ack_val):
        """Track duplicate ACKs"""
        self.dup_counter[ack_val] = self.dup_counter.get(ack_val, 0) + 1
        return self.dup_counter[ack_val]
    
    def clear_dup_tracking(self):
        self.dup_counter.clear()
    
    def get_inflight(self):
        """Return number of unacknowledged packets"""
        return self.inflight_count
    
    def _build_header(self, seq):
        return struct.pack('!I', seq) + b'\x00' * 16

class ReliableServer:
    """Reliable UDP server with BIC congestion control"""
    
    def __init__(self, host, port):
        self.bind_addr = (host, port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        self.socket.bind(('0.0.0.0', port))
        
        # Components
        self.bic = BICEngine()
        self.timeout_calc = TimeoutCalculator()
        self.registry = PacketRegistry()
        
        # Transfer state
        self.client = None
        self.file_content = None
        self.content_size = 0
        
        # Metrics
        self.total_transmissions = 0
        self.total_retransmits = 0
        self.fast_retransmits = 0
        self.ack_count = 0
        
        print(f"[SRV] Ready on {host}:{port}")
    
    def wait_for_client(self):
        """Accept client connection"""
        print("[SRV] Awaiting client...")
        self.socket.settimeout(30.0)
        try:
            msg, addr = self.socket.recvfrom(PACKET_MAX)
            self.client = addr
            print(f"[SRV] Client: {addr}")
            self.socket.settimeout(None)
            return True
        except socket.timeout:
            print("[SRV] Timeout waiting for client")
            return False
    
    def load_file_data(self, filepath="data.txt"):
        """Load file to transmit"""
        if not os.path.exists(filepath):
            print(f"[SRV] Missing: {filepath}")
            return False
        
        with open(filepath, 'rb') as f:
            self.file_content = f.read()
        self.content_size = len(self.file_content)
        print(f"[SRV] Loaded {filepath}: {self.content_size} bytes")
        return True
    
    def _parse_acknowledgment(self, packet):
        """Extract ACK and SACK from packet"""
        if len(packet) < 4:
            return None, []
        
        cumulative = struct.unpack('!I', packet[:4])[0]
        sack_blocks = []
        
        if len(packet) >= 20:
            try:
                for i in range(2):
                    offset = 4 + i * 8
                    if offset + 8 <= len(packet):
                        left = struct.unpack('!I', packet[offset:offset+4])[0]
                        right = struct.unpack('!I', packet[offset+4:offset+8])[0]
                        if left > 0 and right > left and left >= self.registry.base_pointer:
                            sack_blocks.append((left, right))
            except:
                pass
        
        return cumulative, sack_blocks
    
    def _send_from_window(self):
        """Transmit packets within congestion window"""
        quota = self.bic.get_send_quota()
        window_limit = self.registry.base_pointer + quota
        
        while self.registry.send_pointer < window_limit and \
              self.registry.send_pointer < self.content_size:
            
            seq = self.registry.send_pointer
            
            if not self.registry.is_confirmed(seq):
                end_pos = min(seq + PAYLOAD_BYTES, self.content_size)
                chunk = self.file_content[seq:end_pos]
                
                self.registry.record_transmission(seq, chunk, time.time(), 
                                                 self.timeout_calc.get_timeout())
                self.socket.sendto(self.registry.fetch_packet(seq), self.client)
                self.total_transmissions += 1
            
            self.registry.send_pointer += PAYLOAD_BYTES
    
    def _handle_received_ack(self, packet, recv_time):
        """Process incoming ACK"""
        cumulative, sack_list = self._parse_acknowledgment(packet)
        if cumulative is None:
            return
        
        self.ack_count += 1
        new_data_acked = False
        
        # Process cumulative ACK
        if cumulative > self.registry.base_pointer:
            new_data_acked = True
            bytes_acknowledged = cumulative - self.registry.base_pointer
            
            # Update RTT and congestion control
            send_time = self.registry.get_timestamp(self.registry.base_pointer)
            if send_time:
                rtt = recv_time - send_time
                self.timeout_calc.incorporate_sample(rtt)
                self.bic.update_on_ack(bytes_acknowledged, rtt)
            
            # Mark packets as confirmed
            seq = self.registry.base_pointer
            while seq < cumulative:
                self.registry.mark_confirmed(seq)
                seq += PAYLOAD_BYTES
            
            self.registry.shift_base_forward()
            self.registry.clear_dup_tracking()
        
        # Process SACK blocks
        for left, right in sack_list:
            seq = left
            while seq < right and seq < self.content_size:
                if seq >= self.registry.base_pointer:
                    self.registry.mark_confirmed(seq)
                seq += PAYLOAD_BYTES
        
        # Check for fast retransmit trigger
        if cumulative == self.registry.base_pointer and not new_data_acked:
            dup_count = self.registry.increment_dup_count(cumulative)
            if dup_count == 3:
                if not self.registry.is_confirmed(self.registry.base_pointer):
                    self._retransmit_packet(self.registry.base_pointer, "fast_recovery")
                    self.bic.react_to_loss("fast_recovery")
    
    def _retransmit_packet(self, seq, reason="timeout"):
        """Retransmit single packet"""
        packet = self.registry.fetch_packet(seq)
        if packet:
            self.socket.sendto(packet, self.client)
            self.registry.update_retransmission(seq, time.time(), 
                                               self.timeout_calc.get_timeout())
            self.total_retransmits += 1
            if reason == "fast_recovery":
                self.fast_retransmits += 1
    
    def _handle_timeout_event(self):
        """Process timeout - retransmit expired packets"""
        expired = self.registry.identify_expired()
        if not expired:
            return
        
        # Retransmit first expired (conservative approach for fairness)
        self._retransmit_packet(expired[0], "timeout")
        self.bic.react_to_loss("timeout")
        self.timeout_calc.increase_timeout()
    
    def run_transfer(self):
        """Main transfer loop"""
        if not self.file_content:
            print("[SRV] No data loaded")
            return
        
        print(f"[SRV] Transferring {self.content_size} bytes...")
        start = time.time()
        
        while self.registry.base_pointer < self.content_size:
            # Send phase
            self._send_from_window()
            
            # Receive phase
            timeout_duration = self.registry.calculate_wait_time(self.timeout_calc.get_timeout())
            self.socket.settimeout(timeout_duration)
            
            try:
                ack_packet, addr = self.socket.recvfrom(PACKET_MAX)
                self._handle_received_ack(ack_packet, time.time())
            except socket.timeout:
                self._handle_timeout_event()
        
        # Transfer complete
        elapsed = time.time() - start
        throughput = (self.content_size * 8 / elapsed / 1_000_000)
        
        print(f"[SRV] Done: {elapsed:.2f}s @ {throughput:.2f} Mbps")
        print(f"[SRV] Sent={self.total_transmissions} Retx={self.total_retransmits} "
              f"FastRetx={self.fast_retransmits} ACKs={self.ack_count}")
        
        # Send EOF
        eof_pkt = self.registry._build_header(self.content_size) + TERMINATOR
        for _ in range(5):
            self.socket.sendto(eof_pkt, self.client)
            time.sleep(0.025)
        
        self.socket.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <IP> <PORT>")
        sys.exit(1)
    
    server = ReliableServer(sys.argv[1], int(sys.argv[2]))
    if server.wait_for_client() and server.load_file_data():
        server.run_transfer()

if __name__ == "__main__":
    main()
