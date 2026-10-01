"""Episode runner and metrics."""
import numpy as np

from . import controllers as C
from . import scenarios as S
from . import topology as T
from . import workload as W
from .sim import CloudSim, DT, AVAIL_SLO

WARM = 120  # ticks (10 min) excluded from metrics


def run_episode(ctrl_name, scenario, seed, faults=None, load=None, ctrl=None, keep=False):
    rng = np.random.default_rng(10_000 + seed)
    if load is None:
        base = W.window(seed % len(W.WINDOWS))
        load = base * np.exp(rng.normal(0, 0.03, len(base)))
    if faults is None:
        faults = S.make_faults(scenario, rng)
    sim = CloudSim(load, faults, seed=20_000 + seed)
    ctrl = ctrl or C.make(ctrl_name, seed)
    obs = sim.initial_obs()
    for _ in range(len(load) - 1):
        act = ctrl.step(obs)
        obs = sim.step(act)
    m = metrics(sim, ctrl)
    m.update(controller=ctrl_name, scenario=scenario, seed=seed)
    if keep:
        return m, sim, ctrl
    return m


def metrics(sim, ctrl):
    L = {k: np.array(v)[WARM:] for k, v in sim.log.items()}   # drop the 10-min warm-up
    legit, good, avail = L["legit"], L["good"], L["avail"]
    viol = avail < AVAIL_SLO
    tot = legit.sum()
    out = {
        "slo_viol_s": float(viol.sum() * DT),
        "loss_pct": float(100 * (1 - good.sum() / tot)),
        "shed_pct": float(100 * L["shed_legit"].sum() / tot),
        "replica_hours": float(L["pods"].sum() * DT / 3600),
        "peak_pods": float(L["pods"].max()),
        "p95_lat_s": float(np.percentile(L["lat"], 95)),
        "min_avail": float(avail.min()),
        "scale_actions": int(sim.n_scale_actions),
    }
    # MTTR over non-chaos fault starts
    mttrs = []
    for (t0, kind, svc) in sim.events:
        t0 = max(t0 - WARM, 0)
        seg = avail[t0:t0 + 240]
        bad = np.where(seg < AVAIL_SLO)[0]
        if len(bad) == 0:
            mttrs.append(0.0)
            continue
        first = bad[0]
        rec = None
        for k in range(first, len(seg) - 2):
            if (seg[k:k + 3] >= AVAIL_SLO).all():
                rec = k
                break
        mttrs.append(float(((rec if rec is not None else len(seg)) - first) * DT))
    out["mttr_s"] = float(np.mean(mttrs)) if mttrs else 0.0
    # chaos overhead: SLO violation ticks within 60 s after a chaos probe
    ce = [(t - WARM, s_) for t, s_ in sim.chaos_events if t >= WARM]
    out["n_chaos"] = len(ce)
    out["chaos_harm_s"] = float(sum((avail[t:t + 12] < AVAIL_SLO).sum() for t, _ in ce) * DT) if ce else 0.0
    # attack detection delay and poison-detection quality (ARC variants only)
    out["attack_det_delay_s"] = np.nan
    if hasattr(ctrl, "t_attack_first") and ctrl.t_attack_first is not None:
        for f in sim.faults:
            if f.kind == "ddos":
                out["attack_det_delay_s"] = float((ctrl.t_attack_first - f.t0) * DT)
    out["poison_f1"] = np.nan
    if hasattr(ctrl, "log") and ctrl.log.get("flag_poison"):
        for f in sim.faults:
            if f.kind == "poison":
                a, b = f.t0, f.t0 + f.dur
                truth = set(f.svcs)
                tp = fp = fn = 0
                for t in range(a, min(b, len(ctrl.log["flag_poison"]))):
                    fl = ctrl.log["flag_poison"][t]
                    tp += len(fl & truth)
                    fp += len(fl - truth)
                    fn += len(truth - fl)
                out["poison_f1"] = 2 * tp / max(2 * tp + fp + fn, 1)
    return out
