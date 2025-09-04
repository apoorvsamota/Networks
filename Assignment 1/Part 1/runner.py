#!/usr/bin/env python3
import argparse, json, math, os, statistics as stats, subprocess, time
import matplotlib.pyplot as plt

# 95% t-critical for small samples (df = n-1). Fallback to 1.96 for large n.
T95 = {1:12.706,2:4.303,3:3.182,4:2.776,5:2.571,6:2.447,7:2.365,8:2.306,9:2.262,10:2.228,
       11:2.201,12:2.179,13:2.160,14:2.145,15:2.131,16:2.120,17:2.110,18:2.101,19:2.093,20:2.086,
       21:2.080,22:2.074,23:2.069,24:2.064,25:2.060,26:2.056,27:2.052,28:2.048,29:2.045,30:2.042}

def tcrit_95(n):
    return 1.96 if n-1 not in T95 else T95[n-1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", default="1,2,5,10,20,50,100",
                    help="comma-separated k values")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--client", default="./client")
    ap.add_argument("--csv", default="p1_results.csv")
    ap.add_argument("--png", default="p1_plot.png")
    args = ap.parse_args()

    ks = [int(x) for x in args.ks.split(",") if x.strip()!=""]
    with open(args.config) as f: cfg = json.load(f)
    n = int(cfg.get("num_iterations", 5))  # assignment says 5 runs/k

    rows = []
    for k in ks:
        cfg["k"] = k
        # write k into config.json (server doesn't care; client does)
        with open(args.config, "w") as f: json.dump(cfg, f, indent=2)

        samples = []
        for i in range(n):
            t0 = time.perf_counter()
            # Run client once; we only care about completion time
            res = subprocess.run([args.client],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            t1 = time.perf_counter()
            if res.returncode != 0:
                print(f"[warn] client failed at k={k}, iter={i+1}")
            samples.append(t1 - t0)

        mu = stats.mean(samples)
        sd = stats.pstdev(samples) if n==1 else stats.stdev(samples)
        se = (sd / math.sqrt(n)) if n>1 else 0.0
        crit = tcrit_95(n)
        ci = crit * se
        rows.append((k, mu, mu-ci, mu+ci))

    # write CSV
    with open(args.csv, "w") as f:
        f.write("k,mean_s,ci_low_s,ci_high_s\n")
        for k, mu, lo, hi in rows:
            f.write(f"{k},{mu:.6f},{lo:.6f},{hi:.6f}\n")

    # plot
    ks_plot = [r[0] for r in rows]
    means   = [r[1] for r in rows]
    errs    = [r[1]-r[2] for r in rows]  # symmetric error from mean

    plt.figure()
    plt.errorbar(ks_plot, means, yerr=errs, fmt='-o', capsize=4)
    plt.xlabel("k (words per request)")
    plt.ylabel("Completion time (s)")
    plt.title("Part 1: Completion time vs k (95% CI)")
    plt.grid(True, which="both", linestyle="--", alpha=0.4)
    plt.savefig(args.png, dpi=150, bbox_inches="tight")
    print(f"Wrote {args.csv} and {args.png}")

if __name__ == "__main__":
    main()
