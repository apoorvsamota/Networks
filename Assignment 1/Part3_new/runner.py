# runner.py
import json, os, time, subprocess, statistics, shutil
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from topo_wordcount import make_net

TMP_WORDS_PATH = "/tmp/words_wc.txt"

def jfi(xs):
    n = len(xs); s = sum(xs); ss = sum(x*x for x in xs)
    return (s*s) / (n*ss) if ss > 0 else 0.0

def stage_words_to_tmp(src_path: str):
    # copy once per run to a fast, uniform path (tmpfs/ext4), avoiding /mnt/… slowness in WSL
    shutil.copyfile(src_path, TMP_WORDS_PATH)

def run_once(cfg, c, script_dir):
    num_clients = int(cfg["num_clients"])
    server_ip   = cfg["server_ip"]
    server_port = int(cfg["server_port"])
    src_words   = os.path.join(script_dir, cfg["filename"])
    if not os.path.isfile(src_words):
        raise FileNotFoundError(f"words file not found: {src_words}")

    # copy dataset to /tmp (all mininet hosts see same root FS)
    stage_words_to_tmp(src_words)

    net = make_net(num_clients=num_clients)
    net.start()

    h_srv  = net.get("h_srv")
    hosts  = [net.get(f"h_cli_{i}") for i in range(1, num_clients+1)]

    # --- server ---
    srv_cmd = [
        "python3", "-u",
        os.path.join(script_dir, "server.py"),
        "--config", os.path.join(script_dir, "config.json"),
        "--port", str(server_port),
    ]
    env = os.environ.copy()
    env["FAIR_EPOCH_ROUNDS"] = "50" if c == 1 else "0"   # only tighten for c=1
    print(f"[runner] FAIR_EPOCH_ROUNDS={env['FAIR_EPOCH_ROUNDS']} for c={c}", flush=True)
    srv = h_srv.popen(srv_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    time.sleep(0.8)

    # --- clients ---
    procs = []
    rogue_cmd = [
        "python3", "-u",
        os.path.join(script_dir, "client.py"),
        "--server-ip", server_ip,
        "--server-port", str(server_port),
        "--mode", "greedy",
        "--batch-size", str(c),
        "--client-id", "0",
        "--filename", TMP_WORDS_PATH,
    ]
    procs.append(hosts[0].popen(rogue_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))

    for idx in range(1, num_clients):
        cmd = [
            "python3", "-u",
            os.path.join(script_dir, "client.py"),
            "--server-ip", server_ip,
            "--server-port", str(server_port),
            "--mode", "seq",
            "--batch-size", "1",
            "--client-id", str(idx),
            "--filename", TMP_WORDS_PATH,
        ]
        procs.append(hosts[idx].popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))

    # --- gather ---
    results = []
    for p in procs:
        out, err = p.communicate()
        line = out.strip().splitlines()[-1] if out.strip() else "{}"
        try:
            rec = json.loads(line)
        except Exception:
            rec = {"processed": 0, "elapsed_ms": float("inf")}
        if rec.get("processed", 0) == 0 and err:
            print("[client stderr]", err.strip()[:200], flush=True)
        results.append(rec)

    # --- teardown ---
    try: h_srv.cmd("pkill -f server.py")
    except Exception: pass
    srv.terminate()
    net.stop()

    # --- throughputs ---
    thr = []
    for rec in results:
        processed = max(0, int(rec.get("processed", 0)))
        secs = max(1e-9, float(rec.get("elapsed_ms", 0.0)) / 1000.0)
        thr.append(processed / secs)
    return thr

def main():
    script_dir = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(script_dir, "config.json"), "r") as f:
        cfg = json.load(f)

    outdir = os.path.join(script_dir, "results2"); os.makedirs(outdir, exist_ok=True)
    c_start = int(cfg.get("c", 1))
    c_values = list(range(max(1, c_start), 100, 4))
    iters = int(cfg.get("num_iterations", 3))

    rows = []; jfi_points = []
    for c in c_values:
        per_iter = []
        for _ in range(iters):
            thr = run_once(cfg, c, script_dir)
            if c == 1:
                print("[c=1 thr]", [round(x, 2) for x in thr], flush=True)
            per_iter.append(jfi(thr))
            time.sleep(0.15)
        J = statistics.mean(per_iter)
        rows.append({"c": c, "jfi": J}); jfi_points.append(J)
        print(f"[c={c}] JFI={J:.3f}", flush=True)

    # CSV
    csv_path = os.path.join(outdir, "experiment_results.csv")
    with open(csv_path, "w") as f:
        f.write("c,jfi\n")
        for r in rows: f.write(f"{r['c']},{r['jfi']:.6f}\n")

    # Plot
    plt.figure()
    plt.plot(c_values, jfi_points, marker="o", label="JFI")
    plt.title("Fairness Index vs. Rogue Client Greediness")
    plt.xlabel("Greediness Factor (c)"); plt.ylabel("Jain's Fairness Index (JFI)")
    plt.ylim(0, 1.1); plt.grid(True, linestyle="--", alpha=0.5); plt.legend()
    png_path = os.path.join(outdir, "jfi_vs_c.png")
    plt.savefig(png_path, bbox_inches="tight")
    print(f"Saved: {csv_path}\nSaved: {png_path}")

if __name__ == "__main__":
    main()
