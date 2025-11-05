#!/usr/bin/env python3
import socket
import sys
import struct
import time
from collections import deque

# Constants
PACKET_MAX = 1200
HEADER_BYTES = 20
PAYLOAD_BYTES = PACKET_MAX - HEADER_BYTES
TERMINATOR = b"EOF"

class PacketAssembler:
    """Handles packet buffering, ordering, and SACK generation"""
    
    def __init__(self):
        self.sequence_expected = 0
        self.buffer_pool = {}
        self.completed_data = bytearray()
        self.previous_ack = -1
        
        # Statistics
        self.total_received = 0
        self.duplicates = 0
        self.out_of_order = 0
    
    def next_sequence(self):
        """Get next expected sequence number"""
        return self.sequence_expected
    
    def get_completed(self):
        """Return assembled data"""
        return self.completed_data
    
    def pending_count(self):
        """Return buffered packet count"""
        return len(self.buffer_pool)
    
    def add_data_packet(self, seq, data):
        """Add packet to buffer - returns (accepted, duplicate)"""
        self.total_received += 1
        
        # Check if already processed
        if seq < self.sequence_expected:
            self.duplicates += 1
            return False, True
        
        # Check if already buffered
        if seq in self.buffer_pool:
            self.duplicates += 1
            return False, True
        
        # New packet
        if seq > self.sequence_expected:
            self.out_of_order += 1
        
        self.buffer_pool[seq] = data
        
        # Try to assemble if in order
        if seq == self.sequence_expected:
            self._merge_sequential_packets()
        
        return True, False
    
    def _merge_sequential_packets(self):
        """Merge all consecutive packets into completed data"""
        while self.sequence_expected in self.buffer_pool:
            payload = self.buffer_pool.pop(self.sequence_expected)
            self.completed_data.extend(payload)
            self.sequence_expected += len(payload)
    
    def generate_ack_with_sack(self):
        """Build ACK packet with SACK information"""
        # Cumulative ACK
        ack_bytes = struct.pack('!I', self.sequence_expected)
        
        # Generate SACK blocks if out-of-order packets exist
        if not self.buffer_pool:
            return ack_bytes + b'\x00' * (HEADER_BYTES - 4)
        
        sack_regions = self._compute_sack_blocks()
        
        # Add up to 2 SACK blocks
        for left, right in sack_regions[:2]:
            ack_bytes += struct.pack('!II', left, right)
        
        # Pad to header size
        while len(ack_bytes) < HEADER_BYTES:
            ack_bytes += b'\x00'
        
        return ack_bytes
    
    def _compute_sack_blocks(self):
        """Calculate SACK block ranges from buffer"""
        if not self.buffer_pool:
            return []
        
        sequences = sorted(self.buffer_pool.keys())
        blocks = []
        
        block_start = sequences[0]
        block_end = block_start + len(self.buffer_pool[block_start])
        
        for seq in sequences[1:]:
            if seq == block_end:
                # Extend current block
                block_end = seq + len(self.buffer_pool[seq])
            else:
                # Save current block and start new one
                blocks.append((block_start, block_end))
                if len(blocks) >= 2:
                    break
                block_start = seq
                block_end = seq + len(self.buffer_pool[seq])
        
        # Add last block if space
        if len(blocks) < 2:
            blocks.append((block_start, block_end))
        
        return blocks
    
    def get_stats(self):
        """Return reception statistics"""
        return {
            'total': self.total_received,
            'duplicates': self.duplicates,
            'out_of_order': self.out_of_order,
            'completed_bytes': len(self.completed_data)
        }

class ConnectionManager:
    """Manages connection establishment with retry logic"""
    
    def __init__(self, sock, server_addr):
        self.sock = sock
        self.server = server_addr
        self.max_attempts = 5
        self.retry_timeout = 2.0
    
    def establish_connection(self):
        """Attempt connection with retries"""
        for attempt in range(1, self.max_attempts + 1):
            print(f"[CLI] Connect attempt {attempt}/{self.max_attempts}")
            
            self.sock.sendto(b'R', self.server)
            self.sock.settimeout(self.retry_timeout)
            
            try:
                packet, addr = self.sock.recvfrom(PACKET_MAX)
                print("[CLI] Connection established")
                return packet
            except socket.timeout:
                if attempt < self.max_attempts:
                    print(f"[CLI] Timeout, retrying...")
                continue
        
        print("[CLI] Connection failed")
        return None

class TransferMonitor:
    """Monitors transfer progress and handles timeouts"""
    
    def __init__(self):
        self.start_time = time.time()
        self.last_progress_print = self.start_time
        self.consecutive_timeouts = 0
        self.max_timeouts = 15
        self.print_interval = 1.0
    
    def reset_timeout_counter(self):
        """Reset timeout streak"""
        self.consecutive_timeouts = 0
    
    def register_timeout(self):
        """Record a timeout occurrence"""
        self.consecutive_timeouts += 1
        return self.consecutive_timeouts
    
    def is_stalled(self):
        """Check if transfer appears stalled"""
        return self.consecutive_timeouts >= self.max_timeouts
    
    def should_print_progress(self):
        """Check if progress should be displayed"""
        now = time.time()
        if now - self.last_progress_print >= self.print_interval:
            self.last_progress_print = now
            return True
        return False
    
    def get_elapsed(self):
        """Get elapsed time"""
        return time.time() - self.start_time

class ReliableClient:
    """Reliable UDP client with SACK support"""
    
    def __init__(self, server_ip, server_port, output_prefix):
        self.server_addr = (server_ip, server_port)
        self.output_file = f"{output_prefix}received_data.txt"
        
        # Setup socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        
        # Components
        self.assembler = PacketAssembler()
        self.connector = ConnectionManager(self.sock, self.server_addr)
        self.monitor = TransferMonitor()
        
        # ACK state
        self.ack_counter = 0
        self.last_ack_time = time.time()
        self.ack_frequency = 0.001  # Send ACKs frequently for fairness
        
        print(f"[CLI] Target: {server_ip}:{server_port}")
    
    def _send_acknowledgment(self):
        """Send ACK to server"""
        ack_packet = self.assembler.generate_ack_with_sack()
        self.sock.sendto(ack_packet, self.server_addr)
        self.ack_counter += 1
        self.last_ack_time = time.time()
    
    def _extract_sequence_and_data(self, packet):
        """Parse packet header"""
        if len(packet) < HEADER_BYTES:
            return None, None
        
        seq = struct.unpack('!I', packet[:4])[0]
        payload = packet[HEADER_BYTES:]
        return seq, payload
    
    def _process_packet_batch(self, packets):
        """Process multiple packets efficiently"""
        eof_detected = False
        
        for packet in packets:
            seq, payload = self._extract_sequence_and_data(packet)
            
            if seq is None:
                continue
            
            # Check for EOF
            if payload == TERMINATOR:
                eof_detected = True
                break
            
            # Add to assembler
            accepted, duplicate = self.assembler.add_data_packet(seq, payload)
            
            # Always ACK (immediate feedback for fairness)
            self._send_acknowledgment()
        
        return eof_detected
    
    def _periodic_ack_check(self):
        """Send periodic ACK if needed"""
        now = time.time()
        if now - self.last_ack_time >= 0.05:
            self._send_acknowledgment()
    
    def _print_progress(self):
        """Display transfer progress"""
        stats = self.assembler.get_stats()
        print(f"[CLI] Progress: {stats['completed_bytes']} bytes, "
              f"Buffered: {self.assembler.pending_count()}, "
              f"OOO: {stats['out_of_order']}, Dup: {stats['duplicates']}")
    
    def start_reception(self):
        """Main reception loop"""
        # Establish connection
        first_packet = self.connector.establish_connection()
        if not first_packet:
            return False
        
        # Configure for transfer
        self.sock.settimeout(4.0)  # Moderate timeout for fairness
        
        # Process first packet
        packet_queue = [first_packet]
        
        while True:
            # Process queued packets
            if self._process_packet_batch(packet_queue):
                # EOF received
                self._finalize_transfer()
                return True
            
            # Reset queue
            packet_queue = []
            
            # Display progress periodically
            if self.monitor.should_print_progress():
                self._print_progress()
            
            # Wait for next packet
            try:
                packet, addr = self.sock.recvfrom(PACKET_MAX)
                packet_queue.append(packet)
                self.monitor.reset_timeout_counter()
                
            except socket.timeout:
                timeout_count = self.monitor.register_timeout()
                print(f"[CLI] Timeout {timeout_count}/{self.monitor.max_timeouts}")
                
                # Send keep-alive ACK
                self._periodic_ack_check()
                
                if self.monitor.is_stalled():
                    print("[CLI] Transfer stalled - saving partial")
                    self._save_to_file()
                    self._show_statistics()
                    return False
    
    def _finalize_transfer(self):
        """Complete transfer successfully"""
        self._save_to_file()
        self._show_statistics()
        print("[CLI] Transfer successful")
    
    def _save_to_file(self):
        """Write data to output file"""
        data = self.assembler.get_completed()
        
        if not data:
            print("[CLI] No data to save")
            return
        
        print(f"[CLI] Saving {len(data)} bytes -> {self.output_file}")
        with open(self.output_file, 'wb') as f:
            f.write(data)
    
    def _show_statistics(self):
        """Display transfer statistics"""
        stats = self.assembler.get_stats()
        elapsed = self.monitor.get_elapsed()
        
        print("\n=== Transfer Complete ===")
        print(f"Time:         {elapsed:.2f}s")
        print(f"Data:         {stats['completed_bytes']} bytes")
        
        if elapsed > 0:
            throughput = (stats['completed_bytes'] * 8 / elapsed / 1_000_000)
            print(f"Throughput:   {throughput:.2f} Mbps")
        
        print(f"Packets:      {stats['total']}")
        print(f"Out-of-order: {stats['out_of_order']}")
        print(f"Duplicates:   {stats['duplicates']}")
        print(f"ACKs sent:    {self.ack_counter}")
    
    def execute(self):
        """Run client"""
        try:
            success = self.start_reception()
            return success
        except KeyboardInterrupt:
            print("\n[CLI] Interrupted by user")
            return False
        except Exception as e:
            print(f"\n[CLI] Error: {e}")
            return False
        finally:
            self.sock.close()

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>")
        sys.exit(1)
    
    client = ReliableClient(sys.argv[1], int(sys.argv[2]), sys.argv[3])
    success = client.execute()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
