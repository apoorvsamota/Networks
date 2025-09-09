import argparse, json, socket, time

def load_words(path, limit=760):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        words = [w.strip() for w in f if w.strip()]
    return words[: min(limit, len(words))]

def run_client(server_ip, server_port, mode, batch_size, client_id, filename):
    words = load_words(filename)
    n = len(words)
    window = batch_size if mode == "greedy" else 1

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if hasattr(socket, "TCP_QUICKACK"):
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
        except OSError:
            pass
    s.connect((server_ip, server_port))
    s.setblocking(True)

    buf = bytearray()

    # ----- barrier handshake -----
    s.sendall(b"READY\n")
    # wait for single line "GO\n"
    while True:
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError("server closed before GO")
        buf.extend(chunk)
        j = buf.find(b"\n")
        if j != -1:
            line = bytes(buf[:j]); del buf[:j+1]
            if line != b"GO":
                # ignore any noise before GO (shouldn't happen)
                continue
            break

    # ----- measurement starts only after GO -----
    sent = recv = outstanding = 0
    time.sleep(0.02)  # 20 ms: lets every client reach the same start tick
    t0 = time.perf_counter()

    try:
        while recv < n:
            # fill pipeline to 'window'
            while sent < n and outstanding < window:
                s.sendall(words[sent].encode() + b"\n")
                sent += 1
                outstanding += 1

            # pull whatever acks have arrived
            chunk = s.recv(4096)
            if not chunk:
                raise RuntimeError("server closed")
            buf.extend(chunk)

            # consume all complete acks
            while True:
                i = buf.find(b"\n")
                if i == -1:
                    break
                del buf[:i+1]
                recv += 1
                outstanding -= 1
    finally:
        s.close()

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(json.dumps({
        "client_id": client_id,
        "mode": mode,
        "batch_size": batch_size,
        "processed": n,
        "elapsed_ms": elapsed_ms
    }), flush=True)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-ip", required=True)
    ap.add_argument("--server-port", type=int, required=True)
    ap.add_argument("--mode", choices=["greedy", "seq"], required=True)
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--client-id", type=int, default=0)
    ap.add_argument("--filename", required=True)
    args = ap.parse_args()
    cid = getattr(args, "client_id")
    run_client(args.server_ip, args.server_port, args.mode, args.batch_size, args.client_id, args.filename)
