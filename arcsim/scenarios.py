"""Stress scenarios, fixed before any controller was run. Fault timing/targets are randomised per seed."""
import numpy as np

from . import topology as T
from .sim import Fault

I = T.IDX
TICKS = 1440  # 2 h at 5 s


def _t(rng, lo, hi):
    return int(rng.integers(lo, hi))


def make_faults(name, rng, flash_mag=3.0, ddos_mag=2.5):
    t0 = _t(rng, 420, 560)
    if name == "baseline":
        return []
    if name == "flash":
        return [Fault("flash", t0, 180, mag=flash_mag, ramp=12)]
    if name == "flash_step":     # abrupt, unforecastable surge
        return [Fault("flash", t0, 180, mag=flash_mag, ramp=1)]
    if name == "kill_cascade":   # lose most pods of a shared dependency at peak -> retry storm
        s = int(rng.choice([I["productcatalog"], I["cart"], I["currency"]]))
        return [Fault("kill", t0, svc=s, mag=0.7)]
    if name == "node_failure":   # one node holds a share of pods of several services
        svcs = tuple(int(x) for x in rng.choice(np.arange(T.N), 4, replace=False))
        return [Fault("node", t0, svcs=svcs, mag=0.4)]
    if name == "gray":           # pods stay "healthy" for liveness probes but serve slowly
        s = int(rng.choice([I["checkout"], I["productcatalog"], I["cart"]]))
        return [Fault("gray", t0, 240, svc=s, mag=0.3)]
    if name == "ddos":
        return [Fault("ddos", t0, 180, mag=ddos_mag, ramp=6)]
    if name == "ddos_kill":
        s = int(rng.choice([I["productcatalog"], I["cart"]]))
        return [Fault("ddos", t0, 180, mag=ddos_mag, ramp=6), Fault("kill", t0 + 30, svc=s, mag=0.5)]
    if name == "poison_flash":   # compromised exporters under-report saturation while load surges
        svcs = tuple(int(x) for x in rng.choice([I["productcatalog"], I["cart"], I["currency"], I["recommendation"]], 3, replace=False))
        return [Fault("poison", t0 - 20, 260, svcs=svcs, mag=0.25), Fault("flash", t0, 180, mag=flash_mag, ramp=12)]
    if name == "storm":          # sequence of mixed faults
        s1, s2 = (int(x) for x in rng.choice([I["productcatalog"], I["cart"], I["checkout"], I["payment"]], 2, replace=False))
        return [Fault("kill", t0 - 100, svc=s1, mag=0.5),
                Fault("flash", t0 + 100, 120, mag=2.0, ramp=12),
                Fault("gray", t0 + 330, 180, svc=s2, mag=0.35),
                Fault("ddos", t0 + 560, 120, mag=2.0, ramp=6)]
    raise KeyError(name)


SCENARIOS = ["baseline", "flash", "flash_step", "kill_cascade", "node_failure", "gray", "ddos", "ddos_kill", "poison_flash", "storm"]
