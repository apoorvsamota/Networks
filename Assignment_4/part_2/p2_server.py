#!/usr/bin/env python3
'''
This is a server program that implements a reliable file transfer protocol
using UDP sockets. It employs the CUBIC congestion control algorithm and
manages retransmissions based on RTT estimations and timeouts.
The code is modularized into several classes to handle different aspects
of the protocol, improving readability and maintainability.
'''

import socket, time, sys, math, os, struct
#It is the maximum segment size
class CongestionController:
    """Manages the CUBIC congestion window state."""
    def __init__(self):
        self.window_size = 1180
        self.slow_start_threshold = 280 * 1180
        self.max_window = 0
        self.cubic_start_time = 0
        self.tcp_window = 0
        self.is_slow_start = True

    def get_current_window(self):
        return int(self.window_size)

    def handle_acknowledgment(self, bytes_acked, round_trip_time):
        if self.is_slow_start:
            if self.window_size >= self.slow_start_threshold - bytes_acked:
                self.is_slow_start = False
                self.cubic_start_time = 0
            self.window_size = self.window_size + bytes_acked
        else:
            self._update_cubic_window(bytes_acked, round_trip_time)

        self.window_size = min(self.window_size, 520 * 1180) # Cap

    def _update_cubic_window(self, bytes_acked, round_trip_time):
        """The CUBIC growth function."""

        if self.cubic_start_time == 0:
            self.cubic_start_time = time.time()

            previous_max = self.max_window
            if self.window_size < previous_max:
                self.max_window = self.window_size * 1.35 / 2
            else:
                self.max_window = self.window_size

        time_elapsed = time.time() - self.cubic_start_time
        cubic_constant = math.pow((self.max_window * 0.35) / 0.85, 1.0/3.0)
        cubic_value = 0.85 * math.pow(time_elapsed - cubic_constant, 3)

        self.tcp_window += (3 * 0.65 / 1.35) * (bytes_acked / self.window_size)
        target_window = 0.0
        if(cubic_value + self.max_window < self.tcp_window):
            target_window = self.tcp_window
        else:
            target_window = cubic_value + self.max_window
        self.window_size += max(1180, int((target_window - self.window_size) / 8))

    def handle_loss(self, loss_type):
        if loss_type == "fast":
            self.max_window = self.window_size
            if self.window_size < self.max_window:
                self.max_window *= 1.35 / 2
            if(self.window_size * 0.65 >= 2 * 1180):
                self.slow_start_threshold = int(self.window_size * 0.65)
            else:
                self.slow_start_threshold = 2 * 1180
            self.window_size = self.slow_start_threshold
        else:
            if(self.window_size / 2 >= 2 * 1180):
                self.slow_start_threshold = int(self.window_size / 2)
            else:
                self.slow_start_threshold = 2 * 1180
            self.window_size = 1180
            self.is_slow_start = True
            self.max_window = 0

        self.cubic_start_time = 0


class RttCalculator:
    """Manages RTT estimation and RTO calculation."""

    def __init__(self):
        self.average_rtt = None
        self.rtt_deviation = None
        self.retransmission_timeout = 0.15

    def get_retransmission_timeout(self):
        return self.retransmission_timeout

    def update_estimates(self, rtt_sample):
        """Update RTO based on a new sample."""
        if self.average_rtt is not None:
            self.retransmission_timeout = 3 * self.rtt_deviation
            self.rtt_deviation *= 0.75
            if(rtt_sample > self.average_rtt) :
                self.rtt_deviation += 0.25 * (rtt_sample - self.average_rtt)
                self.retransmission_timeout += (rtt_sample - self.average_rtt)
            else:
                self.rtt_deviation += 0.25 * (self.average_rtt - rtt_sample)
                self.retransmission_timeout += (self.average_rtt - rtt_sample)
            self.average_rtt *= 0.875
            self.average_rtt += 0.125 * rtt_sample
            self.retransmission_timeout += self.average_rtt
        else:
            self.average_rtt = rtt_sample
            self.rtt_deviation = rtt_sample / 2
            self.retransmission_timeout = rtt_sample * 3
        if(self.retransmission_timeout < 0.04):
            self.retransmission_timeout = 0.04
        elif(self.retransmission_timeout > 0.8):
            self.retransmission_timeout = 0.8

    def increase_timeout(self):
        """Apply RTO backoff on timeout."""
        self.retransmission_timeout = min(self.retransmission_timeout * 1.15, 0.8)


class TransmissionHandler:
    """
    Manages all packet state, including window, buffers,
    and timeouts. This simplifies the main server class.
    """

    def __init__(self):
        self.window_start = 0
        self.next_sequence = 0
        self.acknowledged_sequences = set()
        self.transmission_times = {}
        self.packet_storage = {}
        self.packet_deadlines = {}
        self.duplicate_ack_counters = {}

    def is_acknowledged(self, sequence_number):
        return sequence_number in self.acknowledged_sequences

    def save_packet(self, sequence_number, packet_data, transmission_time, current_rto):
        """Stores a packet that has been sent."""
        packet = self._create_packet(sequence_number, packet_data)
        self.transmission_times[sequence_number] = transmission_time
        self.packet_storage[sequence_number] = packet
        self.packet_deadlines[sequence_number] = transmission_time + current_rto

    def update_packet_timing(self, sequence_number, transmission_time, current_rto):
        """Updates tracking for a re-sent packet."""
        self.transmission_times[sequence_number] = transmission_time
        self.packet_deadlines[sequence_number] = transmission_time + current_rto

    def get_stored_packet(self, sequence_number):
        return self.packet_storage.get(sequence_number)

    def mark_as_acknowledged(self, sequence_number):
        self.acknowledged_sequences.add(sequence_number)

    def get_transmission_time(self, sequence_number):
        return self.transmission_times.get(sequence_number)

    def advance_window(self):
        """Advances the base of the window."""
        while self.window_start in self.acknowledged_sequences:
            self.acknowledged_sequences.remove(self.window_start)
            self.transmission_times.pop(self.window_start, None)
            self.packet_storage.pop(self.window_start, None)
            self.packet_deadlines.pop(self.window_start, None)
            self.window_start += 1180

    def calculate_next_timeout(self, default_rto):
        """Calculates the socket timeout value."""
        if not self.packet_deadlines:
            return default_rto
        current_time = time.time()
        next_deadline = min(self.packet_deadlines.values())
        return max(0.01, next_deadline - current_time)

    def get_expired_packets(self):
        """Returns a list of sequence numbers that have timed out."""
        current_time = time.time()
        expired_packets = []
        for seq_num, deadline in list(self.packet_deadlines.items()):
            if seq_num not in self.acknowledged_sequences and current_time >= deadline:
                expired_packets.append(seq_num)
        return expired_packets

    def increment_duplicate_count(self, ack_number):
        """Increments and returns the duplicate ACK count."""
        count = self.duplicate_ack_counters.get(ack_number, 0) + 1
        self.duplicate_ack_counters[ack_number] = count
        return count

    def reset_duplicate_counts(self):
        self.duplicate_ack_counters.clear()

    def _create_packet(self, sequence_number, data):
        header = struct.pack('!I', sequence_number) + b'\x00' * 16
        return header + data


class FileTransferServer:
    """
    Main server class. Owns the socket and coordinates the
    CongestionController, RttCalculator, and TransmissionHandler.
    """

    def __init__(self, server_ip, server_port):
        self.server_address = (server_ip, server_port)
        self.connection_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.connection_socket.bind(('0.0.0.0', server_port))

        # Coordinated components
        self.congestion_control = CongestionController()
        self.rtt_estimator = RttCalculator()
        self.transmission_manager = TransmissionHandler()

        self.client_address = None
        self.file_data = None
        self.file_length = 0

        # Stats
        self.total_packets_sent = 0
        self.total_retransmissions = 0
        self.fast_retransmissions = 0

        print(f"[Server] Ready at {server_ip}:{server_port}")

    def await_client_connection(self):
        """Blocks until a client sends a request."""
        print("[Server] Waiting for client...")
        self.connection_socket.settimeout(30.0)
        try:
            client_request, self.client_address = self.connection_socket.recvfrom(1200)
            print(f"[Server] Client connected: {self.client_address}")
            self.connection_socket.settimeout(None)
            return True
        except socket.timeout:
            print("[Server] No client request received.")
            return False

    def read_file_data(self, filename="data.txt"):
        """Loads the file to be sent."""
        if not os.path.exists(filename):
            print(f"[Server] ERROR: File '{filename}' not found.")
            return False

        with open(filename, 'rb') as file_handle:
            self.file_data = file_handle.read()
        self.file_length = len(self.file_data)
        print(f"[Server] Loaded '{filename}': {self.file_length} bytes")
        return True

    def _extract_ack_info(self, ack_packet):
        """Utility to parse ACK packets."""
        if len(ack_packet) < 4: return None, []
        ack_number = struct.unpack('!I', ack_packet[:4])[0]
        selective_acks = []
        if len(ack_packet) >= 20:
            try:
                for i in range(2):
                    offset = 4 + i * 8
                    if (8 * i + 12) <= len(ack_packet):
                        left = struct.unpack('!I', ack_packet[offset:offset+4])[0]
                        right = struct.unpack('!I', ack_packet[offset+4:offset+8])[0]
                        if left > 0 and right > left:
                            selective_acks.append((left, right))
            except: pass
        return ack_number, selective_acks

    def _transmit_available_packets(self):
        """Sends all packets permitted by the current CWND."""
        window_limit = self.transmission_manager.window_start + self.congestion_control.get_current_window()

        while self.transmission_manager.next_sequence < window_limit and \
              self.transmission_manager.next_sequence < self.file_length:

            current_sequence = self.transmission_manager.next_sequence
            if not self.transmission_manager.is_acknowledged(current_sequence):
                end_position = min(current_sequence + 1180, self.file_length)
                data_chunk = self.file_data[current_sequence:end_position]

                self.transmission_manager.save_packet(current_sequence, data_chunk, time.time(), self.rtt_estimator.get_retransmission_timeout())
                self.connection_socket.sendto(self.transmission_manager.get_stored_packet(current_sequence), self.client_address)
                self.total_packets_sent += 1

            self.transmission_manager.next_sequence += 1180

    def _process_acknowledgment(self, ack_packet, receive_time):
        """Processes an incoming ACK packet."""
        ack_value, sack_blocks = self._extract_ack_info(ack_packet)
        if ack_value is None: return

        # --- 1. Process Cumulative ACK ---
        if ack_value > self.transmission_manager.window_start:
            bytes_acknowledged = ack_value - self.transmission_manager.window_start

            send_timestamp = self.transmission_manager.get_transmission_time(self.transmission_manager.window_start)
            if send_timestamp:
                measured_rtt = receive_time - send_timestamp
                self.rtt_estimator.update_estimates(measured_rtt)
                self.congestion_control.handle_acknowledgment(bytes_acknowledged, measured_rtt)

            # Mark packets as ACKed and slide window
            seq = self.transmission_manager.window_start
            while seq < ack_value:
                self.transmission_manager.mark_as_acknowledged(seq)
                seq += 1180
            self.transmission_manager.advance_window()
            self.transmission_manager.reset_duplicate_counts()

        # --- 2. Process SACK Blocks ---
        for start, end in sack_blocks:
            seq = start
            while seq < end and seq < self.file_length:
                if seq >= self.transmission_manager.window_start:
                    self.transmission_manager.mark_as_acknowledged(seq)
                seq += 1180

        # --- 3. Check for Fast Retransmit ---
        if ack_value == self.transmission_manager.window_start:
            duplicate_count = self.transmission_manager.increment_duplicate_count(ack_value)
            if duplicate_count == 3 and not self.transmission_manager.is_acknowledged(self.transmission_manager.window_start):
                self._resend_packet(self.transmission_manager.window_start, "fast_retransmit")
                self.congestion_control.handle_loss("fast")

    def _resend_packet(self, sequence_number, retransmit_reason="timeout"):
        """Retransmits a single packet."""
        packet_data = self.transmission_manager.get_stored_packet(sequence_number)
        if packet_data:
            self.connection_socket.sendto(packet_data, self.client_address)
            self.transmission_manager.update_packet_timing(sequence_number, time.time(), self.rtt_estimator.get_retransmission_timeout())
            self.total_retransmissions += 1
            if retransmit_reason == "fast_retransmit":
                self.fast_retransmissions += 1

    def _handle_packet_timeout(self):
        """Handles a socket timeout event."""
        expired_packets = self.transmission_manager.get_expired_packets()
        if not expired_packets: return

        for sequence_number in expired_packets:
            self._resend_packet(sequence_number, "timeout")

        # Only trigger one loss event per timeout
        self.congestion_control.handle_loss("timeout")
        self.rtt_estimator.increase_timeout()

    def begin_file_transfer(self):
        """Main transfer loop."""
        if not self.file_data:
            print("[Server] No file loaded. Aborting.")
            return

        print(f"[Server] Starting transfer of {self.file_length} bytes...")
        transfer_start_time = time.time()

        while self.transmission_manager.window_start < self.file_length:
            # 1. Send packets
            self._transmit_available_packets()

            # 2. Wait for ACK or Timeout
            timeout_duration = self.transmission_manager.calculate_next_timeout(self.rtt_estimator.get_retransmission_timeout())
            self.connection_socket.settimeout(timeout_duration)

            try:
                acknowledgment_packet, client_addr = self.connection_socket.recvfrom(1200)
                self._process_acknowledgment(acknowledgment_packet, time.time())
            except socket.timeout:
                self._handle_packet_timeout()

        # --- Transfer Complete ---
        total_time = time.time() - transfer_start_time
        transfer_rate = (self.file_length * 8 / total_time / 1_000_000)

        print(f"[Server] Done: {total_time:.2f}s, {transfer_rate:.2f} Mbps")
        print(f"[Server] Sent: {self.total_packets_sent}, Retrans: {self.total_retransmissions} (Fast: {self.fast_retransmissions})")

        # Send EOF
        eof_packet = self.transmission_manager._create_packet(self.file_length, b"EOF")
        for _ in range(5):
            self.connection_socket.sendto(eof_packet, self.client_address)
            time.sleep(0.04)

        self.connection_socket.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 p2_server_refactored.py <IP> <PORT>")
        sys.exit(1)

    server_instance = FileTransferServer(sys.argv[1], int(sys.argv[2]))
    if server_instance.await_client_connection() and server_instance.read_file_data():
        server_instance.begin_file_transfer()

if __name__ == "__main__":
    main()