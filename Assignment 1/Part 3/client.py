#!/usr/bin/env python3
import socket, json, time, os, select

cfg = json.load(open("config.json"))
HOST = cfg.get("server_ip", "10.0.0.2")
PORT = int(cfg.get("server_port", 5000))
K    = int(cfg.get("k", 5))
P0   = int(cfg.get("p", 0))

MODE = os.environ.get("CLIENT_MODE", "seq")   # "seq" or "greedy"
BATCH= int(os.environ.get("BATCH_SIZE", "1")) # window size for greedy
CID  = int(os.environ.get("CLIENT_ID", "-1"))

def readline_bytewise(sock: socket.socket) -> str:
    buf = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch: return ""
        buf.extend(ch)
        if ch == b"\n":
            return buf.decode(errors="ignore")

def seq() -> float:
    t0 = time.perf_counter()
    p = P0
    with socket.create_connection((HOST, PORT)) as s:
        while True:
            s.sendall(f"{p},{K}\n".encode())
            line = readline_bytewise(s)
            if not line or "EOF" in line:
                break
            p += K
    return time.perf_counter() - t0

def greedy_window(c: int) -> float:
    c = max(1, c)
    p = P0
    t0 = time.perf_counter()
    with socket.create_connection((HOST, PORT), timeout=60) as s:
        if IO_MODE == "line":
            fin = s.makefile("r", encoding="utf-8", newline="\n")
            inflight, saw_eof = 0, False
            # prime window
            while inflight < c and not saw_eof:
                s.sendall(f"{p},{K}\n".encode()); p += K; inflight += 1
            while inflight > 0:
                line = fin.readline(); inflight -= 1
                if not line or "EOF" in line: saw_eof = True
                if not saw_eof:
                    s.sendall(f"{p},{K}\n".encode()); p += K; inflight += 1
        else:
            inflight, saw_eof = 0, False
            while inflight < c and not saw_eof:
                s.sendall(f"{p},{K}\n".encode()); p += K; inflight += 1
            while inflight > 0:
                line = readline_bytewise(s); inflight -= 1
                if not line or "EOF" in line: saw_eof = True
                if not saw_eof:
                    s.sendall(f"{p},{K}\n".encode()); p += K; inflight += 1
    return time.perf_counter() - t0




def main():
    elapsed = greedy_window(BATCH) if MODE == "greedy" else seq()
    print(json.dumps({"time": elapsed, "id": CID, "mode": MODE}))

if __name__ == "__main__":
    main()
