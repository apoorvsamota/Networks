# SDN Implementation for Part 4 - Usage Guide

## Files Created

1. **p4_l3spf_lf.py** - Ryu controller with link failure detection and recovery
2. **p4_sdn_topo.py** - Mininet topology with OVS switches
3. **p4_sdn_runner.py** - Experiment runner with comparison graphs

## Key Features Implemented

### Controller (p4_l3spf_lf.py)
- **Link Failure Detection**: Automatically detects when links go down using Ryu's topology discovery
- **Dynamic Rerouting**: Recalculates shortest paths using Dijkstra when topology changes
- **Flow Reinstallation**: Clears and reinstalls all flows when topology changes
- **L3 Routing**: Rewrites Ethernet headers for inter-subnet communication
- **TTL Decrement**: Decrements TTL like a real router
- **Logging**: Logs link up/down events with timestamps for convergence analysis

### Topology (p4_sdn_topo.py)
- Uses **OVS switches** instead of Linux routers
- Same ring topology as OSPF setup
- Bandwidth settings: s2-s3 = 100 Mbps (primary), s5-s4 = 10 Mbps (backup)
- Remote controller connection on port 6653

### Runner (p4_sdn_runner.py)
- Starts Ryu controller automatically
- Runs iperf test with link failure simulation
- Generates logs: `h1_iperf_sdn.log` and `h2_iperf_sdn.log`
- Parses both SDN and OSPF logs
- **Generates comparison graph** with both curves
- Calculates convergence times automatically

## How to Run

### Step 1: Run OSPF Experiment First
```bash
# This creates h1_iperf.log for comparison
sudo python3 p4_runner.py --input-file p4_config.json --no-cli
```

### Step 2: Run SDN Experiment
```bash
# Make sure you're in the same directory as all files
sudo python3 p4_sdn_runner.py --ospf-log h1_iperf.log
```

### Step 3: View Results
The script will automatically:
- Create `h1_iperf_sdn.log` (client log)
- Create `h2_iperf_sdn.log` (server log)  
- Generate `throughput_comparison.png` with both SDN and OSPF curves
- Print convergence times for both approaches

## Command Line Options

```bash
sudo python3 p4_sdn_runner.py --help
```

Available options:
- `--no-cli`: Skip Mininet CLI after test
- `--iperf-time`: Duration of iperf test (default: 30s)
- `--link-down-duration`: How long link stays down (default: 5s)
- `--wait-before-down`: Wait before failure (default: 2s)
- `--ospf-log`: Path to OSPF log for comparison (default: h1_iperf.log)
- `--controller-ip`: Controller IP (default: 127.0.0.1)
- `--controller-port`: Controller port (default: 6653)

## Understanding the Output

### Console Output
```
*** SDN Convergence Times:
    After link down: 0.50s
    After link up: 0.30s

*** OSPF Convergence Times:
    After link down: 2.10s
    After link up: 1.80s
```

### Graph (throughput_comparison.png)
- **Blue line**: SDN controller performance
- **Red line**: OSPF performance
- **Gray dashed line**: Link down event (at 2s)
- **Green dashed line**: Link up event (at 7s)

### Log Files
- `h1_iperf_sdn.log`: Client-side iperf output with per-second throughput
- `h2_iperf_sdn.log`: Server-side iperf output

## Inspecting Flow Rules

If you use the CLI (without `--no-cli`):
```bash
# View flows on switch 1
s1 ovs-ofctl dump-flows s1 -O OpenFlow13

# View flows on switch 2
s2 ovs-ofctl dump-flows s2 -O OpenFlow13

# Test connectivity
h1 ping -c 3 h2
```

## How Link Failure Detection Works

1. **Normal Operation**: Traffic flows s1 → s2 → s3 → s6 (shortest path)
2. **Link Down**: s1-s2 link goes down at 2 seconds
3. **Detection**: Ryu's topology discovery detects the link failure
4. **Rerouting**: Controller removes old flows and calculates new path
5. **New Path**: s1 → s4 → s5 → s6 (alternate path via s4-s5)
6. **Link Up**: s1-s2 link comes back at 7 seconds
7. **Recovery**: Controller detects link up and reverts to shortest path

## Convergence Time Analysis

The script automatically calculates:
- **Link Down Convergence**: Time from link failure until throughput recovers
- **Link Up Convergence**: Time from link recovery until throughput stabilizes

These times are inferred from the iperf throughput measurements where:
- Link down occurs at 2 seconds
- Link up occurs at 7 seconds
- Convergence threshold is 50 Mbps

## Troubleshooting

### Issue: Controller not starting
```bash
# Check if Ryu is installed
ryu-manager --version

# Install if needed
pip3 install ryu
```

### Issue: No connectivity between h1 and h2
```bash
# Check if controller discovered all switches
# Look for "Register datapath" messages in controller output
```

### Issue: Graph not generated
```bash
# Install matplotlib if needed
pip3 install matplotlib

# Check if log files exist
ls -l h1_iperf*.log h2_iperf*.log
```

### Issue: "No route to host"
```bash
# Wait longer for topology discovery
# Increase sleep time in runner after building network
```

## Differences from OSPF Setup

| Aspect | OSPF | SDN |
|--------|------|-----|
| **Switches** | Linux routers (Mininet hosts) | OVS switches |
| **Control Plane** | Distributed (OSPF daemons) | Centralized (Ryu controller) |
| **Failure Detection** | OSPF Hello packets | Topology discovery |
| **Rerouting** | Each router computes independently | Controller computes and pushes rules |
| **Convergence** | Slower (protocol overhead) | Faster (centralized control) |

## Expected Results

Based on the bandwidth configuration:
- **Primary path** (s1→s2→s3→s6): High throughput (~90-95 Mbps)
- **Backup path** (s1→s4→s5→s6): Low throughput (~8-9 Mbps due to 10 Mbps link)
- **SDN convergence**: Typically < 1 second
- **OSPF convergence**: Typically 1-3 seconds

The graph should clearly show:
1. High throughput initially
2. Drop to near-zero during convergence
3. Low throughput on backup path
4. Another drop during recovery
5. High throughput restored on primary path
