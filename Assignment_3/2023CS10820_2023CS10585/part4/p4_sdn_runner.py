#!/usr/bin/env python3
# p4_sdn_runner.py - SDN experiment runner (iperf-only analysis)

import argparse
import time
import re
import os
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from p4_sdn_topo import build_sdn, H1_IP, H2_IP
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# (Removed start_ryu_controller)
# (Removed stop_ryu_controller)

def if_down_up(net, s_i, s_j, i_if, j_if, down=True):
    """Bring both sides of a switch-switch link down/up"""
    si, sj = net.get(s_i), net.get(s_j)
    action = "down" if down else "up"
    si.cmd(f"ip link set {i_if} {action}")
    sj.cmd(f"ip link set {j_if} {action}")

def start_iperf(h1, h2, h2_ip, total_seconds, prefer_iperf3=True):
    """Start iperf server on h2 and client on h1"""
    s_log = "h2_iperf_sdn.log"
    c_log = "h1_iperf_sdn.log"
    
    # Check for iperf3
    have_iperf3 = prefer_iperf3 and ("iperf3" in h1.cmd("which iperf3"))
    
    if have_iperf3:
        h2.cmd(f"iperf3 -s -1 > {s_log} 2>&1 &")
        time.sleep(0.5)
        ip = h2_ip.split("/")[0]
        h1.cmd(f"iperf3 -c {ip} -t {int(total_seconds)} -i 1 > {c_log} 2>&1 &")
    else:
        h2.cmd(f"iperf -s > {s_log} 2>&1 &")
        time.sleep(0.5)
        ip = h2_ip.split("/")[0]
        h1.cmd(f"iperf -c {ip} -t {int(total_seconds)} -i 1 > {c_log} 2>&1 &")
    
    return s_log, c_log

def link_flap_exp(net, s_i, s_j, i_if, j_if, iperf_time=30, 
                  link_down_duration=5, wait_before_link_down=2):
    """Run iperf with link failure experiment"""
    h1, h2 = net.get("h1"), net.get("h2")
    
    # Start iperf
    s_log, c_log = start_iperf(h1, h2, H2_IP, iperf_time)
    print(f"*** SDN iperf running: client log {c_log}, server log {s_log}")
    
    # Wait before causing failure
    time.sleep(wait_before_link_down)
    
    # Bring link down
    print(f"*** SDN DOWN {s_i}:{i_if} <-> {s_j}:{j_if} for {link_down_duration}s")
    if_down_up(net, s_i, s_j, i_if, j_if, down=True)
    
    # Wait during failure
    time.sleep(link_down_duration)
    
    # Bring link back up
    print(f"*** SDN UP   {s_i}:{i_if} <-> {s_j}:{j_if}")
    if_down_up(net, s_i, s_j, i_if, j_if, down=False)
    
    # Wait for iperf to finish
    remaining_time = iperf_time - link_down_duration - wait_before_link_down + 5
    print(f"*** Waiting {remaining_time}s for iperf to finish...")
    time.sleep(remaining_time)
    
    # Get logs
    c_out = h1.cmd(f"cat {c_log} || true")
    s_out = h2.cmd(f"cat {s_log} || true")
    
    return c_log, s_log, c_out, s_out

def parse_iperf_log(log_content, use_iperf3=True):
    """Parse iperf output to extract per-second throughput"""
    throughputs = []
    
    if use_iperf3:
        # iperf3 format: [ ID] Interval           Transfer     Bitrate         Retr
        # [  5]   0.00-1.00   sec  11.2 MBytes  94.4 Mbits/sec    0
        pattern = r'\[\s*\d+\]\s+(\d+\.\d+)-\s*(\d+\.\d+)\s+sec.*?(\d+\.?\d*)\s+([KMG]?)bits/sec'
    else:
        # iperf format: [ ID] Interval       Transfer     Bandwidth
        # [  3]  0.0- 1.0 sec  12.5 MBytes  105 Mbits/sec
        pattern = r'\[\s*\d+\]\s+(\d+\.\d+)-\s*(\d+\.\d+)\s+sec.*?(\d+\.?\d*)\s+([KMG]?)bits/sec'
    
    for line in log_content.split('\n'):
        match = re.search(pattern, line)
        if match:
            start_time = float(match.group(1))
            end_time = float(match.group(2))
            throughput = float(match.group(3))
            unit = match.group(4)
            
            if unit == 'G':
                throughput *= 1000
            elif unit == 'K':
                throughput /= 1000
            
            throughputs.append((end_time, throughput))
    
    return throughputs

def plot_comparison(sdn_data, ospf_data, down_time_s, up_time_s, output_file='throughput_comparison.png'):
    """Plot throughput comparison between SDN and OSPF"""
    fig, ax = plt.subplots(figsize=(12, 6))
    
    if sdn_data:
        sdn_times, sdn_throughputs = zip(*sdn_data)
        ax.plot(sdn_times, sdn_throughputs, 'b-o', label='SDN Controller', linewidth=2, markersize=4)
    
    if ospf_data:
        ospf_times, ospf_throughputs = zip(*ospf_data)
        ax.plot(ospf_times, ospf_throughputs, 'r-s', label='OSPF', linewidth=2, markersize=4)
    
    ax.set_xlabel('Time (seconds)', fontsize=12)
    ax.set_ylabel('Throughput (Mbits/sec)', fontsize=12)
    ax.set_title('SDN vs OSPF Throughput During Link Failure', fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=10)
    ax.grid(True, alpha=0.3)
    
    # Add annotations for link down/up events
    ax.axvline(x=down_time_s, color='gray', linestyle='--', alpha=0.5, label='Link Down')
    ax.axvline(x=up_time_s, color='green', linestyle='--', alpha=0.5, label='Link Up')
    ax.text(down_time_s, ax.get_ylim()[1] * 0.95, 'Link Down', rotation=90, 
            verticalalignment='top', fontsize=9, color='gray')
    ax.text(up_time_s, ax.get_ylim()[1] * 0.95, 'Link Up', rotation=90,
            verticalalignment='top', fontsize=9, color='green')
    
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"\n*** Comparison graph saved to {output_file}")
    plt.close()

def calculate_convergence_time(throughput_data, failure_time=2, recovery_time=7, down_threshold=5, up_threshold=50):
    """
    Calculate convergence time after link failure and recovery based on iperf throughput.
    """
    down_convergence = None
    up_convergence = None
    
    # Find convergence time after link goes DOWN
    for time, throughput in throughput_data:
        if time > failure_time and throughput > down_threshold:
            down_convergence = time - failure_time
            break
            
    # Find convergence time after link comes back UP
    for time, throughput in throughput_data:
        if time > recovery_time and throughput > up_threshold:
            up_convergence = time - recovery_time
            break

    return down_convergence, up_convergence

# (Removed parse_sdn_controller_log)
# (Removed parse_ospf_pcap)

def main():
    print("--- SDN vs OSPF Experiment Runner ---")
    
    parser = argparse.ArgumentParser(description="SDN Link Failure Experiment with Comparison")
    parser.add_argument("--no-cli", action="store_true", help="Exit after test (no Mininet CLI)")
    parser.add_argument("--iperf-time", type=int, default=30, help="Duration of iperf test")
    parser.add_argument("--link-down-duration", type=int, default=5, help="How long link stays down")
    parser.add_argument("--wait-before-down", type=int, default=2, help="Wait time before bringing link down")
    parser.add_argument("--ospf-log", default="h1_iperf.log", help="Path to OSPF iperf log for comparison")
    parser.add_argument("--controller-ip", default="127.0.0.1", help="Controller IP address")
    parser.add_argument("--controller-port", type=int, default=6653, help="Controller port")
    args = parser.parse_args()

    # (Ryu start/stop calls are commented out, as you run it in a separate terminal)
    # ryu_proc, ryu_log_handle = start_ryu_controller(args.ryu_log) 
    
    try:
        # Build topology
        net = build_sdn(controller_ip=args.controller_ip, controller_port=args.controller_port)
        
        # Test connectivity
        print("\n*** Testing initial connectivity")
        h1, h2 = net.get('h1'), net.get('h2')
        result = h1.cmd(f'ping -c 3 {H2_IP.split("/")[0]}')
        print(result)
        
        # Run link flap experiment
        print("\n*** Starting link failure experiment")
        print("*** Link to fail: s1 <-> s2 (s1-eth2 <-> s2-eth1)")
        
        c_log, s_log, c_out, s_out = link_flap_exp(
            net, 
            s_i="s1", s_j="s2", 
            i_if="s1-eth2", j_if="s2-eth1",
            iperf_time=args.iperf_time,
            link_down_duration=args.link_down_duration,
            wait_before_link_down=args.wait_before_down
        )
        
        print("\n==== SDN iperf CLIENT (h1) ====")
        print(c_out)
        print("\n==== SDN iperf SERVER (h2) ====")
        print(s_out)
        
        # Parse SDN results
        print("\n*** Parsing SDN iperf results...")
        sdn_data = parse_iperf_log(c_out, use_iperf3=True)
        if not sdn_data:
            sdn_data = parse_iperf_log(c_out, use_iperf3=False)
        
        # Load and parse OSPF results if available
        ospf_data = []
        if os.path.exists(args.ospf_log):
            print(f"*** Loading OSPF results from {args.ospf_log}...")
            with open(args.ospf_log, 'r') as f:
                ospf_content = f.read()
                ospf_data = parse_iperf_log(ospf_content, use_iperf3=True)
                if not ospf_data:
                    ospf_data = parse_iperf_log(ospf_content, use_iperf3=False)
        else:
            print(f"*** OSPF iperf log not found at {args.ospf_log}, skipping comparison")
        
        
        # Calculate event times
        down_time_s = args.wait_before_down
        up_time_s = args.wait_before_down + args.link_down_duration
        
        # Calculate convergence times from iperf
        if sdn_data:
            sdn_down_iperf_s, sdn_up_iperf_s = calculate_convergence_time(
                sdn_data, 
                failure_time=down_time_s,
                recovery_time=up_time_s
            )
            print(f"\n*** SDN Convergence Times (Data Plane from iperf):") 
            print(f"    After link down: {sdn_down_iperf_s:.2f}s" if sdn_down_iperf_s else "    After link down: N/A")
            print(f"    After link up: {sdn_up_iperf_s:.2f}s" if sdn_up_iperf_s else "    After link up: N/A")
            
        if ospf_data:
            ospf_down_iperf_s, ospf_up_iperf_s = calculate_convergence_time(
                ospf_data,
                failure_time=down_time_s,
                recovery_time=up_time_s
            )
            print(f"\n*** OSPF Convergence Times (Data Plane from iperf):") 
            print(f"    After link down: {ospf_down_iperf_s:.2f}s" if ospf_down_iperf_s else "    After link down: N/A")
            print(f"    After link up: {ospf_up_iperf_s:.2f}s" if ospf_up_iperf_s else "    After link up: N/A")
        
        # Generate comparison plot
        if sdn_data or ospf_data:
            print("\n*** Generating throughput comparison graph...")
            plot_comparison(sdn_data, ospf_data, down_time_s, up_time_s)
        
        # Optional CLI
        if not args.no_cli:
            print("\n*** Entering CLI for inspection")
            print("*** Useful commands:")
            print("    h1 ping -c 3 h2")
            print("    s1 ovs-ofctl dump-flows s1 -O OpenFlow13")
            print("    s2 ovs-ofctl dump-flows s2 -O OpenFlow13")
            CLI(net)
        
    finally:
        # Cleanup
        print("\n*** Cleaning up...")
        try:
            net.stop()
        except:
            pass
        # stop_ryu_controller(ryu_proc, ryu_log_handle) 

if __name__ == "__main__":
    setLogLevel('info')
    main()