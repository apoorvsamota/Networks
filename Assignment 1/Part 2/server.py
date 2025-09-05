#!/usr/bin/env python3
import socket, json, selectors, threading, queue

# --- config & data ---
cfg = json.load(open("config.json"))
HOST, PORT = cfg["server_ip"], int(cfg["server_port"])
words = [w.strip() for w in open(cfg.get("filename", "words.txt"))
         .read().replace("\n", "").split(",") if w.strip()]

def reply_line(p: int, k: int) -> str:
    n = len(words)
    if k <= 0 or p < 0 or p >= n:
        return "EOF\n"
    end = min(p + k, n)
    out = ",".join(words[p:end])
    if end == n:
        out += ",EOF"
    return out + "\n"

# --- global FIFO of *requests* (not connections) ---
req_q: "queue.Queue[tuple[socket.socket,str]]" = queue.Queue()

def worker():
    # Process requests strictly FIFO; one reply per dequeued request
    while True:
        c, line = req_q.get()
        try:
            try:
                ps, ks = line.strip().split(",", 1)
                p, k = int(ps), int(ks)
            except Exception:
                out = "EOF\n"
            else:
                out = reply_line(p, k)
            try:
                c.sendall(out.encode())
            except Exception:
                # client might have gone away; ignore
                pass
        finally:
            req_q.task_done()

# --- event loop: accept sockets, read at most ONE line per readable socket, enqueue it ---
sel = selectors.DefaultSelector()
ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
ls.bind((HOST, PORT))
ls.listen(128)
ls.setblocking(False)
sel.register(ls, selectors.EVENT_READ)

buffers: dict[socket.socket, bytearray] = {}

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

def main():
    print(f"FCFS-per-request server listening on {HOST}:{PORT} ({cfg.get('filename')})", flush=True)
    threading.Thread(target=worker, daemon=True).start()

    while True:
        for key, _ in sel.select(timeout=0.5):
            if key.fileobj is ls:
                c, _ = ls.accept()
                c.setblocking(False)
                sel.register(c, selectors.EVENT_READ)
                buffers[c] = bytearray()
            else:
                c = key.fileobj
                try:
                    data = c.recv(4096)
                except Exception:
                    close_conn(c); continue
                if not data:
                    close_conn(c); continue
                b = buffers[c]
                b.extend(data)
                # Extract at most ONE complete line → one request enqueued
                i = b.find(b"\n")
                if i != -1:
                    line = b[:i].decode(errors="ignore")
                    del b[:i+1]
                    req_q.put((c, line))
                # If no full line yet, keep accumulating; next readiness event will enqueue it

if __name__ == "__main__":
    main()
