#!/usr/bin/env python3
import socket
import sys
import struct
import time

# Configuration constants
PKT_SIZE = 1200
HDR_SIZE = 20
DATA_SIZE = PKT_SIZE - HDR_SIZE
END_MARKER = b"EOF"

class ReceptionBuffer:
    """Manages out-of-order packet buffering and reassembly"""
    
    def __init__(self):
        self.next_expected = 0
        self.ooo_storage = {}
        self.assembled_data = bytearray()
        self.last_ack = -1
        
    def expected_sequence(self):
        return self.next_expected
        
    def assembled_bytes(self):
        return self.assembled_data
        
    def buffer_depth(self):
        return len(self.ooo_storage)
        
    def insert_packet(self, seq, payload):
        """Insert packet and return (is_new, is_duplicate)"""
        
        # Already processed - duplicate
        if seq < self.next_expected:
            return False, True
        
        # Already buffered - duplicate
        if seq in self.ooo_storage:
            return False, True
        
        # New packet
        self.ooo_storage[seq] = payload
        
        # Assemble if in-order
        if seq == self.next_expected:
            self._assemble_contiguous()
            
        return True, False

    def _assemble_contiguous(self):
        """Assemble all contiguous packets"""
        while self.next_expected in self.ooo_storage:
            chunk = self.ooo_storage.pop(self.next_expected)
            self.assembled_data.extend(chunk)
            self.next_expected += len(chunk)

    def build_ack_packet(self):
        """Construct ACK with selective acknowledgments"""
        # Start with cumulative ACK
        ack_data = struct.pack('!I', self.next_expected)
        
        # Add SACK blocks
        if not self.ooo_storage:
            return ack_data.ljust(HDR_SIZE, b'\x00')
            
        sorted_sequences = sorted(self.ooo_storage.keys())
        sack_ranges = []
        
        range_start = sorted_sequences[0]
        range_end = range_start + len(self.ooo_storage[range_start])
        
        for seq in sorted_sequences[1:]:
            if seq == range_end:
                range_end = seq + len(self.ooo_storage[seq])
            else:
                if len(sack_ranges) < 2:
                    sack_ranges.append((range_start, range_end))
                range_start = seq
                range_end = seq + len(self.ooo_storage[seq])
        
        if len(sack_ranges) < 2:
            sack_ranges.append((range_start, range_end))
        
        for start, end in sack_ranges[:2]:
            ack_data += struct.pack('!II', start, end)
            
        return ack_data.ljust(HDR_SIZE, b'\x00')

class UDPClient:
    """Main client orchestrator"""
    
    def __init__(self, target_ip, target_port, file_prefix):
        self.server_addr = (target_ip, target_port)
        self.output_path = f"{file_prefix}received_data.txt"
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        
        self.rx_buffer = ReceptionBuffer()
        
        # Statistics
        self.pkts_received = 0
        self.acks_transmitted = 0
        self.ooo_packets = 0
        self.duplicate_packets = 0
        
        print(f"[CLIENT] Targeting {target_ip}:{target_port}")

    def _transmit_ack(self):
        """Send acknowledgment to server"""
        ack_pkt = self.rx_buffer.build_ack_packet()
        self.udp_sock.sendto(ack_pkt, self.server_addr)
        self.acks_transmitted += 1

    def _extract_packet(self, raw_pkt):
        if len(raw_pkt) < HDR_SIZE:
            return None, None
        seq = struct.unpack('!I', raw_pkt[:4])[0]
        payload = raw_pkt[HDR_SIZE:]
        return seq, payload

    def _request_connection(self):
        """Initiate connection with retries"""
        for attempt in range(5):
            print(f"[CLIENT] Connection attempt {attempt + 1}")
            self.udp_sock.sendto(b'R', self.server_addr)
            self.udp_sock.settimeout(2.0)
            
            try:
                pkt, addr = self.udp_sock.recvfrom(PKT_SIZE)
                print("[CLIENT] Server responded")
                return pkt
            except socket.timeout:
                continue
        
        print("[CLIENT] Connection failed")
        return None

    def execute(self):
        """Main reception loop"""
        initial_pkt = self._request_connection()
        if not initial_pkt:
            return False

        self.udp_sock.settimeout(5.0)  # Shorter timeout for faster response
        start_ts = time.time()
        last_status_ts = start_ts
        
        pending_packets = [initial_pkt]
        timeout_streak = 0
        last_ack_time = start_ts
        
        while True:
            # Process batch
            for pkt in pending_packets:
                seq, payload = self._extract_packet(pkt)
                if seq is None:
                    continue
                
                self.pkts_received += 1
                timeout_streak = 0
                
                # EOF detection
                if payload == END_MARKER:
                    self._save_file()
                    self._display_stats(time.time() - start_ts)
                    return True
                
                # Buffer packet
                is_new, is_dup = self.rx_buffer.insert_packet(seq, payload)
                
                if is_new:
                    self.ooo_packets += 1
                elif is_dup:
                    self.duplicate_packets += 1
                
                # Send ACK immediately for every packet (better feedback)
                self._transmit_ack()
                last_ack_time = time.time()
                
                # Progress update
                now = time.time()
                if now - last_status_ts > 1.0:
                    print(f"[CLIENT] Received: {len(self.rx_buffer.assembled_bytes())} bytes, "
                          f"Buffered: {self.rx_buffer.buffer_depth()}")
                    last_status_ts = now
            
            # Wait for next packet
            pending_packets = []
            try:
                pkt, addr = self.udp_sock.recvfrom(PKT_SIZE)
                pending_packets.append(pkt)
            except socket.timeout:
                timeout_streak += 1
                print(f"[CLIENT] Timeout {timeout_streak}/12")
                
                # Send periodic ACKs
                now = time.time()
                if now - last_ack_time >= 0.05:
                    self._transmit_ack()
                    last_ack_time = now
                
                if timeout_streak >= 12:
                    print("[CLIENT] Transfer stalled")
                    self._save_file()
                    self._display_stats(time.time() - start_ts)
                    return False

    def _save_file(self):
        """Write assembled data to file"""
        data = self.rx_buffer.assembled_bytes()
        if not data:
            print("[CLIENT] No data to save")
            return

        print(f"[CLIENT] Saving {len(data)} bytes to {self.output_path}")
        with open(self.output_path, 'wb') as f:
            f.write(data)

    def _display_stats(self, duration):
        print("\n=== Transfer Statistics ===")
        print(f"Duration:     {duration:.2f}s")
        data_len = len(self.rx_buffer.assembled_bytes())
        print(f"Data:         {data_len} bytes")
        if duration > 0:
            throughput = (data_len * 8 / duration / 1_000_000)
            print(f"Throughput:   {throughput:.2f} Mbps")
        print(f"Packets:      {self.pkts_received}")
        print(f"Out-of-order: {self.ooo_packets}")
        print(f"Duplicates:   {self.duplicate_packets}")
        print(f"ACKs sent:    {self.acks_transmitted}")

    def run(self):
        try:
            result = self.execute()
            if result:
                print("\n[CLIENT] Transfer successful")
            else:
                print("\n[CLIENT] Transfer incomplete")
            return result
        except KeyboardInterrupt:
            print("\n[CLIENT] Interrupted")
            return False
        finally:
            self.udp_sock.close()

def main():
    if len(sys.argv) != 4:
        print("Usage: python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>")
        sys.exit(1)
    
    client = UDPClient(sys.argv[1], int(sys.argv[2]), sys.argv[3])
    success = client.run()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
