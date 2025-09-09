#!/usr/bin/env python3
import socket, json, time, threading

cfg = json.load(open("config.json"))
HOST, PORT = cfg["server_ip"], int(cfg["server_port"])
K   = int(cfg["k"])
P0  = int(cfg.get("p", 0))
N   = int(cfg.get("num_clients", 1))
C   = int(cfg.get("c", 1))

def one_client() -> float:
    p = P0
    t0 = time.perf_counter()
    with socket.create_connection((HOST, PORT), timeout=60) as s:
        fin = s.makefile("r", encoding="utf-8", newline="\n")
        while True:
            s.sendall(f"{p},{K}\n".encode())  # send one request
            line = fin.readline()             # ... WAIT for reply
            if not line:
                break
            toks = line.strip().split(",")
            if "EOF" in toks:
                break
            p += K
    return time.perf_counter() - t0

def greedy_client() -> float:
    p = P0
    t0 = time.perf_counter()
    with socket.create_connection((HOST, PORT), timeout=60) as s:
        fin = s.makefile("r", encoding="utf-8", newline="\n")
        rem = True
        while rem:
          for _ in range(C):
              s.sendall(f"{p},{K}\n".encode())
              p += K
          replies = 0
          while replies < C:
              line = fin.readline()             
              if not line:
                  rem = False
                  break
              toks = line.strip().split(",")
              if "EOF" in toks:
                  rem = False
                  break
              replies += 1
          
    return time.perf_counter() - t0

def worker(bar, out, i):
    bar.wait()                 # start together
    if i==0:
        out[i] = greedy_client()
    else:
        out[i] = one_client()

if __name__ == "__main__":
    times = [0.0]*N
    barrier = threading.Barrier(N)
    ts = [threading.Thread(target=worker, args=(barrier, times, i)) for i in range(N)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    print(",".join(f"{x:.6f}" for x in times))

