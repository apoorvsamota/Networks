#!/usr/bin/env python3
import socket
import time
import json
import argparse
import sys
import os


def load_config(path):
    """Loads a JSON configuration file."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def read_one_line(sock):
    """Helper function to read a single newline-terminated line from a socket."""
    buf = bytearray()
    while True:
        b = sock.recv(1)
        if not b or b == b'\n':
            break
        buf.extend(b)
    return buf.decode('utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--client-id", type=str, required=True)
    # NEW: Keep the debug flag to enable/disable logging
    ap.add_argument("--debug", action="store_true", help="Enable detailed debug logging to a file.")
    args = ap.parse_args()

    # --- NEW: Setup for error/debug logging to a dedicated file ---
    error_log_handle = None
    if args.debug:
        # Ensure the logs directory exists
        os.makedirs("logs", exist_ok=True)
        error_log_path = f"logs/{args.client_id}.err.log"
        # Open the file in write mode, which overwrites it on each run
        error_log_handle = open(error_log_path, 'w')

    # Helper for conditional printing to the error log file
    def debug_print(msg):
        if error_log_handle:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            error_log_handle.write(f"[{timestamp}] [{args.client_id}] {msg}\n")
            error_log_handle.flush()  # Flush to see logs in real-time

    cfg = load_config(args.config)
    ip = cfg.get("server_ip", "127.0.0.1")
    port = int(cfg.get("server_port", 9090))
    p = int(cfg.get("p", 0))
    k = int(cfg.get("k", 5))

    print(f"[client] Starting client. IP={ip}, Port={port}, Batch Size (c)={args.batch_size}")

    all_words = []
    eof_reached = False
    t0 = time.time()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(20.0)
            # debug_print(f"Connecting to {ip}:{port}...")
            s.connect((ip, port))
            # debug_print("Connection successful.")

            loop_count = 0
            while not eof_reached:
                loop_count += 1
                # debug_print(f"--- Loop #{loop_count}: Starting to send batch ---")

                requests_sent = 0
                for i in range(args.batch_size):
                    if eof_reached:
                        # debug_print("EOF was reached in previous batch, breaking send loop.")
                        break
                    request_str = f"{p},{k}"
                    request = f"{request_str}\n".encode('utf-8')
                    # debug_print(f"Sending request {i+1}/{args.batch_size}: '{request_str}'")
                    s.sendall(request)
                    p += k
                    requests_sent += 1

                print(f"[client] Batch of {requests_sent} requests sent. Now waiting for responses.")

                for i in range(requests_sent):
                    response_line = read_one_line(s)
                    # debug_print(f"Received response {i+1}/{requests_sent}: '{response_line.strip()}'")

                    if not response_line:
                        # debug_print("Received empty response. Server likely closed connection.")
                        eof_reached = True
                        break

                    received_words = response_line.strip().split(',')
                    if "EOF" in received_words:
                        # debug_print("EOF token found in response.")
                        eof_reached = True
                        received_words.remove("EOF")

                    all_words.extend(w for w in received_words if w)

            # debug_print("Download loop finished.")

    except socket.timeout:
        err_msg = f"[{args.client_id}] Connection timed out."
        debug_print(f"ERROR: {err_msg}")
        # Also print to stderr as a fallback if debug is off
        print(err_msg, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        err_msg = f"[{args.client_id}] An error occurred: {e}"
        debug_print(f"ERROR: {err_msg}")
        print(err_msg, file=sys.stderr)
        sys.exit(1)

    t1 = time.time()
    elapsed_ms = int(round((t1 - t0) * 1000))
    # print(f"{args.batch_size} ELAPSED_MS:{elapsed_ms}")
    debug_print(f"Total words downloaded: {len(all_words)}. Writing completion time log.")
    debug_print(f"Elapsed time: {elapsed_ms} ms.")

    # Save final completion time to its own log file for the runner
    completion_log_file = f"logs/{args.client_id}.log"
    with open(completion_log_file, "w") as f:
        f.write(f"{elapsed_ms}\n")

    # This print to stdout is required by the runner script to parse results
    # print(f"ELAPSED_MS:{elapsed_ms}")

    if not args.quiet:
        counts = {word: all_words.count(word) for word in set(all_words)}
        for word, count in sorted(counts.items()):
            # print(f"{word},{count}")
            pass

    # --- NEW: Close the error log file handle at the end ---
    if error_log_handle:
        debug_print("Client finished.")
        error_log_handle.close()


if __name__ == "__main__":
    main()
