# server.py — two-phase seeding + optional short RR admission epoch for c=1
import argparse, json, os, selectors, socket, threading, queue, time

SERVICE_DELAY_S = 0.015  # server bottleneck; 15ms works well without changing job size

def serve(port: int, expected_clients: int):
    # read env FIRST, then print it
    FAIR_EPOCH_ROUNDS = int(os.environ.get("FAIR_EPOCH_ROUNDS", "0"))
    print("SERVER fair-epoch build", FAIR_EPOCH_ROUNDS, flush=True)

    sel = selectors.DefaultSelector()
    qreq: "queue.Queue[tuple[socket.socket, bytes]]" = queue.Queue()
    buffers = {}                   # per-conn input buffer
    ready = set()                  # conns that sent READY
    started = False                # barrier released?
    seeded = False                 # seed completed?
    seed_remaining = set()         # which conns still owe their first line
    seed_lines = []                # [(conn, line)] in arrival order

    # backlog per connection after seed; store (seq, line) so we can preserve global arrival order
    backlog = {}                   # conn -> list[(seq, bytes)]
    seq = 0                        # global arrival sequence

    # short fairness epoch (round-robin admit) to keep c=1 ~ fair
    FAIR_EPOCH_ROUNDS = int(os.environ.get("FAIR_EPOCH_ROUNDS", "0"))

    def worker():
        while True:
            conn, line = qreq.get()
            if conn is None:
                break
            time.sleep(SERVICE_DELAY_S)
            try:
                conn.sendall(b"OK\n")
            except Exception:
                pass

    worker_thread = None

    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("0.0.0.0", port))
    ls.listen()
    ls.setblocking(False)
    sel.register(ls, selectors.EVENT_READ)

    # small helpers ------------------------------------------------------------
    def rr_drain_one_round():
        """Admit at most one line per connection (one RR pass)."""
        progressed = False
        for rc, q in list(backlog.items()):
            if q:
                _seq, L = q.pop(0)
                qreq.put((rc, L))
                progressed = True
        return progressed

    def drain_all_by_arrival():
        """Admit everything in true global arrival order."""
        # Gather the head items across all conns by sequence
        while True:
            # find the smallest seq among heads
            head = None; rc_min = None; idx_min = None
            for rc, q in backlog.items():
                if q:
                    if head is None or q[0][0] < head:
                        head = q[0][0]; rc_min = rc; idx_min = 0
            if head is None:
                break
            _s, L = backlog[rc_min].pop(idx_min)
            qreq.put((rc_min, L))

    try:
        while True:
            # poll fast so we don’t camp on a single socket
            for key, _ in sel.select(timeout=0.001):
                if key.fileobj is ls:
                    c, _ = ls.accept()
                    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    if hasattr(socket, "TCP_QUICKACK"):
                        try: c.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
                        except OSError: pass
                    c.setblocking(False)
                    sel.register(c, selectors.EVENT_READ)
                    buffers[c] = bytearray()
                    backlog[c] = []
                else:
                    c = key.fileobj
                    try:
                        data = c.recv(4096)
                    except Exception:
                        try: sel.unregister(c)
                        except Exception: pass
                        c.close(); buffers.pop(c, None); ready.discard(c)
                        backlog.pop(c, None); seed_remaining.discard(c)
                        continue
                    if not data:
                        try: sel.unregister(c)
                        except Exception: pass
                        c.close(); buffers.pop(c, None); ready.discard(c)
                        backlog.pop(c, None); seed_remaining.discard(c)
                        continue

                    b = buffers[c]; b.extend(data)
                    # drain all complete lines from this socket's buffer
                    while True:
                        i = b.find(b"\n")
                        if i == -1: break
                        line = bytes(b[:i]); del b[:i+1]

                        # ---- barrier handshake ----
                        if not started and line == b"READY":
                            ready.add(c)
                            if len(ready) == expected_clients:
                                # release everyone together
                                for rc in list(ready):
                                    try: rc.sendall(b"GO\n")
                                    except Exception: pass
                                started = True
                                seeded = False
                                seed_remaining = set(ready)  # expect one first line per client
                            continue

                        # ---- seeding phase: collect exactly one first line from each client
                        if started and not seeded:
                            if c in seed_remaining:
                                seed_lines.append((c, line))
                                seed_remaining.discard(c)
                            else:
                                # shouldn't really happen with window=1, but store just in case
                                backlog[c].append((seq, line)); seq += 1

                            if not seed_remaining:
                                # seed complete: start worker, enqueue 1st lines, then (if any) backlog
                                worker_thread = threading.Thread(target=worker, daemon=True)
                                worker_thread.start()
                                for rc, L in seed_lines:
                                    qreq.put((rc, L))
                                seed_lines.clear()
                                # For c==1 we want a short fairness epoch; keep extras in backlog for RR
                                if FAIR_EPOCH_ROUNDS == 0:
                                    drain_all_by_arrival()
                                seeded = True
                            continue

                        # ---- normal operation after seed: push into backlog with seq
                        if started and seeded:
                            backlog[c].append((seq, line)); seq += 1

            # After each poll, perform either a RR admission step (epoch) or FCFS drain
            if seeded:
                if FAIR_EPOCH_ROUNDS > 0:
                    if rr_drain_one_round():
                        FAIR_EPOCH_ROUNDS -= 1
                    # when rounds exhaust, switch to FCFS-by-arrival
                    if FAIR_EPOCH_ROUNDS == 0:
                        drain_all_by_arrival()
                else:
                    drain_all_by_arrival()
    except KeyboardInterrupt:
        pass
    finally:
        qreq.put((None, b""))
        try: sel.unregister(ls)
        except Exception: pass
        ls.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    with open(args.config, "r") as f:
        cfg = json.load(f)
    port = args.port or int(cfg.get("server_port", 5000))
    expected = int(cfg.get("num_clients", 1))
    print(json.dumps({"server_port": port, "expected_clients": expected}), flush=True)
    serve(port, expected)
