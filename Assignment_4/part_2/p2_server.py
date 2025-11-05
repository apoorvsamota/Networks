#!/usr/bin/env python3
import socket
import sys
import struct
import time
import os

# Packet structure constants
PKT_SIZE = 1200
HDR_SIZE = 20
DATA_SIZE = PKT_SIZE - HDR_SIZE
END_MARKER = b"EOF"

# Timing parameters
INIT_RTO = 0.1
MIN_RTO = 0.03
MAX_RTO = 0.6
ALPHA_RTT = 0.125
BETA_RTT = 0.25

class BICController:
    """Binary Increase Congestion Control - tuned for fairness"""
    
    def __init__(self):
        # Window parameters
        self.window = 2 * DATA_SIZE  # Start with 2 MSS for faster convergence
        self.slow_start_thresh = 256 * DATA_SIZE  # Lower threshold for fairer competition
        self.max_window_before_loss = 0
        self.previous_window = 0
        self.epoch_time = 0
        self.target_window = 1
        
        # BIC parameters tuned for fairness
        self.beta_multiplicative = 0.75  # Less aggressive reduction (was 0.8)
        self.enable_fast_convergence = True
        self.low_utilization_threshold = 14 * DATA_SIZE
        self.binary_search_max = 16 * DATA_SIZE  # Smaller jumps for better fairness (was 32)
        self.smoothing_factor = 20
        self.binary_search_coeff = 4
        
        # ACK tracking
        self.ack_ratio = 1
        self.slow_start_mode = True
        
        # Fairness enhancements
        self.min_rtt = float('inf')
        self.rtt_samples = []
        
    def current_window(self):
        return int(self.window)
        
    def process_ack(self, bytes_newly_acked, measured_rtt):
        """Update window on receiving ACK"""
        # Track RTT for fairness
        if measured_rtt > 0:
            self.min_rtt = min(self.min_rtt, measured_rtt)
            self.rtt_samples.append(measured_rtt)
            if len(self.rtt_samples) > 10:
                self.rtt_samples.pop(0)
        
        if self.slow_start_mode:
            # Standard slow start
            self.window += bytes_newly_acked
            if self.window >= self.slow_start_thresh:
                self.slow_start_mode = False
                self.epoch_time = 0
        else:
            self._binary_increase()
            if self.target_window > 0:
                delta = bytes_newly_acked / self.target_window
            else:
                delta = bytes_newly_acked
            self.window += delta
            
        # Cap maximum window
        self.window = min(self.window, 600 * DATA_SIZE)  # Moderate cap for fairness
        
    def _binary_increase(self):
        """BIC binary search increase - fairness focused"""
        curr_win = int(self.window)
        
        if self.previous_window == curr_win:
            return
            
        self.previous_window = curr_win
        
        if self.epoch_time == 0:
            self.epoch_time = time.time()
            
        # Low window - linear increase
        if self.window <= self.low_utilization_threshold:
            self.target_window = self.window / DATA_SIZE
            return
            
        # Binary search phase - more conservative
        if self.window < self.max_window_before_loss:
            distance = (self.max_window_before_loss - self.window) / self.binary_search_coeff
            
            if distance > self.binary_search_max:
                self.target_window = self.window / self.binary_search_max
            elif distance <= DATA_SIZE:
                self.target_window = (self.window * self.smoothing_factor) / (self.binary_search_coeff * DATA_SIZE)
            else:
                self.target_window = self.window / distance
        else:
            # Additive increase phase - slower for fairness
            if self.window < self.max_window_before_loss + self.binary_search_coeff * DATA_SIZE:
                self.target_window = (self.window * self.smoothing_factor) / (self.binary_search_coeff * DATA_SIZE)
            elif self.window < self.max_window_before_loss + self.binary_search_max * (self.binary_search_coeff - 1):
                self.target_window = (self.window * (self.binary_search_coeff - 1)) / max(self.window - self.max_window_before_loss, 1)
            else:
                self.target_window = self.window / self.binary_search_max
                
        # Handle initial phase
        if self.max_window_before_loss == 0:
            if self.target_window > 20 * DATA_SIZE:
                self.target_window = 20 * DATA_SIZE
                
        self.target_window = max(self.target_window / self.ack_ratio, 1)
        
    def handle_loss_event(self, event_type="timeout"):
        """React to packet loss - fairness aware"""
        self.epoch_time = 0
        
        if event_type == "duplicate_ack":
            # Fast retransmit - symmetric reduction for fairness
            if self.window < self.max_window_before_loss and self.enable_fast_convergence:
                self.max_window_before_loss = self.window * (1 + self.beta_multiplicative) / 2
            else:
                self.max_window_before_loss = self.window
                
            # Uniform reduction regardless of window size for fairness
            self.slow_start_thresh = max(int(self.window * self.beta_multiplicative), 2 * DATA_SIZE)
            self.window = self.slow_start_thresh
            self.slow_start_mode = False
        else:
            # Timeout - moderate response
            self.slow_start_thresh = max(int(self.window * 0.6), 2 * DATA_SIZE)
            self.window = 2 * DATA_SIZE
            self.slow_start_mode = True
            self.max_window_before_loss = 0

class RTTManager:
    """Manages round-trip time estimation"""
    
    def __init__(self):
        self.smoothed_rtt = None
        self.rtt_variance = None
        self.timeout = INIT_RTO

    def current_timeout(self):
        return self.timeout

    def add_sample(self, rtt_sample):
        if self.smoothed_rtt is None:
            self.smoothed_rtt = rtt_sample
            self.rtt_variance = rtt_sample / 2
        else:
            self.rtt_variance = (1 - BETA_RTT) * self.rtt_variance + \
                           BETA_RTT * abs(rtt_sample - self.smoothed_rtt)
            self.smoothed_rtt = (1 - ALPHA_RTT) * self.smoothed_rtt + \
                                 ALPHA_RTT * rtt_sample
        
        self.timeout = self.smoothed_rtt + 4 * self.rtt_variance
        self.timeout = max(MIN_RTO, min(self.timeout, MAX_RTO))

    def exponential_backoff(self):
        self.timeout = min(self.timeout * 1.15, MAX_RTO)

class TransmissionState:
    """Tracks packet transmission state"""
    
    def __init__(self):
        self.window_base = 0
        self.next_to_send = 0
        self.ack_record = set()
        self.transmission_log = {}
        self.payload_cache = {}
        self.expiry_times = {}
        self.duplicate_tracker = {}

    def acknowledged(self, seq):
        return seq in self.ack_record

    def cache_packet(self, seq, payload, tx_time, timeout):
        pkt = self._construct_packet(seq, payload)
        self.transmission_log[seq] = tx_time
        self.payload_cache[seq] = pkt
        self.expiry_times[seq] = tx_time + timeout

    def update_retransmit(self, seq, tx_time, timeout):
        self.transmission_log[seq] = tx_time
        self.expiry_times[seq] = tx_time + timeout
    
    def retrieve_packet(self, seq):
        return self.payload_cache.get(seq)

    def record_ack(self, seq):
        self.ack_record.add(seq)

    def fetch_tx_time(self, seq):
        return self.transmission_log.get(seq)

    def advance_base(self):
        while self.window_base in self.ack_record:
            self.ack_record.remove(self.window_base)
            self.transmission_log.pop(self.window_base, None)
            self.payload_cache.pop(self.window_base, None)
            self.expiry_times.pop(self.window_base, None)
            self.window_base += DATA_SIZE

    def compute_timeout(self, fallback_rto):
        if not self.expiry_times:
            return 0.01
        now = time.time()
        next_expiry = min(self.expiry_times.values())
        return max(0.002, next_expiry - now)

    def find_expired(self):
        now = time.time()
        expired = []
        for seq, expiry in list(self.expiry_times.items()):
            if seq not in self.ack_record and now >= expiry:
                expired.append(seq)
        return expired

    def track_duplicate(self, ack_val):
        count = self.duplicate_tracker.get(ack_val, 0) + 1
        self.duplicate_tracker[ack_val] = count
        return count

    def reset_duplicates(self):
        self.duplicate_tracker.clear()

    def _construct_packet(self, seq, payload):
        hdr = struct.pack('!I', seq) + b'\x00' * 16
        return hdr + payload

class UDPServer:
    """Main server orchestrator"""
    
    def __init__(self, bind_ip, bind_port):
        self.addr = (bind_ip, bind_port)
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        self.udp_sock.bind(('0.0.0.0', bind_port))
        
        self.congestion_ctrl = BICController()
        self.rtt_mgr = RTTManager()
        self.tx_state = TransmissionState()
        
        self.peer_addr = None
        self.file_bytes = None
        self.file_length = 0
        
        self.packets_transmitted = 0
        self.retransmissions = 0
        self.fast_retrans_count = 0
        
        print(f"[SERVER] Initialized at {bind_ip}:{bind_port}")

    def await_connection(self):
        print("[SERVER] Listening for client...")
        self.udp_sock.settimeout(30.0)
        try:
            req, self.peer_addr = self.udp_sock.recvfrom(PKT_SIZE)
            print(f"[SERVER] Connected to {self.peer_addr}")
            self.udp_sock.settimeout(None)
            return True
        except socket.timeout:
            print("[SERVER] Connection timeout")
            return False

    def load_data(self, filename="data.txt"):
        if not os.path.exists(filename):
            print(f"[SERVER] File not found: {filename}")
            return False
        
        with open(filename, 'rb') as f:
            self.file_bytes = f.read()
        self.file_length = len(self.file_bytes)
        print(f"[SERVER] Loaded {filename}: {self.file_length} bytes")
        return True

    def _decode_ack(self, pkt):
        if len(pkt) < 4: 
            return None, []
        cumulative_ack = struct.unpack('!I', pkt[:4])[0]
        selective_acks = []
        if len(pkt) >= 20:
            try:
                for idx in range(2):
                    pos = 4 + idx * 8
                    if pos + 8 <= len(pkt):
                        left_edge = struct.unpack('!I', pkt[pos:pos+4])[0]
                        right_edge = struct.unpack('!I', pkt[pos+4:pos+8])[0]
                        if left_edge > 0 and right_edge > left_edge and left_edge >= self.tx_state.window_base:
                            selective_acks.append((left_edge, right_edge))
            except: 
                pass
        return cumulative_ack, selective_acks

    def _transmit_window(self):
        allowed_end = self.tx_state.window_base + self.congestion_ctrl.current_window()
        
        while self.tx_state.next_to_send < allowed_end and \
              self.tx_state.next_to_send < self.file_length:
            
            seq = self.tx_state.next_to_send
            if not self.tx_state.acknowledged(seq):
                end_idx = min(seq + DATA_SIZE, self.file_length)
                data_chunk = self.file_bytes[seq:end_idx]
                
                self.tx_state.cache_packet(seq, data_chunk, time.time(), self.rtt_mgr.current_timeout())
                self.udp_sock.sendto(self.tx_state.retrieve_packet(seq), self.peer_addr)
                self.packets_transmitted += 1
            
            self.tx_state.next_to_send += DATA_SIZE

    def _process_ack(self, pkt, rx_time):
        cumul_ack, sack_list = self._decode_ack(pkt)
        if cumul_ack is None: 
            return

        is_new_ack = False
        if cumul_ack > self.tx_state.window_base:
            is_new_ack = True
            bytes_acked = cumul_ack - self.tx_state.window_base
            
            tx_time = self.tx_state.fetch_tx_time(self.tx_state.window_base)
            if tx_time:
                rtt_sample = rx_time - tx_time
                self.rtt_mgr.add_sample(rtt_sample)
                self.congestion_ctrl.process_ack(bytes_acked, rtt_sample)
            
            seq = self.tx_state.window_base
            while seq < cumul_ack:
                self.tx_state.record_ack(seq)
                seq += DATA_SIZE
            self.tx_state.advance_base()
            self.tx_state.reset_duplicates()

        for left, right in sack_list:
            seq = left
            while seq < right and seq < self.file_length:
                if seq >= self.tx_state.window_base and seq not in self.tx_state.ack_record:
                    self.tx_state.record_ack(seq)
                seq += DATA_SIZE

        if cumul_ack == self.tx_state.window_base and not is_new_ack:
            dup_count = self.tx_state.track_duplicate(cumul_ack)
            if dup_count == 3 and not self.tx_state.acknowledged(self.tx_state.window_base):
                self._retransmit_one(self.tx_state.window_base, "duplicate_ack")
                self.congestion_ctrl.handle_loss_event("duplicate_ack")

    def _retransmit_one(self, seq, reason="timeout"):
        pkt = self.tx_state.retrieve_packet(seq)
        if pkt:
            self.udp_sock.sendto(pkt, self.peer_addr)
            self.tx_state.update_retransmit(seq, time.time(), self.rtt_mgr.current_timeout())
            self.retransmissions += 1
            if reason == "duplicate_ack":
                self.fast_retrans_count += 1

    def _check_timeouts(self):
        expired_list = self.tx_state.find_expired()
        if not expired_list: 
            return
            
        self._retransmit_one(expired_list[0], "timeout")
        self.congestion_ctrl.handle_loss_event("timeout")
        self.rtt_mgr.exponential_backoff()

    def begin_transfer(self):
        if not self.file_bytes:
            print("[SERVER] No data loaded")
            return

        print(f"[SERVER] Transfer starting: {self.file_length} bytes")
        start_ts = time.time()
        
        while self.tx_state.window_base < self.file_length:
            self._transmit_window()
            
            wait_time = self.tx_state.compute_timeout(self.rtt_mgr.current_timeout())
            self.udp_sock.settimeout(wait_time)
            
            try:
                ack_pkt, addr = self.udp_sock.recvfrom(PKT_SIZE)
                self._process_ack(ack_pkt, time.time())
            except socket.timeout:
                self._check_timeouts()
        
        elapsed = time.time() - start_ts
        throughput = (self.file_length * 8 / elapsed / 1_000_000)
        
        print(f"[SERVER] Complete: {elapsed:.2f}s, {throughput:.2f} Mbps")
        print(f"[SERVER] Tx: {self.packets_transmitted}, Retx: {self.retransmissions} (Fast: {self.fast_retrans_count})")
        
        # Send EOF markers
        eof_pkt = self.tx_state._construct_packet(self.file_length, END_MARKER)
        for _ in range(5):
            self.udp_sock.sendto(eof_pkt, self.peer_addr)
            time.sleep(0.02)
        
        self.udp_sock.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server.py <IP> <PORT>")
        sys.exit(1)
    
    srv = UDPServer(sys.argv[1], int(sys.argv[2]))
    if srv.await_connection() and srv.load_data():
        srv.begin_transfer()

if __name__ == "__main__":
    main()
