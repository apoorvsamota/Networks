#!/usr/bin/env python3
import subprocess, json, os, time, statistics

cfg = json.load(open("config.json"))
CLIENTS     = int(cfg.get("num_clients", 10))
GREEDY_IDX  = int(cfg.get("greedy_index", 0))
C_SINGLE    = int(cfg.get("greedy_window", cfg.get("c", 20)))  # batch size c for the greedy
GREEDY_LEAD = float(cfg.get("greedy_first_delay", 0.05))
SPAWN_GAP   = float(cfg.get("inter_spawn_delay", 0.01))

def jfi(vals):
    n = len(vals); s = sum(vals); s2 = sum(v*v for v in vals)
    return 0.0 if n==0 or s2==0 else (s*s)/(n*s2)

def launch(mode, batch, cid):
    env = os.environ.copy()
    env["CLIENT_MODE"] = mode
    env["BATCH_SIZE"]  = str(batch)
    env["CLIENT_ID"]   = str(cid)
    return subprocess.Popen(["python3", "client.py"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def run_once():
    procs = []
    # Launch greedy first (small head-start, like your friend)
    if 0 <= GREEDY_IDX < CLIENTS:
        procs.append((GREEDY_IDX, launch("greedy", C_SINGLE, GREEDY_IDX)))
        time.sleep(GREEDY_LEAD)
    # Others sequential (no barrier)
    for i in range(CLIENTS):
        if i == GREEDY_IDX: continue
        procs.append((i, launch("seq", 1, i)))
        time.sleep(SPAWN_GAP)

    times = [None]*CLIENTS
    for i, p in procs:
        out, err = p.communicate()
        payload = None
        for ln in reversed([ln for ln in out.splitlines() if ln.strip()]):
            try:
                payload = json.loads(ln); break
            except json.JSONDecodeError:
                continue
        if payload is None:
            raise RuntimeError(f"client {i} produced no JSON.\nstdout:\n{out}\nstderr:\n{err}")
        times[i] = float(payload["time"])
    shares = [(1.0/t) if t and t>0 else 0.0 for t in times]
    return jfi(shares), times

if __name__ == "__main__":
    jf, times = run_once()
    print("JFI(time-based):", f"{jf:.6f}")
    print("Times:", ",".join(f"{t:.6f}" for t in times))
