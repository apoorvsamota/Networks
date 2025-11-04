#!/usr/bin/env python3
"""
Part 2 Server: High-Performance CUBIC Sender
A refactored implementation of a reliable UDP server using CUBIC
congestion control, optimized for speed and efficient state management.
"""

import socket
import sys
import time
import struct
import os
import math

# --- Protocol Definitions ---
PACKET_LIMIT_BYTES = 1200
HEADER_OVERHEAD = 20
PAYLOAD_CAPACITY = PACKET_LIMIT_BYTES - HEADER_OVERHEAD
MSS = PAYLOAD_CAPACITY  # Maximum Segment Size
FIN_MARKER = b"EOF"

# --- RTO Tunables ---
RTO_START = 0.15
RTO_MINIMUM = 0.04
RTO_MAXIMUM = 0.8
EWMA_ALPHA = 0.125
EWMA_BETA = 0.25

# --- CUBIC Tunables ---
CUBIC_SCALE_FACTOR = 0.85
CUBIC_REDUCTION_FACTOR = 0.65
USE_FAST_CONVERGENCE = True


class CubicFlowManager:
    """Manages congestion window (cwnd) using CUBIC algorithm."""
    
    def __init__(self):
        # Window sizes in bytes
        self.congestion_window = MSS  # cwnd
        self.slow_start_thresh = 280 * MSS  # ssthresh
        
        # CUBIC state
        self.max_window_prev_loss = 0 # W_max
        self.epoch_start_timestamp = 0
        self.origin_point_window = 0
        self.min_round_trip_time = float('inf')
        self.tcp_friendly_target = 0
        self.ack_byte_count = 0
        
        self.is_in_slow_start = True
        
    def track_min_rtt(self, rtt_sample):
        """Monitors the minimum observed RTT."""
        if rtt_sample > 0 and rtt_sample < self.min_round_trip_time:
            self.min_round_trip_time = rtt_sample
    
    def on_ack_received(self, bytes_acknowledged, rtt_sample):
        """Updates the congestion window based on a received ACK."""
        self.track_min_rtt(rtt_sample)
        
        if self.is_in_slow_start:
            # Exponential growth
            self.congestion_window += bytes_acknowledged
            
            # Transition to congestion avoidance
            if self.congestion_window >= self.slow_start_thresh:
                self.is_in_slow_start = False
                self.epoch_start_timestamp = 0
        else:
            # CUBIC growth
            self._update_cubic_window(bytes_acknowledged, rtt_sample)
        
        # Enforce a high upper bound
        max_allowed_window = 520 * MSS
        self.congestion_window = min(self.congestion_window, max_allowed_window)
        
        return int(self.congestion_window)
    
    def _update_cubic_window(self, bytes_acknowledged, rtt_sample):
        """Performs CUBIC window calculation for congestion avoidance."""
        self.ack_byte_count += bytes_acknowledged
        
        if self.epoch_start_timestamp == 0:
            # Begin new CUBIC epoch
            self.epoch_start_timestamp = time.time()
            self.ack_byte_count = bytes_acknowledged
            
            # Set W_max and origin point
            if self.max_window_prev_loss < self.congestion_window:
                if USE_FAST_CONVERGENCE:
                    self.max_window_prev_loss = self.congestion_window * (2 - CUBIC_REDUCTION_FACTOR) / 2
                else:
                    self.max_window_prev_loss = self.congestion_window
            else:
                self.max_window_prev_loss = self.congestion_window
            
            self.origin_point_window = self.max_window_prev_loss
        
        # Time elapsed since epoch start
        time_delta = time.time() - self.epoch_start_timestamp
        
        # CUBIC K (time to reach W_max)
        time_to_max = math.pow((self.max_window_prev_loss * (1 - CUBIC_REDUCTION_FACTOR)) / CUBIC_SCALE_FACTOR, 1.0/3.0)
        
        # CUBIC target window
        cubic_target_window = CUBIC_SCALE_FACTOR * math.pow(time_delta - time_to_max, 3) + self.max_window_prev_loss
        
        # TCP-friendly target (for fairness)
        self.tcp_friendly_target += (3 * CUBIC_REDUCTION_FACTOR / (2 - CUBIC_REDUCTION_FACTOR)) * (bytes_acknowledged / self.congestion_window)
        
        # Use the more aggressive of the two targets
        current_target = max(cubic_target_window, self.tcp_friendly_target)
        
        # Grow window towards target
        if current_target > self.congestion_window:
            increment = max(MSS, int((current_target - self.congestion_window) / 8))
            self.congestion_window += increment
        else:
            self.congestion_window += MSS
    
    def on_loss_detected(self, event_type="timeout"):
        """Responds to a packet loss event."""
        if event_type == "fast_retransmit":
            # Multiplicative decrease
            if USE_FAST_CONVERGENCE and self.congestion_window < self.max_window_prev_loss:
                self.max_window_prev_loss = self.congestion_window * (2 - CUBIC_REDUCTION_FACTOR) / 2
            else:
                self.max_window_prev_loss = self.congestion_window
            
            self.slow_start_thresh = max(int(self.congestion_window * CUBIC_REDUCTION_FACTOR), 2 * MSS)
            self.congestion_window = self.slow_start_thresh
        else:
            # Timeout: reset to slow start
            self.slow_start_thresh = max(int(self.congestion_window / 2), 2 * MSS)
            self.congestion_window = MSS
            self.is_in_slow_start = True
            self.max_window_prev_loss = 0
            
        self.epoch_start_timestamp = 0
    
    def get_current_window(self):
        """Returns the current congestion window size in bytes."""
        return int(self.congestion_window)


class FastDataTransmitter:
    """Server that transmits file data reliably over UDP."""
    
    def __init__(self, host_ip, host_port):
        self.host_ip = host_ip
        self.host_port = host_port
        self.listener_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener_socket.bind(('0.0.0.0', self.host_port))
        
        self.flow_manager = CubicFlowManager()
        
        # RTT state
        self.current_rtt_avg = None
        self.current_rtt_dev = None
        self.current_rto = RTO_START
        
        # Window state
        self.window_base = 0  # Oldest un-acked packet seq
        self.next_packet_seq = 0 # Next new packet seq to send
        
        # Packet tracking
        self.confirmed_packets = set() # For SACK
        self.packet_send_times = {}
        self.packet_payload_cache = {}
        self.packet_timeout_map = {}
        self.dup_ack_tracker = {}
        
        # Statistics
        self.total_packets_sent = 0
        self.total_retransmits = 0
        self.total_acks_rcvd = 0
        self.total_fast_retransmits = 0
        
        print(f"[Server] CUBIC Transmitter listening on {self.host_ip}:{self.host_port}")
    
    def _create_packet(self, seq_id, payload):
        """Builds a data packet with a header."""
        header = struct.pack('!I', seq_id) + b'\x00' * 16 # 4-byte seq, 16-byte padding
        return header + payload
    
    def _parse_ack(self, ack_data):
        """Extracts cumulative ACK and SACK blocks from an ACK packet."""
        if len(ack_data) < 4:
            return None, []
        
        cum_ack = struct.unpack('!I', ack_data[:4])[0]
        
        sack_blocks = []
        if len(ack_data) >= 20:
            try:
                for i in range(2): # Max 2 SACK blocks
                    offset = 4 + i * 8
                    if offset + 8 <= len(ack_data):
                        left = struct.unpack('!I', ack_data[offset:offset+4])[0]
                        right = struct.unpack('!I', ack_data[offset+4:offset+8])[0]
                        if left > 0 and right > left:
                            sack_blocks.append((left, right))
            except:
                pass # Ignore malformed SACK
        
        return cum_ack, sack_blocks
    
    def _calculate_new_rto(self, rtt_sample):
        """Updates RTO based on a new RTT sample."""
        if self.current_rtt_avg is None:
            self.current_rtt_avg = rtt_sample
            self.current_rtt_dev = rtt_sample / 2
        else:
            self.current_rtt_dev = (1 - EWMA_BETA) * self.current_rtt_dev + \
                                   EWMA_BETA * abs(rtt_sample - self.current_rtt_avg)
            self.current_rtt_avg = (1 - EWMA_ALPHA) * self.current_rtt_avg + \
                                   EWMA_ALPHA * rtt_sample
        
        self.current_rto = self.current_rtt_avg + 4 * self.current_rtt_dev
        self.current_rto = max(RTO_MINIMUM, min(self.current_rto, RTO_MAXIMUM))
    
    def _send_data_packet(self, seq_id, payload, client_address):
        """Sends a new data packet and tracks its state."""
        packet = self._create_packet(seq_id, payload)
        self.listener_socket.sendto(packet, client_address)
        
        now = time.time()
        self.packet_send_times[seq_id] = now
        self.packet_payload_cache[seq_id] = packet
        self.packet_timeout_map[seq_id] = now + self.current_rto
        self.total_packets_sent += 1
    
    def _resend_data_packet(self, seq_id, client_address, trigger_reason="timeout"):
        """Retransmits an existing packet from the cache."""
        if seq_id in self.packet_payload_cache and seq_id not in self.confirmed_packets:
            self.listener_socket.sendto(self.packet_payload_cache[seq_id], client_address)
            
            now = time.time()
            self.packet_send_times[seq_id] = now # Update send time for RTT
            self.packet_timeout_map[seq_id] = now + self.current_rto
            self.total_retransmits += 1
            
            if trigger_reason == "fast_retransmit":
                self.total_fast_retransmits += 1
    
    def _get_next_timeout_interval(self):
        """Calculates the time to wait until the next packet timeout."""
        if not self.packet_timeout_map:
            return self.current_rto
        
        now = time.time()
        earliest_deadline = min(self.packet_timeout_map.values())
        return max(0.01, earliest_deadline - now) # 10ms minimum wait
    
    def _check_for_timeouts(self, client_address):
        """Scans for and retransmits any timed-out packets."""
        now = time.time()
        timed_out_packets = []
        
        # Find all timed-out packets
        for seq_id, deadline in list(self.packet_timeout_map.items()):
            if seq_id not in self.confirmed_packets and now >= deadline:
                timed_out_packets.append(seq_id)
        
        if timed_out_packets:
            # Retransmit all timed-out packets (more aggressive)
            for seq_id in timed_out_packets:
                self._resend_data_packet(seq_id, client_address, "timeout")
            
            # Trigger congestion event
            self.flow_manager.on_loss_detected("timeout")
            # Apply RTO backoff
            self.current_rto = min(self.current_rto * 1.15, RTO_MAXIMUM)
    
    def _advance_window(self):
        """Cleans up internal state as the window base moves forward."""
        while self.window_base in self.confirmed_packets:
            self.confirmed_packets.remove(self.window_base)
            
            # Clear old packet data
            self.packet_send_times.pop(self.window_base, None)
            self.packet_payload_cache.pop(self.window_base, None)
            self.packet_timeout_map.pop(self.window_base, None)
            
            self.window_base += MSS
    
    def transmit_file_data(self, data_to_send, client_address):
        """Main transfer loop to send all file data."""
        file_byte_size = len(data_to_send)
        print(f"\n[Server] Beginning transfer of {file_byte_size} bytes.")
        num_packets = (file_byte_size + MSS - 1) // MSS
        print(f"[Server] Total packets to send: {num_packets}")
        
        transfer_start_time = time.time()
        
        while self.window_base < file_byte_size:
            # --- SEND PHASE ---
            current_cwnd = self.flow_manager.get_current_window()
            
            # Send all packets allowed by the current window
            while self.next_packet_seq < self.window_base + current_cwnd and \
                  self.next_packet_seq < file_byte_size:
                
                if self.next_packet_seq not in self.confirmed_packets:
                    chunk_end = min(self.next_packet_seq + MSS, file_byte_size)
                    data_chunk = data_to_send[self.next_packet_seq:chunk_end]
                    self._send_data_packet(self.next_packet_seq, data_chunk, client_address)
                
                self.next_packet_seq += MSS
            
            # --- RECEIVE PHASE ---
            wait_time = self._get_next_timeout_interval()
            self.listener_socket.settimeout(wait_time)
            
            try:
                ack_data, addr = self.listener_socket.recvfrom(PACKET_LIMIT_BYTES)
                ack_rcvd_time = time.time()
                
                cum_ack, sack_blocks = self._parse_ack(ack_data)
                if cum_ack is None:
                    continue # Bad ACK
                
                self.total_acks_rcvd += 1
                
                # --- Process Cumulative ACK ---
                if cum_ack > self.window_base:
                    bytes_acked = cum_ack - self.window_base
                    rtt_sample = 0
                    
                    if self.window_base in self.packet_send_times:
                        rtt_sample = ack_rcvd_time - self.packet_send_times[self.window_base]
                        self._calculate_new_rto(rtt_sample)
                    
                    self.flow_manager.on_ack_received(bytes_acked, rtt_sample)
                    
                    # Mark all packets up to cum_ack as confirmed
                    seq_ptr = self.window_base
                    while seq_ptr < cum_ack:
                        self.confirmed_packets.add(seq_ptr)
                        seq_ptr += MSS
                    
                    self._advance_window()
                    self.dup_ack_tracker.clear()
                
                # --- Process SACK Blocks ---
                for left, right in sack_blocks:
                    seq_ptr = left
                    while seq_ptr < right and seq_ptr < file_byte_size:
                        if seq_ptr >= self.window_base:
                            self.confirmed_packets.add(seq_ptr)
                        seq_ptr += MSS
                
                # --- Fast Retransmit Check ---
                if cum_ack == self.window_base:
                    self.dup_ack_tracker[cum_ack] = self.dup_ack_tracker.get(cum_ack, 0) + 1
                    
                    if self.dup_ack_tracker[cum_ack] == 3:
                        if self.window_base not in self.confirmed_packets:
                            self._resend_data_packet(self.window_base, client_address, "fast_retransmit")
                            self.flow_manager.on_loss_detected("fast_retransmit")
            
            except socket.timeout:
                self._check_for_timeouts(client_address)
        
        # --- Finalize Transfer ---
        elapsed_time = time.time() - transfer_start_time
        throughput_mbps = (file_byte_size * 8 / elapsed_time / 1_000_000)
        
        print(f"[Server] Transfer complete in {elapsed_time:.2f}s ({throughput_mbps:.2f} Mbps)")
        print(f"[Server] Total Packets: {self.total_packets_sent}, Retransmits: {self.total_retransmits}")
        
        # Send FIN marker
        fin_packet = self._create_packet(file_byte_size, FIN_MARKER)
        for _ in range(5):
            self.listener_socket.sendto(fin_packet, client_address)
            time.sleep(0.04)
    
    def start_server(self):
        """Waits for a client, loads the file, and starts the transfer."""
        print(f"\n[Server] Waiting for client connection...")
        self.listener_socket.settimeout(30.0) # Wait 30s for a client
        
        try:
            request, client_address = self.listener_socket.recvfrom(PACKET_LIMIT_BYTES)
            print(f"[Server] Client connected from: {client_address}")
        except socket.timeout:
            print("[Server] ERROR: No client request received. Shutting down.")
            return
        
        self.listener_socket.settimeout(None)
        
        # Load the file
        data_filename = "data.txt"
        if not os.path.exists(data_filename):
            print(f"[Server] ERROR: Data file '{data_filename}' not found.")
            return
        
        with open(data_filename, 'rb') as f:
            file_data = f.read()
        
        print(f"[Server] Loaded '{data_filename}': {len(file_data)} bytes")
        
        self.transmit_file_data(file_data, client_address)
        self.listener_socket.close()


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server_refactored.py <SERVER_IP> <SERVER_PORT>")
        sys.exit(1)
    
    server = FastDataTransmitter(sys.argv[1], int(sys.argv[2]))
    server.start_server()


if __name__ == "__main__":
    main()