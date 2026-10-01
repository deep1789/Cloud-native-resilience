"""Repeated-stress experiment: the same faults hit every 30 min for 6 h. Does the system get better with exposure?"""
import sys, time
import numpy as np, pandas as pd
from common import pmap, RES
from arcsim import controllers as C, run, workload as W, topology as T
from arcsim.sim import Fault, AVAIL_SLO, DT

SEEDS = int(sys.argv[1]) if len(sys.argv) > 1 else 12
S0 = int(sys.argv[2]) if len(sys.argv) > 2 else 100   # evaluation seeds start at 100 (development used 0-19)
CTRL = ["HPA", "HPA-fast", "HPA-45", "PredHPA", "ARC-noChaos", "ARC"]
EPOCH = 360   # ticks (30 min)
N_EP = 12
I = T.IDX


def job(a):
    c, s = a
    rng = np.random.default_rng(10_000 + s)
    base = W.window(s % len(W.WINDOWS), minutes=N_EP * 30)
    load = base * np.exp(rng.normal(0, 0.03, len(base)))
    faults = []
    for e in range(N_EP):
        o = e * EPOCH + 40 if e > 0 else EPOCH + 40   # first epoch is a quiet warm-up for all controllers
        if e == 0:
            continue
        faults += [Fault("kill", o, svc=I["productcatalog"], mag=0.7),
                   Fault("kill", o + 120, svc=I["cart"], mag=0.7),
                   Fault("kill", o + 200, svc=I["payment"], mag=0.5)]
    m, sim, ctrl = run.run_episode(c, "repeated", s, faults=faults, load=load, keep=True)
    av = np.array(sim.log["avail"])
    pods = np.array(sim.log["pods"])
    rows = []
    for e in range(1, N_EP):
        seg = av[e * EPOCH:(e + 1) * EPOCH]
        rows.append(dict(controller=c, seed=s, epoch=e, viol_s=float((seg < AVAIL_SLO).sum() * DT),
                         loss_pct=float(100 * (1 - (np.array(sim.log["good"])[e * EPOCH:(e + 1) * EPOCH].sum()
                                                    / np.array(sim.log["legit"])[e * EPOCH:(e + 1) * EPOCH].sum()))),
                         pods=float(pods[e * EPOCH:(e + 1) * EPOCH].mean())))
    return rows


if __name__ == "__main__":
    t0 = time.time()
    out = [r for rows in pmap(job, [(c, s) for c in CTRL for s in range(S0, S0 + SEEDS)]) for r in rows]
    pd.DataFrame(out).to_csv(f"{RES}/antifragile.csv", index=False)
    print("done %.0fs" % (time.time() - t0))
