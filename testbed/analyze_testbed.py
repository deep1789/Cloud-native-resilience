"""Summarise results/testbed.jsonl: per-scenario table (mean over seeds, with min-max) and a timeline figure."""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "results")
FIG = os.path.join(HERE, "..", "figures")
src = sys.argv[1] if len(sys.argv) > 1 else "testbed.jsonl"
rows = [json.loads(l) for l in open(os.path.join(RES, src))]
df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
order = [c for c in ["HPA-45", "HPA-45-r0", "PredHPA-r1", "ARC", "ARC-r0"] if c in set(df.controller)]
scen = [s for s in ["baseline", "flash_step", "kill_cascade", "gray"] if s in set(df.scenario)]
out = [f"**Container testbed: runs = {len(df)}, seeds per cell = {df.groupby(['controller', 'scenario']).seed.nunique().min()}-"
       f"{df.groupby(['controller', 'scenario']).seed.nunique().max()}**", ""]
for metric, label in [("slo_viol_s", "SLO-violation time (s, sim-equivalent)"), ("loss_pct", "Legitimate-request loss (%)"),
                      ("replica_hours", "Cost (replica-hours, sim-equivalent)"), ("mttr_s", "MTTR (s)")]:
    out += [f"**{label}: mean [min-max] over seeds**", "", "| Scenario | " + " | ".join(order) + " |", "|---|" + "---|" * len(order)]
    for s in scen:
        cells = []
        for c in order:
            v = df[(df.scenario == s) & (df.controller == c)][metric]
            cells.append(f"{v.mean():.1f} [{v.min():.1f}-{v.max():.1f}]" if len(v) else "-")
        out.append(f"| {s} | " + " | ".join(cells) + " |")
    out.append("")
open(os.path.join(RES, "testbed_tables.md"), "w").write("\n".join(out))
print("\n".join(out))

# timeline for one seed/scenario
COL = {"HPA-45": "#009E73", "HPA-45-r0": "#117733", "PredHPA-r1": "#DDAA33", "ARC": "#D55E00", "ARC-r0": "#8c564b"}
for s in [x for x in ["gray", "kill_cascade", "flash_step"] if x in scen]:
    fig, axs = plt.subplots(2, 1, figsize=(6, 4), sharex=True)
    seed = min(df[df.scenario == s].seed)
    for r in rows:
        if r["scenario"] == s and r["seed"] == seed and r["controller"] in order:
            t = np.arange(len(r["_avail"])) * 5 / 60
            axs[0].plot(t, np.array(r["_avail"]) * 100, color=COL[r["controller"]], label=r["controller"], lw=1)
            axs[1].plot(t, r["_pods"], color=COL[r["controller"]], lw=1)
    axs[0].set_ylabel("Availability (%)")
    axs[1].set_ylabel("Pods")
    axs[1].set_xlabel("Time (sim min, after warm-up)")
    axs[0].set_title(f"Container testbed, {s}, seed {seed}", fontsize=9)
    axs[0].legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, f"fig_testbed_{s}.png"), dpi=150)
    plt.close(fig)
