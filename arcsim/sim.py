"""Trace-driven fluid simulator of a Kubernetes-style microservice cluster.

One tick = 5 s. Per service we track ready pods, pods still starting, a request
backlog and a capacity-health multiplier. Requests flow down the dependency graph;
failed calls are retried (retry storms), slow calls time out, pods start with a
delay (auto-scaling lag), and the ReplicaSet replaces lost pods automatically.
Controllers act through ``Actions``; they only see (noisy, delayed, possibly
poisoned) telemetry through ``Obs``.
"""
from dataclasses import dataclass, field

import numpy as np

from . import topology as T
from .workload import DT

TIMEOUT = 2.0       # s, per-call timeout
LAT_SLO = 1.0       # s, end-to-end latency objective
AVAIL_SLO = 0.95    # a tick violates the SLO when goodput / offered legit load < this
QUEUE_SECS = 3.0    # queue holds this many seconds of work per pod
DEFAULT_RETRIES = 2


@dataclass
class Fault:
    kind: str          # flash | ddos | kill | node | gray | poison
    t0: int            # start tick
    dur: int = 0       # duration in ticks (where applicable)
    svc: int = -1      # target service (kill/gray)
    svcs: tuple = ()   # target services (node/poison)
    mag: float = 1.0   # flash: load multiplier | ddos: attack/baseline | kill/node: fraction | gray: capacity factor | poison: reporting factor
    ramp: int = 12     # ticks to reach full magnitude (flash/ddos)
    chaos: bool = False


@dataclass
class Actions:
    desired: np.ndarray = None          # desired replicas per service
    shed_legit: float = 0.0
    shed_attack: float = 0.0
    retries: np.ndarray = None          # retry cap per caller service
    breaker: set = field(default_factory=set)   # open (caller, callee) edges
    remediate: set = field(default_factory=set)  # services whose pods are rescheduled
    chaos_kill: tuple = None            # (service, n_pods)


@dataclass
class Obs:
    t: int
    ingress: float          # total requests/s arriving at ingress (legit + attack)
    suspect: float          # fraction of ingress flagged by a noisy L7 classifier
    ready: np.ndarray
    starting: np.ndarray
    desired: np.ndarray
    util: np.ndarray        # may be poisoned
    queue: np.ndarray       # backlog / queue capacity, may be poisoned
    err: np.ndarray         # local failure fraction, may be poisoned
    served: np.ndarray      # req/s served (not poisoned)
    offered: np.ndarray     # req/s offered (not poisoned)
    avail: float            # user-visible availability (legit goodput / legit load)
    total_pods: int


class CloudSim:
    def __init__(self, load, faults, seed, p95_rps=600.0):
        self.load = np.asarray(load, float)
        self.faults = list(faults)
        self.rng = np.random.default_rng(seed)
        self.p95 = p95_rps
        self.n_ticks = len(self.load)
        self.t = 0
        N = T.N
        l0 = float(self.load[:12].mean())
        self.ready = np.maximum(T.MIN_REP, np.ceil(T.M * l0 / (T.MU * 0.6))).astype(float)
        if self.ready.sum() > T.QUOTA:      # initial sizing must respect the cluster quota
            self.ready = np.maximum(T.MIN_REP, np.floor(self.ready * T.QUOTA / self.ready.sum()))
        self.starting = [[] for _ in range(N)]     # remaining ticks for each starting pod
        self.desired = self.ready.copy()
        self.queue = np.zeros(N)
        self.health = np.ones(N)
        self.restore_at = {}                       # service -> tick when remediation completes
        self.E_prev = np.ones(N)                   # end-to-end success of each subtree, previous tick
        self.s = np.ones(N)
        self.served = np.zeros(N)
        self.offered = np.zeros(N)
        self.util = np.zeros(N)
        self.wait = np.zeros(N)
        self.retries = np.full(N, float(DEFAULT_RETRIES))
        self.breaker = set()
        self.shed_legit = 0.0
        self.shed_attack = 0.0
        self.attack_base = 0.5 * float(self.load.mean())
        self.log = {k: [] for k in ["legit", "good", "avail", "lat", "pods", "attack", "shed_legit",
                                    "E0", "ingress"]}
        self.svc_log = {k: [] for k in ["s", "util", "ready"]}
        self.events = []    # (tick, kind, service) of non-chaos fault starts
        self.chaos_events = []
        self.last_obs = None
        self.n_scale_actions = 0

    # ------------------------------------------------------------------ faults
    def _legit_attack(self, t):
        legit = self.load[t]
        attack = 0.0
        for f in self.faults:
            if f.kind == "flash" and f.t0 <= t < f.t0 + f.dur:
                up = min(1.0, (t - f.t0 + 1) / max(f.ramp, 1))
                down = min(1.0, (f.t0 + f.dur - t) / max(f.ramp, 1))
                legit *= 1.0 + (f.mag - 1.0) * min(up, down)
            if f.kind == "ddos" and f.t0 <= t < f.t0 + f.dur:
                up = min(1.0, (t - f.t0 + 1) / max(f.ramp, 1))
                down = min(1.0, (f.t0 + f.dur - t) / max(f.ramp, 1))
                attack += f.mag * self.attack_base * min(up, down)
        return legit, attack

    def _kill(self, svc, frac=None, n=None):
        have = int(self.ready[svc])
        k = n if n is not None else int(np.ceil(frac * have))
        k = max(0, min(k, have - 1)) if n is not None else max(0, min(k, have))
        self.ready[svc] -= k
        return k

    def _apply_faults(self, t):
        for f in self.faults:
            if f.t0 == t:
                if f.kind == "kill":
                    self._kill(f.svc, frac=f.mag)
                elif f.kind == "node":
                    for s in f.svcs:
                        self._kill(s, frac=f.mag)
                elif f.kind == "gray":
                    self.health[f.svc] = f.mag
                if not f.chaos:
                    self.events.append((t, f.kind, f.svc if f.svc >= 0 else (f.svcs[0] if f.svcs else -1)))
            if f.kind == "gray" and t == f.t0 + f.dur and f.svc not in self.restore_at:
                self.health[f.svc] = 1.0
        for s, tr in list(self.restore_at.items()):
            if t >= tr:
                self.health[s] = 1.0
                del self.restore_at[s]

    # ---------------------------------------------------------------- dynamics
    def step(self, act: Actions = None):
        t = self.t
        N = T.N
        if act is not None:
            self._apply_actions(act)
        self._apply_faults(t)

        # pods finishing start-up
        for i in range(N):
            if self.starting[i]:
                self.starting[i] = [r - 1 for r in self.starting[i]]
                done = sum(1 for r in self.starting[i] if r <= 0)
                self.starting[i] = [r for r in self.starting[i] if r > 0]
                self.ready[i] += done
        # ReplicaSets: scale down / cancel surplus, then schedule missing pods within the cluster quota.
        # Scheduling is fair: one pod at a time to the service with the lowest have/want ratio.
        for i in range(N):
            want = int(self.desired[i])
            have = int(self.ready[i]) + len(self.starting[i])
            if have > want:
                excess = have - want
                cancel = min(excess, len(self.starting[i]))      # cancel pods still starting first
                self.starting[i] = self.starting[i][cancel:]
                self.ready[i] -= min(excess - cancel, int(self.ready[i]) - 1)
        total = int(self.ready.sum() + sum(len(x) for x in self.starting))
        while total < T.QUOTA:
            best, ratio = -1, 2.0
            for i in range(N):
                want = int(self.desired[i])
                have = int(self.ready[i]) + len(self.starting[i])
                if have < want and have / max(want, 1) < ratio:
                    best, ratio = i, have / max(want, 1)
            if best < 0:
                break
            delay = max(1, int(round(T.STARTUP[best] * self.rng.uniform(0.8, 1.3) / DT)))
            self.starting[best].append(delay)
            total += 1

        legit, attack = self._legit_attack(t)
        legit_adm = legit * (1 - self.shed_legit)
        attack_adm = attack * (1 - self.shed_attack)
        ext = legit_adm + attack_adm

        served = np.zeros(N)
        offered = np.zeros(N)
        s = np.ones(N)
        wait = np.zeros(N)
        cap = self.ready * T.MU * self.health
        util = np.zeros(N)
        # retry amplification of a call to child c from caller j: sum_{k<=R_j} F_c^k
        F = np.clip(1.0 - self.E_prev, 0.0, 1.0)

        def amp(j, c):
            r = int(self.retries[j])
            return sum(F[c] ** k for k in range(r + 1))

        for i in T.TOPO:
            if i == 0:
                off = ext
            else:
                off = 0.0
                for j in range(N):
                    w = T.W[j, i]
                    if w > 0 and (j, i) not in self.breaker:
                        off += w * served[j] * amp(j, i)
            demand = off + self.queue[i] / DT
            c_i = max(cap[i], 1e-6)
            sv = min(demand, c_i)
            q_new = self.queue[i] + (off - sv) * DT
            qmax = self.ready[i] * T.MU[i] * QUEUE_SECS
            q_new = min(max(q_new, 0.0), qmax)
            w_i = q_new / c_i
            g = 1.0 if w_i <= TIMEOUT else max(0.0, 1.0 - (w_i - TIMEOUT) / TIMEOUT)
            s[i] = g * min(1.0, sv / max(demand, 1e-9)) if demand > 1e-9 else 1.0
            served[i] = sv
            offered[i] = off
            self.queue[i] = q_new
            wait[i] = w_i
            util[i] = min(1.5, off / c_i)

        # end-to-end success and latency (children before parents)
        E = s.copy()
        lat = T.BASE_LAT + wait
        for i in reversed(T.TOPO):
            fail_prod = 1.0
            slow = 0.0
            for c in range(N):
                w = T.W[i, c]
                if w > 0 and T.CRIT[i, c] > 0 and (i, c) not in self.breaker:
                    fail_prod *= 1.0 - min(1.0, w) * (1.0 - E[c])
                    slow = max(slow, lat[c])
            E[i] = s[i] * fail_prod
            lat[i] = T.BASE_LAT[i] + wait[i] + slow
        self.E_prev = E
        lat_ok = 1.0 if lat[0] <= LAT_SLO else float(np.exp(-(lat[0] - LAT_SLO) / LAT_SLO))
        good = legit_adm * E[0] * lat_ok
        avail = good / legit if legit > 1e-9 else 1.0

        self.s, self.served, self.offered, self.util, self.wait = s, served, offered, util, wait
        L = self.log
        L["legit"].append(legit)
        L["good"].append(good)
        L["avail"].append(avail)
        L["lat"].append(lat[0])
        L["pods"].append(self.ready.sum() + sum(len(x) for x in self.starting))
        L["attack"].append(attack)
        L["shed_legit"].append(legit * self.shed_legit)
        L["E0"].append(E[0])
        L["ingress"].append(ext)
        self.svc_log["s"].append(s.copy())
        self.svc_log["util"].append(util.copy())
        self.svc_log["ready"].append(self.ready.copy())
        self.t += 1
        self.last_obs = self._observe(t, legit + attack, attack, avail)
        return self.last_obs

    def _apply_actions(self, a: Actions):
        N = T.N
        if a.desired is not None:
            new = np.clip(np.round(a.desired), T.MIN_REP, T.MAX_REP)
            self.n_scale_actions += int((new != self.desired).sum())
            self.desired = new
        if a.retries is not None:
            self.retries = np.asarray(a.retries, float)
        self.breaker = set(a.breaker)
        self.shed_legit = float(np.clip(a.shed_legit, 0, 0.95))
        self.shed_attack = float(np.clip(a.shed_attack, 0, 0.99))
        for s in a.remediate:
            if s not in self.restore_at and self.health[s] < 1.0:
                self.restore_at[s] = self.t + int(np.ceil(T.STARTUP[s] / DT))
        if a.chaos_kill is not None:
            svc, k = a.chaos_kill
            killed = self._kill(svc, n=k)
            if killed:
                self.chaos_events.append((self.t, svc))

    # --------------------------------------------------------------- telemetry
    def _observe(self, t, ingress, attack, avail):
        rng = self.rng
        N = T.N
        util = self.util * (1 + rng.normal(0, 0.05, N))
        qobs = self.queue / np.maximum(self.ready * T.MU * QUEUE_SECS, 1e-9)
        qobs = qobs * (1 + rng.normal(0, 0.05, N))
        err = (1 - self.s) + np.abs(rng.normal(0, 0.01, N))
        for f in self.faults:
            if f.kind == "poison" and f.t0 <= t < f.t0 + f.dur:
                for s in f.svcs:       # compromised exporter under-reports saturation
                    util[s] *= f.mag
                    qobs[s] *= f.mag
                    err[s] *= f.mag
        share = attack / ingress if ingress > 1e-9 else 0.0
        suspect = float(np.clip(0.8 * share + 0.05 * (1 - share) + rng.normal(0, 0.03), 0, 1))
        starting = np.array([len(x) for x in self.starting], float)
        return Obs(t=t, ingress=ingress * (1 + rng.normal(0, 0.02)), suspect=suspect,
                   ready=self.ready.copy(), starting=starting, desired=self.desired.copy(),
                   util=np.clip(util, 0, None), queue=np.clip(qobs, 0, None),
                   err=np.clip(err, 0, 1), served=self.served * (1 + rng.normal(0, 0.02, N)),
                   offered=self.offered * (1 + rng.normal(0, 0.02, N)), avail=avail,
                   total_pods=int(self.ready.sum() + starting.sum()))

    def initial_obs(self):
        return self.step(None)
