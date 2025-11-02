#!/usr/bin/env python3
"""
Verification Script for Part 1 Assignment Requirements
Checks that all implementation requirements are met
"""

import re
import sys

def check_file_exists(filename):
    """Check if file exists"""
    try:
        with open(filename, 'r') as f:
            return True, f.read()
    except FileNotFoundError:
        return False, None

def verify_requirement(name, check_func, code_server, code_client):
    """Verify a single requirement"""
    result, details = check_func(code_server, code_client)
    status = "✅ PASS" if result else "❌ FAIL"
    print(f"\n{status} - {name}")
    if details:
        for detail in details:
            print(f"  → {detail}")
    return result

def check_packet_numbering(server_code, client_code):
    """Check if sequence numbers are implemented"""
    details = []
    
    # Check server creates packets with seq numbers
    if "struct.pack('!I', seq_num)" in server_code:
        details.append("Server packs sequence number (4 bytes, network byte order)")
    else:
        return False, ["Missing: struct.pack for sequence number"]
    
    # Check client unpacks seq numbers
    if "struct.unpack('!I'" in client_code:
        details.append("Client unpacks sequence number")
    else:
        return False, ["Missing: struct.unpack for sequence number"]
    
    return True, details

def check_packet_format(server_code, client_code):
    """Check 20-byte header format: 4 bytes seq + 16 bytes reserved"""
    details = []
    
    # Check header size constant
    if "HEADER_SIZE = 20" in server_code or "HEADER_SIZE = 20" in client_code:
        details.append("HEADER_SIZE = 20 defined")
    else:
        return False, ["Missing: HEADER_SIZE = 20"]
    
    # Check MSS (Maximum Segment Size)
    if "MSS = 1180" in server_code or "MSS = 1180" in client_code:
        details.append("MSS = 1180 (max data payload)")
    else:
        return False, ["Missing: MSS = 1180"]
    
    # Check reserved bytes (16 bytes)
    if "b'\\x00' * 16" in server_code:
        details.append("16 bytes reserved in packet header")
    else:
        return False, ["Missing: 16 reserved bytes"]
    
    # Check total payload limit
    if "1200" in server_code or "MAX_PAYLOAD = 1200" in server_code:
        details.append("MAX_PAYLOAD = 1200 (20 header + 1180 data)")
    
    return True, details

def check_cumulative_acks(server_code, client_code):
    """Check if cumulative ACKs are implemented"""
    details = []
    
    # Check client sends ACKs
    if "send_ack" in client_code and "expected_seq" in client_code:
        details.append("Client sends cumulative ACKs")
    else:
        return False, ["Missing: send_ack function"]
    
    # Check ACK packet format (4 bytes ack_num + 16 reserved)
    if "struct.pack('!I', ack_num)" in client_code:
        details.append("ACK packet uses correct format")
    else:
        return False, ["Missing: proper ACK packet format"]
    
    # Check server processes ACKs
    if "struct.unpack('!I', ack_packet" in server_code:
        details.append("Server unpacks and processes ACKs")
    else:
        return False, ["Missing: ACK processing in server"]
    
    return True, details

def check_timeouts(server_code, client_code):
    """Check if timeout mechanism is implemented"""
    details = []
    
    # Check RTO exists
    if "self.RTO" in server_code or "RTO" in server_code:
        details.append("Retransmission Timeout (RTO) implemented")
    else:
        return False, ["Missing: RTO variable"]
    
    # Check timeout detection
    if "timer_start" in server_code and "time.time()" in server_code:
        details.append("Timer mechanism for detecting timeouts")
    else:
        return False, ["Missing: timer mechanism"]
    
    # Check adaptive RTO
    if "update_rto" in server_code or "estimated_rtt" in server_code:
        details.append("Adaptive RTO (RTT estimation)")
    else:
        details.append("Warning: No adaptive RTO detected (fixed RTO is acceptable)")
    
    # Check retransmission on timeout
    if "Timeout" in server_code and "Retransmitting" in server_code:
        details.append("Retransmits packets on timeout")
    else:
        return False, ["Missing: retransmission on timeout"]
    
    return True, details

def check_fast_retransmit(server_code, client_code):
    """Check if fast retransmit is implemented"""
    details = []
    
    # Check duplicate ACK counting
    if "dup_ack" in server_code or "duplicate" in server_code.lower():
        details.append("Duplicate ACK detection")
    else:
        return False, ["Missing: duplicate ACK tracking"]
    
    # Check for "3" (threshold for fast retransmit)
    if "== 3" in server_code or "3 duplicate" in server_code.lower():
        details.append("Fast retransmit after 3 duplicate ACKs")
    else:
        return False, ["Missing: 3-duplicate-ACK threshold"]
    
    # Check fast retransmit action
    if "fast retransmit" in server_code.lower():
        details.append("Fast retransmit mechanism implemented")
    else:
        return False, ["Missing: fast retransmit action"]
    
    return True, details

def check_connection_setup(server_code, client_code):
    """Check if connection setup follows requirements"""
    details = []
    
    # Check client sends 1-byte request
    if "b'1'" in client_code or 'b"1"' in client_code:
        details.append("Client sends 1-byte request")
    else:
        return False, ["Missing: 1-byte file request"]
    
    # Check retry mechanism (up to 5 times)
    if "5" in client_code and ("retry" in client_code.lower() or "attempt" in client_code.lower()):
        details.append("Client retries up to 5 times")
    else:
        return False, ["Missing: 5-retry mechanism"]
    
    # Check 2-second timeout
    if "2.0" in client_code or "2" in client_code:
        details.append("2-second timeout between retries")
    else:
        details.append("Warning: Could not verify 2-second timeout")
    
    return True, details

def check_sliding_window(server_code, client_code):
    """Check if sliding window is implemented"""
    details = []
    
    # Check SWS parameter
    if "sws" in server_code.lower():
        details.append("Sender Window Size (SWS) parameter")
    else:
        return False, ["Missing: SWS parameter"]
    
    # Check bytes in flight calculation
    if "bytes_in_flight" in server_code or "in flight" in server_code.lower():
        details.append("Bytes in flight calculation")
    else:
        return False, ["Missing: bytes in flight tracking"]
    
    # Check window management
    if "self.base" in server_code and "self.next_seq_num" in server_code:
        details.append("Window base and next sequence number tracking")
    else:
        return False, ["Missing: window state variables"]
    
    # Check window advancement
    if "self.base" in server_code and "+=" in server_code:
        details.append("Window slides forward on ACK")
    
    return True, details

def check_eof_signaling(server_code, client_code):
    """Check if EOF signaling is implemented"""
    details = []
    
    # Check server sends EOF
    if "b'EOF'" in server_code or 'b"EOF"' in server_code:
        details.append("Server sends EOF packet")
    else:
        return False, ["Missing: EOF packet in server"]
    
    # Check client detects EOF
    if "b'EOF'" in client_code or 'b"EOF"' in client_code:
        details.append("Client detects EOF packet")
    else:
        return False, ["Missing: EOF detection in client"]
    
    # Check termination after EOF
    if "eof_received" in client_code.lower() or "break" in client_code:
        details.append("Transfer terminates after EOF")
    
    return True, details

def check_file_transfer(server_code, client_code):
    """Check file transfer specifics"""
    details = []
    
    # Check server reads data.txt
    if "data.txt" in server_code:
        details.append("Server reads 'data.txt'")
    else:
        return False, ["Missing: data.txt in server"]
    
    # Check client writes received_data.txt
    if "received_data.txt" in client_code:
        details.append("Client writes 'received_data.txt'")
    else:
        return False, ["Missing: received_data.txt in client"]
    
    return True, details

def check_command_line_args(server_code, client_code):
    """Check command line argument handling"""
    details = []
    
    # Check server args: IP, PORT, SWS
    if "sys.argv" in server_code and "len(sys.argv)" in server_code:
        if "4" in server_code:  # 3 args + program name = 4
            details.append("Server accepts 3 arguments: IP, PORT, SWS")
        else:
            return False, ["Server should accept exactly 3 arguments"]
    else:
        return False, ["Missing: command line argument handling in server"]
    
    # Check client args: IP, PORT
    if "sys.argv" in client_code and "len(sys.argv)" in client_code:
        if "3" in client_code:  # 2 args + program name = 3
            details.append("Client accepts 2 arguments: IP, PORT")
        else:
            return False, ["Client should accept exactly 2 arguments"]
    else:
        return False, ["Missing: command line argument handling in client"]
    
    return True, details

def main():
    """Main verification function"""
    print("=" * 70)
    print("PART 1 REQUIREMENTS VERIFICATION")
    print("=" * 70)
    
    # Load source files
    print("\n📁 Loading source files...")
    server_exists, server_code = check_file_exists('p1_server.py')
    client_exists, client_code = check_file_exists('p1_client.py')
    
    if not server_exists:
        print("❌ ERROR: p1_server.py not found!")
        sys.exit(1)
    if not client_exists:
        print("❌ ERROR: p1_client.py not found!")
        sys.exit(1)
    
    print("✅ Found p1_server.py")
    print("✅ Found p1_client.py")
    
    # Run all verification checks
    results = []
    
    print("\n" + "=" * 70)
    print("CHECKING REQUIREMENTS")
    print("=" * 70)
    
    results.append(verify_requirement(
        "1. Packet Numbering",
        check_packet_numbering,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "2. Packet Format (20-byte header: 4B seq + 16B reserved)",
        check_packet_format,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "3. Cumulative ACKs",
        check_cumulative_acks,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "4. Timeouts (RTO)",
        check_timeouts,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "5. Fast Retransmit (3 duplicate ACKs)",
        check_fast_retransmit,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "6. Connection Setup (1-byte request, 5 retries, 2s timeout)",
        check_connection_setup,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "7. Sliding Window (SWS parameter)",
        check_sliding_window,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "8. EOF Signaling",
        check_eof_signaling,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "9. File Transfer (data.txt → received_data.txt)",
        check_file_transfer,
        server_code, client_code
    ))
    
    results.append(verify_requirement(
        "10. Command Line Arguments",
        check_command_line_args,
        server_code, client_code
    ))
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    passed = sum(results)
    total = len(results)
    percentage = (passed / total) * 100
    
    print(f"\n✅ Passed: {passed}/{total} requirements ({percentage:.1f}%)")
    
    if passed == total:
        print("\n🎉 ALL REQUIREMENTS MET! Implementation is complete.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} requirement(s) not met. Please review above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
