#!/usr/bin/env python3
import json, math, subprocess, statistics as stats
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

T95={1:12.706,2:4.303,3:3.182,4:2.776,5:2.571,6:2.447,7:2.365,8:2.306,9:2.262,10:2.228}
def tcrit(n): return 1.96 if (n-1) not in T95 else T95[n-1]

cfg = json.load(open("config.json"))
iters = int(cfg.get("num_iterations", 5))
xs = list(range(1, 33, 4))  # 1,5,9,...,29

rows=[]
for nc in xs:
    cfg["num_clients"]=nc
    json.dump(cfg, open("config.json","w"), indent=2)

    means=[]
    for _ in range(iters):
        r = subprocess.run(["python3","client.py"], capture_output=True, text=True, timeout=180)
        times = [float(x) for x in r.stdout.strip().split(",") if x.strip()]
        means.append(stats.mean(times))

    mu = stats.mean(means)
    sd = 0.0 if len(means)==1 else stats.stdev(means)
    se = 0.0 if len(means)==1 else sd/math.sqrt(len(means))
    ci = tcrit(len(means)) * se
    rows.append((nc, mu, mu-ci, mu+ci))

with open("p2_results.csv","w") as f:
    f.write("num_clients,mean_per_client_s,ci_low_s,ci_high_s\n")
    for nc, mu, lo, hi in rows:
        f.write(f"{nc},{mu:.6f},{lo:.6f},{hi:.6f}\n")

plt.figure()
xs=[r[0] for r in rows]; ys=[r[1] for r in rows]; errs=[r[1]-r[2] for r in rows]
plt.errorbar(xs, ys, yerr=errs, fmt='-o', capsize=4)
plt.xlabel("Number of concurrent clients")
plt.ylabel("Average completion time per client (s)")
plt.title("Part 2 (FCFS per request): Avg completion time per client (95% CI)")
plt.grid(True, linestyle="--", alpha=0.4)
plt.savefig("p2_plot.png", dpi=150, bbox_inches="tight")
print("Wrote p2_results.csv and p2_plot.png")
