#!/usr/bin/env python3
import socket
import threading
import queue
import json
import argparse
import sys

# --- Helper functions (load_config, load_words, handle_request) are unchanged ---

def load_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def load_words(filename):
    try:
        with open(filename, "r") as f:
            raw = f.read()
    except FileNotFoundError:
        print(f"[server] words file not found: {filename}", file=sys.stderr)
        return []

    words, cur = [], []
    for ch in raw:
        if ch == ',':
            if cur:
                words.append(''.join(cur))
                cur = []
        elif ch not in ('\n', '\r', ' '):
            cur.append(ch)
    if cur:
        words.append(''.join(cur))
    return words

def handle_request(line, words):
    try:
        a, b = line.strip().split(',', 1)
        p = int(a)
        k = int(b)
    except Exception:
        return "EOF\n"

    if p < 0 or k <= 0 or p >= len(words):
        return "EOF\n"

    out = []
    end = min(len(words), p + k)
    out.extend(words[p:end])
    if p + k >= len(words):
        out.append("EOF")
    return ",".join(out) + "\n"

# --- NEW ARCHITECTURE ---

def client_reader_thread(cfd, addr, request_queue):
    """
    A dedicated thread for each connected client. Its only job is to
    read requests from its client and put them into the shared request_queue.
    """
    # print(f"[reader-{addr[1]}] Thread started.")
    try:
        while True:
            buf = bytearray()
            while True:
                b = cfd.recv(1)
                if not b:  # Client has disconnected
                    return
                buf.extend(b)
                if b == b'\n':
                    break

            line = buf.decode('utf-8')
            if not line.strip():  # Ignore empty lines
                continue

            # Put the request and a reference to the client socket in the queue
            # print(f"[reader-{addr[1]}] [cfd-{cfd}] Received request: {line.strip()}")
            request_queue.put((line, cfd))
    except (ConnectionResetError, BrokenPipeError):
        print(f"[reader-{addr[1]}] Client disconnected abruptly.")
    finally:
        print(f"[reader-{addr[1]}] Thread finished.")
        cfd.close()

def worker_thread(request_queue, words):
    """
    A single worker thread that processes requests from the queue one by one,
    ensuring serialized, FCFS processing of individual requests.
    """
    print("[worker] Thread started.")
    while True:
        try:
            # Get a (request, client_socket) tuple from the queue
            line, cfd = request_queue.get()
            print(f"[worker] [cfd-{cfd}] Processing request: {line.strip()}")
            if line is None:  # Shutdown signal
                break

            resp = handle_request(line, words)
            cfd.sendall(resp.encode())
        except (ConnectionResetError, BrokenPipeError):
            # The client might have disconnected while its request was in the queue.
            # This is fine, we just ignore the error and move to the next request.
            print(f"[worker] Client disconnected before response could be sent.")
        except Exception as e:
            print(f"[worker] An error occurred: {e}", file=sys.stderr)
        finally:
            request_queue.task_done()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()

    cfg = load_config(args.config)
    server_ip = cfg.get("server_ip", "0.0.0.0")
    server_port = int(cfg.get("server_port", 9090))
    filename = cfg.get("filename", "words.txt")

    words = load_words(filename)
    if not words:
        sys.exit(1)

    # This queue now holds (request_line, client_socket) tuples
    request_queue = queue.Queue()

    # Start the single worker thread
    worker = threading.Thread(target=worker_thread, args=(request_queue, words), daemon=True)
    worker.start()

    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind((server_ip, server_port))
    lsock.listen(64)
    print(f"[server] Listening for connections on {server_ip}:{server_port}")

    try:
        while True:
            # Accept a new connection
            cfd, addr = lsock.accept()
            print(f"[server] Accepted connection from {addr}")

            # For each client, spawn a dedicated reader thread
            reader = threading.Thread(target=client_reader_thread, args=(cfd, addr, request_queue), daemon=True)
            reader.start()

    except KeyboardInterrupt:
        print("\n[server] Shutting down...")
    finally:
        request_queue.put((None, None))  # Signal worker to stop
        lsock.close()
        print("[server] Stopped.")

if __name__ == "__main__":
    main()
