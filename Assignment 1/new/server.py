#!/usr/bin/env python3
# FCFS server: enqueues each newline-terminated request from any client into
# a single global queue and serves them strictly in arrival order.
#
# IMPORTANT: We intentionally add a small per-request processing time so that
# a greedy client (sending a batch of size c) can monopolize the server under FCFS.
# Do NOT read this from config.json per user's constraint (no service_ms key).
#
# TUNE: Per-request processing time (seconds)
PROC_TIME_S = 0.003   # 3 ms per request

import json, os, socket, selectors, threading, queue, time, sys, errno

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

with open(CONFIG_PATH, "r") as f:
    cfg = json.load(f)

HOST = cfg.get("server_ip", "10.0.0.2")
PORT = int(cfg.get("server_port", 5000))
BACKLOG = 128

sel = selectors.DefaultSelector()
req_q: "queue.Queue[tuple[socket.socket, bytes]]" = queue.Queue()
buffers = {}

def accept(sock):
    c, addr = sock.accept()
    c.setblocking(False)
    sel.register(c, selectors.EVENT_READ)
    buffers[c] = bytearray()

def close_conn(c: socket.socket):
    try:
        sel.unregister(c)
    except Exception:
        pass
    try:
        c.close()
    except Exception:
        pass
    buffers.pop(c, None)

def reader_loop(lsock):
    while True:
        for key, _ in sel.select(timeout=0.5):
            if key.fileobj is lsock:
                accept(lsock)
            else:
                c = key.fileobj
                try:
                    data = c.recv(4096)
                except Exception:
                    close_conn(c); continue
                if not data:
                    close_conn(c); continue
                b = buffers.get(c)
                if b is None:
                    # closed concurrently
                    continue
                b.extend(data)
                while True:
                    i = b.find(b"\n")
                    if i == -1: break
                    line = bytes(b[:i])  # one request
                    del b[:i+1]
                    # Enqueue in global FCFS queue
                    req_q.put((c, line))

def worker_loop():
    while True:
        c, line = req_q.get()
        # Simulate work per request
        t_end = time.perf_counter() + PROC_TIME_S
        # Sleep-based timing that's friendly inside Mininet
        while True:
            now = time.perf_counter()
            if now >= t_end: break
            time.sleep(min(0.0005, t_end - now))
        # Respond minimal bytes
        try:
            c.sendall(b"OK\n")
        except Exception:
            # client may have disconnected
            pass

def main():
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((HOST, PORT))
    ls.listen(BACKLOG)
    ls.setblocking(False)
    sel.register(ls, selectors.EVENT_READ)
    print(f"FCFS server listening on {HOST}:{PORT}", flush=True)

    t_reader = threading.Thread(target=reader_loop, args=(ls,), daemon=True)
    t_worker = threading.Thread(target=worker_loop, daemon=True)
    t_reader.start()
    t_worker.start()

    # Run forever; Ctrl+C from runner will stop the process
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
