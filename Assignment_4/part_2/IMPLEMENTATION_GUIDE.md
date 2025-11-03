# Part 2: Congestion Control Implementation
## Complete Guide and Documentation

## Overview

This implementation provides a high-performance TCP Reno-style congestion control algorithm built on top of the reliable UDP protocol from Part 1. The design focuses on efficiency, correctness, and fairness across competing flows.

---

## Key Features

### 1. **TCP Reno Congestion Control**
- **Slow Start**: Exponential window growth (cwnd doubles per RTT)
- **Congestion Avoidance**: Linear window growth (cwnd += MSS per RTT)
- **Fast Retransmit**: Retransmit on 3 duplicate ACKs
- **Fast Recovery**: Efficient recovery from packet loss
- **Timeout Handling**: Conservative response to severe congestion

### 2. **State Machine**
Three congestion control states:
- **SLOW_START**: Exponential growth until ssthresh or loss
- **CONGESTION_AVOIDANCE**: Linear growth after reaching threshold
- **FAST_RECOVERY**: Temporary state during loss recovery

### 3. **Optimizations**
- Dictionary-based packet storage for O(1) lookups
- Efficient RTT estimation using EWMA (Exponential Weighted Moving Average)
- SACK (Selective Acknowledgment) support for faster recovery
- Adaptive RTO (Retransmission Timeout) calculation

---

## Design Decisions

### Congestion Window Management

**Initial Values:**
```python
cwnd = 1 MSS (1180 bytes)
ssthresh = 64 MSS (75,520 bytes)
```

**Slow Start Growth:**
```python
cwnd += bytes_acked  # Doubles every RTT
```
When cwnd reaches ssthresh, transition to Congestion Avoidance.

**Congestion Avoidance Growth:**
```python
bytes_acked_ca += bytes_acked
if bytes_acked_ca >= cwnd:
    cwnd += MSS
    bytes_acked_ca = 0
```
This increases cwnd by ~1 MSS per RTT, implementing Additive Increase.

**Fast Retransmit (on 3 duplicate ACKs):**
```python
ssthresh = max(cwnd / 2, 2 * MSS)
cwnd = ssthresh + 3 * MSS
state = FAST_RECOVERY
```

**Fast Recovery:**
- Inflate cwnd by 1 MSS for each additional dup ACK
- On new ACK: cwnd = ssthresh, exit to Congestion Avoidance

**Timeout (severe congestion):**
```python
ssthresh = max(cwnd / 2, 2 * MSS)
cwnd = 1 MSS
state = SLOW_START
rto *= 2  # Exponential backoff
```

---

## Performance Analysis

### Expected Performance (from benchmarks)

**Fixed Bandwidth (100 Mbps, no loss):**
- Link Utilization: ~54%
- Jain Fairness Index: 0.99
- Achievable throughput: ~27 Mbps per flow

**Why not 100% utilization?**
1. Conservative congestion control prevents queue buildup
2. ACK traffic consumes bandwidth
3. Retransmissions from random losses
4. Protocol overhead (headers)

**Varying Loss:**
- 0.5% loss: 6.8% utilization
- 1.0% loss: 3.5% utilization
- Loss severely impacts TCP-like protocols due to Multiplicative Decrease

**Asymmetric Flows:**
- Fairness degrades with RTT asymmetry (JFI: 0.99 → 0.80)
- This is expected behavior in TCP variants
- Flows with higher RTT get less bandwidth

---

## Implementation Details

### Server (`p2_server.py`)

**Core Algorithm:**

1. **Initialization**
   - Set cwnd = 1 MSS
   - Set ssthresh = 64 MSS
   - Start in SLOW_START state

2. **Sending Loop**
   - Send packets while `bytes_in_flight < cwnd`
   - Track send times for RTT estimation
   - Set timeouts for each packet

3. **ACK Processing**
   - **New ACK (advances window)**:
     * Calculate bytes_acked
     * Update RTT using Karn's algorithm
     * Update cwnd based on current state
     * Slide window forward
   
   - **Duplicate ACK (same ACK number)**:
     * Count duplicates
     * On 3rd dup ACK: Fast Retransmit + enter Fast Recovery
     * In Fast Recovery: Inflate cwnd

4. **Timeout Handling**
   - Set ssthresh = cwnd/2
   - Reset cwnd = 1 MSS
   - Enter SLOW_START
   - Apply exponential backoff to RTO

### Client (`p2_client.py`)

**Responsibilities:**
- Receive data packets
- Send cumulative ACKs
- Include SACK information for out-of-order packets
- Reorder packets and deliver in-order data
- Save to `<PREF_FILENAME>received_data.txt`

**SACK Format:**
```
Bytes 0-3:   ACK number (cumulative)
Bytes 4-11:  SACK range 1 (left edge, right edge)
Bytes 12-19: SACK range 2 (left edge, right edge)
```

---

## Running the Code

### Starting the Server:
```bash
python3 p2_server.py <SERVER_IP> <SERVER_PORT>
```

Example:
```bash
python3 p2_server.py 10.0.0.3 6555
```

### Starting the Client:
```bash
python3 p2_client.py <SERVER_IP> <SERVER_PORT> <PREF_FILENAME>
```

Example:
```bash
python3 p2_client.py 10.0.0.3 6555 1
# This will save the file as "1received_data.txt"
```

### In Mininet:
```bash
# On server host (s1):
python3 p2_server.py 10.0.0.3 6555 > /tmp/server.log 2>&1 &

# On client host (c1):
python3 p2_client.py 10.0.0.3 6555 1
```

---

## Debugging Tips

### 1. **Monitor Congestion Window Evolution**
The server logs show:
- Current cwnd in MSS units
- Current ssthresh
- State (SS/CA/FR)
- RTO value

Look for:
- Smooth exponential growth in Slow Start
- Linear growth in Congestion Avoidance
- Proper halving on loss events

### 2. **Check for Synchronization**
If two flows have identical throughput oscillations:
- May indicate synchronization effects
- Add small random delays to desynchronize

### 3. **RTT Estimation Issues**
If throughput is poor:
- Check if RTO is too conservative (too large)
- Verify RTT samples are being collected
- Ensure send_times dictionary is properly maintained

### 4. **Fast Recovery Not Triggering**
If you see many timeouts instead of fast retransmits:
- Client may not be sending duplicate ACKs
- Check SACK implementation
- Verify dup_ack counting logic

### 5. **Window Not Growing**
If cwnd stays small:
- Check if bytes_acked calculation is correct
- Verify state transitions (SS → CA)
- Ensure new ACKs are triggering cwnd updates

---

## Performance Optimization Strategies

### 1. **Tune Initial Parameters**
```python
# More aggressive (faster convergence):
self.ssthresh = 128 * MSS  # Higher threshold
self.cwnd = 2 * MSS        # Start with 2 MSS

# More conservative (better stability):
self.ssthresh = 32 * MSS   # Lower threshold
self.cwnd = 1 * MSS        # Standard start
```

### 2. **Adjust RTO Parameters**
```python
# Faster reaction (good for low-latency networks):
MIN_RTO = 0.05  # 50ms
MAX_RTO = 1.0   # 1s

# More patient (better for high-latency/lossy networks):
MIN_RTO = 0.2   # 200ms
MAX_RTO = 3.0   # 3s
```

### 3. **Fine-tune ALPHA and BETA**
```python
# Standard TCP values:
ALPHA = 0.125  # RTT smoothing
BETA = 0.25    # Deviation smoothing

# For more responsive RTT tracking:
ALPHA = 0.25   # React faster to RTT changes
BETA = 0.5     # Track variability more closely
```

### 4. **Improve SACK Handling**
The server can use SACK information to:
- Avoid unnecessary retransmissions
- Identify which packets need retransmission
- Better estimate network conditions

### 5. **Alternative Algorithms**

**TCP CUBIC** (better for high BDP networks):
- Window grows as cubic function of time since last loss
- More aggressive than Reno
- Better utilization on high-bandwidth links

**BBR** (Bottleneck Bandwidth and RTT):
- Probe bandwidth and RTT independently
- Aim for optimal operating point
- Better performance under buffer bloat

---

## Algorithm Comparison

| Algorithm | Slow Start | Congestion Avoidance | Loss Response | Best For |
|-----------|------------|---------------------|---------------|----------|
| **Reno** | Exponential | Linear (AIMD) | Halve on loss | General purpose, fair |
| **CUBIC** | Exponential | Cubic growth | More aggressive | High BDP networks |
| **BBR** | Probe-based | Probe-based | Model-based | Modern networks |

---

## Expected Results

### Experiment 1: Fixed Bandwidth
- **Observation**: Utilization decreases as bandwidth increases
- **Reason**: Fixed file size takes less time on faster links
- **JFI**: Should remain high (~0.99) showing good fairness

### Experiment 2: Varying Loss
- **Observation**: Throughput drops significantly with loss
- **Reason**: Multiplicative Decrease on each loss event
- **Critical**: Loss > 1% severely impacts TCP performance

### Experiment 3: Asymmetric Flows
- **Observation**: JFI degrades with RTT difference
- **Reason**: Higher RTT flows take longer to ramp up cwnd
- **Expected**: JFI drops from 0.99 to ~0.80 at 25ms asymmetry

### Experiment 4: Background UDP
- **Observation**: TCP throughput and fairness both degrade
- **Reason**: UDP doesn't back off, steals bandwidth
- **Impact**: Heavier UDP load → worse TCP performance

---

## Troubleshooting Common Issues

### Issue: Very Low Throughput
**Possible Causes:**
1. cwnd not growing (check state transitions)
2. RTO too large (check RTT estimation)
3. Too many timeouts (increase buffer size)
4. Client not sending ACKs fast enough

**Solutions:**
- Add debug logging for cwnd evolution
- Monitor state transitions
- Check if fast recovery is triggering
- Verify ACK generation rate

### Issue: Poor Fairness
**Possible Causes:**
1. Flows starting at different times
2. RTT differences too large
3. Synchronization effects

**Solutions:**
- Start flows simultaneously
- Add small random jitter
- Tune decrease behavior for better fairness

### Issue: High Retransmission Rate
**Possible Causes:**
1. RTO too small (spurious timeouts)
2. Network buffer too small
3. Actual packet loss

**Solutions:**
- Increase MIN_RTO
- Adjust RTO multiplier in timeout handling
- Increase buffer size in experiments

---

## Code Structure

### Server State Variables

```python
# Window Management
self.base          # First unacknowledged byte
self.next_seq      # Next sequence to send

# Congestion Control
self.cwnd          # Congestion window (bytes)
self.ssthresh      # Slow start threshold (bytes)
self.state         # Current CC state
self.bytes_acked_ca # Bytes ACKed in CA mode

# Packet Tracking
self.packets       # Dict: seq → packet data
self.pkt_lens      # Dict: seq → packet length
self.send_times    # Dict: seq → send timestamp
self.timeouts      # Dict: seq → timeout time
self.acked         # Set of acknowledged sequences

# RTT and RTO
self.est_rtt       # Estimated RTT
self.dev_rtt       # RTT deviation
self.rto           # Retransmission timeout

# Fast Retransmit
self.dup_acks      # Dict: ack → count
self.last_ack      # Last ACK received
self.recover       # Recovery sequence number
```

---

## Testing Checklist

Before submitting, verify:

- [ ] Server starts with cwnd = 1 MSS
- [ ] Slow start increases cwnd exponentially
- [ ] Transition to CA at ssthresh
- [ ] Fast retransmit triggers on 3 dup ACKs
- [ ] Fast recovery inflates window correctly
- [ ] Timeout resets cwnd to 1 MSS
- [ ] RTO is updated based on RTT samples
- [ ] Client sends SACKs for out-of-order packets
- [ ] File transfer completes successfully
- [ ] MD5 checksum matches original file
- [ ] Multiple flows achieve good fairness

---

## Performance Targets

Based on the benchmark data:

**Target Metrics:**
- Link utilization: 50-60% for 100 Mbps
- JFI: > 0.95 for symmetric flows
- JFI: > 0.80 for asymmetric flows (25ms difference)
- Graceful degradation under loss
- Complete file transfer with correct MD5

**Scoring Rubric:**
- 70% for meeting performance targets + report
- 30% for relative performance ranking
- Higher is better: (JFI × Link Utilization)

---

## Advanced Optimizations (Optional)

### 1. **Pacing**
Send packets at regular intervals instead of bursts:
```python
inter_packet_delay = RTT / cwnd_in_packets
```

### 2. **Better SACK Processing**
Use SACK info to determine which packets to retransmit:
```python
# Instead of always retransmitting base
# Find the first un-SACKed packet in the window
```

### 3. **Limited Transmit**
Send new data on first 1-2 dup ACKs:
```python
if dup_acks_count < 3:
    # Send new unsent data if available
```

### 4. **Appropriate Byte Counting (ABC)**
More careful cwnd increase to avoid bursts:
```python
# Count only bytes that weren't retransmitted
```

---

## References

- RFC 5681: TCP Congestion Control
- RFC 2018: TCP Selective Acknowledgment Options
- RFC 6298: Computing TCP's Retransmission Timer
- "TCP/IP Illustrated, Volume 1" by W. Richard Stevens

---

## Summary

This implementation provides a solid foundation for congestion control experiments. The TCP Reno algorithm balances:
- **Efficiency**: Good link utilization
- **Fairness**: Equal bandwidth sharing
- **Responsiveness**: Quick reaction to congestion
- **Stability**: Smooth convergence

The code is well-structured, efficient, and ready for experimentation. Good luck with your assignment!
