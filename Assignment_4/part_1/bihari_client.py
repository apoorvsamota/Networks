#!/usr/bin/env python3
"""
Part 1 Client: Reliable File Transfer over UDP
Implements Selective Repeat with SACK support
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

class SelectiveRepeatClient:
def __init__(self, server_ip, server_port):
self.server_ip = server_ip
self.server_port = server_port
self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Reception state
self.expected_seq = 0 # Next in-order byte expected
self.buffer = {} # seq_num -> data (for out-of-order packets)
self.received_data = bytearray()

# Statistics
self.total_packets_received = 0
self.total_acks_sent = 0
self.out_of_order_count = 0
self.duplicate_count = 0

print(f"[CLIENT] Initialized (Selective Repeat with SACK)")
print(f"[CLIENT] Server: {server_ip}:{server_port}")

def create_ack_with_sack(self, ack_num):
"""Create ACK packet with SACK blocks"""
# Build SACK blocks from buffered out-of-order packets
sack_blocks = []

if self.buffer:
sorted_seqs = sorted(self.buffer.keys())

# Find contiguous blocks
if sorted_seqs:
left = sorted_seqs[0]
right = left + len(self.buffer[left])

for seq in sorted_seqs[1:]:
if seq == right:
# Extend current block
right = seq + len(self.buffer[seq])
else:
# Save block and start new one
if len(sack_blocks) < 2:
sack_blocks.append((left, right))
left = seq
right = seq + len(self.buffer[seq])

# Add final block
if len(sack_blocks) < 2:
sack_blocks.append((left, right))

# Build packet: 4 bytes ack_num + SACK blocks (up to 16 bytes)
ack_packet = struct.pack('!I', ack_num)

# Add SACK blocks (each is 8 bytes: left_edge + right_edge)
for left, right in sack_blocks[:2]:
ack_packet += struct.pack('!II', left, right)

# Pad to 20 bytes
while len(ack_packet) < 20:
ack_packet += b'\x00'

return ack_packet, sack_blocks

def send_ack(self, ack_num):
"""Send ACK with SACK information"""
ack_packet, sack_blocks = self.create_ack_with_sack(ack_num)
self.socket.sendto(ack_packet, (self.server_ip, self.server_port))
self.total_acks_sent += 1

# Reduced logging for performance
if self.total_acks_sent % 50 == 0 or sack_blocks:
if sack_blocks:
sack_str = ", ".join([f"[{l},{r})" for l, r in sack_blocks])
print(f"[CLIENT] ACK={ack_num} SACK: {sack_str}")
else:
print(f"[CLIENT] ACK={ack_num} (total={self.total_acks_sent})")

def parse_packet(self, packet):
"""Parse received packet"""
if len(packet) < HEADER_SIZE:
return None, None

seq_num = struct.unpack('!I', packet[:4])[0]
data = packet[HEADER_SIZE:]

return seq_num, data

def send_request(self):
"""Send request with retries"""
print(f"\n[CLIENT] Sending request...")

for attempt in range(REQUEST_RETRIES):
self.socket.sendto(b'R', (self.server_ip, self.server_port))
self.socket.settimeout(REQUEST_TIMEOUT)

try:
data, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
print(f"[CLIENT] Got response!")
return data, addr
except socket.timeout:
print(f"[CLIENT] Timeout {attempt+1}/{REQUEST_RETRIES}")
continue

print(f"[CLIENT] ERROR: No response")
sys.exit(1)

def process_in_order_packets(self):
"""Process buffered packets that are now in order"""
while self.expected_seq in self.buffer:
data = self.buffer[self.expected_seq]
data_len = len(data)

self.received_data.extend(data)
del self.buffer[self.expected_seq]
self.expected_seq += data_len

def receive_file(self, output_filename):
"""Receive file using Selective Repeat"""
print(f"\n[CLIENT] Receiving file...")

start_time = time.time()
last_progress_time = start_time

# Get first packet
first_packet, addr = self.send_request()
self.socket.settimeout(None)

packets_to_process = [first_packet]

while True:
for packet in packets_to_process:
seq_num, data = self.parse_packet(packet)

if seq_num is None:
continue

self.total_packets_received += 1

# Progress indicator
current_time = time.time()
if current_time - last_progress_time > 1.0:
print(f"[CLIENT] Received {len(self.received_data)} bytes, buffered: {len(self.buffer)}")
last_progress_time = current_time

# Check for EOF
if data == EOF_MARKER:
elapsed = time.time() - start_time
print(f"\n[CLIENT] EOF received")
print(f"[CLIENT] Time: {elapsed:.2f}s")
print(f"[CLIENT] Throughput: {(len(self.received_data) * 8 / elapsed / 1_000_000):.2f} Mbps")
print(f"[CLIENT] Packets: {self.total_packets_received}")
print(f"[CLIENT] ACKs sent: {self.total_acks_sent}")
print(f"[CLIENT] Out-of-order: {self.out_of_order_count}")
print(f"[CLIENT] Duplicates: {self.duplicate_count}")
print(f"[CLIENT] Bytes: {len(self.received_data)}")

with open(output_filename, 'wb') as f:
f.write(self.received_data)

print(f"[CLIENT] Saved to {output_filename}")
return

# Process packet
if seq_num == self.expected_seq:
# Expected packet
self.buffer[seq_num] = data
self.process_in_order_packets()

elif seq_num < self.expected_seq:
# Duplicate
self.duplicate_count += 1
if self.duplicate_count % 20 == 0:
print(f"[CLIENT] Duplicate seq={seq_num} (total={self.duplicate_count})")

else:
# Out-of-order - buffer it
if seq_num not in self.buffer:
self.out_of_order_count += 1
self.buffer[seq_num] = data
if self.out_of_order_count % 20 == 0:
print(f"[CLIENT] Out-of-order seq={seq_num} (total={self.out_of_order_count})")
else:
self.duplicate_count += 1

# Send ACK with SACK
self.send_ack(self.expected_seq)

# Receive next packet
packets_to_process = []
try:
packet, addr = self.socket.recvfrom(MAX_PACKET_SIZE)
packets_to_process.append(packet)
except Exception as e:
print(f"[CLIENT] Error: {e}")
break

def run(self, output_filename):
"""Main client loop"""
try:
self.receive_file(output_filename)
except KeyboardInterrupt:
print(f"\n[CLIENT] Interrupted")
except Exception as e:
print(f"\n[CLIENT] Error: {e}")
import traceback
traceback.print_exc()
finally:
self.socket.close()
print(f"[CLIENT] Closed")

def main():
if len(sys.argv) != 3:
print("Usage: python3 p1_client.py <SERVER_IP> <SERVER_PORT>")
sys.exit(1)

client = SelectiveRepeatClient(sys.argv[1], int(sys.argv[2]))
client.run("received_data.txt")

if __name__ == "__main__":
main()