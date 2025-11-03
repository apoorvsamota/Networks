#!/usr/bin/env python3
"""
Debug script to identify where corruption occurs
"""

import sys

def analyze_files(original='data.txt', received='received_data.txt'):
    """Compare files byte by byte and report differences"""
    
    print("=" * 70)
    print("DETAILED FILE CORRUPTION ANALYSIS")
    print("=" * 70)
    
    try:
        with open(original, 'rb') as f1:
            orig_data = f1.read()
        with open(received, 'rb') as f2:
            recv_data = f2.read()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return
    
    print(f"\n📊 File Sizes:")
    print(f"   Original:  {len(orig_data):,} bytes")
    print(f"   Received:  {len(recv_data):,} bytes")
    
    if len(orig_data) != len(recv_data):
        print(f"\n❌ SIZE MISMATCH: Difference of {abs(len(orig_data) - len(recv_data)):,} bytes")
        if len(recv_data) < len(orig_data):
            print(f"   → File is TRUNCATED")
        else:
            print(f"   → File has EXTRA data")
        return
    
    print(f"   ✅ Sizes match")
    
    # Find all differences
    print(f"\n🔍 Comparing byte-by-byte...")
    differences = []
    for i in range(len(orig_data)):
        if orig_data[i] != recv_data[i]:
            differences.append(i)
    
    if not differences:
        print(f"   ✅ Files are IDENTICAL!")
        return
    
    print(f"\n❌ Found {len(differences):,} byte differences!")
    
    # Analyze difference patterns
    print(f"\n📍 First 10 corruption locations:")
    for i, pos in enumerate(differences[:10]):
        print(f"   Byte {pos:,}: orig=0x{orig_data[pos]:02x} recv=0x{recv_data[pos]:02x}")
    
    # Find contiguous corruption regions
    print(f"\n📊 Corruption Regions:")
    regions = []
    if differences:
        start = differences[0]
        end = differences[0]
        
        for diff in differences[1:]:
            if diff == end + 1:
                end = diff
            else:
                regions.append((start, end))
                start = diff
                end = diff
        regions.append((start, end))
    
    print(f"   Total regions: {len(regions)}")
    for i, (start, end) in enumerate(regions[:10]):
        length = end - start + 1
        print(f"   Region {i+1}: bytes {start:,} to {end:,} ({length:,} bytes)")
    
    # Check if corruption aligns with packet boundaries (1180 bytes)
    MSS = 1180
    print(f"\n🔍 Checking packet boundary alignment (MSS={MSS}):")
    
    for i, pos in enumerate(differences[:5]):
        packet_num = pos // MSS
        offset_in_packet = pos % MSS
        print(f"   Diff at byte {pos:,}: packet {packet_num}, offset {offset_in_packet}")
    
    # Check for patterns
    if len(differences) > 1:
        gaps = [differences[i+1] - differences[i] for i in range(min(100, len(differences)-1))]
        common_gap = max(set(gaps), key=gaps.count) if gaps else 0
        if common_gap > 1:
            print(f"\n🔍 Pattern detected:")
            print(f"   Most common gap between corruptions: {common_gap} bytes")
            if common_gap == MSS:
                print(f"   ⚠️  Gap matches MSS! Suggests whole packet corruption")
    
    # Sample corrupted data
    print(f"\n📄 Sample of first corruption:")
    if differences:
        pos = differences[0]
        start = max(0, pos - 20)
        end = min(len(orig_data), pos + 20)
        
        print(f"   Original  [{start:,}:{end:,}]:")
        print(f"      {orig_data[start:end][:40]}")
        print(f"   Received  [{start:,}:{end:,}]:")
        print(f"      {recv_data[start:end][:40]}")

if __name__ == "__main__":
    analyze_files()
