# 🚀 Part 1: Reliable UDP - Complete Guide & Results

## ✅ What I've Built For You

I've implemented a **complete reliable file transfer protocol over UDP** with all required features:

### ✨ Key Features Implemented
- ✅ **Sliding Window Protocol** with configurable window size (SWS)
- ✅ **Cumulative ACKs** (like TCP)
- ✅ **Adaptive Timeout (RTO)** with RTT estimation
- ✅ **Fast Retransmit** (triggers after 3 duplicate ACKs)
- ✅ **Exponential Backoff** on timeouts
- ✅ **Out-of-order Packet Buffering**
- ✅ **Proper Connection Setup** (5 retries with 2s timeout)
- ✅ **EOF Signaling** for clean termination
- ✅ **Comprehensive Logging** for debugging

## 📁 Files Delivered

| File | Description |
|------|-------------|
| `p1_server.py` | Server implementation (10KB) |
| `p1_client.py` | Client implementation (8.5KB) |
| `README_PART1.md` | Detailed documentation |
| `test_transfer.py` | Local testing script |
| `analyze_benchmarks.py` | Performance analysis tool |
| `part1_benchmark_analysis.png` | Benchmark visualization |

## 🎯 Quick Start

### 1. Basic Test (Verify Everything Works)

```bash
# In your terminal:
cd /mnt/user-data/outputs
python3 test_transfer.py
```

**Expected Output:**
```
✓ File transfer SUCCESSFUL - MD5 matches!
Original MD5:  cc83be85db391e9396e1427b3e124968
Received MD5:  cc83be85db391e9396e1427b3e124968
Transfer time: ~0.2-0.3 seconds
```

### 2. Manual Test (Two Terminals)

```bash
# Terminal 1 - Server
python3 p1_server.py 127.0.0.1 6555 5900

# Terminal 2 - Client  
python3 p1_client.py 127.0.0.1 6555

# Verify
md5sum received_data.txt
# Should be: cc83be85db391e9396e1427b3e124968
```

### 3. Mininet Testing (With Network Conditions)

**Prerequisites:**
```bash
# Install Mininet if not already installed
sudo apt-get install mininet

# Start Ryu controller in separate terminal
ryu-manager ryu.app.simple_switch_13
```

**Run Experiments:**
```bash
# Copy data.txt and scripts to same directory
cp /mnt/user-data/uploads/data.txt .
cp /mnt/user-data/uploads/p1_exp.py .

# Loss experiment (1% to 5% packet loss)
sudo python3 p1_exp.py loss

# Jitter experiment (20ms to 100ms jitter)
sudo python3 p1_exp.py jitter

# Results saved to:
# - reliability_loss.csv
# - reliability_jitter.csv
```

## 📊 Test Results & Verification

### ✅ Local Test (No Network Conditions)

I've already tested the implementation locally and it **works perfectly**:

```
[SERVER] Transfer complete!
[SERVER] Time: 0.20s
[SERVER] Total packets sent: 5479
[SERVER] Retransmissions: 0
[SERVER] Efficiency: 100.0%

[CLIENT] Transfer complete!
[CLIENT] Time: 0.20s
[CLIENT] Bytes received: 6463538
[CLIENT] Average throughput: 32055.44 KB/s

✓ MD5 MATCH: cc83be85db391e9396e1427b3e124968
✓ FILE SIZE MATCH: 6463538 bytes
```

### 📈 Benchmark Analysis

Based on `part1.txt` benchmark data, here's what to expect:

#### Loss Impact (20ms delay, varying loss)
- **1% loss**: ~53 seconds (baseline)
- **3% loss**: ~63 seconds (+19% slower)
- **5% loss**: ~77 seconds (+45% slower)
- **Pattern**: Roughly linear (~6s per 1% loss)

#### Jitter Impact (20ms delay, 1% loss, varying jitter)
- **20ms jitter**: ~55 seconds (baseline)
- **60ms jitter**: ~77 seconds (+40% slower)
- **100ms jitter**: ~103 seconds (+87% slower)
- **Pattern**: Super-linear (accelerating degradation)

See `part1_benchmark_analysis.png` for visualizations!

## 🎯 Performance Targets

To be **competitive** (top 50%):
- 1% loss: **< 53 seconds**
- 5% loss: **< 77 seconds**  
- 100ms jitter: **< 103 seconds**

To be **excellent** (top 25%):
- 1% loss: **< 45 seconds**
- 5% loss: **< 65 seconds**
- 100ms jitter: **< 90 seconds**

## 🔍 How to Verify Correctness

### Method 1: MD5 Checksum
```bash
md5sum data.txt received_data.txt
# Both should output: cc83be85db391e9396e1427b3e124968
```

### Method 2: File Size
```bash
ls -lh data.txt received_data.txt
# Both should be: 6463538 bytes (6.2 MB)
```

### Method 3: Binary Comparison
```bash
diff data.txt received_data.txt
# Should output nothing (files identical)
```

### Method 4: Check Logs
Server should show:
- ✅ "Transfer complete!"
- ✅ Retransmissions count
- ✅ Efficiency percentage

Client should show:
- ✅ "Transfer complete!"  
- ✅ Bytes received matches file size
- ✅ "File saved to 'received_data.txt'"

## 🐛 Debugging Tips

### Common Issues & Solutions

**Problem: "Connection refused" or timeout**
```bash
# Solution:
1. Start server FIRST
2. Wait 1 second
3. Then start client
4. Check firewall: sudo ufw status
```

**Problem: Transfer very slow**
```bash
# Solution: Increase window size
python3 p1_server.py 127.0.0.1 6555 11800  # Larger SWS
```

**Problem: MD5 mismatch**
```bash
# Solution: Check for:
1. Sequence number bugs
2. EOF detection issues
3. Out-of-order packet handling

# Enable verbose logging (already in code)
# Check server/client output for errors
```

**Problem: In Mininet - Scripts not found**
```bash
# Solution: Use full paths
mininet> h1 python3 /home/claude/p1_server.py 10.0.0.1 6555 5900 &
mininet> h2 python3 /home/claude/p1_client.py 10.0.0.1 6555
```

### Monitoring Commands

```bash
# In Mininet CLI:

# Watch packets in real-time
mininet> h1 tcpdump -i h1-eth0 -n udp port 6555 &

# Check if file is being written
mininet> h2 ls -lh received_data.txt

# Monitor in real-time
mininet> h2 watch -n 1 ls -lh received_data.txt

# Add network conditions manually
mininet> h1 tc qdisc add dev h1-eth0 root netem loss 5% delay 20ms

# Remove network conditions
mininet> h1 tc qdisc del dev h1-eth0 root
```

## 🚀 Optimization Strategies

To improve competitive ranking:

### 1. **Tune Window Size (SWS)**
```python
# Current: 5900 bytes (5 packets)
# Try: 4-8 packets (4720-9440 bytes)
# Balance: throughput vs. retransmissions
```

### 2. **Optimize RTO Parameters**
```python
# Current: α=0.125, β=0.25 (TCP-style)
# For high jitter: Try α=0.1, β=0.3
# For low jitter: Try α=0.15, β=0.2
```

### 3. **Improve Fast Retransmit**
```python
# Current: 3 duplicate ACKs
# Could: Adjust based on RTT variance
# Or: Implement SACK for selective retransmit
```

### 4. **Add SACK Support** (Advanced)
```python
# Use the 16 reserved bytes for:
# - Selective ACK blocks
# - Timestamps
# - More efficient loss recovery
```

## 📝 Implementation Highlights

### Packet Format
```
| 4 bytes      | 16 bytes     | Up to 1180 bytes |
| Seq Number   | Reserved     | Data Payload     |
```

### Server Algorithm
1. Bind and wait for client request
2. Read file, split into packets
3. Send packets within window (≤ SWS bytes in flight)
4. Receive ACKs, slide window forward
5. Retransmit on:
   - Timeout (adaptive RTO)
   - 3 duplicate ACKs (fast retransmit)
6. Send EOF when done

### Client Algorithm
1. Send request with retries (5 × 2s timeout)
2. Receive packets, buffer out-of-order
3. Send cumulative ACKs
4. Write complete file
5. Verify EOF received

## 📚 Next Steps

1. ✅ **Verify basic functionality** - Done! (Test passed)
2. 🔄 **Run Mininet experiments** - Ready to go
3. 📊 **Analyze results** - Use analyze_benchmarks.py
4. 🎯 **Optimize performance** - Tune parameters
5. 📈 **Compare with benchmarks** - Aim for top 50%
6. 🚀 **Move to Part 2** - Add congestion control

## 🎓 Key Learnings

### What Makes This Protocol Reliable?

1. **Sequence Numbers**: Track bytes, detect loss/reordering
2. **ACKs**: Confirm receipt, cumulative for efficiency  
3. **Timeouts**: Detect lost packets, trigger retransmission
4. **Fast Retransmit**: Quick recovery without timeout
5. **Sliding Window**: Pipelining for throughput
6. **Adaptive RTO**: Adjusts to network conditions

### Performance Insights

- **Loss**: Roughly linear impact (predictable)
- **Jitter**: Super-linear impact (RTO estimation harder)
- **Window Size**: Critical for throughput vs. loss tradeoff
- **Fast Retransmit**: Essential for good performance

## 🎉 Summary

You now have:
- ✅ Working reliable UDP implementation
- ✅ All required features (sliding window, ACKs, timeouts, fast retransmit)
- ✅ Verified correctness (MD5 matches, file size correct)
- ✅ Performance analysis tools
- ✅ Ready for Mininet testing
- ✅ Clear path to optimization

**The code is ready to use! Just follow the Quick Start section above.**

Good luck with the experiments! 🚀

---

*For questions, refer to README_PART1.md for detailed documentation.*
