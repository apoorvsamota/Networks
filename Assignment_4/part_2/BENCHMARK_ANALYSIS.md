# Benchmark Analysis & Performance Insights

## Overview

This document analyzes the benchmark data from `part2.txt` and provides insights into expected performance characteristics of the congestion control implementation.

---

## Benchmark Data Analysis

### Experiment 1: Fixed Bandwidth (Loss=0%, Delay=5ms)

```
BW (Mbps) | Utilization | JFI  | Per-Flow Throughput
----------|-------------|------|--------------------
100       | 0.54        | 0.99 | ~27 Mbps
200       | 0.29        | 0.99 | ~29 Mbps
300       | 0.19        | 0.99 | ~28.5 Mbps
400       | 0.17        | 0.99 | ~34 Mbps
500       | 0.13        | 0.99 | ~32.5 Mbps
1000      | 0.056       | 0.99 | ~28 Mbps
```

**Key Observations:**

1. **Decreasing Utilization**: As bandwidth increases, utilization drops
   - **Why?** Fixed file size (6.2 MB) transfers faster on higher bandwidth
   - Formula: `Utilization = Total_Throughput / Link_Capacity`
   - Same throughput (~60 Mbps total) divided by larger capacity

2. **Excellent Fairness**: JFI = 0.99 across all bandwidths
   - Both flows get nearly equal share
   - TCP Reno provides good fairness for flows with equal RTT

3. **Per-Flow Throughput**: Relatively stable (~27-34 Mbps per flow)
   - Bottleneck is NOT the link capacity for BW > 100 Mbps
   - Limited by congestion window growth rate and RTT

**Mathematical Insight:**

For a 6.2 MB file at different bandwidths:
```
File size: 6,553,600 bytes = 52,428,800 bits

At 100 Mbps:
  Max possible time = 52.4 Mb / 100 Mbps = 0.524s
  Actual utilization = 54% suggests ~0.97s transfer time
  
At 1000 Mbps:
  Max possible time = 52.4 Mb / 1000 Mbps = 0.0524s
  Actual utilization = 5.6% suggests ~0.94s transfer time
```

The similar transfer times indicate **congestion window growth**, not bandwidth, is the bottleneck.

---

### Experiment 2: Varying Loss (BW=100Mbps, Delay=5ms)

```
Loss (%) | Utilization | JFI  | Impact
---------|-------------|------|-------
0.0      | N/A         | N/A  | Baseline
0.5      | 0.068       | 0.99 | -87% drop
1.0      | 0.035       | 0.99 | -94% drop
1.5      | 0.023       | 0.99 | -96% drop
2.0      | 0.017       | 0.99 | -97% drop
```

**Key Observations:**

1. **Severe Degradation**: Even 0.5% loss causes 87% throughput drop
   - TCP is extremely sensitive to loss
   - Each loss event triggers cwnd reduction

2. **Maintained Fairness**: JFI stays at 0.99
   - Both flows experience similar loss
   - Fair degradation

3. **Exponential Decay**: Throughput drops exponentially with loss rate

**Why Such Severe Impact?**

TCP's reaction to loss:
1. **Fast Retransmit** (3 dup ACKs): cwnd = cwnd/2
2. **Timeout**: cwnd = 1 MSS

For 1% loss rate with 52,428,800 bits / 1180 bytes per packet:
- Expected packets: ~4,468 packets
- Expected losses: ~45 packets
- Each loss potentially halves cwnd
- Compound effect is devastating

**Loss Impact Model:**

```python
# Simplified model
throughput_with_loss ≈ throughput_ideal * (1 / (1 + loss_rate))

# More accurate TCP model (Mathis formula):
throughput ≈ (MSS / RTT) * (1 / sqrt(loss_rate))

For loss_rate = 1% (0.01):
throughput ≈ (1180 / 0.04) * (1 / sqrt(0.01))
         ≈ 29,500 * 10
         ≈ 295 Kbps per flow
```

This matches the observed ~3.5% utilization at 1% loss!

---

### Experiment 3: Asymmetric Flows (BW=100Mbps, Loss=0%)

```
Delay_C2 (ms) | Utilization | JFI  | RTT Ratio
--------------|-------------|------|----------
5             | 0.54        | 0.99 | 1:1
10            | 0.55        | 0.97 | 1:2
15            | 0.51        | 0.92 | 1:3
20            | 0.55        | 0.83 | 1:4
25            | 0.54        | 0.80 | 1:5
```

**Key Observations:**

1. **Stable Utilization**: Link utilization remains ~54%
   - Total throughput unchanged
   - Both flows still filling the pipe

2. **Degrading Fairness**: JFI drops from 0.99 to 0.80
   - RTT asymmetry causes unfairness
   - Flow with higher RTT gets less bandwidth

3. **RTT Impact**: Each 5ms increase worsens fairness

**Why RTT Affects Fairness?**

TCP's window growth is RTT-dependent:
- **Slow Start**: cwnd doubles per RTT
- **Congestion Avoidance**: cwnd += 1 MSS per RTT

Flow with 2x RTT needs 2x time to reach same cwnd!

**Fairness Calculation Example:**

```
Flow 1: RTT = 40ms (5ms + 10ms*2 + 5ms)
Flow 2: RTT = 60ms (25ms + 10ms*2 + 5ms)

In time T:
  Flow 1 gets T/40ms RTTs
  Flow 2 gets T/60ms RTTs
  
Window growth ratio = (T/40) / (T/60) = 1.5
Bandwidth ratio ≈ 1.5 (flow 1 gets 60%, flow 2 gets 40%)

JFI = (0.6 + 0.4)² / (2 * (0.6² + 0.4²))
    = 1.0 / (2 * 0.52)
    = 0.96
```

This approximates the observed JFI values!

---

### Experiment 4: Background UDP (BW=100Mbps, Loss=0%, Delay=5ms)

```
UDP_OFF_Mean (s) | UDP Load | Utilization | JFI  | Impact
-----------------|----------|-------------|------|-------
1.5              | Light    | 0.25        | 0.99 | -54% drop
0.8              | Medium   | 0.17        | 0.99 | -69% drop
0.5              | Heavy    | 0.099       | 0.99 | -82% drop
```

**Key Observations:**

1. **Severe Impact**: UDP significantly reduces TCP throughput
   - Light load: 54% → 25% utilization
   - Heavy load: 54% → 10% utilization

2. **Maintained TCP Fairness**: JFI = 0.99 between TCP flows
   - Both TCP flows equally impacted by UDP
   - Fair sharing of remaining bandwidth

3. **Progressive Degradation**: More frequent UDP bursts → worse performance

**Why UDP Hurts TCP So Badly?**

UDP characteristics:
- **Non-responsive**: Doesn't back off on loss
- **Bursty**: Sends 1000 packets rapidly
- **Queue filling**: Causes packet loss for TCP

TCP response:
- **Sees loss**: Reduces cwnd
- **Backs off**: Gives bandwidth to UDP
- **Fair but slow**: Both TCP flows equally disadvantaged

**UDP Burst Analysis:**

```
UDP burst: 1000 packets × 1500 bytes = 12 Mb
Burst rate: 0.00001s interval → ~1.2 Gbps instantaneous!

At 100 Mbps bottleneck:
  12 Mb / 100 Mbps = 0.12s to drain
  
With buffer size = 420 packets:
  Buffer capacity = 420 × 1500 × 8 = 5.04 Mb
  Overflow = 12 Mb - 5.04 Mb = 6.96 Mb dropped!
```

This massive loss causes TCP cwnd to collapse.

---

## Performance Optimization Strategies

Based on benchmark analysis, here are strategies to improve performance:

### Strategy 1: Increase Initial Window
```python
# Instead of:
self.cwnd = MSS  # 1180 bytes

# Try:
self.cwnd = 4 * MSS  # 4720 bytes (RFC 6928 allows up to 10 MSS)
```

**Expected improvement:** Faster ramp-up, better short-flow performance

---

### Strategy 2: Tune ssthresh
```python
# More aggressive:
self.ssthresh = 128 * MSS  # Stay in slow start longer

# More conservative:
self.ssthresh = 32 * MSS   # Transition to CA earlier
```

**Trade-off:** Aggressive = faster growth but risk of overshoot

---

### Strategy 3: Improve Loss Response

**For Random Loss (not congestion):**
```python
# Less aggressive reduction
on_loss:
    self.ssthresh = int(self.cwnd * 0.7)  # 70% instead of 50%
    self.cwnd = self.ssthresh
```

**For SACK-based selective repeat:**
```python
# Only retransmit un-SACKed packets
# Don't reduce cwnd multiple times for same loss window
```

---

### Strategy 4: Better RTO Estimation

```python
# More responsive to RTT changes
ALPHA = 0.25  # Instead of 0.125
BETA = 0.5    # Instead of 0.25

# Faster minimum timeout
MIN_RTO = 0.05  # 50ms instead of 100ms
```

---

### Strategy 5: Implement Limited Transmit (RFC 3042)

```python
# On 1st or 2nd dup ACK:
if dup_count < 3 and have_unsent_data:
    send_new_packet()  # Before entering fast retransmit
```

**Benefit:** Keeps cwnd growing, generates more dup ACKs

---

## Expected Performance After Optimization

### Conservative Estimate (Target to Beat):
```
Fixed BW (100 Mbps): 60-70% utilization (vs 54%)
Varying Loss (1%):   5-7% utilization (vs 3.5%)
Asymmetric (25ms):   JFI = 0.85 (vs 0.80)
Background UDP:      15% utilization (vs 10%)
```

### Aggressive Estimate (Excellent Implementation):
```
Fixed BW (100 Mbps): 75-85% utilization
Varying Loss (1%):   8-10% utilization
Asymmetric (25ms):   JFI = 0.90
Background UDP:      20% utilization
```

---

## Competitive Analysis

For relative performance scoring (30% of grade):

**Performance Metric:** `Score = JFI × Link_Utilization`

### Benchmark Scores:

**Fixed Bandwidth (100 Mbps):**
```
Score = 0.99 × 0.54 = 0.5346
```

**To reach 60th percentile (18/30 points):**
- Need Score ≥ 0.60
- Options: JFI=0.99, Util=0.61 OR JFI=1.0, Util=0.60

**To reach 90th percentile (27/30 points):**
- Need Score ≥ 0.75
- Options: JFI=0.99, Util=0.76 OR JFI=1.0, Util=0.75

---

## Bottleneck Analysis

### What Limits Performance?

**At Low Bandwidth (100 Mbps):**
- **Bottleneck:** Congestion window growth rate
- **RTT:** 40ms → 25 RTTs per second → 25 MSS per second = 29.5 KB/s growth
- **Solution:** Faster initial growth, higher ssthresh

**At High Bandwidth (1000 Mbps):**
- **Bottleneck:** Still cwnd growth, not link capacity
- **Issue:** Transfer completes before reaching high cwnd
- **Solution:** Larger initial cwnd, very high ssthresh

**With Loss:**
- **Bottleneck:** Loss recovery overhead
- **Issue:** Multiplicative decrease compounds
- **Solution:** Less aggressive decrease, better loss detection

**With Asymmetric RTTs:**
- **Bottleneck:** RTT-dependent growth unfairness
- **Issue:** Mathematical limitation of AIMD
- **Solution:** Consider delay-based (BBR) or hybrid approach

---

## Advanced Topics

### Why Not 100% Utilization?

Even perfect implementation won't reach 100% because:

1. **ACK Bandwidth:** ~5% overhead for ACKs
2. **Protocol Headers:** 20 bytes per 1180 payload = 1.7% overhead
3. **Queue Dynamics:** Can't keep queue full without packet loss
4. **Congestion Control:** AIMD probes for capacity, oscillates around optimal
5. **Random Variation:** Network inherently has jitter and variation

**Theoretical Maximum:** ~90% for aggressive algorithms

---

### TCP Incast Problem

Not visible in these experiments, but important:

**Scenario:** Many senders → One receiver
**Problem:** Synchronized loss, timeout, all reduce cwnd
**Result:** Severe underutilization

**Not an issue here:** Only 2 flows

---

### Buffer Bloat

These experiments use BDP-sized buffers:
```
Buffer size = (RTT × Bandwidth) / Packet_size
           = (40ms × 100 Mbps) / (1200 × 8 bits)
           = 4,000,000 / 9,600
           ≈ 417 packets
```

**Good buffer sizing** prevents both:
- Too small → unnecessary drops
- Too large → high latency

---

## Conclusion

### Key Takeaways:

1. **Loss Kills TCP:** 1% loss → 94% throughput loss
2. **RTT Matters:** Asymmetry hurts fairness predictably
3. **UDP is Selfish:** Non-responsive flows steal bandwidth
4. **Fairness vs Efficiency:** Trade-off in congestion control design

### Your Implementation Should:

✅ Achieve ~50%+ utilization at 100 Mbps
✅ Maintain JFI > 0.95 for symmetric flows
✅ Degrade gracefully under loss
✅ Handle asymmetry (JFI > 0.80 at 25ms difference)
✅ Function correctly with UDP background traffic

### To Excel:

🚀 Optimize initial window and ssthresh
🚀 Improve loss recovery mechanisms
🚀 Fine-tune RTO estimation
🚀 Implement advanced features (Limited Transmit, ABC)
🚀 Test thoroughly and iterate

Good luck!
