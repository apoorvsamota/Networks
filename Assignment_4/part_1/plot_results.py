#!/usr/bin/env python3
"""
Plot experimental results with 90% confidence intervals
Reads reliability_loss.csv and reliability_jitter.csv
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats
import sys
import os

def calculate_confidence_interval(data, confidence=0.90):
    """Calculate mean and confidence interval"""
    n = len(data)
    if n < 2:
        return np.mean(data), 0, 0
    
    mean = np.mean(data)
    std_err = stats.sem(data)  # Standard error of mean
    margin = std_err * stats.t.ppf((1 + confidence) / 2, n - 1)  # t-distribution
    
    return mean, mean - margin, mean + margin

def plot_loss_experiment(csv_file='reliability_loss.csv', output_file='loss_plot.png'):
    """Plot loss experiment results with 90% CI"""
    
    # Check if file exists
    if not os.path.exists(csv_file):
        print(f"❌ File not found: {csv_file}")
        return False
    
    # Read data
    df = pd.read_csv(csv_file)
    
    # Filter out failed transfers (wrong MD5)
    correct_md5 = 'cc83be85db391e9396e1427b3e124968'
    df_filtered = df[df['md5_hash'] == correct_md5].copy()
    
    failed_count = len(df) - len(df_filtered)
    if failed_count > 0:
        print(f"⚠️  Warning: {failed_count} transfers had incorrect MD5 (filtered out)")
    
    # Group by loss rate and calculate statistics
    loss_rates = sorted(df_filtered['loss'].unique())
    means = []
    lower_bounds = []
    upper_bounds = []
    
    print("\n" + "=" * 70)
    print("LOSS EXPERIMENT RESULTS")
    print("=" * 70)
    print(f"{'Loss Rate':<12} {'Mean (s)':<12} {'Std Dev':<12} {'90% CI':<25}")
    print("-" * 70)
    
    for loss in loss_rates:
        times = df_filtered[df_filtered['loss'] == loss]['ttc'].values
        mean, lower, upper = calculate_confidence_interval(times, confidence=0.90)
        
        means.append(mean)
        lower_bounds.append(lower)
        upper_bounds.append(upper)
        
        std_dev = np.std(times, ddof=1)
        ci_range = upper - lower
        
        print(f"{loss:>10}% {mean:>11.2f} {std_dev:>11.2f} [{lower:.2f}, {upper:.2f}]")
    
    # Create plot
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Plot with error bars
    ax.errorbar(loss_rates, means, 
                yerr=[np.array(means) - np.array(lower_bounds), 
                      np.array(upper_bounds) - np.array(means)],
                fmt='o-', linewidth=2, markersize=8, capsize=5, capthick=2,
                color='#2E86AB', ecolor='#A23B72', label='Experimental Results')
    
    # Add benchmark comparison (optional)
    benchmark_loss = [1, 2, 3, 4, 5]
    benchmark_times = [53, 58, 63, 68, 77]
    ax.plot(benchmark_loss, benchmark_times, 's--', linewidth=2, markersize=7,
            color='#F18F01', alpha=0.7, label='Benchmark')
    
    # Labels and formatting
    ax.set_xlabel('Packet Loss Rate (%)', fontsize=13, fontweight='bold')
    ax.set_ylabel('Download Time (seconds)', fontsize=13, fontweight='bold')
    ax.set_title('Impact of Packet Loss on Transfer Time\n(with 90% Confidence Intervals)', 
                 fontsize=14, fontweight='bold')
    ax.legend(fontsize=11, loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.set_xlim(0, 6)
    
    # Save
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"\n✅ Plot saved to: {output_file}")
    
    return True

def plot_jitter_experiment(csv_file='reliability_jitter.csv', output_file='jitter_plot.png'):
    """Plot jitter experiment results with 90% CI"""
    
    # Check if file exists
    if not os.path.exists(csv_file):
        print(f"❌ File not found: {csv_file}")
        print(f"   (Run: sudo python3 p1_exp.py jitter)")
        return False
    
    # Read data
    df = pd.read_csv(csv_file)
    
    # Filter out failed transfers
    correct_md5 = 'cc83be85db391e9396e1427b3e124968'
    df_filtered = df[df['md5_hash'] == correct_md5].copy()
    
    failed_count = len(df) - len(df_filtered)
    if failed_count > 0:
        print(f"⚠️  Warning: {failed_count} transfers had incorrect MD5 (filtered out)")
    
    # Group by jitter and calculate statistics
    jitter_values = sorted(df_filtered['jitter'].unique())
    means = []
    lower_bounds = []
    upper_bounds = []
    
    print("\n" + "=" * 70)
    print("JITTER EXPERIMENT RESULTS")
    print("=" * 70)
    print(f"{'Jitter (ms)':<12} {'Mean (s)':<12} {'Std Dev':<12} {'90% CI':<25}")
    print("-" * 70)
    
    for jitter in jitter_values:
        times = df_filtered[df_filtered['jitter'] == jitter]['ttc'].values
        mean, lower, upper = calculate_confidence_interval(times, confidence=0.90)
        
        means.append(mean)
        lower_bounds.append(lower)
        upper_bounds.append(upper)
        
        std_dev = np.std(times, ddof=1)
        
        print(f"{jitter:>10} {mean:>11.2f} {std_dev:>11.2f} [{lower:.2f}, {upper:.2f}]")
    
    # Create plot
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Plot with error bars
    ax.errorbar(jitter_values, means,
                yerr=[np.array(means) - np.array(lower_bounds),
                      np.array(upper_bounds) - np.array(means)],
                fmt='o-', linewidth=2, markersize=8, capsize=5, capthick=2,
                color='#C73E1D', ecolor='#6A994E', label='Experimental Results')
    
    # Add benchmark comparison (optional)
    benchmark_jitter = [20, 40, 60, 80, 100]
    benchmark_times = [55, 64, 77, 92, 103]
    ax.plot(benchmark_jitter, benchmark_times, 's--', linewidth=2, markersize=7,
            color='#F18F01', alpha=0.7, label='Benchmark')
    
    # Labels and formatting
    ax.set_xlabel('Delay Jitter (ms)', fontsize=13, fontweight='bold')
    ax.set_ylabel('Download Time (seconds)', fontsize=13, fontweight='bold')
    ax.set_title('Impact of Delay Jitter on Transfer Time\n(with 90% Confidence Intervals)',
                 fontsize=14, fontweight='bold')
    ax.legend(fontsize=11, loc='upper left')
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.set_xlim(10, 110)
    
    # Save
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"\n✅ Plot saved to: {output_file}")
    
    return True

def compare_with_benchmark():
    """Compare your results with benchmark"""
    
    print("\n" + "=" * 70)
    print("COMPARISON WITH BENCHMARK")
    print("=" * 70)
    
    # Loss comparison
    if os.path.exists('reliability_loss.csv'):
        df = pd.read_csv('reliability_loss.csv')
        correct_md5 = 'cc83be85db391e9396e1427b3e124968'
        df_filtered = df[df['md5_hash'] == correct_md5]
        
        benchmark = {1: 53, 2: 58, 3: 63, 4: 68, 5: 77}
        
        print("\nLoss Experiment:")
        print(f"{'Loss':<8} {'Your Avg':<12} {'Benchmark':<12} {'Difference':<15}")
        print("-" * 50)
        
        for loss in sorted(df_filtered['loss'].unique()):
            times = df_filtered[df_filtered['loss'] == loss]['ttc'].values
            your_avg = np.mean(times)
            bench = benchmark.get(loss, 0)
            diff = your_avg - bench
            diff_pct = (diff / bench * 100) if bench > 0 else 0
            
            status = "🚀 FASTER" if diff < 0 else "🐌 SLOWER"
            print(f"{loss}%      {your_avg:>10.2f}s {bench:>10}s {diff:+10.2f}s ({diff_pct:+.1f}%) {status}")
    
    # Jitter comparison
    if os.path.exists('reliability_jitter.csv'):
        df = pd.read_csv('reliability_jitter.csv')
        correct_md5 = 'cc83be85db391e9396e1427b3e124968'
        df_filtered = df[df['md5_hash'] == correct_md5]
        
        benchmark = {20: 55, 40: 64, 60: 77, 80: 92, 100: 103}
        
        print("\nJitter Experiment:")
        print(f"{'Jitter':<8} {'Your Avg':<12} {'Benchmark':<12} {'Difference':<15}")
        print("-" * 50)
        
        for jitter in sorted(df_filtered['jitter'].unique()):
            times = df_filtered[df_filtered['jitter'] == jitter]['ttc'].values
            your_avg = np.mean(times)
            bench = benchmark.get(jitter, 0)
            diff = your_avg - bench
            diff_pct = (diff / bench * 100) if bench > 0 else 0
            
            status = "🚀 FASTER" if diff < 0 else "🐌 SLOWER"
            print(f"{jitter}ms    {your_avg:>10.2f}s {bench:>10}s {diff:+10.2f}s ({diff_pct:+.1f}%) {status}")

def main():
    """Main function"""
    print("=" * 70)
    print("PLOTTING EXPERIMENTAL RESULTS")
    print("=" * 70)
    
    # Plot loss experiment
    if os.path.exists('reliability_loss.csv'):
        print("\n📊 Processing loss experiment...")
        plot_loss_experiment()
    else:
        print("\n⚠️  reliability_loss.csv not found")
        print("   Run: sudo python3 p1_exp.py loss")
    
    # Plot jitter experiment
    if os.path.exists('reliability_jitter.csv'):
        print("\n📊 Processing jitter experiment...")
        plot_jitter_experiment()
    else:
        print("\n⚠️  reliability_jitter.csv not found")
        print("   Run: sudo python3 p1_exp.py jitter")
    
    # Comparison
    compare_with_benchmark()
    
    print("\n" + "=" * 70)
    print("✅ DONE!")
    print("=" * 70)
    print("\nGenerated files:")
    if os.path.exists('loss_plot.png'):
        print("  - loss_plot.png")
    if os.path.exists('jitter_plot.png'):
        print("  - jitter_plot.png")

if __name__ == "__main__":
    main()
