#!/usr/bin/env python3
"""
Simple test script to verify the client-server implementation
"""

import subprocess
import time
import hashlib
import os
import signal

def compute_md5(file_path):
    """Compute MD5 hash of a file"""
    hasher = hashlib.md5()
    try:
        with open(file_path, 'rb') as file:
            while chunk := file.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return None

def test_basic_transfer():
    """Test basic file transfer without network conditions"""
    print("=" * 60)
    print("TEST: Basic File Transfer (No Loss, No Delay)")
    print("=" * 60)
    
    SERVER_IP = "127.0.0.1"
    SERVER_PORT = 6555
    SWS = 5 * 1180  # 5 packets worth of data
    
    # Clean up any existing received file
    if os.path.exists('received_data.txt'):
        os.remove('received_data.txt')
    
    # Start server in background
    print("\n[TEST] Starting server...")
    server_proc = subprocess.Popen(
        ['python3', 'p1_server.py', SERVER_IP, str(SERVER_PORT), str(SWS)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Give server time to start
    time.sleep(0.5)
    
    # Start client
    print("[TEST] Starting client...")
    start_time = time.time()
    client_proc = subprocess.Popen(
        ['python3', 'p1_client.py', SERVER_IP, str(SERVER_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Wait for client to complete
    client_stdout, client_stderr = client_proc.communicate(timeout=30)
    end_time = time.time()
    
    # Wait for server to complete
    time.sleep(0.5)
    server_proc.terminate()
    server_stdout, server_stderr = server_proc.communicate()
    
    # Print outputs
    print("\n--- SERVER OUTPUT ---")
    print(server_stdout)
    if server_stderr:
        print("Server errors:", server_stderr)
    
    print("\n--- CLIENT OUTPUT ---")
    print(client_stdout)
    if client_stderr:
        print("Client errors:", client_stderr)
    
    # Verify transfer
    duration = end_time - start_time
    print(f"\n[TEST] Transfer completed in {duration:.2f} seconds")
    
    # Check file integrity
    if os.path.exists('received_data.txt'):
        original_md5 = compute_md5('data.txt')
        received_md5 = compute_md5('received_data.txt')
        
        print(f"[TEST] Original MD5:  {original_md5}")
        print(f"[TEST] Received MD5:  {received_md5}")
        
        if original_md5 == received_md5:
            print("[TEST] ✓ File transfer SUCCESSFUL - MD5 matches!")
            
            # Check file sizes
            orig_size = os.path.getsize('data.txt')
            recv_size = os.path.getsize('received_data.txt')
            print(f"[TEST] Original size: {orig_size} bytes")
            print(f"[TEST] Received size: {recv_size} bytes")
            
            return True
        else:
            print("[TEST] ✗ File transfer FAILED - MD5 mismatch!")
            return False
    else:
        print("[TEST] ✗ File transfer FAILED - received_data.txt not found!")
        return False

if __name__ == "__main__":
    # os.chdir('/home/claude')
    success = test_basic_transfer()
    exit(0 if success else 1)
