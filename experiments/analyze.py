"""Tables (markdown), statistics and figures from results/*.csv."""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
FIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "figures")
os.makedirs(FIG, exist_ok=True)

BASE = ["HPA", "HPA-fast", "HPA-45", "PredHPA", "HPA+RL"]
ABL = ["ARC-noGraph", "ARC-noChaos", "ARC-noSec", "ARC-noGuard", "ARC-noRemed"]
SCEN = ["baseline", "flash", "flash_step", "kill_cascade", "node_failure", "gray", "ddos", "ddos_kill", "poison_flash", "storm"]
# Okabe-Ito colour-blind-safe palette
COL = {"Static": "#999999", "HPA": "#0072B2", "HPA-fast": "#56B4E9", "HPA-45": "#009E73", "PredHPA": "#E69F00",
       "HPA+RL": "#CC79A7", "ARC": "#D55E00", "ARC-noChaos": "#8c564b"}
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})


def ci(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return float(np.mean(x)) if len(x) else np.nan, 0.0
    return float(x.mean()), float(1.96 * x.std(ddof=1) / np.sqrt(len(x)))


def fmt(x):
    m, h = ci(x)
    return f"{m:.1f} ± {h:.1f}"


def holm(ps):
    ps = np.asarray(ps, float)
    order = np.argsort(ps)
    adj = np.empty_like(ps)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, (len(ps) - rank) * ps[i])
        adj[i] = min(1.0, run)
    return adj


def paired_p(df, scen, a, b, metric):
    x = df[(df.scenario == scen) & (df.controller == a)].sort_values("seed")[metric].values
    y = df[(df.scenario == scen) & (df.controller == b)].sort_values("seed")[metric].values
    if np.allclose(x, y):
        return 1.0
    try:
        return float(stats.wilcoxon(x, y).pvalue)
    except ValueError:
        return 1.0


def table(df, metric, ctrls, title):
    lines = [f"**{title}**", "", "| Scenario | " + " | ".join(ctrls) + " |", "|---|" + "---|" * len(ctrls)]
    for sc in SCEN:
        row = []
        vals = {c: ci(df[(df.scenario == sc) & (df.controller == c)][metric])[0] for c in ctrls}
        best = min(vals, key=vals.get)
        for c in ctrls:
            s = fmt(df[(df.scenario == sc) & (df.controller == c)][metric])
            row.append(f"**{s}**" if c == best else s)
        lines.append(f"| {sc} | " + " | ".join(row) + " |")
    return "\n".join(lines)


def main_tables(df):
    out = []
    ctrls = ["Static"] + BASE + ["ARC"]
    out.append(table(df, "slo_viol_s", ctrls, f"SLO-violation time (s per 110-min measured window; lower is better; mean ± 95% CI, n={df.seed.nunique()} seeds)"))
    out.append(table(df, "loss_pct", ctrls, "Legitimate-request loss (% of offered load, including requests shed on purpose)"))
    out.append(table(df, "replica_hours", ctrls, "Cost: replica-hours (lower is cheaper)"))
    out.append(table(df, "mttr_s", ctrls, "Mean time to recover from an injected fault (s)"))
    # ARC vs strongest baseline in each scenario
    lines = ["**ARC vs. each baseline, paired Wilcoxon on SLO-violation time (Holm-corrected within baseline)**", "",
             "| Scenario | " + " | ".join(BASE) + " |", "|---|" + "---|" * len(BASE)]
    cells = {}
    for b in BASE:
        ps = [paired_p(df, sc, "ARC", b, "slo_viol_s") for sc in SCEN]
        adj = holm(ps)
        for sc, p in zip(SCEN, adj):
            ma = df[(df.scenario == sc) & (df.controller == "ARC")].slo_viol_s.mean()
            mb = df[(df.scenario == sc) & (df.controller == b)].slo_viol_s.mean()
            tag = "ARC better" if ma < mb else "baseline better"
            cells[(sc, b)] = f"{tag}, p={p:.3g}" if p < 0.05 else "n.s."
    for sc in SCEN:
        lines.append(f"| {sc} | " + " | ".join(cells[(sc, b)] for b in BASE) + " |")
    out.append("\n".join(lines))
    # aggregate over the 9 fault scenarios
    f = df[df.scenario != "baseline"]
    agg = f.groupby("controller")[["slo_viol_s", "loss_pct", "replica_hours", "mttr_s", "p95_lat_s"]].mean().loc[["Static"] + BASE + ["ARC"]]
    out.append("**Mean over the nine fault scenarios**\n\n" + agg.round(2).to_markdown())
    # ablation
    lines = ["**Ablation: SLO-violation time (s), mean ± 95% CI**", "", "| Scenario | ARC | " + " | ".join(ABL) + " |", "|---|---|" + "---|" * len(ABL)]
    for sc in SCEN:
        lines.append(f"| {sc} | " + " | ".join(fmt(df[(df.scenario == sc) & (df.controller == c)].slo_viol_s) for c in ["ARC"] + ABL) + " |")
    out.append("\n".join(lines))
    lines = ["**Ablation: cost (replica-hours) and chaos overhead**", "", "| Variant | replica-hours (all scenarios) | harm from probes (s per run) | probes per run |", "|---|---|---|---|"]
    for c in ["ARC"] + ABL:
        d = df[df.controller == c]
        lines.append(f"| {c} | {d.replica_hours.mean():.1f} | {d.chaos_harm_s.mean():.2f} | {d.n_chaos.mean():.1f} |")
    out.append("\n".join(lines))
    # security-specific metrics
    d = df[(df.controller == "ARC") & df.scenario.isin(["ddos", "ddos_kill"])]
    p = df[(df.controller == "ARC") & (df.scenario == "poison_flash")]
    out.append("**Security-mechanism diagnostics (ARC)**\n\n"
               f"- DDoS detection delay: {ci(d.attack_det_delay_s)[0]:.1f} s (95% CI ± {ci(d.attack_det_delay_s)[1]:.1f}); "
               f"legitimate requests shed as collateral in DDoS scenarios: {d.shed_pct.mean():.2f}%\n"
               f"- Telemetry-poisoning detection F1 (per-tick, per-service): {ci(p.poison_f1)[0]:.2f} ± {ci(p.poison_f1)[1]:.2f}")
    open(f"{RES}/tables.md", "w").write("\n\n".join(out) + "\n")


def fig_bars(df):
    ctrls = ["HPA", "HPA-fast", "HPA-45", "PredHPA", "HPA+RL", "ARC"]
    fig, ax = plt.subplots(figsize=(9, 3.4))
    w = 0.13
    x = np.arange(len(SCEN))
    for k, c in enumerate(ctrls):
        m = [ci(df[(df.scenario == s) & (df.controller == c)].slo_viol_s)[0] for s in SCEN]
        h = [ci(df[(df.scenario == s) & (df.controller == c)].slo_viol_s)[1] for s in SCEN]
        ax.bar(x + (k - 2.5) * w, np.maximum(m, 0.5), w, yerr=h, label=c, color=COL[c], error_kw={"lw": 0.6})
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(SCEN, rotation=25, ha="right")
    ax.set_ylabel("SLO-violation time (s, log)")
    ax.legend(ncol=6, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, 1.18), frameon=False)
    fig.tight_layout()
    fig.savefig(f"{FIG}/fig_slo_by_scenario.png")
    plt.close(fig)


def fig_pareto(df):
    f = df[df.scenario != "baseline"]
    fig, ax = plt.subplots(figsize=(4.6, 3.6))
    for c in ["HPA", "HPA-fast", "HPA-45", "PredHPA", "HPA+RL", "ARC"]:
        d = f[f.controller == c]
        ax.scatter(d.replica_hours.mean(), d.slo_viol_s.mean(), s=70, color=COL[c], label=c, zorder=3)
        ax.errorbar(d.replica_hours.mean(), d.slo_viol_s.mean(), xerr=ci(d.replica_hours)[1], yerr=ci(d.slo_viol_s)[1], color=COL[c], lw=0.8, zorder=2)
    ax.set_xlabel("Cost (replica-hours per run)")
    ax.set_ylabel("SLO-violation time (s)")
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(f"{FIG}/fig_pareto.png")
    plt.close(fig)


def fig_ablation(df):
    arc = df[df.controller == "ARC"].groupby("scenario").slo_viol_s.mean()
    mat = np.array([[df[(df.controller == c) & (df.scenario == s)].slo_viol_s.mean() - arc[s] for c in ABL] for s in SCEN])
    fig, ax = plt.subplots(figsize=(5.2, 3.8))
    lim = np.percentile(np.abs(mat), 90)
    im = ax.imshow(mat, cmap="RdBu_r", vmin=-lim, vmax=lim, aspect="auto")
    ax.set_xticks(range(len(ABL)))
    ax.set_xticklabels([a.replace("ARC-", "") for a in ABL])
    ax.set_yticks(range(len(SCEN)))
    ax.set_yticklabels(SCEN)
    for i in range(len(SCEN)):
        for j in range(len(ABL)):
            ax.text(j, i, f"{mat[i, j]:+.0f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, label="Δ violation time vs. full ARC (s)")
    fig.tight_layout()
    fig.savefig(f"{FIG}/fig_ablation.png")
    plt.close(fig)


def fig_sweeps(sw):
    fig, axs = plt.subplots(1, 2, figsize=(8, 3.1))
    for ax, kind, xl in [(axs[0], "flash", "Surge magnitude (× normal load)"), (axs[1], "ddos", "Attack volume (× baseline attack unit)")]:
        d = sw[sw.scenario == kind]
        for c in ["HPA", "HPA-fast", "HPA-45", "PredHPA", "HPA+RL", "ARC"]:
            g = d[d.controller == c].groupby("magnitude").loss_pct
            m, h = g.mean(), g.apply(lambda v: ci(v)[1])
            ax.errorbar(m.index, m.values, yerr=h.values, color=COL[c], label=c, marker="o", ms=3, lw=1.2, capsize=1.5)
        ax.set_xlabel(xl)
        ax.set_ylabel("Legitimate-request loss (%)")
    axs[0].legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(f"{FIG}/fig_sweeps.png")
    plt.close(fig)


def fig_antifragile(af, name="fig_antifragile"):
    fig, ax = plt.subplots(figsize=(5.2, 3.3))
    for c in ["HPA", "HPA-fast", "HPA-45", "PredHPA", "ARC-noChaos", "ARC"]:
        g = af[af.controller == c].groupby("epoch").viol_s
        m, h = g.mean(), g.apply(lambda v: ci(v)[1])
        ax.errorbar(m.index, m.values, yerr=h.values, color=COL[c], label=c, marker="o", ms=3, lw=1.2, capsize=1.5)
    ax.set_xlabel("Exposure epoch (identical fault set every 30 min)")
    ax.set_ylabel("SLO-violation time per epoch (s)")
    ax.legend(fontsize=7, frameon=False, ncol=2)
    fig.tight_layout()
    fig.savefig(f"{FIG}/{name}.png")
    plt.close(fig)


def antifragile_stats(af, title="Repeated-stress experiment (same faults every 30 min, 6 h; drifting real load)"):
    lines = [f"**{title}**", "",
             "| Controller | epochs 1-3 (s) | epochs 8-11 (s) | change | Spearman ρ (epoch vs violation) | mean pods |", "|---|---|---|---|---|---|"]
    for c in ["HPA", "HPA-fast", "HPA-45", "PredHPA", "ARC-noChaos", "ARC"]:
        d = af[af.controller == c]
        early = d[d.epoch <= 3].groupby("seed").viol_s.mean()
        late = d[d.epoch >= 8].groupby("seed").viol_s.mean()
        rho = stats.spearmanr(d.epoch, d.viol_s)
        try:
            p = stats.wilcoxon(early.values, late.values).pvalue
        except ValueError:
            p = 1.0
        lines.append(f"| {c} | {fmt(early)} | {fmt(late)} | {100 * (late.mean() - early.mean()) / max(early.mean(), 1e-9):+.0f}% (p={p:.3g}) | {rho.statistic:+.2f} | {d.pods.mean():.1f} |")
    open(f"{RES}/tables.md", "a").write("\n" + "\n".join(lines) + "\n")


def fig_timeline():
    from arcsim import run
    fig, axs = plt.subplots(2, 2, figsize=(9, 4.4), sharex="col")
    for col, (scen, title) in enumerate([("flash_step", "Abrupt 3x surge"), ("gray", "Gray failure (pods at 30% capacity)")]):
        for c in ["HPA", "PredHPA", "ARC"]:
            m, sim, ctrl = run.run_episode(c, scen, 100, keep=True)
            t = np.arange(len(sim.log["avail"])) * 5 / 60
            axs[0, col].plot(t, np.array(sim.log["avail"]) * 100, color=COL[c], label=c, lw=1.1)
            axs[1, col].plot(t, sim.log["pods"], color=COL[c], lw=1.1)
        t0 = sim.events[0][0] * 5 / 60
        for r in (0, 1):
            axs[r, col].axvline(t0, color="k", ls=":", lw=0.8)
            axs[r, col].set_xlim(t0 - 10, t0 + 30)
        axs[0, col].set_title(title, fontsize=9)
        axs[0, col].set_ylim(-3, 103)
        axs[1, col].set_xlabel("Time (min)")
    axs[0, 0].set_ylabel("Availability (%)")
    axs[1, 0].set_ylabel("Pods in cluster")
    axs[0, 0].legend(fontsize=7, frameon=False, loc="lower left")
    fig.tight_layout()
    fig.savefig(f"{FIG}/fig_timeline.png")
    plt.close(fig)


if __name__ == "__main__":
    fig_timeline()
    df = pd.read_csv(f"{RES}/main.csv")
    main_tables(df)
    fig_bars(df)
    fig_pareto(df)
    fig_ablation(df)
    if os.path.exists(f"{RES}/sweeps.csv"):
        fig_sweeps(pd.read_csv(f"{RES}/sweeps.csv"))
    if os.path.exists(f"{RES}/antifragile.csv"):
        af = pd.read_csv(f"{RES}/antifragile.csv")
        fig_antifragile(af)
        antifragile_stats(af)
    if os.path.exists(f"{RES}/antifragile_stationary.csv"):
        st = pd.read_csv(f"{RES}/antifragile_stationary.csv")
        fig_antifragile(st, "fig_antifragile_stationary")
        antifragile_stats(st, "Repeated-stress experiment with identical load every epoch (isolates learning from workload drift)")
    print("ok")
