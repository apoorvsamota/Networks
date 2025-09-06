#!/usr/bin/env python3
# Client sends a batch of newline-terminated requests, waits for same number of replies,
# and prints a single JSON line containing the total completion time.
#
# No CLI flags. Runner sets environment variables:
#   CLIENT_MODE = "greedy" | "seq"
#   BATCH_SIZE  = integer (>=1)
#   CLIENT_ID   = integer
import os, json, socket, time

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

with open(CONFIG_PATH, "r") as f:
    cfg = json.load(f)

HOST = cfg.get("server_ip", "10.0.0.2")
PORT = int(cfg.get("server_port", 5000))

MODE = os.getenv("CLIENT_MODE", "seq")
BATCH = int(os.getenv("BATCH_SIZE", "1"))
CID = int(os.getenv("CLIENT_ID", "0"))

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    start = time.perf_counter()
    s.connect((HOST, PORT))
    # Send BATCH requests quickly
    payload = f"{CID}:{MODE} ping\n".encode()
    for _ in range(BATCH):
        s.sendall(payload)

    # Receive BATCH replies
    got = 0
    buf = bytearray()
    while got < BATCH:
        chunk = s.recv(4096)
        if not chunk: break
        buf.extend(chunk)
        while True:
            i = buf.find(b"\n")
            if i == -1: break
            del buf[:i+1]
            got += 1

    s.close()
    end = time.perf_counter()
    total = end - start
    print(json.dumps({"time": total, "id": CID, "mode": MODE}), flush=True)

if __name__ == "__main__":
    main()
