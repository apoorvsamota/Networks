# Part 1: Reliable UDP File Transfer

## Overview

This implementation provides reliable file transfer over UDP using:
- **Sliding window protocol** with configurable window size
- **Cumulative ACKs** for efficient acknowledgment
- **Adaptive timeout** with RTT estimation (similar to TCP)
- **Fast retransmit** on 3 duplicate ACKs
- **Exponential backoff** on timeout

## Files

- `p1_server.py` - Server implementation
- `p1_client.py` - Client implementation
- `data.txt` - File to transfer (6.4 MB)
- `test_transfer.py` - Basic local testing script

## Quick Start

### Basic Usage

```bash
# Terminal 1: Start server
python3 p1_server.py <SERVER_IP> <SERVER_PORT> <SWS>

# Terminal 2: Start client  
python3 p1_client.py <SERVER_IP> <SERVER_PORT>
```

Example:
```bash
# Server
python3 p1_server.py 127.0.0.1 6555 5900

# Client
python3 p1_client.py 127.0.0.1 6555
```

### Parameters

- `SERVER_IP`: IP address for server to bind to
- `SERVER_PORT`: Port number for server
- `SWS`: Sender Window Size in bytes (e.g., 5900 = 5 packets × 1180 bytes)

## Testing

### 1. Local Testing (No Network Emulation)

```bash
python3 test_transfer.py
```

This runs a basic test without packet loss or delay. Expected result:
- Transfer time: ~0.2-0.3 seconds
- MD5 checksum matches
- 0 retransmissions

### 2. Mininet Testing (With Network Conditions)

#### Prerequisites
1. **Install Mininet**:
   ```bash
   sudo apt-get install mininet
   ```

2. **Start Ryu Controller** (in separate terminal):
   ```bash
   ryu-manager ryu.app.simple_switch_13
   ```

#### Running Experiments

The provided `p1_exp.py` script automates testing with different network conditions.

**Loss Experiment** (vary packet loss from 1% to 5%):
```bash
sudo python3 p1_exp.py loss
```

**Jitter Experiment** (vary delay jitter from 20ms to 100ms):
```bash
sudo python3 p1_exp.py jitter
```

Results are saved to CSV files: `reliability_loss.csv` and `reliability_jitter.csv`

#### Manual Mininet Testing

For custom testing:

```bash
# Start Mininet with custom topology
sudo mn --custom your_topology.py --topo custom --controller=remote,ip=127.0.0.1

# In Mininet CLI:
mininet> h1 python3 p1_server.py 10.0.0.1 6555 5900 &
mininet> h2 python3 p1_client.py 10.0.0.1 6555

# Add packet loss (5%) and delay (20ms) to link h1-s1:
mininet> h1 tc qdisc add dev h1-eth0 root netem loss 5% delay 20ms

# Check if transfer succeeded:
mininet> h2 md5sum received_data.txt
mininet> h1 md5sum data.txt
```

## Verification

### Check File Integrity

```bash
# Compare MD5 hashes
md5sum data.txt
md5sum received_data.txt
```

They should match: `cc83be85db391e9396e1427b3e124968`

### Check File Size

```bash
ls -lh data.txt received_data.txt
```

Both should be 6463538 bytes (6.2 MB)

## Implementation Details

### Packet Format

Each packet consists of:
- **4 bytes**: Sequence number (byte offset in file)
- **16 bytes**: Reserved (for future use: SACK, timestamps, etc.)
- **Up to 1180 bytes**: Data payload

Total maximum: 1200 bytes per UDP packet

### Server Behavior

1. **Binds** to specified IP and port
2. **Waits** for 1-byte client request
3. **Reads** data.txt and splits into packets
4. **Sends** packets within sliding window
5. **Receives** ACKs and updates window
6. **Retransmits** on:
   - Timeout (adaptive RTO)
   - 3 duplicate ACKs (fast retransmit)
7. **Sends** EOF packet to signal completion

### Client Behavior

1. **Sends** file request with up to 5 retries (2s timeout each)
2. **Receives** packets in sequence
3. **Buffers** out-of-order packets
4. **Sends** cumulative ACKs for all received data
5. **Writes** complete file to `received_data.txt`

### Key Features

**Adaptive Timeout (RTO)**:
- Uses exponential weighted moving average of RTT
- Formula: `RTO = EstimatedRTT + 4 × DevRTT`
- Clamped between 0.2s and 2.0s
- Doubles on timeout (exponential backoff)

**Fast Retransmit**:
- Triggers on 3rd duplicate ACK
- Avoids waiting for timeout
- Significantly improves performance with packet loss

**Sliding Window**:
- Limits bytes "in flight" to SWS
- Allows pipelining for better throughput
- Similar to TCP's send window

## Performance Analysis

### Benchmark Results (from part1.txt)

**Loss Experiment** (20ms delay, varying loss):
```
Loss | Delay | Jitter | Time (s)
-----|-------|--------|----------
1%   | 20ms  | 0ms    | 53
2%   | 20ms  | 0ms    | 58
3%   | 20ms  | 0ms    | 63
4%   | 20ms  | 0ms    | 68
5%   | 20ms  | 0ms    | 77
```

**Jitter Experiment** (20ms delay, 1% loss, varying jitter):
```
Loss | Delay | Jitter | Time (s)
-----|-------|--------|----------
1%   | 20ms  | 20ms   | 55
1%   | 20ms  | 40ms   | 64
1%   | 20ms  | 60ms   | 77
1%   | 20ms  | 80ms   | 92
1%   | 20ms  | 100ms  | 103
```

### Observations

1. **Loss Impact**: Transfer time increases approximately linearly with packet loss (53s @ 1% → 77s @ 5%)
2. **Jitter Impact**: Higher jitter significantly degrades performance due to timeout variations (55s @ 20ms → 103s @ 100ms)
3. **Efficiency**: With proper tuning, retransmissions are minimized through fast retransmit

## Debugging Tips

### Enable Verbose Output

The implementation already includes detailed logging:
- Server shows: progress, RTO, retransmissions
- Client shows: bytes received, packets, ACKs, buffered packets

### Common Issues

**1. "Connection refused" or timeout:**
- Check firewall settings
- Verify IP and port are correct
- Ensure server starts before client

**2. MD5 mismatch:**
- Check for bugs in sequence number handling
- Verify EOF detection logic
- Check buffer management for out-of-order packets

**3. Slow transfer:**
- Increase SWS (sender window size)
- Check if fast retransmit is working
- Verify RTO is reasonable (not too large)

**4. In Mininet - "command not found":**
- Ensure scripts are in current directory or use full paths
- Check Python version (use python3)

### Monitoring in Mininet

```bash
# In Mininet CLI:

# Monitor packets in real-time
mininet> h1 tcpdump -i h1-eth0 udp port 6555 &

# Check link statistics
mininet> h1 ifconfig h1-eth0

# Monitor bandwidth
mininet> h1 iperf -s &
mininet> h2 iperf -c 10.0.0.1
```

## Optimization Tips

For competitive performance:

1. **Tune SWS**: Balance between throughput and congestion
   - Too small: Poor utilization
   - Too large: Excessive retransmissions
   - Good starting point: 4-8 packets (4720-9440 bytes)

2. **Optimize RTO**: 
   - Current implementation uses TCP-style estimation
   - Consider adjusting α (0.125) and β (0.25) parameters

3. **Improve Fast Retransmit**:
   - Current: 3 duplicate ACKs
   - Could optimize based on RTT variance

4. **Add SACK Support**:
   - Use reserved 16 bytes for selective ACKs
   - Reduces unnecessary retransmissions

## Next Steps

After Part 1 is working well:
1. Analyze performance vs. benchmarks
2. Optimize for competitive ranking
3. Move to Part 2: Add congestion control

## Contact

For issues or questions, refer to the assignment PDF or consult with TAs.
