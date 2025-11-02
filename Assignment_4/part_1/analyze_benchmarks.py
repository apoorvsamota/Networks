#!/usr/bin/env python3
"""
Analysis script for Part 1 benchmarking results
Interprets part1.txt and compares with our implementation
"""

import matplotlib.pyplot as plt
import numpy as np

def parse_benchmark_data():
    """Parse the benchmark data from part1.txt"""
    
    # Loss experiment data (from part1.txt)
    loss_data = {
        'loss': [1, 2, 3, 4, 5],
        'delay': [20, 20, 20, 20, 20],
        'jitter': [0, 0, 0, 0, 0],
        'ttc': [53, 58, 63, 68, 77]  # Time to complete in seconds
    }
    
    # Jitter experiment data
    jitter_data = {
        'loss': [1, 1, 1, 1, 1],
        'delay': [20, 20, 20, 20, 20],
        'jitter': [20, 40, 60, 80, 100],
        'ttc': [55, 64, 77, 92, 103]
    }
    
    return loss_data, jitter_data

def calculate_throughput(file_size_bytes, time_seconds):
    """Calculate throughput in Mbps"""
    throughput_mbps = (file_size_bytes * 8) / (time_seconds * 1e6)
    return throughput_mbps

def analyze_loss_impact(loss_data, file_size=6463538):
    """Analyze the impact of packet loss on transfer time"""
    print("=" * 70)
    print("LOSS EXPERIMENT ANALYSIS")
    print("=" * 70)
    print(f"File size: {file_size / 1e6:.2f} MB\n")
    
    print("Loss Rate | Time (s) | Throughput (Mbps) | Overhead vs Baseline")
    print("-" * 70)
    
    baseline_time = loss_data['ttc'][0]
    
    for i in range(len(loss_data['loss'])):
        loss = loss_data['loss'][i]
        time_s = loss_data['ttc'][i]
        throughput = calculate_throughput(file_size, time_s)
        overhead = ((time_s - baseline_time) / baseline_time) * 100
        
        print(f"{loss:8}% | {time_s:8.1f} | {throughput:17.2f} | {overhead:+19.1f}%")
    
    # Calculate loss sensitivity
    time_increase = loss_data['ttc'][-1] - loss_data['ttc'][0]
    loss_increase = loss_data['loss'][-1] - loss_data['loss'][0]
    sensitivity = time_increase / loss_increase
    
    print(f"\nKey Insights:")
    print(f"- Baseline (1% loss): {baseline_time}s")
    print(f"- At 5% loss: {loss_data['ttc'][-1]}s ({((loss_data['ttc'][-1]/baseline_time - 1) * 100):.1f}% slower)")
    print(f"- Loss sensitivity: ~{sensitivity:.1f}s per 1% increase in loss")
    print(f"- The relationship appears roughly linear")

def analyze_jitter_impact(jitter_data, file_size=6463538):
    """Analyze the impact of delay jitter on transfer time"""
    print("\n\n" + "=" * 70)
    print("JITTER EXPERIMENT ANALYSIS")
    print("=" * 70)
    print(f"File size: {file_size / 1e6:.2f} MB\n")
    
    print("Jitter (ms) | Time (s) | Throughput (Mbps) | Overhead vs Baseline")
    print("-" * 70)
    
    baseline_time = jitter_data['ttc'][0]
    
    for i in range(len(jitter_data['jitter'])):
        jitter = jitter_data['jitter'][i]
        time_s = jitter_data['ttc'][i]
        throughput = calculate_throughput(file_size, time_s)
        overhead = ((time_s - baseline_time) / baseline_time) * 100
        
        print(f"{jitter:11} | {time_s:8.1f} | {throughput:17.2f} | {overhead:+19.1f}%")
    
    # Calculate jitter sensitivity
    time_increase = jitter_data['ttc'][-1] - jitter_data['ttc'][0]
    jitter_increase = jitter_data['jitter'][-1] - jitter_data['jitter'][0]
    sensitivity = time_increase / jitter_increase
    
    print(f"\nKey Insights:")
    print(f"- Baseline (20ms jitter): {baseline_time}s")
    print(f"- At 100ms jitter: {jitter_data['ttc'][-1]}s ({((jitter_data['ttc'][-1]/baseline_time - 1) * 100):.1f}% slower)")
    print(f"- Jitter sensitivity: ~{sensitivity:.2f}s per 1ms increase in jitter")
    print(f"- Impact is super-linear (gets worse as jitter increases)")

def plot_results(loss_data, jitter_data):
    """Create plots for the benchmark results"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    
    # Loss plot
    ax1.plot(loss_data['loss'], loss_data['ttc'], 'bo-', linewidth=2, markersize=8)
    ax1.set_xlabel('Packet Loss Rate (%)', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Transfer Time (seconds)', fontsize=12, fontweight='bold')
    ax1.set_title('Impact of Packet Loss on Transfer Time', fontsize=14, fontweight='bold')
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(0, 6)
    
    # Add trend line
    z = np.polyfit(loss_data['loss'], loss_data['ttc'], 1)
    p = np.poly1d(z)
    ax1.plot(loss_data['loss'], p(loss_data['loss']), "r--", alpha=0.5, label='Linear trend')
    ax1.legend()
    
    # Jitter plot
    ax2.plot(jitter_data['jitter'], jitter_data['ttc'], 'ro-', linewidth=2, markersize=8)
    ax2.set_xlabel('Delay Jitter (ms)', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Transfer Time (seconds)', fontsize=12, fontweight='bold')
    ax2.set_title('Impact of Delay Jitter on Transfer Time', fontsize=14, fontweight='bold')
    ax2.grid(True, alpha=0.3)
    ax2.set_xlim(10, 110)
    
    plt.tight_layout()
    plt.savefig('part1_benchmark_analysis.png', dpi=150, bbox_inches='tight')
    print(f"\n\n[PLOT] Saved analysis plot to 'part1_benchmark_analysis.png'")
    
    return fig

def performance_targets():
    """Define performance targets based on benchmark"""
    print("\n\n" + "=" * 70)
    print("PERFORMANCE TARGETS FOR IMPLEMENTATION")
    print("=" * 70)
    print("\nBased on the benchmark data, here are reasonable targets:\n")
    
    print("Target Performance (to be in top 50%):")
    print("- 1% loss, 20ms delay: < 53 seconds")
    print("- 3% loss, 20ms delay: < 63 seconds")
    print("- 5% loss, 20ms delay: < 77 seconds")
    print("- 1% loss, 20ms delay, 60ms jitter: < 77 seconds")
    print("- 1% loss, 20ms delay, 100ms jitter: < 103 seconds")
    
    print("\n\nStretch Goals (to be in top 25%):")
    print("- 1% loss, 20ms delay: < 45 seconds")
    print("- 5% loss, 20ms delay: < 65 seconds")
    print("- 1% loss, 20ms delay, 100ms jitter: < 90 seconds")
    
    print("\n\nKey Optimization Areas:")
    print("1. Fast Retransmit: Minimize timeout-based retransmissions")
    print("2. Adaptive RTO: Tune RTT estimation parameters for jitter resilience")
    print("3. Window Size: Find optimal SWS for different conditions")
    print("4. Selective ACK: Use reserved header bytes for better loss recovery")

def main():
    """Main analysis function"""
    loss_data, jitter_data = parse_benchmark_data()
    
    analyze_loss_impact(loss_data)
    analyze_jitter_impact(jitter_data)
    performance_targets()
    
    # Try to create plots (may fail if matplotlib not available)
    try:
        plot_results(loss_data, jitter_data)
    except Exception as e:
        print(f"\n\n[WARNING] Could not create plots: {e}")
        print("Install matplotlib to generate plots: pip install matplotlib")

if __name__ == "__main__":
    main()
