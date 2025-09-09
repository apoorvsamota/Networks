import json
import subprocess
import time
import os
import matplotlib.pyplot as plt
import numpy as np

def jains_fairness_index(times):
    """Calculate Jain's Fairness Index for completion times"""
    numerator = sum(times) ** 2
    denominator = len(times) * sum(t**2 for t in times)
    return numerator / denominator if denominator != 0 else 0

def run_experiment(c_value):
    print(f"Running experiment with c={c_value}")
    
    # Load config
    with open("config.json", "r") as f:
        config = json.load(f)
    
    # Start server
    server_process = subprocess.Popen(["python3", "server.py"])
    time.sleep(2)  # Give server time to start
    
    # Start clients (9 regular, 1 greedy)
    client_processes = []
    
    # Start regular clients
    for i in range(9):
        proc = subprocess.Popen([
            "python3", "client.py", 
            "--c", "1"
        ])
        client_processes.append(proc)
    
    # Start greedy client
    proc = subprocess.Popen([
        "python3", "client.py", 
        "--c", str(c_value)
    ])
    client_processes.append(proc)
    
    # Wait for all clients to finish
    for proc in client_processes:
        proc.wait()
    
    # Collect results
    completion_times = []
    for i in range(9):
        try:
            with open(f"result_{i}.json", "r") as f:
                data = json.load(f)
                completion_times.append(data["completion_time"])
        except:
            completion_times.append(0)  # Client failed
    
    try:
        with open("result_greedy.json", "r") as f:
            data = json.load(f)
            completion_times.append(data["completion_time"])
    except:
        completion_times.append(0)  # Greedy client failed
    
    # Clean up result files
    for i in range(9):
        try:
            os.remove(f"result_{i}.json")
        except:
            pass
    try:
        os.remove("result_greedy.json")
    except:
        pass
    
    # Stop server
    server_process.terminate()
    server_process.wait()
    
    # Calculate JFI
    jfi = jains_fairness_index(completion_times)
    print(f"Completion times: {completion_times}")
    print(f"JFI for c={c_value}: {jfi}")
    
    return jfi

def main():
    # Run experiments for c from 1 to 10
    c_values = list(range(1, 11))
    jfi_values = []
    
    for c in c_values:
        jfi = run_experiment(c)
        jfi_values.append(jfi)
        time.sleep(2)  # Brief pause between experiments
    
    # Plot results
    plt.figure(figsize=(10, 6))
    plt.plot(c_values, jfi_values, 'o-', linewidth=2, markersize=8)
    plt.xlabel('Number of parallel requests (c)')
    plt.ylabel("Jain's Fairness Index (JFI)")
    plt.title('Fairness vs. Number of Parallel Requests')
    plt.grid(True, alpha=0.3)
    plt.xticks(c_values)
    plt.ylim(0, 1.1)
    
    # Save plot
    plt.savefig('p3_plot.png', dpi=300, bbox_inches='tight')
    plt.show()
    
    # Save results to file
    with open('results.json', 'w') as f:
        json.dump({'c_values': c_values, 'jfi_values': jfi_values}, f)

if __name__ == "__main__":
    main()