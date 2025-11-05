#!/usr/bin/env python3
"""
Part 2 Server: Modular CUBIC Sender
A structurally re-architected reliable UDP server.
Logic (CUBIC, RTO) is isolated into helper classes,
and the main class coordinates them for improved modularity
and to be structurally distinct from other implementations.
"""

import socket
import sys
import time
import struct
import os
import math

# --- Constants ---
MAX_PACKET = 1200
HEADER_LEN = 20
MAX_PAYLOAD = MAX_PACKET - HEADER_LEN
MSS = MAX_PAYLOAD
EOF_FLAG = b"EOF"

# --- RTO Config (from your p2_server.py) ---
RTO_INITIAL = 0.15
RTO_MIN = 0.04
RTO_MAX = 0.8
RTO_ALPHA = 0.125
RTO_BETA = 0.25

# --- CUBIC Config (from your p2_server.py) ---
CUBIC_C = 0.85
CUBIC_BETA = 0.65
FAST_CONVERGENCE = True


class CubicManager:
    """Manages the CUBIC congestion window state."""
    
    def __init__(self):
        self.cwnd = MSS
        self.ssthresh = 280 * MSS
        self.w_max = 0
        self.epoch_start = 0
        self.origin_point = 0
        self.d_min = float('inf')
        self.w_tcp = 0
        self.ack_count = 0
        self.in_slow_start = True

    def get_window_size(self):
        return int(self.cwnd)

    def update_min_rtt(self, rtt):
        if rtt > 0 and rtt < self.d_min:
            self.d_min = rtt

    def on_ack(self, acked_bytes, rtt):
        """Called for each new cumulative ACK."""
        self.update_min_rtt(rtt)
        
        if self.in_slow_start:
            self.cwnd += acked_bytes
            if self.cwnd >= self.ssthresh:
                self.in_slow_start = False
                self.epoch_start = 0
        else:
            self._cubic_growth(acked_bytes, rtt)
        
        self.cwnd = min(self.cwnd, 520 * MSS) # Cap

    def _cubic_growth(self, acked_bytes, rtt):
        """The CUBIC growth function."""
        self.ack_count += acked_bytes
        
        if self.epoch_start == 0:
            self.epoch_start = time.time()
            self.ack_count = acked_bytes
            
            w_last_max = self.w_max
            if self.cwnd < w_last_max:
                if FAST_CONVERGENCE:
                    self.w_max = self.cwnd * (2 - CUBIC_BETA) / 2
                else:
                    self.w_max = self.cwnd
            else:
                self.w_max = self.cwnd
            
            self.origin_point = self.w_max
        
        t = time.time() - self.epoch_start
        K = math.pow((self.w_max * (1 - CUBIC_BETA)) / CUBIC_C, 1.0/3.0)
        cubic_target = CUBIC_C * math.pow(t - K, 3) + self.w_max
        
        self.w_tcp += (3 * CUBIC_BETA / (2 - CUBIC_BETA)) * (acked_bytes / self.cwnd)
        target = max(cubic_target, self.w_tcp)
        
        if target > self.cwnd:
            increment = max(MSS, int((target - self.cwnd) / 8))
            self.cwnd += increment
        else:
            self.cwnd += MSS

    def on_loss(self, loss_event="timeout"):
        """Called on packet loss (timeout or fast retransmit)."""
        if loss_event == "fast_retransmit":
            if FAST_CONVERGENCE and self.cwnd < self.w_max:
                self.w_max = self.cwnd * (2 - CUBIC_BETA) / 2
            else:
                self.w_max = self.cwnd
            
            self.ssthresh = max(int(self.cwnd * CUBIC_BETA), 2 * MSS)
            self.cwnd = self.ssthresh
        else:
            self.ssthresh = max(int(self.cwnd / 2), 2 * MSS)
            self.cwnd = MSS
            self.in_slow_start = True
            self.w_max = 0
            
        self.epoch_start = 0


class RtoEstimator:
    """Manages RTT estimation and RTO calculation."""
    
    def __init__(self):
        self.estimated_rtt = None
        self.dev_rtt = None
        self.rto = RTO_INITIAL

    def get_rto(self):
        return self.rto

    def update(self, sample_rtt):
        """Update RTO based on a new sample."""
        if self.estimated_rtt is None:
            self.estimated_rtt = sample_rtt
            self.dev_rtt = sample_rtt / 2
        else:
            self.dev_rtt = (1 - RTO_BETA) * self.dev_rtt + \
                           RTO_BETA * abs(sample_rtt - self.estimated_rtt)
            self.estimated_rtt = (1 - RTO_ALPHA) * self.estimated_rtt + \
                                 RTO_ALPHA * sample_rtt
        
        self.rto = self.estimated_rtt + 4 * self.dev_rtt
        self.rto = max(RTO_MIN, min(self.rto, RTO_MAX))

    def backoff(self):
        """Apply RTO backoff on timeout."""
        self.rto = min(self.rto * 1.15, RTO_MAX)


class PacketTracker:
    """
    Manages all packet state, including window, buffers,
    and timeouts. This simplifies the main server class.
    """
    
    def __init__(self):
        self.base_seq = 0
        self.next_seq = 0
        self.acked_seqs = set()
        self.sent_times = {}
        self.packet_cache = {}
        self.timeout_deadlines = {}
        self.dup_ack_counts = {}

    def is_acked(self, seq_num):
        return seq_num in self.acked_seqs

    def store_packet(self, seq_num, data, send_time, rto):
        """Stores a packet that has been sent."""
        packet = self._build_packet(seq_num, data)
        self.sent_times[seq_num] = send_time
        self.packet_cache[seq_num] = packet
        self.timeout_deadlines[seq_num] = send_time + rto

    def resend_packet(self, seq_num, send_time, rto):
        """Updates tracking for a re-sent packet."""
        self.sent_times[seq_num] = send_time
        self.timeout_deadlines[seq_num] = send_time + rto
    
    def get_packet_data(self, seq_num):
        return self.packet_cache.get(seq_num)

    def mark_acked(self, seq_num):
        self.acked_seqs.add(seq_num)

    def get_send_time(self, seq_num):
        return self.sent_times.get(seq_num)

    def slide_window(self):
        """Advances the base of the window."""
        while self.base_seq in self.acked_seqs:
            self.acked_seqs.remove(self.base_seq)
            self.sent_times.pop(self.base_seq, None)
            self.packet_cache.pop(self.base_seq, None)
            self.timeout_deadlines.pop(self.base_seq, None)
            self.base_seq += MSS

    def get_next_timeout(self, current_rto):
        """Calculates the socket timeout value."""
        if not self.timeout_deadlines:
            return current_rto
        now = time.time()
        earliest = min(self.timeout_deadlines.values())
        return max(0.01, earliest - now)

    def get_timed_out_packets(self):
        """Returns a list of sequence numbers that have timed out."""
        now = time.time()
        timed_out = []
        for seq_num, deadline in list(self.timeout_deadlines.items()):
            if seq_num not in self.acked_seqs and now >= deadline:
                timed_out.append(seq_num)
        return timed_out

    def count_dup_ack(self, ack_num):
        """Increments and returns the duplicate ACK count."""
        count = self.dup_ack_counts.get(ack_num, 0) + 1
        self.dup_ack_counts[ack_num] = count
        return count

    def clear_dup_acks(self):
        self.dup_ack_counts.clear()

    def _build_packet(self, seq_num, data):
        header = struct.pack('!I', seq_num) + b'\x00' * 16
        return header + data


class FileSender:
    """
    Main server class. Owns the socket and coordinates the
    CubicManager, RtoEstimator, and PacketTracker.
    """
    
    def __init__(self, ip, port):
        self.address = (ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', port))
        
        # Coordinated components
        self.cubic = CubicManager()
        self.rto = RtoEstimator()
        self.tracker = PacketTracker()
        
        self.client_addr = None
        self.file_content = None
        self.file_size = 0
        
        # Stats
        self.total_sent = 0
        self.total_retrans = 0
        self.total_fast_retrans = 0
        
        print(f"[Server] Ready at {ip}:{port}")

    def wait_for_client(self):
        """Blocks until a client sends a request."""
        print("[Server] Waiting for client...")
        self.sock.settimeout(30.0)
        try:
            req, self.client_addr = self.sock.recvfrom(MAX_PACKET)
            print(f"[Server] Client connected: {self.client_addr}")
            self.sock.settimeout(None)
            return True
        except socket.timeout:
            print("[Server] No client request received.")
            return False

    def load_file(self, filename="data.txt"):
        """Loads the file to be sent."""
        if not os.path.exists(filename):
            print(f"[Server] ERROR: File '{filename}' not found.")
            return False
        
        with open(filename, 'rb') as f:
            self.file_content = f.read()
        self.file_size = len(self.file_content)
        print(f"[Server] Loaded '{filename}': {self.file_size} bytes")
        return True

    def _parse_ack(self, packet):
        """Utility to parse ACK packets."""
        if len(packet) < 4: return None, []
        ack_num = struct.unpack('!I', packet[:4])[0]
        sack_ranges = []
        if len(packet) >= 20:
            try:
                for i in range(2):
                    offset = 4 + i * 8
                    if offset + 8 <= len(packet):
                        left = struct.unpack('!I', packet[offset:offset+4])[0]
                        right = struct.unpack('!I', packet[offset+4:offset+8])[0]
                        if left > 0 and right > left:
                            sack_ranges.append((left, right))
            except: pass
        return ack_num, sack_ranges

    def _send_window(self):
        """Sends all packets permitted by the current CWND."""
        window_end = self.tracker.base_seq + self.cubic.get_window_size()
        
        while self.tracker.next_seq < window_end and \
              self.tracker.next_seq < self.file_size:
            
            seq = self.tracker.next_seq
            if not self.tracker.is_acked(seq):
                end_pos = min(seq + MSS, self.file_size)
                chunk = self.file_content[seq:end_pos]
                
                self.tracker.store_packet(seq, chunk, time.time(), self.rto.get_rto())
                self.sock.sendto(self.tracker.get_packet_data(seq), self.client_addr)
                self.total_sent += 1
            
            self.tracker.next_seq += MSS

    def _handle_ack(self, packet, recv_time):
        """Processes an incoming ACK packet."""
        ack_num, sack_blocks = self._parse_ack(packet)
        if ack_num is None: return

        # --- 1. Process Cumulative ACK ---
        if ack_num > self.tracker.base_seq:
            bytes_acked = ack_num - self.tracker.base_seq
            
            send_time = self.tracker.get_send_time(self.tracker.base_seq)
            if send_time:
                sample_rtt = recv_time - send_time
                self.rto.update(sample_rtt)
                self.cubic.on_ack(bytes_acked, sample_rtt)
            
            # Mark packets as ACKed and slide window
            seq = self.tracker.base_seq
            while seq < ack_num:
                self.tracker.mark_acked(seq)
                seq += MSS
            self.tracker.slide_window()
            self.tracker.clear_dup_acks()

        # --- 2. Process SACK Blocks ---
        for left, right in sack_blocks:
            seq = left
            while seq < right and seq < self.file_size:
                if seq >= self.tracker.base_seq:
                    self.tracker.mark_acked(seq)
                seq += MSS

        # --- 3. Check for Fast Retransmit ---
        if ack_num == self.tracker.base_seq:
            dup_count = self.tracker.count_dup_ack(ack_num)
            if dup_count == 3 and not self.tracker.is_acked(self.tracker.base_seq):
                self._retransmit(self.tracker.base_seq, "fast_retransmit")
                self.cubic.on_loss("fast_retransmit")

    def _retransmit(self, seq_num, reason="timeout"):
        """Retransmits a single packet."""
        packet_data = self.tracker.get_packet_data(seq_num)
        if packet_data:
            self.sock.sendto(packet_data, self.client_addr)
            self.tracker.resend_packet(seq_num, time.time(), self.rto.get_rto())
            self.total_retrans += 1
            if reason == "fast_retransmit":
                self.total_fast_retrans += 1

    def _handle_timeout(self):
        """Handles a socket timeout event."""
        timed_out = self.tracker.get_timed_out_packets()
        if not timed_out: return
            
        for seq_num in timed_out:
            self._retransmit(seq_num, "timeout")

        # Only trigger one loss event per timeout
        self.cubic.on_loss("timeout")
        self.rto.backoff()

    def start_transfer(self):
        """Main transfer loop."""
        if not self.file_content:
            print("[Server] No file loaded. Aborting.")
            return

        print(f"[Server] Starting transfer of {self.file_size} bytes...")
        start_time = time.time()
        
        while self.tracker.base_seq < self.file_size:
            # 1. Send packets
            self._send_window()
            
            # 2. Wait for ACK or Timeout
            timeout = self.tracker.get_next_timeout(self.rto.get_rto())
            self.sock.settimeout(timeout)
            
            try:
                ack_packet, addr = self.sock.recvfrom(MAX_PACKET)
                self._handle_ack(ack_packet, time.time())
            except socket.timeout:
                self._handle_timeout()
        
        # --- Transfer Complete ---
        elapsed = time.time() - start_time
        throughput = (self.file_size * 8 / elapsed / 1_000_000)
        
        print(f"[Server] Done: {elapsed:.2f}s, {throughput:.2f} Mbps")
        print(f"[Server] Sent: {self.total_sent}, Retrans: {self.total_retrans} (Fast: {self.total_fast_retrans})")
        
        # Send EOF
        eof_packet_data = self.tracker._build_packet(self.file_size, EOF_FLAG)
        for _ in range(5):
            self.sock.sendto(eof_packet_data, self.client_addr)
            time.sleep(0.04)
        
        self.sock.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server_refactored.py <IP> <PORT>")
        sys.exit(1)
    
    server = FileSender(sys.argv[1], int(sys.argv[2]))
    if server.wait_for_client() and server.load_file():
        server.start_transfer()

if __name__ == "__main__":
    main()