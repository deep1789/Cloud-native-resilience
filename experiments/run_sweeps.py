"""Stress sweeps: surge magnitude and DDoS intensity (including beyond the cluster quota)."""
import sys, time
import numpy as np, pandas as pd
from common import pmap, RES
from arcsim import controllers as C, scenarios as S, run, workload as W
from arcsim.sim import Fault

SEEDS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
S0 = int(sys.argv[2]) if len(sys.argv) > 2 else 100   # evaluation seeds start at 100 (development used 0-19)
CTRL = ["HPA", "HPA-fast", "HPA-45", "PredHPA", "HPA+RL", "ARC"]


def job(a):
    kind, mag, c, s = a
    rng = np.random.default_rng(10_000 + s)
    t0 = int(rng.integers(420, 560))
    f = [Fault("flash", t0, 180, mag=mag, ramp=12)] if kind == "flash" else [Fault("ddos", t0, 180, mag=mag, ramp=6)]
    base = W.window(s % len(W.WINDOWS))
    load = base * np.exp(rng.normal(0, 0.03, len(base)))
    m = run.run_episode(c, kind, s, faults=f, load=load)
    m["magnitude"] = mag
    return m


if __name__ == "__main__":
    t0 = time.time()
    jobs = [("flash", m, c, s) for m in [1.5, 2, 3, 4, 5, 6] for c in CTRL for s in range(S0, S0 + SEEDS)]
    jobs += [("ddos", m, c, s) for m in [1, 2, 3, 5, 8] for c in CTRL for s in range(S0, S0 + SEEDS)]
    pd.DataFrame(pmap(job, jobs)).to_csv(f"{RES}/sweeps.csv", index=False)
    print("done %.0fs" % (time.time() - t0))
