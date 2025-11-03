# Part 1 vs Part 2: What Changed?

## Overview

This document highlights the key differences between Part 1 (Reliability) and Part 2 (Congestion Control) implementations.

---

## Command Line Arguments

### Part 1:
```bash
# Server
python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>
                                                  ^^^
                                        Fixed window size

# Client
python3 p1_client.py <SERVER_IP> <SERVER_PORT>
```

### Part 2:
```bash
# Server
python3 p2_server.py <SERVER_IP> <SERVER_PORT>
                                  (no SWS - dynamic cwnd)

# Client
python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>
                                                ^^^^^^^^^^^^^^
                                              New parameter!
```

---

## Core Differences

### Window Management

**Part 1 (Fixed Window):**
```python
class ReliableUDPServer:
    def __init__(self, ip, port, sws):
        self.sws = sws  # Fixed sender window size
        
    def send_packets(self):
        while self.next_seq < file_size:
            bytes_in_flight = self.next_seq - self.base
            if bytes_in_flight >= self.sws:  # Fixed limit
                break
            # Send packet
```

**Part 2 (Dynamic Window):**
```python
class CongestionControlServer:
    def __init__(self, ip, port):
        self.cwnd = MSS           # Dynamic congestion window
        self.ssthresh = 64 * MSS  # Slow start threshold
        self.state = SLOW_START   # CC state
        
    def send_packets(self):
        while self.next_seq < file_size:
            bytes_in_flight = self.next_seq - self.base
            if bytes_in_flight >= self.cwnd:  # Dynamic limit
                break
            # Send packet
```

---

## Key New Components in Part 2

### 1. Congestion Control States

```python
SLOW_START = 0           # Exponential growth
CONGESTION_AVOIDANCE = 1 # Linear growth
FAST_RECOVERY = 2        # Loss recovery
```

### 2. Window Update Functions

```python
def on_new_ack(self, bytes_acked):
    """Update cwnd based on current state"""
    if self.state == SLOW_START:
        self.cwnd += bytes_acked  # Exponential
    elif self.state == CONGESTION_AVOIDANCE:
        # Linear growth
        self.bytes_acked_ca += bytes_acked
        if self.bytes_acked_ca >= self.cwnd:
            self.cwnd += MSS
            self.bytes_acked_ca = 0

def on_fast_retransmit(self):
    """Enter fast recovery"""
    self.ssthresh = max(self.cwnd // 2, 2 * MSS)
    self.cwnd = self.ssthresh + 3 * MSS
    self.state = FAST_RECOVERY

def on_timeout(self):
    """Handle timeout - severe congestion"""
    self.ssthresh = max(self.cwnd // 2, 2 * MSS)
    self.cwnd = MSS
    self.state = SLOW_START
    self.rto = min(self.rto * 2, MAX_RTO)
```

### 3. State Transitions

```
       [Start]
          ↓
    SLOW_START (cwnd = 1 MSS)
          ↓
    (cwnd >= ssthresh OR loss)
          ↓
    CONGESTION_AVOIDANCE
          ↓
    (3 dup ACKs)
          ↓
    FAST_RECOVERY
          ↓
    (new ACK >= recover)
          ↓
    CONGESTION_AVOIDANCE
    
    
    TIMEOUT from any state
          ↓
    SLOW_START (cwnd = 1 MSS)
```

---

## Client Changes

### Part 1 Client:
```python
def receive_file(self, output='received_data.txt'):
    # Fixed output filename
    with open(output, 'wb') as f:
        f.write(self.file_data)
```

### Part 2 Client:
```python
def __init__(self, server_ip, server_port, pref_filename):
    self.pref_filename = pref_filename
    
def receive_file(self):
    # Dynamic output filename
    output = f"{self.pref_filename}received_data.txt"
    with open(output, 'wb') as f:
        f.write(self.file_data)
```

**Why?** Multiple clients can run simultaneously without overwriting each other's files.

---

## Performance Comparison

### Part 1 Performance:
```
Fixed SWS = 10,000 bytes at 100 Mbps
- Constant sending rate
- No adaptation to network conditions
- Good for understanding reliability mechanisms
```

### Part 2 Performance:
```
Dynamic cwnd at 100 Mbps
- Starts at 1 MSS (1,180 bytes)
- Grows to ~30-40 MSS during transfer
- Adapts to loss and delay
- More realistic TCP behavior
```

---

## Code Complexity Comparison

### Lines of Code:
```
p1_server.py: ~250 lines
p2_server.py: ~380 lines (+52%)

p1_client.py: ~180 lines
p2_client.py: ~190 lines (+5%)
```

### Additional Concepts in Part 2:
- ✅ Congestion window (cwnd)
- ✅ Slow start threshold (ssthresh)
- ✅ State machine (SS/CA/FR)
- ✅ Fast recovery logic
- ✅ Timeout handling
- ✅ Window growth algorithms

---

## Reliability Features (Same in Both)

Both Part 1 and Part 2 implement:

✅ Sequence numbers
✅ Cumulative ACKs
✅ Selective ACKs (SACK)
✅ Timeouts and retransmission
✅ Fast retransmit (3 dup ACKs)
✅ Out-of-order delivery
✅ RTO estimation

**Difference:** Part 2 adds *congestion control* on top of these.

---

## Experiment Differences

### Part 1 Experiments:
```
Variable: Loss rate (1-5%) and Delay jitter (20-100ms)
Metric: Download time
Goal: Understand reliability overhead
```

### Part 2 Experiments:
```
Variables:
  - Bandwidth (100-1000 Mbps)
  - Loss rate (0-2%)
  - RTT asymmetry (5-25ms)
  - Background UDP traffic
  
Metrics: Link utilization AND Jain Fairness Index
Goal: Understand congestion control and fairness
```

---

## Visual Comparison

### Part 1 Window Behavior:

```
Bytes in Flight
    ^
SWS |════════════════════════════════  ← Fixed ceiling
    |████████████████████████
    |████████████████████████
    |████████████████████████
    +──────────────────────────> Time
```

### Part 2 Window Behavior:

```
Bytes in Flight (cwnd)
    ^
    |                    ╱╲
    |                  ╱    ╲
    |              ╱╲╱        ╲    ← Adapts to congestion
    |          ╱╲╱              ╲╱╲
    |      ╱╲╱                      ╲
    |  ╱╲╱                            ╲
    +──────────────────────────────────> Time
    
    ← Slow Start → ← Congestion Avoidance → ← Loss → ← Recovery →
```

---

## Implementation Strategies Comparison

### Part 1 Strategy:
1. Implement reliability (ACKs, retransmission)
2. Set fixed window size
3. Optimize for minimal retransmissions
4. Focus on correctness

### Part 2 Strategy:
1. Build on Part 1 reliability
2. Add dynamic window management
3. Implement state machine
4. Balance efficiency and fairness
5. Handle multiple flows

---

## Common Pitfalls and Fixes

### Part 1 Pitfalls:
❌ RTO too small → spurious timeouts
❌ Window too large → overwhelming network
❌ Window too small → poor utilization

### Part 2 Pitfalls:
❌ Not updating cwnd on ACKs
❌ Incorrect state transitions
❌ cwnd not decreasing on loss
❌ Not entering fast recovery
❌ Reducing cwnd multiple times for same loss

---

## Testing Differences

### Part 1 Testing:
```bash
# Simple: Start server, start client
python3 p1_server.py 127.0.0.1 9999 10000
python3 p1_client.py 127.0.0.1 9999

# Mininet: Test with loss and delay
tc qdisc add dev s1-eth1 root netem loss 2%
```

### Part 2 Testing:
```bash
# Simple: Start server, start client (no SWS)
python3 p2_server.py 127.0.0.1 9999
python3 p2_client.py 127.0.0.1 9999 test_

# Mininet: Test fairness with multiple flows
# Start 2 servers and 2 clients simultaneously
# Measure: JFI and link utilization
```

---

## Debug Output Comparison

### Part 1 Debug:
```
[SERVER] Sent: 1250 | Retrans: 42 (3.4%) | RTO: 0.245s
[CLIENT] Packets: 1208 | Out-of-order: 23 | Dups: 19
```

### Part 2 Debug:
```
[SERVER] 67.3% | cwnd: 24.5 MSS | ssthresh: 32.0 MSS | 
         state: CA | RTO: 0.145s | sent: 1250 | retrans: 42
[CLIENT] Received: 3.2MB | Buffered: 3 | Packets: 1208
```

**Key Addition:** cwnd, ssthresh, and state information

---

## Grading Weight Comparison

### Part 1 (40% of assignment):
- 50% - Correctness
- 25% - Meeting performance targets
- 25% - Relative efficiency

### Part 2 (60% of assignment):
- 70% - Meeting targets + report
- 30% - Relative performance (Score = JFI × Utilization)

**Part 2 is worth more because it's more complex!**

---

## Migration Guide: Part 1 → Part 2

If you're adapting your Part 1 code:

### Step 1: Remove SWS parameter
```python
# Remove from __init__
- def __init__(self, ip, port, sws):
+ def __init__(self, ip, port):

# Remove from main
- if len(sys.argv) != 4:
+ if len(sys.argv) != 3:
```

### Step 2: Add congestion control variables
```python
# Add to __init__
self.cwnd = MSS
self.ssthresh = 64 * MSS
self.state = SLOW_START
self.bytes_acked_ca = 0
```

### Step 3: Replace window check
```python
# Replace fixed window check
- if bytes_in_flight >= self.sws:
+ if bytes_in_flight >= self.cwnd:
```

### Step 4: Add window update logic
```python
# After processing new ACK
if ack_num > self.base:
    bytes_acked = # calculate
+   self.on_new_ack(bytes_acked)
```

### Step 5: Add loss handling
```python
# On 3 dup ACKs
if dup_count == 3:
+   self.on_fast_retransmit()
    # retransmit

# On timeout
except socket.timeout:
+   self.on_timeout()
    # retransmit
```

### Step 6: Update client filename
```python
# Client __init__
+ def __init__(self, server_ip, server_port, pref_filename):
+     self.pref_filename = pref_filename

# In receive_file
- output = 'received_data.txt'
+ output = f"{self.pref_filename}received_data.txt"
```

---

## Summary Table

| Feature | Part 1 | Part 2 |
|---------|--------|--------|
| **Window Size** | Fixed (SWS) | Dynamic (cwnd) |
| **Adaptation** | No | Yes |
| **States** | None | SS/CA/FR |
| **Congestion Control** | No | Yes (TCP Reno) |
| **Client Output** | Fixed name | Prefix + name |
| **Experiments** | Loss/Delay | BW/Loss/RTT/UDP |
| **Metrics** | Download time | Utilization + JFI |
| **Complexity** | Medium | High |
| **Weight** | 40% | 60% |

---

## Key Takeaway

**Part 1** implements **reliability** (ensuring all packets arrive)
**Part 2** adds **congestion control** (adapting to network conditions)

Together, they form a complete transport protocol similar to TCP!

---

## File Checklist

After implementation, you should have:

**From Part 1:**
- [x] p1_client.py
- [x] p1_server.py
- [x] Part 1 report (with plots)

**From Part 2:**
- [x] p2_client.py (with PREF_FILENAME support)
- [x] p2_server.py (with congestion control)
- [x] Part 2 report (with 4 experiment plots)

**Plus:**
- [x] data.txt (test file)
- [x] p2_exp.py (experiment runner)
- [x] udp_client.py, udp_server.py (for Exp 4)

---

## Next Steps

1. ✅ Understand the differences (read this document)
2. ✅ Review the congestion control algorithm
3. ✅ Test basic functionality
4. ✅ Run experiments
5. ✅ Analyze results and create report
6. ✅ Submit!

Good luck! 🚀
