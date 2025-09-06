#!/usr/bin/env python3

import json
import os
import time
import glob
import numpy as np
import csv
import subprocess
import sys
import matplotlib.pyplot as plt
# Make sure your topo_wordcount.py is in the same directory
from topo_wordcount import make_net


class Runner:
    def __init__(self, config_file='config.json'):
        with open(config_file, 'r') as f:
            self.config = json.load(f)

        # We don't need server_ip and port from config as Mininet assigns them.
        self.num_clients = self.config['num_clients']
        self.c = self.config['c']  # Starting batch size for rogue client
        self.p = self.config['p']
        self.k = self.config['k']

        # Determine the directory this script is in for robust file paths
        self.script_dir = os.path.dirname(os.path.realpath(__file__))

        print(f"Config: {self.num_clients} clients, starting c={self.c}, p={self.p}, k={self.k}")

    def cleanup_logs(self):
        """Clean old log and error files."""
        log_dir = os.path.join(self.script_dir, "logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        files_to_delete = glob.glob(os.path.join(log_dir, "*.log"))
        for f in files_to_delete:
            os.remove(f)
        print("Cleaned old logs")

    def parse_logs(self):
        """
        Parse log files by reading the single integer value.
        The client writes ONLY the elapsed milliseconds to its .log file.
        """
        results = {'rogue': [], 'normal': []}
        log_dir = os.path.join(self.script_dir, "logs")
        log_files = glob.glob(os.path.join(log_dir, "*.log"))

        # Exclude error logs from parsing for results
        log_files = [f for f in log_files if not f.endswith('.err.log')]

        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    # Read the first line and convert to int
                    time_ms = int(f.readline().strip())

                    if 'rogue' in os.path.basename(log_file):
                        results['rogue'].append(time_ms)
                    else:
                        results['normal'].append(time_ms)
            except (IOError, ValueError, IndexError) as e:
                print(f"Warning: Could not parse log file {log_file}. Error: {e}", file=sys.stderr)

        return results

    def calculate_jfi(self, completion_times):
        """
        Calculate Jain's Fairness Index using a more robust
        definition of throughput (words/sec).
        """
        all_times_ms = completion_times['rogue'] + completion_times['normal']
        if not all_times_ms:
            return 0.0

        # JFI requires a "more is better" metric. We convert time to throughput.
        total_words = 760

        throughputs = [(total_words / (t / 1000.0)) for t in all_times_ms if t > 0]

        if not throughputs:
            return 0.0

        n = len(throughputs)
        sum_x = sum(throughputs)
        sum_x_squared = sum(x * x for x in throughputs)

        jfi = (sum_x * sum_x) / (n * sum_x_squared) if sum_x_squared > 0 else 0
        return jfi

    def run_experiment(self, c_value):
        """
        Run a single experiment within a Mininet topology.
        """
        print(f"\nRunning experiment with c={c_value}")

        self.cleanup_logs()

        net = make_net(num_clients=self.num_clients)
        net.start()

        server_proc = None
        client_procs = []

        try:
            # Get Mininet host objects
            server_host = net.get('h_srv')
            client_hosts = [net.get(f'h_cli_{i}') for i in range(1, self.num_clients + 1)]

            # Define file paths within the Mininet environment
            server_path = os.path.join(self.script_dir, "server.py")
            client_path = os.path.join(self.script_dir, "client.py")
            config_path = os.path.join(self.script_dir, "config.json")

            print("Starting server...")
            server_cmd = f"python3 -u \"{server_path}\" --config \"{config_path}\""
            server_proc = server_host.popen(server_cmd, shell=True,
                                            stdout=sys.stdout, stderr=sys.stderr)
            time.sleep(2)  # Wait for server to start

            # Start rogue client
            print(f"Starting rogue client (c={c_value})...")
            rogue_cmd = (f"python3 \"{client_path}\" --config \"{config_path}\" "
                         f"--batch-size {c_value} --client-id rogue --debug")
            client_procs.append(client_hosts[0].popen(rogue_cmd, shell=True))

            # Start normal clients
            print(f"Starting {self.num_clients - 1} normal clients...")
            for i in range(1, self.num_clients):
                client_host = client_hosts[i]
                client_id = f"normal_{i+1}"
                normal_cmd = (f"python3 \"{client_path}\" --config \"{config_path}\" "
                              f"--batch-size 1 --client-id {client_id} --debug")
                client_procs.append(client_host.popen(normal_cmd, shell=True))

            # Wait for all clients to finish
            print("Waiting for clients to complete...")
            for proc in client_procs:
                proc.wait()

        except Exception as e:
            print(f"ERROR: An error occurred during the experiment: {e}", file=sys.stderr)
        finally:
            print("Cleaning up Mininet...")
            if server_proc and server_proc.poll() is None:
                server_proc.kill()
            net.stop()

        time.sleep(1)  # Give a moment for log files to be fully written
        return self.parse_logs()

    def run_varying_c(self):
        """Run experiments with c starting from config value, up to 20."""
        c_values = list(range(self.c, 100, 4))
        all_results = []

        results_dir = os.path.join(self.script_dir, 'results2')
        os.makedirs(results_dir, exist_ok=True)

        csv_path = os.path.join(results_dir, 'experiment_results.csv')
        # Write header to CSV file once before the loop starts
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['c_value', 'jfi', 'avg_rogue_ms', 'avg_normal_ms'])

        for c in c_values:
            results = self.run_experiment(c)

            jfi = self.calculate_jfi(results)
            rogue_avg = np.mean(results['rogue']) if results['rogue'] else 0
            normal_avg = np.mean(results['normal']) if results['normal'] else 0

            summary = {
                'c': c, 'jfi': jfi,
                'avg_rogue_ms': rogue_avg, 'avg_normal_ms': normal_avg
            }
            all_results.append(summary)

            # Open the file in append mode and create a new writer
            with open(csv_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([c, jfi, rogue_avg, normal_avg])

            print(f"--> Results for c={c}: JFI={jfi:.4f}, Rogue Time={rogue_avg:.2f}ms, Normal Time={normal_avg:.2f}ms")

        print("\nAll experiments completed.")
        self.plot_results(all_results)

        # Write a detailed summary.txt file
        summary_path = os.path.join(results_dir, 'summary.txt')
        with open(summary_path, 'w') as f:
            f.write("Experiment Summary\n")
            f.write("==================\n\n")
            for result in all_results:
                f.write(f"Batch Size (c) = {result['c']}\n")
                f.write(f" - Jain's Fairness Index: {result['jfi']:.4f}\n")
                f.write(f" - Avg. Rogue Client Time: {result['avg_rogue_ms']:.2f} ms\n")
                f.write(f" - Avg. Normal Client Time: {result['avg_normal_ms']:.2f} ms\n\n")
        print(f"Detailed summary saved to {summary_path}")

    def plot_results(self, results_data):
        """MODIFIED: Plot JFI values only vs c values"""
        if not results_data:
            print("No results to plot.")
            return

        results_dir = os.path.join(self.script_dir, 'results2')

        c_values = [r['c'] for r in results_data]
        jfi_values = [r['jfi'] for r in results_data]

        plt.figure(figsize=(10, 6))
        plt.plot(c_values, jfi_values, 'o-', color='blue', label='JFI')

        plt.xlabel('Greediness Factor (c)')
        plt.ylabel("Jain's Fairness Index (JFI)")
        plt.title("Fairness Index vs. Rogue Client Greediness")
        plt.grid(True)
        plt.ylim(0, 1.1)
        plt.xticks(c_values)
        plt.legend()

        plot_path = os.path.join(results_dir, 'jfi_vs_c.png')
        plt.savefig(plot_path)
        print(f"Plot saved to {plot_path}")
        plt.close()


def main():
    runner = Runner()
    runner.run_varying_c()


if __name__ == '__main__':
    main()
