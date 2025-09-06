#!/usr/bin/env python3
import json, selectors, socket, threading, queue

# --- config & data ---
cfg = json.load(open("config.json"))
HOST = cfg.get("server_ip", "10.0.0.2")
PORT = int(cfg.get("server_port", 5000))
WORDS = [w.strip() for w in open(cfg.get("filename", "words.txt")).read()
         .replace("\n", "").split(",") if w.strip()]

def reply_line(p: int, k: int) -> str:
    n = len(WORDS)
    if k <= 0 or p < 0 or p >= n:  # out-of-range or done
        return "EOF\n"
    end = min(p + k, n)
    return ",".join(WORDS[p:end]) + ("\n" if end < n else "\nEOF\n")

# --- single worker (strict FCFS over one global queue) ---
REQ_Q = queue.Queue()
BUFFERS = {}

def worker():
    while True:
        c, line = REQ_Q.get()
        try:
            parts = line.strip().split(",")
            if len(parts) != 2:
                c.sendall(b"EOF\n"); continue
            p, k = int(parts[0]), int(parts[1])
            out = reply_line(p, k).encode()
            c.sendall(out)
        except Exception:
            try: c.close()
            except: pass

def close_conn(c):
    try:
        if c in BUFFERS: del BUFFERS[c]
    except: pass
    try: c.close()
    except: pass

def main():
    sel = selectors.DefaultSelector()
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((HOST, PORT)); ls.listen(); ls.setblocking(False)
    sel.register(ls, selectors.EVENT_READ)
    threading.Thread(target=worker, daemon=True).start()
    print(f"FCFS-per-request (admits bursts) on {HOST}:{PORT} ({cfg.get('filename')})", flush=True)

    while True:
        for key, _ in sel.select(timeout=0.5):
            if key.fileobj is ls:
                c, _ = ls.accept()
                c.setblocking(False)
                sel.register(c, selectors.EVENT_READ)
                BUFFERS[c] = bytearray()
            else:
                c = key.fileobj
                try:
                    data = c.recv(4096)
                except Exception:
                    close_conn(c); continue
                if not data:
                    close_conn(c); continue
                b = BUFFERS[c]; b.extend(data)

                # Enqueue ALL complete lines (burst-friendly)
                while True:
                    i = b.find(b"\n")
                    if i == -1: break
                    line = b[:i].decode(errors="ignore")
                    del b[:i+1]
                    REQ_Q.put((c, line))

if __name__ == "__main__":
    main()
