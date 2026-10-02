"""Scores the pre-registered hypotheses (experiments/PREREGISTRATION.md) and writes results/prereg_tables.md + fig_prereg.png."""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import RES, FIG, SCEN, ci, fmt, holm, paired_p      # noqa: E402

CT = ["HPA-r0", "HPA-fast-r0", "HPA-45-r0", "PredHPA-r0", "PredHPA-r1", "ARC", "ARC-r1", "ARC-r0"]
RUNS = [("NASA trace, 10-service graph", "main_pr_nasa.csv"), ("ClarkNet trace, 10-service graph", "main_pr_clarknet.csv"),
        ("NASA trace, 24-service deep graph", "main_pr_deep.csv")]
COL = {"HPA-r0": "#0072B2", "HPA-fast-r0": "#56B4E9", "HPA-45-r0": "#117733", "PredHPA-r0": "#E69F00", "PredHPA-r1": "#DDAA33",
       "ARC": "#D55E00", "ARC-r1": "#CC79A7", "ARC-r0": "#8c1c13"}
out = ["# Pre-registered experiments (seeds 300-339 for 10-service, 400-429 for deep graph)", ""]
hyp = ["| Run | H1: ARC-r0 viol. vs best r0 HPA (p), cost ratio | H2: cost saving vs PredHPA-r1; viol. ARC-r0 vs PredHPA-r1 | H3: poison_flash ARC-r0 vs HPA-45-r0 (p) |", "|---|---|---|---|"]
fig, axs = plt.subplots(1, 3, figsize=(11, 3.3))
for ax, (label, f) in zip(axs, RUNS):
    d = pd.read_csv(f"{RES}/{f}")
    g = d[d.scenario != "baseline"]
    agg = g.groupby("controller")[["slo_viol_s", "loss_pct", "replica_hours", "mttr_s", "p95_lat_s"]].mean().loc[CT]
    out += [f"## {label} (n={d.seed.nunique()} seeds): mean over the nine fault scenarios", "", agg.round(2).to_markdown(), ""]
    ex = g[g.scenario != "poison_flash"].groupby("controller")[["slo_viol_s", "replica_hours"]].mean().loc[CT]
    out += [f"Excluding `poison_flash`: ", "", ex.round(2).to_markdown(), ""]
    sc = [s for s in SCEN if s in set(d.scenario)]
    out += ["| Scenario | " + " | ".join(CT) + " |", "|---|" + "---|" * len(CT)]
    for s in sc:
        vals = {c: ci(d[(d.scenario == s) & (d.controller == c)].slo_viol_s)[0] for c in CT}
        best = min(vals, key=vals.get)
        out.append(f"| {s} | " + " | ".join((f"**{fmt(d[(d.scenario == s) & (d.controller == c)].slo_viol_s)}**" if c == best else fmt(d[(d.scenario == s) & (d.controller == c)].slo_viol_s)) for c in CT) + " |")
    out.append("")
    out += ["| ARC-r0 vs. | better | n.s. | worse |  (paired Wilcoxon per scenario, Holm over scenarios)", "|---|---|---|---|"]
    for b in [c for c in CT if c != "ARC-r0"]:
        adj = holm([paired_p(d, s, "ARC-r0", b, "slo_viol_s") for s in sc])
        bt = ns = wr = 0
        for s, p in zip(sc, adj):
            ma = d[(d.scenario == s) & (d.controller == "ARC-r0")].slo_viol_s.mean()
            mb = d[(d.scenario == s) & (d.controller == b)].slo_viol_s.mean()
            if p >= 0.05:
                ns += 1
            elif ma < mb:
                bt += 1
            else:
                wr += 1
        out.append(f"| {b} | {bt} | {ns} | {wr} |")
    out.append("")
    ps = g.groupby(["controller", "seed"]).slo_viol_s.mean().unstack(0)
    cst = g.groupby("controller").replica_hours.mean()
    best = min(["HPA-r0", "HPA-fast-r0", "HPA-45-r0"], key=lambda c: ps[c].mean())
    p1 = stats.wilcoxon(ps["ARC-r0"], ps[best]).pvalue
    pf = d[d.scenario == "poison_flash"].pivot_table(index="seed", columns="controller", values="slo_viol_s")
    p3 = stats.wilcoxon(pf["ARC-r0"], pf["HPA-45-r0"]).pvalue
    h = ci(ps["PredHPA-r1"])
    hyp.append(f"| {label} | {ps['ARC-r0'].mean():.1f} s vs {ps[best].mean():.1f} s ({best}), p={p1:.2g}; cost ratio {cst['ARC-r0'] / cst[best]:.3f} "
               f"| {1 - cst['ARC-r0'] / cst['PredHPA-r1']:.1%} cheaper; {ps['ARC-r0'].mean():.1f} s vs {h[0]:.1f} ± {h[1]:.1f} s "
               f"| {pf['ARC-r0'].mean():.1f} s vs {pf['HPA-45-r0'].mean():.1f} s (p={p3:.2g}) |")
    for c in CT:
        gg = g[g.controller == c]
        x, y = gg.groupby("seed").replica_hours.mean(), gg.groupby("seed").slo_viol_s.mean()
        ax.errorbar(x.mean(), max(y.mean(), 1), xerr=ci(x)[1], yerr=ci(y)[1], marker="o", ms=6, color=COL[c], label=c, capsize=1.5, lw=0.8)
    ax.set_yscale("log")
    ax.set_title(label, fontsize=8)
    ax.set_xlabel("Cost (replica-hours)")
axs[0].set_ylabel("SLO-violation time (s, log)")
axs[0].legend(fontsize=6, frameon=False)
fig.tight_layout()
fig.savefig(f"{FIG}/fig_prereg.png")
plt.close(fig)
out = out[:2] + ["## Hypotheses H1-H3 (see PREREGISTRATION.md)", ""] + hyp + [""] + out[2:]
# sweeps
s = pd.read_csv(f"{RES}/sweeps_pr.csv")
out += ["## Sweeps (loss %, 20 seeds, seeds 300-319)", ""]
for k in ["flash", "ddos"]:
    out += [f"**{k}**", "", s[s.scenario == k].pivot_table(index="magnitude", columns="controller", values="loss_pct").round(1).to_markdown(), ""]
    out += [f"**{k}: cost (replica-hours)**", "", s[s.scenario == k].pivot_table(index="magnitude", columns="controller", values="replica_hours").round(0).to_markdown(), ""]
a = pd.read_csv(f"{RES}/antifragile_stationary_pr.csv")
out += ["## H4: repeated stress, identical load (30 seeds)", "", "| Controller | epochs 1-3 (s) | epochs 8-11 (s) | change | mean pods |", "|---|---|---|---|---|"]
for c in ["HPA-45-r0", "PredHPA-r1", "ARC", "ARC-r0", "ARC-r0-noChaos"]:
    x = a[a.controller == c]
    e, l_ = x[x.epoch <= 3].groupby("seed").viol_s.mean(), x[x.epoch >= 8].groupby("seed").viol_s.mean()
    out.append(f"| {c} | {fmt(e)} | {fmt(l_)} | {100 * (l_.mean() - e.mean()) / e.mean():+.0f}% (p={stats.wilcoxon(e.values, l_.values).pvalue:.3g}) | {x.pods.mean():.1f} |")
open(f"{RES}/prereg_tables.md", "w").write("\n".join(out) + "\n")
print("\n".join(out[:12]))
