#!/usr/bin/env python3
"""
Part 2 Client: Reliable File Transfer with SACK support
"""

import socket
import sys
import struct
import time

# Constants
MAX_PACKET_SIZE = 1200
HEADER_SIZE = 20
MAX_DATA_SIZE = MAX_PACKET_SIZE - HEADER_SIZE
EOF_MARKER = b"EOF"
REQUEST_RETRIES = 5
REQUEST_TIMEOUT = 2.0


class ReliableClient:
    def __init__(self, server_ip, server_port, prefix_filename):
        self.server_ip = server_ip
        self.server_port = server_port
        self.prefix_filename = prefix_filename
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Reception state
        self.expected_seq = 0
        self.buffer = {}
        self.received_data = bytearray()

        # Statistics
        self.total_packets_received = 0
        self.total_acks_sent = 0
        self.out_of_order_count = 0
        self.duplicate_count = 0

        print(f"[CLIENT] {server_ip}:{server_port}, prefix={prefix_filename}")

    def create_ack_with_sack(self, ack_num):
        """Create ACK packet with SACK blocks"""
        sack_blocks = []

        if self.buffer:
            sorted_seqs = sorted(self.buffer.keys())

            if sorted_seqs:
                left = sorted_seqs[0]
                right = left + len(self.buffer[left])

                for seq in sorted_seqs[1:]:
                    if seq == right:
                        right = seq + len(self.buffer[seq])
                    else:
                        if len(sack_blocks) < 2:
                            sack_blocks.append((left, right))
                        left = seq
                        right = seq + len(self.buffer[seq])

                if len(sack_blocks) < 2:
                    sack_blocks.append((left, right))

        ack_packet = struct.pack('!I', ack_num)

        for left, right in sack_blocks[:2]:
            ack_packet += struct.pack('!II', left, right)

        while len(ack_packet) < 20:
            ack_packet += b'\x00'

        return ack_packet, sack_blocks

    def send_ack(self, ack_num):
        """Send ACK with SACK"""
        ack_packet, sack_blocks = self.create_ack_with_sack(ack_num)
        self.socket.sendto(ack_packet, (self.server_ip, self.server_port))
        self.total_acks_sent += 1

    def parse_packet(self, packet):
        """Parse received packet"""
        if len(packet) < HEADER_SIZE:
            return None, None

        seq_num = struct.unpack('!I', packet[:4])[0]
        data = packet[HEADER_SIZE:]

        return seq_num, data

    def send_request(self):
        """Send request with retries"""
        for attempt in range(REQUEST_RETRIES):
            self.socket.sendto(b'R', (self.server_ip, self.server_port))
            self.socket.settimeout(REQUEST_TIMEOUT)

            try:
                data, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
                return data, addr
            except socket.timeout:
                continue

        print(f"[CLIENT] ERROR: No response")
        sys.exit(1)

    def process_in_order_packets(self):
        """Process buffered packets"""
        while self.expected_seq in self.buffer:
            data = self.buffer[self.expected_seq]
            data_len = len(data)

            self.received_data.extend(data)
            del self.buffer[self.expected_seq]
            self.expected_seq += data_len

    def receive_file(self, output_filename):
        """Receive file"""
        start_time = time.time()
        last_packet_time = start_time

        first_packet, addr = self.send_request()
        self.socket.settimeout(10.0)

        packets_to_process = [first_packet]
        consecutive_timeouts = 0
        max_consecutive_timeouts = 10

        while True:
            for packet in packets_to_process:
                seq_num, data = self.parse_packet(packet)

                if seq_num is None:
                    continue

                self.total_packets_received += 1
                last_packet_time = time.time()
                consecutive_timeouts = 0

                # Check EOF
                if data == EOF_MARKER:
                    elapsed = time.time() - start_time
                    print(
                        f"[CLIENT] Done: {elapsed:.2f}s, {len(self.received_data)} bytes")

                    with open(output_filename, 'wb') as f:
                        f.write(self.received_data)

                    return

                # Process packet
                if seq_num == self.expected_seq:
                    self.buffer[seq_num] = data
                    self.process_in_order_packets()
                elif seq_num < self.expected_seq:
                    self.duplicate_count += 1
                else:
                    if seq_num not in self.buffer:
                        self.out_of_order_count += 1
                        self.buffer[seq_num] = data
                    else:
                        self.duplicate_count += 1

                # Send ACK
                self.send_ack(self.expected_seq)

            # Receive next packet
            packets_to_process = []
            try:
                packet, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
                packets_to_process.append(packet)
            except socket.timeout:
                consecutive_timeouts += 1
                self.send_ack(self.expected_seq)

                if consecutive_timeouts >= max_consecutive_timeouts:
                    if len(self.received_data) > 0:
                        with open(output_filename, 'wb') as f:
                            f.write(self.received_data)
                    break

                continue
            except Exception as e:
                break

    def run(self):
        """Main client loop"""
        output_filename = f"{self.prefix_filename}received_data.txt"

        try:
            self.receive_file(output_filename)
        except:
            pass
        finally:
            self.socket.close()


def main():
    if len(sys.argv) != 4:
        print(
            "Usage: python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>")
        sys.exit(1)

    client = ReliableClient(sys.argv[1], int(sys.argv[2]), sys.argv[3])
    client.run()


if __name__ == "__main__":
    main()
