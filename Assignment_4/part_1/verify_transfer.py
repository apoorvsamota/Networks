#!/usr/bin/env python3
"""
Simple test to verify file transfer correctness
"""

import hashlib
import os
import sys

def compute_md5(filepath):
    """Compute MD5 hash of a file"""
    hasher = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        return None

def main():
    original = "data.txt"
    received = "received_data.txt"
    
    print("=" * 60)
    print("FILE TRANSFER VERIFICATION")
    print("=" * 60)
    
    # Check if files exist
    if not os.path.exists(original):
        print(f"❌ ERROR: {original} not found!")
        return False
    
    if not os.path.exists(received):
        print(f"❌ ERROR: {received} not found!")
        print("   (Transfer may not have completed)")
        return False
    
    # Get file sizes
    orig_size = os.path.getsize(original)
    recv_size = os.path.getsize(received)
    
    print(f"\n📁 File Sizes:")
    print(f"   Original:  {orig_size:,} bytes")
    print(f"   Received:  {recv_size:,} bytes")
    
    if orig_size != recv_size:
        print(f"\n❌ SIZE MISMATCH!")
        print(f"   Difference: {abs(orig_size - recv_size):,} bytes")
        return False
    else:
        print(f"   ✅ Sizes match")
    
    # Compute MD5 hashes
    print(f"\n🔐 Computing MD5 hashes...")
    orig_md5 = compute_md5(original)
    recv_md5 = compute_md5(received)
    
    print(f"   Original:  {orig_md5}")
    print(f"   Received:  {recv_md5}")
    
    # Compare
    if orig_md5 == recv_md5:
        print(f"\n✅ SUCCESS! Files are identical!")
        print(f"   Transfer was 100% correct")
        return True
    else:
        print(f"\n❌ CORRUPTION DETECTED!")
        print(f"   MD5 hashes do not match")
        print(f"   Data was corrupted during transfer")
        return False

if __name__ == "__main__":
    success = main()
    print("=" * 60)
    sys.exit(0 if success else 1)
