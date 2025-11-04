#!/bin/bash
# Quick test script for Part 2 congestion control

echo "=== Part 2 Congestion Control - Quick Test ==="
echo ""

# Check if files exist
if [ ! -f "p2_server.py" ]; then
    echo "ERROR: p2_server.py not found!"
    exit 1
fi

if [ ! -f "p2_client.py" ]; then
    echo "ERROR: p2_client.py not found!"
    exit 1
fi

# Create a test data file if it doesn't exist
if [ ! -f "data.txt" ]; then
    echo "Creating test data.txt (1 MB)..."
    dd if=/dev/urandom of=data.txt bs=1M count=1 2>/dev/null
fi

echo "Test file size: $(wc -c < data.txt) bytes"
echo ""

# Test 1: Basic functionality
echo "=== Test 1: Basic Functionality ==="
echo "Starting server on port 9999..."
python3 p2_server.py 127.0.0.1 9999 > server_test.log 2>&1 &
SERVER_PID=$!
sleep 1

echo "Starting client..."
python3 p2_client.py 127.0.0.1 9999 "test_" > client_test.log 2>&1 &
CLIENT_PID=$!

# Wait for client to finish (max 30 seconds)
TIMEOUT=30
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if ! ps -p $CLIENT_PID > /dev/null 2>&1; then
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

# Kill server
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null

# Check results
echo ""
if [ -f "test_received_data.txt" ]; then
    RECEIVED_SIZE=$(wc -c < test_received_data.txt)
    ORIGINAL_SIZE=$(wc -c < data.txt)
    
    echo "Original size:  $ORIGINAL_SIZE bytes"
    echo "Received size:  $RECEIVED_SIZE bytes"
    
    if [ "$RECEIVED_SIZE" -eq "$ORIGINAL_SIZE" ]; then
        # Verify MD5
        ORIGINAL_MD5=$(md5sum data.txt | awk '{print $1}')
        RECEIVED_MD5=$(md5sum test_received_data.txt | awk '{print $1}')
        
        echo "Original MD5:   $ORIGINAL_MD5"
        echo "Received MD5:   $RECEIVED_MD5"
        echo ""
        
        if [ "$ORIGINAL_MD5" = "$RECEIVED_MD5" ]; then
            echo "✓ SUCCESS: File transferred correctly!"
            
            # Show performance metrics from logs
            echo ""
            echo "=== Performance Metrics ==="
            echo "Server metrics:"
            grep -E "(Throughput|cwnd|Time:|Complete)" server_test.log | tail -5
            echo ""
            echo "Client metrics:"
            grep -E "(Throughput|Time:|Complete)" client_test.log | tail -3
        else
            echo "✗ FAILED: File corrupted (MD5 mismatch)!"
            exit 1
        fi
    else
        echo "✗ FAILED: Size mismatch!"
        exit 1
    fi
else
    echo "✗ FAILED: Output file not created!"
    exit 1
fi

echo ""
echo "=== Logs ==="
echo "Server log: server_test.log"
echo "Client log: client_test.log"
echo ""
echo "To see full server output: cat server_test.log"
echo "To see full client output: cat client_test.log"
echo ""
echo "Clean up test files: rm -f test_received_data.txt server_test.log client_test.log"
