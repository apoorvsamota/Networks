# Quick Start Guide: Running Part 2 Experiments

## Prerequisites

1. **Files needed in experiment directory:**
   - `p2_server.py`
   - `p2_client.py`
   - `data.txt` (the file to transfer)
   - `p2_exp.py` (experiment runner)
   - `udp_server.py` (for background traffic experiment)
   - `udp_client.py` (for background traffic experiment)

2. **Mininet and Ryu controller must be installed and running**

---

## Running Individual Experiments

### Experiment 1: Fixed Bandwidth

**What it tests:** How throughput scales with bandwidth

```bash
sudo python3 p2_exp.py fixed_bandwidth
```

**Output:** `p2_fairness_fixed_bandwidth.csv`

**Expected behavior:**
- Tests bandwidths from 100 Mbps to 1 Gbps
- Two flows compete on bottleneck link
- Should see decreasing link utilization as BW increases (same file, more capacity)
- JFI should stay high (~0.99)

---

### Experiment 2: Varying Loss

**What it tests:** Impact of packet loss on TCP

```bash
sudo python3 p2_exp.py varying_loss
```

**Output:** `p2_fairness_varying_loss.csv`

**Expected behavior:**
- Tests loss rates: 0%, 0.5%, 1.0%, 1.5%, 2.0%
- Link utilization drops significantly with loss
- TCP's multiplicative decrease makes it sensitive to loss

---

### Experiment 3: Asymmetric Flows

**What it tests:** Fairness when flows have different RTTs

```bash
sudo python3 p2_exp.py asymmetric_flows
```

**Output:** `p2_fairness_asymmetric_flows.csv`

**Expected behavior:**
- Client2 delay varies: 5ms to 25ms (Client1 fixed at 5ms)
- JFI degrades as RTT asymmetry increases
- Flow with higher RTT gets less bandwidth

---

### Experiment 4: Background UDP

**What it tests:** How TCP coexists with non-responsive UDP traffic

```bash
sudo python3 p2_exp.py background_udp
```

**Output:** `p2_fairness_background_udp.csv`

**Expected behavior:**
- UDP generates bursty background traffic
- Tests light, medium, heavy UDP loads
- TCP throughput and fairness both degrade
- UDP doesn't back off, steals bandwidth

---

## Manual Testing (Without Mininet)

For basic functionality testing on localhost:

### Terminal 1 (Server):
```bash
python3 p2_server.py 127.0.0.1 9999
```

### Terminal 2 (Client):
```bash
python3 p2_client.py 127.0.0.1 9999 test_
```

**Verify:**
```bash
# Check file was received
ls -lh test_received_data.txt

# Verify integrity
md5sum data.txt test_received_data.txt
```

---

## Understanding the Output CSV

### Column Descriptions:

- `bw`: Bottleneck bandwidth (Mbps)
- `loss`: Packet loss rate (%)
- `delay_c2_ms`: Client2 to switch delay (ms)
- `udp_off_mean`: Mean UDP OFF period (seconds, None if no UDP)
- `iter`: Iteration number
- `md5_hash_1`: MD5 of client1's received file
- `md5_hash_2`: MD5 of client2's received file
- `ttc1`: Time to complete for client1 (seconds)
- `ttc2`: Time to complete for client2 (seconds)
- `size1_bytes`: Bytes received by client1
- `size2_bytes`: Bytes received by client2
- `thr1_mbps`: Client1 throughput (Mbps)
- `thr2_mbps`: Client2 throughput (Mbps)
- `link_util`: Link utilization (sum of throughputs / capacity)
- `jfi`: Jain Fairness Index

---

## Analyzing Results

### Calculate Average Metrics

```python
import pandas as pd

# Load results
df = pd.read_csv('p2_fairness_fixed_bandwidth.csv')

# Group by parameter and calculate means
results = df.groupby('bw')[['link_util', 'jfi']].mean()
print(results)
```

### Plotting Results

```python
import matplotlib.pyplot as plt
import pandas as pd

# Fixed bandwidth experiment
df = pd.read_csv('p2_fairness_fixed_bandwidth.csv')
grouped = df.groupby('bw')[['link_util', 'jfi']].mean()

fig, ax1 = plt.subplots(figsize=(10, 6))

# Plot utilization
ax1.plot(grouped.index, grouped['link_util'], 'b-o', label='Link Utilization')
ax1.set_xlabel('Bandwidth (Mbps)')
ax1.set_ylabel('Link Utilization', color='b')
ax1.tick_params(axis='y', labelcolor='b')

# Plot JFI on same graph
ax2 = ax1.twinx()
ax2.plot(grouped.index, grouped['jfi'], 'r-s', label='JFI')
ax2.set_ylabel('Jain Fairness Index', color='r')
ax2.tick_params(axis='y', labelcolor='r')

plt.title('Fixed Bandwidth: Utilization and Fairness')
plt.tight_layout()
plt.savefig('fixed_bandwidth.png', dpi=300)
plt.show()
```

---

## Troubleshooting

### Problem: "Connection refused" or "No route to host"

**Solution:**
- Ensure Ryu controller is running: `ryu-manager ryu.app.simple_switch_13`
- Check Mininet topology is created: `sudo mn --topo single,2`
- Verify IP addresses match topology

---

### Problem: Clients hang or timeout

**Solution:**
- Check if server is listening: `netstat -an | grep <port>`
- Verify firewall isn't blocking: `sudo iptables -L`
- Increase REQUEST_TIMEOUT in client code
- Check server logs for errors

---

### Problem: File transfer incomplete or corrupted

**Solution:**
- Compare MD5 hashes
- Check server logs for retransmission statistics
- Verify EOF packet is sent (5 copies)
- Look for errors in client log

---

### Problem: Very poor performance

**Possible causes:**
1. Buffer size too small → increase buffer_size parameter
2. RTO too conservative → tune MIN_RTO/MAX_RTO
3. cwnd not growing → check state transitions
4. Too many timeouts → investigate packet loss

**Debug steps:**
```bash
# Check server log for cwnd evolution
grep "cwnd:" server.log

# Check retransmission rate
grep "retrans:" server.log

# Monitor in real-time
tail -f server.log
```

---

### Problem: Low fairness (JFI < 0.9)

**Possible causes:**
1. Flows not starting simultaneously
2. RTT difference too large
3. Synchronization effects
4. One flow experiencing more loss

**Solutions:**
- Ensure flows start at same time
- Add random jitter to desynchronize
- Check if loss is uniform across flows

---

## Performance Expectations

### Target Metrics (from benchmarks):

**Fixed Bandwidth (100 Mbps):**
- Utilization: ~54%
- JFI: ~0.99
- Per-flow throughput: ~27 Mbps

**With Loss (1% loss, 100 Mbps):**
- Utilization: ~3.5%
- JFI: ~0.99
- Severe degradation expected

**Asymmetric (25ms difference):**
- Utilization: ~54%
- JFI: ~0.80
- Fair degradation with RTT difference

**Background UDP (heavy load):**
- Utilization: ~10%
- JFI: ~0.99
- Significant impact from UDP

---

## Performance Scoring

Your submission is evaluated on:

1. **Correctness (50%):**
   - File transfer completes
   - MD5 matches
   - No crashes or hangs

2. **Meeting Targets (25%):**
   - Link utilization within range
   - JFI within acceptable bounds
   - Appropriate response to conditions

3. **Relative Performance (25%):**
   - Ranked against other submissions
   - Score = JFI × Link Utilization
   - Decile-based grading

---

## Tips for Better Performance

### 1. **Optimize Initial Parameters**
```python
# Tune these in p2_server.py:
self.cwnd = MSS           # Try 2*MSS for faster start
self.ssthresh = 64 * MSS  # Try 128*MSS for longer slow start
MIN_RTO = 0.1             # Lower for faster retransmission
```

### 2. **Improve RTT Estimation**
- Use Karn's algorithm (don't sample retransmitted packets)
- Smooth out variations with appropriate ALPHA/BETA
- Set reasonable MIN/MAX bounds

### 3. **Better Loss Recovery**
- Implement SACK processing on server side
- Retransmit only un-SACKed packets
- Avoid unnecessary retransmissions

### 4. **Reduce Synchronization**
- Add small random delays
- Jitter initial cwnd values
- Stagger flow starts slightly

---

## Validation Checklist

Before running experiments:

- [ ] Files compile without errors
- [ ] Manual test completes successfully
- [ ] MD5 checksum matches
- [ ] Server logs show cwnd growth
- [ ] Client receives all data
- [ ] No crashes or hangs
- [ ] Reasonable performance (~50% utilization)

---

## Common Mistakes to Avoid

1. **Not updating cwnd on ACKs**
   - Make sure on_new_ack() is called

2. **Incorrect bytes_acked calculation**
   - Should sum actual packet lengths, not estimate

3. **Not entering fast recovery**
   - Check dup_acks counter logic

4. **RTO too small/large**
   - Tune based on RTT measurements

5. **Not handling state transitions**
   - SS → CA → FR → CA should work smoothly

6. **Ignoring SACKs**
   - Use them to avoid unnecessary retransmissions

---

## Additional Resources

### TCP Congestion Control References:
- RFC 5681: TCP Congestion Control
- RFC 2018: TCP Selective Acknowledgment
- RFC 6298: RTO Computation

### Helpful Commands:
```bash
# Monitor network in Mininet
mininet> h1 ping h2

# Check bandwidth
mininet> iperf h1 h2

# View queue length
mininet> switch dpctl dump-flows

# Monitor packet loss
mininet> h1 tcpdump -i h1-eth0
```

---

## Report Writing Tips

Your report should include:

1. **Header structure description:**
   - Explain your packet format
   - Mention any enhancements (SACKs, etc.)

2. **Congestion control algorithm:**
   - State you implemented TCP Reno
   - Briefly describe SS, CA, FR, timeout handling

3. **For each experiment:**
   - Plot with readable labels
   - Brief observation (2-3 sentences)
   - Explain why you see this behavior

4. **Keep it concise:**
   - Max 2 pages
   - Focus on results, not code

**Example observation:**
> "Link utilization decreases as bandwidth increases because the fixed file size is transferred faster on higher-capacity links. The JFI remains high (~0.99) indicating fair bandwidth sharing between the two competing flows."

---

Good luck with your experiments!
