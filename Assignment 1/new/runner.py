#!/usr/bin/env python3
# Builds Mininet (server + N clients), runs a sweep over greedy batch size c,
# computes Jain's Fairness Index on 1/time shares, and writes CSV/PNG.
# NO CLI FLAGS and NO service_ms usage (per-user constraint). All knobs from config.json.
import os, json, time, csv, statistics, signal, sys
from subprocess import PIPE
from topo_wordcount import make_net

# ---- Config (no service_ms) ----
CFG_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.json")
cfg = json.load(open(CFG_PATH))

CLIENTS     = int(cfg.get("num_clients", 10))
GREEDY_IDX  = int(cfg.get("greedy_index", 0))         # which client is greedy
ITERATIONS  = int(cfg.get("num_iterations", 5))
# c sweep defaults
C_START     = int(cfg.get("c_start", 1))
C_MAX       = int(cfg.get("c_max", 101))
C_STEP      = int(cfg.get("c_step", 4))

CSV_OUT     = cfg.get("time_results_csv", "time_results.csv")
PNG_OUT     = cfg.get("time_plot_png", "time_plot.png")

SCRIPT_DIR  = os.path.dirname(os.path.realpath(__file__))
SERVER_PATH = os.path.join(SCRIPT_DIR, "server.py")
CLIENT_PATH = os.path.join(SCRIPT_DIR, "client.py")

# Must match server's per-request processing time (PROC_TIME_S ≈ 0.003)
HEAD_START_PER_REQ_S = 0.0033  # small bias upward to ensure the greedy head-start dominates

def jfi(vals):
    n = len(vals)
    if n == 0: return 0.0
    s  = sum(vals); s2 = sum(v*v for v in vals)
    return 0.0 if s2 <= 0 else (s*s)/(n*s2)

def launch_on_host(host, mode, batch, cid):
    env = os.environ.copy()
    env["CLIENT_MODE"] = mode      # "greedy" | "seq"
    env["BATCH_SIZE"]  = str(batch)
    env["CLIENT_ID"]   = str(cid)
    env["PYTHONUNBUFFERED"] = "1"
    return host.popen(["python3", "-u", CLIENT_PATH], env=env, stdout=PIPE, stderr=PIPE, text=True)

def parse_time(out_text):
    import json as _json
    for ln in reversed([ln for ln in out_text.splitlines() if ln.strip()]):
        try:
            payload = _json.loads(ln)
            if "time" in payload: return float(payload["time"])
        except Exception:
            continue
    raise RuntimeError("Could not parse JSON time from client output.")

def run_once(net, c):
    srv = net.get('h_srv')
    clients = [net.get(f'h_cli_{i}') for i in range(1, CLIENTS+1)]

    server_proc = srv.popen(["python3", "-u", SERVER_PATH], stdout=PIPE, stderr=PIPE, text=True)
    time.sleep(0.5)  # let server bind

    try:
        procs = []
        head_start = max(0.0, 1.10 * c * HEAD_START_PER_REQ_S)

        if 0 <= GREEDY_IDX < CLIENTS:
            procs.append((GREEDY_IDX, launch_on_host(clients[GREEDY_IDX], "greedy", c, GREEDY_IDX)))
            time.sleep(head_start)

        for i in range(CLIENTS):
            if i == GREEDY_IDX: continue
            procs.append((i, launch_on_host(clients[i], "seq", 1, i)))
            # slight spacing to reduce SYN storms; not critical
            time.sleep(0.005)

        times = [None] * CLIENTS
        for i, p in procs:
            out, err = p.communicate()
            # Uncomment for debugging:
            # print(f"[client {i}] stderr:\\n{err}")
            times[i] = parse_time(out)

        shares = [(1.0/t) if t and t > 0 else 0.0 for t in times]
        return jfi(shares), times

    finally:
        try:
            if server_proc.poll() is None:
                server_proc.send_signal(signal.SIGINT)
                time.sleep(0.2)
                if server_proc.poll() is None:
                    server_proc.terminate()
        except Exception:
            pass

def main():
    net = make_net(num_clients=CLIENTS)
    net.start()
    try:
        c_values = list(range(C_START, C_MAX + 1, C_STEP))
        rows, mean_jfis = [], []

        for c in c_values:
            run_jfis = []
            for r in range(ITERATIONS):
                jf, times = run_once(net, c)
                run_jfis.append(jf)
                rows.append({"c": c, "run": r+1, "jfi": f"{jf:.6f}",
                             **{f"t{i}": f"{times[i]:.6f}" for i in range(CLIENTS)}})
                print(f"[run] c={c} iter={r+1}/{ITERATIONS} JFI={jf:.4f}")
            mean = statistics.mean(run_jfis)
            mean_jfis.append((c, mean))
        # Write CSV
        with open(CSV_OUT, "w", newline="") as f:
            fieldnames = ["c", "run", "jfi"] + [f"t{i}" for i in range(CLIENTS)]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader(); w.writerows(rows)
        print(f"[runner] wrote {CSV_OUT}")

        # Plot if matplotlib is available; otherwise skip silently
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            xs = [c for c,_ in mean_jfis]; ys = [m for _,m in mean_jfis]
            plt.figure()
            plt.plot(xs, ys, marker="o")
            plt.xlabel("Greedy batch size c")
            plt.ylabel("Jain's Fairness Index (1/T shares)")
            plt.title(f"FCFS: JFI vs c (clients={CLIENTS}, iters={ITERATIONS})")
            plt.grid(True); plt.tight_layout(); plt.savefig(PNG_OUT, dpi=180)
            print(f"[runner] wrote {PNG_OUT}")
        except Exception as e:
            print(f"[runner] plotting skipped: {e}")

    finally:
        net.stop()

if __name__ == "__main__":
    main()
