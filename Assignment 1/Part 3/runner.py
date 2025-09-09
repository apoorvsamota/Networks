#!/usr/bin/env python3
import subprocess, json, os, time, csv, statistics, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---- read defaults from config.json ----
cfg = json.load(open("config.json"))
CLIENTS     = int(cfg.get("num_clients", 10))
GREEDY_IDX  = int(cfg.get("greedy_index", 0))
ITERATIONS  = int(cfg.get("num_iterations", 5))

C_START     = int(cfg.get("c_start", 1))
C_MAX       = int(cfg.get("c_max", 20))
C_STEP      = int(cfg.get("c_step", 4))

HOST        = cfg.get("server_ip", "10.0.0.2")
PORT        = int(cfg.get("server_port", 5000))
K           = int(cfg.get("k", 5))
CSV_OUT     = cfg.get("time_results_csv", "time_results.csv")
PNG_OUT     = cfg.get("time_plot_png", "time_plot.png")

# ---------- HEAD-START (strict cap + early-big -> later-small) ----------
# Hard upper bound (ms). Default: 200ms, never exceeded.
HS_CAP_MS       = int(os.environ.get("HS_CAP_MS", "200"))
# c at which head-start begins to increase (<= c gives 0ms)
HS_START_C      = int(os.environ.get("HS_START_C", "2"))
# Concavity power (0<POW<=1). Smaller -> bigger early increments, gentler later.
HS_EASE_POW     = float(os.environ.get("HS_EASE_POW", "0.6"))
# Optional plateau before C_MAX (0 = use C_MAX as the last c)
HS_PLATEAU_C    = int(os.environ.get("HS_PLATEAU_C", "0"))
# Stagger between launching normal clients (ms) to reduce noise
SPAWN_STAGGER_MS = int(os.environ.get("SPAWN_STAGGER_MS", "5"))

# ---------- helpers ----------
def estimate_requests_per_client(filename, k):
    """Only for info printing; head-start does not depend on it."""
    try:
        with open(filename, "r") as f:
            txt = f.read()
        n_tokens = sum(1 for t in (x.strip() for x in txt.split(",")) if t)
        return max(1, math.ceil(n_tokens / max(1, k)))
    except Exception:
        return 500

def estimate_service_time_via_seq(R_est):
    """Only for info printing; head-start does not depend on it."""
    env = os.environ.copy()
    env["CLIENT_MODE"] = "seq"
    env["BATCH_SIZE"]  = "1"
    env["CLIENT_ID"]   = "999"
    start = time.perf_counter()
    p = subprocess.Popen(["python3", "client.py"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, _ = p.communicate()
    elapsed = time.perf_counter() - start
    try:
        t = json.loads(out.strip()).get("time", elapsed)
    except Exception:
        t = elapsed
    return max(t / max(1, R_est), 0.001)

def head_start_for_time(c, R_est, est_req_time):
    """
    Concave, capped schedule:
      - 0 ms for c <= HS_START_C
      - For c in (HS_START_C, C_END], hs_ms = CAP * ((c - HS_START_C)/(C_END - HS_START_C)) ** HS_EASE_POW
      - C_END = HS_PLATEAU_C if set, else C_MAX
      - Exactly equals CAP at c = C_END, never exceeds CAP.
    """
    if c <= 1:
        return 0.0
    cap = max(1, HS_CAP_MS)  # at least 1ms cap
    c_end = HS_PLATEAU_C if HS_PLATEAU_C > 0 else C_MAX
    c_end = max(HS_START_C + 1, c_end)     # ensure denominator > 0
    if c <= HS_START_C:
        hs_ms = 0.0
    else:
        # Normalized progress in (0,1], then concave easing
        num = min(c, c_end) - HS_START_C
        den = c_end - HS_START_C
        p = max(0.0, min(1.0, num / den))
        hs_ms = cap * (p ** max(1e-3, min(1.0, HS_EASE_POW)))
    # Never exceed the cap, and if c > c_end keep it flat
    hs_ms = min(hs_ms, cap)
    return hs_ms / 1000.0  # seconds

def jfi_from_rates(rates):
    s = sum(rates)
    s2 = sum(r*r for r in rates)
    n = len(rates)
    return (s*s) / (n * s2) if s2 > 0 else 0.0

# ---------- one sweep point ----------
def run_once(c, est_req_time, R_est):
    procs = []
    finishes = [None] * CLIENTS

    def env_for(idx, mode, batch):
        env = os.environ.copy()
        env["CLIENT_ID"]   = str(idx)
        env["CLIENT_MODE"] = mode          # "greedy" (window=c) or "seq"
        env["BATCH_SIZE"]  = str(batch)    # greedy window size = c
        return env

    # 1) Launch greedy first (window size = c) — client.py should be sliding-window greedy
    greedy = subprocess.Popen(["python3", "client.py"],
                              env=env_for(GREEDY_IDX, "greedy", c),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    procs.append((GREEDY_IDX, greedy))

    # 2) Head-start (capped, concave); t0 AFTER delay
    head_start = head_start_for_time(c, R_est, est_req_time)
    if head_start > 0:
        time.sleep(head_start)
    t0 = time.perf_counter()

    # 3) Launch the rest (sequential clients), slight stagger to reduce race noise
    for i in range(CLIENTS):
        if i == GREEDY_IDX:
            continue
        p = subprocess.Popen(["python3", "client.py"],
                             env=env_for(i, "seq", 1),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append((i, p))
        if SPAWN_STAGGER_MS > 0:
            time.sleep(SPAWN_STAGGER_MS / 1000.0)

    # 4) Wait & record finish times relative to t0
    EPS = 1e-6
    for idx, p in procs:
        p.wait()
        dt = time.perf_counter() - t0
        finishes[idx] = dt if dt > EPS else EPS

    rates = [1.0 / max(t, EPS) for t in finishes]
    return finishes, rates, jfi_from_rates(rates), head_start

# ---------- main ----------
def main():
    R_est = estimate_requests_per_client(cfg.get("filename", "words.txt"), K)
    est   = estimate_service_time_via_seq(R_est)
    print(f"[runner] R≈{R_est} req/client, est per-request ≈ {est*1000:.2f} ms")
    print(f"[runner] HS: cap={HS_CAP_MS}ms, start@c={HS_START_C}, pow={HS_EASE_POW}, plateau_c={HS_PLATEAU_C}, "
          f"stagger={SPAWN_STAGGER_MS}ms")
    print(f"[runner] sweeping c={C_START}..{C_MAX} step={C_STEP} (clients={CLIENTS}, iters={ITERATIONS})")

    rows, means = [], []
    for c in range(C_START, C_MAX + 1, C_STEP):
        jfivals = []
        for run in range(ITERATIONS):
            times, rates, jfi, hs = run_once(c, est, R_est)
            jfivals.append(jfi)
            row = {"c": c, "run": run, "jfi": jfi}
            for i, t in enumerate(times):
                row[f"t{i}"] = t
            rows.append(row)
            print(f"[c={c:>3}] run {run}: JFI={jfi:.4f}  head_start={hs*1000:.0f}ms  times={['%.3f'%t for t in times]}")

        means.append((c, statistics.mean(jfivals)))

    # CSV + plot
    with open(CSV_OUT, "w", newline="") as f:
        fieldnames = ["c", "run", "jfi"] + [f"t{i}" for i in range(CLIENTS)]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    xs = [c for c, _ in means]
    ys = [m for _, m in means]
    plt.figure()
    plt.plot(xs, ys, marker="o")
    plt.xlabel("Greedy batch size c")
    plt.ylabel("Jain's Fairness Index (from completion-time rates 1/T, global t0)")
    plt.title(f"FCFS: JFI vs c (clients={CLIENTS}, iters={ITERATIONS})")
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(PNG_OUT, dpi=180)
    print(f"[runner] wrote {CSV_OUT} and {PNG_OUT}")

if __name__ == "__main__":
    main()
