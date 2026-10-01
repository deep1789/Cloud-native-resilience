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
       "HPA+RL": "#CC79A7", "ARC": "#D55E00", "ARC-noChaos": "#8c564b",
       "HPA-45-r0": "#117733", "HPA-45-r1": "#88CCEE", "PredHPA-r1": "#DDAA33",
       "ARC-noGraph": "#A0522D", "ARC-bf": "#5C3317"}
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
        for c in ["HPA", "HPA-45", "PredHPA", "HPA+RL", "HPA-45-r0", "PredHPA-r1", "ARC"]:
            if c not in set(d.controller):
                continue
            g = d[d.controller == c].groupby("magnitude").loss_pct
            m, h = g.mean(), g.apply(lambda v: ci(v)[1])
            ax.errorbar(m.index, m.values, yerr=h.values, color=COL[c], label=c, marker="o", ms=3, lw=1.2, capsize=1.5,
                        ls="--" if c.endswith(("-r0", "-r1")) else "-")
        ax.set_xlabel(xl)
        ax.set_ylabel("Legitimate-request loss (%)")
    axs[0].legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(f"{FIG}/fig_sweeps.png")
    plt.close(fig)


def fig_antifragile(af, name="fig_antifragile"):
    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    for c in ["HPA", "HPA-fast", "HPA-45", "PredHPA", "ARC-noChaos", "HPA-45-r0", "PredHPA-r1", "ARC"]:
        if c not in set(af.controller):
            continue
        g = af[af.controller == c].groupby("epoch").viol_s
        m, h = g.mean(), g.apply(lambda v: ci(v)[1])
        ax.errorbar(m.index, m.values, yerr=h.values, color=COL[c], label=c, marker="o", ms=3, lw=1.2, capsize=1.5)
    ax.set_xlabel("Exposure epoch (identical fault set every 30 min)")
    ax.set_ylabel("SLO-violation time per epoch (s)")
    ax.legend(fontsize=7, frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.22))
    fig.tight_layout()
    fig.savefig(f"{FIG}/{name}.png", bbox_inches="tight")
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


def extra_tables(df, label, ctrls):
    """Compact per-scenario table, fault-scenario means and ARC-vs-baseline win counts for a robustness run."""
    sc = [x for x in SCEN if x in set(df.scenario)]
    lines = [f"**{label}: SLO-violation time (s), mean ± 95% CI, n={df.seed.nunique()} seeds**", "",
             "| Scenario | " + " | ".join(ctrls) + " |", "|---|" + "---|" * len(ctrls)]
    for s_ in sc:
        vals = {c: ci(df[(df.scenario == s_) & (df.controller == c)].slo_viol_s)[0] for c in ctrls}
        best = min(vals, key=vals.get)
        row = []
        for c in ctrls:
            t = fmt(df[(df.scenario == s_) & (df.controller == c)].slo_viol_s)
            row.append(f"**{t}**" if c == best else t)
        lines.append(f"| {s_} | " + " | ".join(row) + " |")
    f = df[df.scenario != "baseline"]
    agg = f.groupby("controller")[["slo_viol_s", "loss_pct", "replica_hours", "mttr_s", "p95_lat_s"]].mean().loc[ctrls]
    lines += ["", f"**{label}: mean over the fault scenarios**", "", agg.round(2).to_markdown()]
    if "ARC" in ctrls:
        lines += ["", f"**{label}: ARC vs. each other controller (paired Wilcoxon on SLO-violation time per scenario, Holm-corrected over scenarios)**", "",
                  "| vs. | ARC better | n.s. | other better |", "|---|---|---|---|"]
        for b in [c for c in ctrls if c != "ARC"]:
            ps = [paired_p(df, s_, "ARC", b, "slo_viol_s") for s_ in sc]
            adj = holm(ps)
            better = n_s = worse = 0
            for s_, p_ in zip(sc, adj):
                ma = df[(df.scenario == s_) & (df.controller == "ARC")].slo_viol_s.mean()
                mb = df[(df.scenario == s_) & (df.controller == b)].slo_viol_s.mean()
                if p_ >= 0.05:
                    n_s += 1
                elif ma < mb:
                    better += 1
                else:
                    worse += 1
            lines.append(f"| {b} | {better} | {n_s} | {worse} |")
    open(f"{RES}/tables.md", "a").write("\n" + "\n".join(lines) + "\n")


def fig_robustness(runs):
    """runs: list of (label, dataframe, controllers)."""
    fig, axs = plt.subplots(1, 2, figsize=(9, 3.3))
    width = 0.8 / len(runs)
    allc = ["HPA", "HPA-fast", "HPA-45", "PredHPA", "HPA+RL", "ARC", "ARC-noGraph", "ARC-bf"]
    for k, (label, d, ctrls) in enumerate(runs):
        f = d[d.scenario != "baseline"]
        for j, c in enumerate([c for c in allc if c in ctrls]):
            x = allc.index(c) + (k - (len(runs) - 1) / 2) * width
            g = f[f.controller == c]
            v, h = ci(g.groupby("seed").slo_viol_s.mean())
            axs[0].bar(x, max(v, 0.5), width * 0.95, yerr=h, color=COL.get(c, "#777777"), alpha=1,
                       hatch=["", "//", "xx"][k], edgecolor="white", error_kw={"lw": 0.6}, label=label if j == 0 or c == "HPA" else None)
            v2, h2 = ci(g.groupby("seed").replica_hours.mean())
            axs[1].bar(x, v2, width * 0.95, yerr=h2, color=COL.get(c, "#777777"), alpha=1,
                       hatch=["", "//", "xx"][k], edgecolor="white", error_kw={"lw": 0.6})
    for ax in axs:
        ax.set_xticks(range(len(allc)))
        ax.set_xticklabels(allc, rotation=30, ha="right")
    axs[0].set_yscale("log")
    axs[0].set_ylabel("SLO-violation time (s, log)\nmean over fault scenarios")
    axs[1].set_ylabel("Cost (replica-hours)")
    handles = [plt.Rectangle((0, 0), 1, 1, fc="#bbbbbb", hatch=["", "//", "xx"][k], ec="white") for k in range(len(runs))]
    axs[0].legend(handles, [r[0] for r in runs], fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(f"{FIG}/fig_robustness.png")
    plt.close(fig)


def retry_tables():
    """How much of ARC's advantage survives against baselines with limited client retries?"""
    lines = []
    cols = ["slo_viol_s", "loss_pct", "replica_hours", "mttr_s"]
    ctrls = ["HPA-45", "HPA-45-r1", "HPA-45-r0", "PredHPA", "PredHPA-r1", "ARC"]
    rdf = {}
    for label, main, extra, seeds in [("NASA trace, 10-service graph", "main.csv", "main_retry.csv", None),
                                      ("NASA trace, 24-service deep graph", "main_deep.csv", "main_deep_retry.csv", (200, 229))]:
        if not (os.path.exists(f"{RES}/{main}") and os.path.exists(f"{RES}/{extra}")):
            continue
        a = pd.read_csv(f"{RES}/{main}")
        if seeds:
            a = a[a.seed.between(*seeds)]
        d = pd.concat([a[a.controller.isin(["HPA-45", "PredHPA", "ARC"])], pd.read_csv(f"{RES}/{extra}")], ignore_index=True)
        rdf[label] = d
        f = d[d.scenario != "baseline"]
        agg = f.groupby("controller")[cols].mean().loc[ctrls]
        lines += [f"**Retry-limited baselines, {label}: mean over fault scenarios** (r1 = at most 1 retry, r0 = no retries; ARC adapts its caps)", "", agg.round(2).to_markdown(), ""]
        sc = [x for x in SCEN if x in set(d.scenario)]
        lines += [f"**Retry-limited baselines, {label}: SLO-violation time (s)**", "", "| Scenario | " + " | ".join(ctrls) + " |", "|---|" + "---|" * len(ctrls)]
        for s_ in sc:
            vals = {c: ci(d[(d.scenario == s_) & (d.controller == c)].slo_viol_s)[0] for c in ctrls}
            best = min(vals, key=vals.get)
            lines.append(f"| {s_} | " + " | ".join((f"**{fmt(d[(d.scenario == s_) & (d.controller == c)].slo_viol_s)}**" if c == best else fmt(d[(d.scenario == s_) & (d.controller == c)].slo_viol_s)) for c in ctrls) + " |")
        lines += ["", f"**{label}: ARC vs. retry-limited baselines (paired Wilcoxon, Holm over scenarios)**", "", "| vs. | ARC better | n.s. | other better |", "|---|---|---|---|"]
        for b in ["HPA-45-r1", "HPA-45-r0", "PredHPA-r1"]:
            adj = holm([paired_p(d, s_, "ARC", b, "slo_viol_s") for s_ in sc])
            bt = ns = wr = 0
            for s_, p_ in zip(sc, adj):
                ma = d[(d.scenario == s_) & (d.controller == "ARC")].slo_viol_s.mean()
                mb = d[(d.scenario == s_) & (d.controller == b)].slo_viol_s.mean()
                if p_ >= 0.05:
                    ns += 1
                elif ma < mb:
                    bt += 1
                else:
                    wr += 1
            lines.append(f"| {b} | {bt} | {ns} | {wr} |")
        lines.append("")
    if os.path.exists(f"{RES}/antifragile_stationary_retry.csv"):
        a = pd.concat([pd.read_csv(f"{RES}/antifragile_stationary.csv").query("controller == 'ARC'"),
                       pd.read_csv(f"{RES}/antifragile_stationary_retry.csv").query("controller != 'ARC'")], ignore_index=True)
        lines += ["**Repeated stress (identical load), retry-limited baselines**", "", "| Controller | epochs 1-3 (s) | epochs 8-11 (s) | change | mean pods |", "|---|---|---|---|---|"]
        for c in ["HPA-45-r0", "HPA-45-r1", "PredHPA-r1", "ARC"]:
            x = a[a.controller == c]
            e = x[x.epoch <= 3].groupby("seed").viol_s.mean()
            l_ = x[x.epoch >= 8].groupby("seed").viol_s.mean()
            try:
                p = stats.wilcoxon(e.values, l_.values).pvalue
            except ValueError:
                p = 1.0
            lines.append(f"| {c} | {fmt(e)} | {fmt(l_)} | {100 * (l_.mean() - e.mean()) / max(e.mean(), 1e-9):+.0f}% (p={p:.3g}) | {x.pods.mean():.1f} |")
        lines.append("")
    open(f"{RES}/tables.md", "a").write("\n" + "\n".join(lines) + "\n")
    return rdf


def fig_retry(rdf):
    fig, axs = plt.subplots(1, 2, figsize=(9, 3.4))
    order = ["HPA-45", "HPA-45-r1", "HPA-45-r0", "PredHPA", "PredHPA-r1", "ARC"]
    for ax, (label, d) in zip(axs, rdf.items()):
        f = d[d.scenario != "baseline"]
        for c in order:
            g = f[f.controller == c]
            x, y = g.groupby("seed").replica_hours.mean(), g.groupby("seed").slo_viol_s.mean()
            ax.errorbar(x.mean(), max(y.mean(), 1), xerr=ci(x)[1], yerr=ci(y)[1], marker="o", ms=7, color=COL[c], label=c, capsize=1.5, lw=0.8)
        ax.set_yscale("log")
        ax.set_title(label, fontsize=9)
        ax.set_xlabel("Cost (replica-hours per run)")
    axs[0].set_ylabel("SLO-violation time (s, log)\nmean over fault scenarios")
    axs[0].legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(f"{FIG}/fig_retry.png")
    plt.close(fig)


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
        sw = pd.read_csv(f"{RES}/sweeps.csv")
        if os.path.exists(f"{RES}/sweeps_retry.csv"):
            sw = pd.concat([sw, pd.read_csv(f"{RES}/sweeps_retry.csv").query("controller != 'ARC'")], ignore_index=True)
        fig_sweeps(sw)
    if os.path.exists(f"{RES}/antifragile.csv"):
        af = pd.read_csv(f"{RES}/antifragile.csv")
        fig_antifragile(af)
        antifragile_stats(af)
    if os.path.exists(f"{RES}/antifragile_stationary.csv"):
        st = pd.read_csv(f"{RES}/antifragile_stationary.csv")
        if os.path.exists(f"{RES}/antifragile_stationary_retry.csv"):
            st = pd.concat([st, pd.read_csv(f"{RES}/antifragile_stationary_retry.csv").query("controller != 'ARC'")], ignore_index=True)
        fig_antifragile(st, "fig_antifragile_stationary")
        antifragile_stats(st, "Repeated-stress experiment with identical load every epoch (isolates learning from workload drift)")
    runs = [("NASA trace, 10-service graph", df, sorted(set(df.controller)))]
    for fn, label, cts in [("main_clarknet.csv", "ClarkNet trace, 10-service graph", ["Static", "HPA", "HPA-fast", "HPA-45", "PredHPA", "HPA+RL", "ARC"]),
                           ("main_deep.csv", "NASA trace, 24-service deep graph", ["HPA", "HPA-45", "PredHPA", "ARC", "ARC-noGraph", "ARC-bf"])]:
        if os.path.exists(f"{RES}/{fn}"):
            d = pd.read_csv(f"{RES}/{fn}")
            if fn == "main_deep.csv" and os.path.exists(f"{RES}/main_deep_abl.csv"):
                d = pd.concat([d, pd.read_csv(f"{RES}/main_deep_abl.csv")], ignore_index=True)
                cts = cts + ["ARC-noRemed", "ARC-noGuard"]
            extra_tables(d, label, cts)
            runs.append((label, d, cts))
    if len(runs) > 1:
        fig_robustness(runs)
    rdf = retry_tables()
    if rdf:
        fig_retry(rdf)
    print("ok")
