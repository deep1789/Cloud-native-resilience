"""Controllers: baselines (Static, HPA, PredHPA, HPA+RL) and the proposed ARC.

ARC = Antifragile Resilience Controller. Five mechanisms, each switchable for ablation:
  graph   - dependency-graph feed-forward capacity planning from a forecast of ingress load
  chaos   - chaos-in-the-loop learning of per-service fragility with a bandit that picks
            low-blast-radius probes; fragility sets redundancy headroom
  sec     - attack/flash-crowd discrimination + selective shedding, and telemetry-poisoning
            detection by checking reported utilisation against flow conservation on the graph
  guard   - safe-adaptation guardrail: model-checks scale-down / chaos actions before applying
  remed   - gray-failure detection, pod rescheduling, circuit breakers and retry budgets
ARC plans with an *imperfect* internal model (per-service capacity error of +-20%).
"""
import numpy as np

from . import topology as T
from .sim import Actions, DT, DEFAULT_RETRIES

N = T.N


class Holt:
    """Double exponential smoothing (level + trend) with multi-step forecast."""

    def __init__(self, alpha=0.4, beta=0.15):
        self.a, self.b, self.l, self.tr = alpha, beta, None, 0.0

    def update(self, x):
        if self.l is None:
            self.l = x
            return
        prev = self.l
        self.l = self.a * x + (1 - self.a) * (self.l + self.tr)
        self.tr = self.b * (self.l - prev) + (1 - self.b) * self.tr

    def forecast(self, h):
        return max(0.0, (self.l if self.l is not None else 0.0) + max(self.tr, 0.0) * h)


class Static:
    name = "Static"

    def __init__(self, seed=0):
        self.desired = None

    def step(self, obs):
        if self.desired is None:
            self.desired = obs.desired.copy()
        return Actions(desired=self.desired)


class HPA(Static):
    """Kubernetes HPA algorithm: desired = ceil(cur * util/target), 10% tolerance, 15 s sync,
    30 s metric window, scale-up <= max(2x, +4), 300 s scale-down stabilisation."""
    name = "HPA"
    TARGET = 0.6

    def __init__(self, seed=0):
        super().__init__(seed)
        self.hist = []
        self.rec = []

    def _recommend(self, obs, util):
        cur = np.maximum(obs.ready, 1.0)
        ratio = util / self.TARGET
        d = np.where(np.abs(ratio - 1) <= 0.1, cur, np.ceil(cur * ratio))
        return d

    def step(self, obs):
        if self.desired is None:
            self.desired = obs.desired.copy()
        self.hist.append(obs.util.copy())
        self.hist = self.hist[-6:]
        if obs.t % 3 == 0:
            util = np.mean(self.hist, axis=0)
            rec = self._postprocess(obs, self._recommend(obs, util))
            self.rec.append(rec)
            self.rec = self.rec[-60:]
            cur = np.maximum(self.desired, 1.0)
            up = np.minimum(rec, np.maximum(2 * cur, cur + 4))
            down = np.max(self.rec, axis=0)           # scale-down stabilisation window
            self.desired = np.where(rec > cur, up, np.minimum(cur, down))
        return Actions(desired=self.desired)

    def _postprocess(self, obs, rec):
        return rec


class PredHPA(HPA):
    """HPA + per-service Holt forecast of the service's own offered load (local, graph-agnostic)."""
    name = "PredHPA"

    def __init__(self, seed=0):
        super().__init__(seed)
        self.f = [Holt() for _ in range(N)]

    def _postprocess(self, obs, rec):
        fc = np.array([f.forecast(12) for f in self.f])
        return np.maximum(rec, np.ceil(fc / (T.MU * self.TARGET)))

    def step(self, obs):
        for i in range(N):
            self.f[i].update(obs.offered[i])
        return super().step(obs)


class HPAFast(HPA):
    """Aggressively tuned HPA: 5 s sync, 10 s metric window (same 300 s scale-down stabilisation)."""
    name = "HPA-fast"

    def step(self, obs):
        if self.desired is None:
            self.desired = obs.desired.copy()
        self.hist.append(obs.util.copy())
        self.hist = self.hist[-2:]
        util = np.mean(self.hist, axis=0)
        rec = self._postprocess(obs, self._recommend(obs, util))
        self.rec.append(rec)
        self.rec = self.rec[-60:]
        cur = np.maximum(self.desired, 1.0)
        up = np.minimum(rec, np.maximum(2 * cur, cur + 4))
        self.desired = np.where(rec > cur, up, np.minimum(cur, np.max(self.rec, axis=0)))
        return Actions(desired=self.desired)


class HPA45(HPA):
    """Cost-matched baseline: HPA with a lower utilisation target (more standing headroom)."""
    name = "HPA-45"
    TARGET = 0.45


class HPARL(HPA):
    """HPA + fixed ingress rate limiter (cannot tell attack from flash crowd)."""
    name = "HPA+RL"
    LIMIT = 900.0

    def step(self, obs):
        a = super().step(obs)
        if obs.ingress > self.LIMIT:
            shed = 1 - self.LIMIT / obs.ingress
            a.shed_legit = shed
            a.shed_attack = shed
        return a


class ARC(Static):
    name = "ARC"
    TARGET = 0.65

    def __init__(self, seed=0, graph=True, chaos=True, sec=True, guard=True, remed=True, label=None):
        super().__init__(seed)
        self.graph, self.chaos, self.sec, self.guard, self.remed = graph, chaos, sec, guard, remed
        if label:
            self.name = label
        rng = np.random.default_rng(seed + 12345)
        self.mu = T.MU * (1 + rng.uniform(-0.2, 0.2, N))       # imperfect internal capacity model
        self.rng = rng
        self.ing = Holt()
        self.loc = [Holt() for _ in range(N)]
        self.f_chaos = np.full(N, 0.2)       # fragility learned from probes
        self.f_inc = np.zeros(N)             # fragility learned from real incidents (slow decay)
        self.n_probe = np.ones(N)
        self.raw_hist = []
        self.last_desired = None
        self.attack_cnt = 0
        self.attacking = False
        self.probe = None                    # (service, start tick, peak_fail, peak_util)
        self.last_probe_t = -10 ** 9
        self.avail_hist = []
        self.deficit_cnt = np.zeros(N)
        self.breaker_until = {}
        self.retry_hold = np.full(N, float(DEFAULT_RETRIES))
        self.inc_loss = 0.0
        self.inc_active = False
        self.inc_service = None
        self.flags = {"poison": set(), "deficit": set()}
        self.log = {"flag_poison": [], "flag_deficit": [], "probe": [], "attack_det": []}
        self.vetoes = 0
        self.t_attack_first = None

    # -- helpers ------------------------------------------------------------
    @property
    def frag(self):
        return np.maximum(self.f_chaos, self.f_inc)

    def _graph_rate(self, obs, breaker):
        """Flow-conservation estimate of each service's offered load from parents' reported served rates."""
        r = np.zeros(N)
        for i in T.TOPO[1:]:
            for j in range(N):
                if T.W[j, i] > 0 and (j, i) not in breaker:
                    r[i] += T.W[j, i] * obs.served[j]
        r[0] = obs.ingress
        return r

    def step(self, obs):
        t = obs.t
        if self.desired is None:
            self.desired = obs.desired.copy()
            self.last_desired = obs.desired.copy()
        act = Actions(desired=self.desired.copy(), retries=self.retry_hold.copy())
        self.avail_hist.append(obs.avail)
        self.avail_hist = self.avail_hist[-12:]

        # ---- attack vs flash-crowd discrimination (security-aware) ---------
        a_hat = float(np.clip((obs.suspect - 0.05) / 0.75, 0, 1)) if self.sec else 0.0
        if a_hat > 0.15:
            self.attack_cnt = min(self.attack_cnt + 1, 6)
        else:
            self.attack_cnt = max(self.attack_cnt - 1, 0)
        det = self.attack_cnt >= 2
        if det and not self.attacking and self.t_attack_first is None:
            self.t_attack_first = t
        self.attacking = det
        self.log["attack_det"].append(det)
        if det:
            act.shed_attack, act.shed_legit = 0.85, 0.03
            legit_hat = obs.ingress * (1 - a_hat) * 0.97 + obs.ingress * a_hat * 0.15
        else:
            legit_hat = obs.ingress
        self.ing.update(legit_hat)

        # ---- robust utilisation: detect poisoned telemetry ------------------
        util_rob = obs.util.copy()
        poisoned = set()
        grate = self._graph_rate(obs, set())
        ugraph = grate / np.maximum(obs.ready * self.mu, 1e-9)
        if self.sec:
            for i in range(1, N):
                if ugraph[i] > 0.3 and obs.util[i] < 0.5 * ugraph[i]:
                    poisoned.add(i)
                    util_rob[i] = ugraph[i]
        self.flags["poison"] = poisoned
        self.log["flag_poison"].append(set(poisoned))

        # ---- gray-failure detection ---------------------------------------
        remediate = set()
        if self.remed:
            for i in range(N):
                cap_model = obs.ready[i] * self.mu[i]
                if cap_model > 0 and ugraph[i] > 0.8 and obs.served[i] < 0.65 * grate[i] and 0.9 * cap_model > 1.3 * obs.served[i] \
                        and obs.starting[i] == 0:
                    self.deficit_cnt[i] += 1
                else:
                    self.deficit_cnt[i] = max(0, self.deficit_cnt[i] - 1)
                if self.deficit_cnt[i] >= 3:
                    remediate.add(i)
            act.remediate = remediate
        self.flags["deficit"] = remediate
        self.log["flag_deficit"].append(set(remediate))

        # ---- circuit breakers (graceful degradation) + retry budgets ---------
        breaker = set()
        if self.remed:
            overloaded = bool(np.max(obs.err[[0, 1, 4, 5]]) > 0.15 or obs.avail < 0.9)
            for (j, c) in T.NONCRIT_EDGES:
                if overloaded and (obs.util[c] > 1.0 or obs.err[c] > 0.2 or obs.avail < 0.85):
                    self.breaker_until[(j, c)] = t + 12
                if self.breaker_until.get((j, c), -1) > t:
                    breaker.add((j, c))
            retries = np.full(N, float(DEFAULT_RETRIES))
            for j in range(N):
                kids = [c for c in range(N) if T.W[j, c] > 0]
                if kids:
                    worst = max(max(obs.err[c], 1 - 1 / max(1.0, obs.util[c])) for c in kids)
                    retries[j] = 0.0 if worst > 0.5 else (1.0 if worst > 0.2 else DEFAULT_RETRIES)
            self.retry_hold = retries
            act.retries = retries
        act.breaker = breaker

        # ---- capacity planning ------------------------------------------------
        h = 12
        Lf = self.ing.forecast(h)
        w_eff = T.W.copy()
        for (j, c) in breaker:
            w_eff[j, c] = 0.0
        m = T.call_multipliers(w_eff)
        if self.graph:
            ff = m * Lf / (self.mu * self.TARGET)
        else:
            for i in range(N):
                self.loc[i].update(obs.offered[i])
            ff = np.array([f.forecast(h) for f in self.loc]) / (self.mu * self.TARGET)
        frag = self.frag
        ff = np.ceil(ff * (1 + 0.8 * frag)) if self.chaos else np.ceil(ff * 1.15)
        fb = np.ceil(np.maximum(obs.ready, 1) * util_rob / self.TARGET)
        raw = np.maximum(ff, fb)
        # gray failure compensation while pods are being rescheduled
        for i in remediate:
            raw[i] = max(raw[i], np.ceil(obs.ready[i] * 1.8))
        raw = np.clip(raw, T.MIN_REP, T.MAX_REP)
        self.raw_hist.append(raw)
        self.raw_hist = self.raw_hist[-24:]

        cur = self.desired.copy()
        new = cur.copy()
        for i in range(N):
            if raw[i] >= cur[i]:
                new[i] = raw[i]
            else:
                target = np.max(self.raw_hist, axis=0)[i] if self.guard else raw[i]
                cand = max(target, np.floor(cur[i] * 0.8)) if self.guard else target
                if self.guard:
                    ok = (m[i] * Lf) / (max(cand, 1) * self.mu[i]) <= 0.8 and not self.inc_active and obs.avail >= 0.97
                    if not ok:
                        cand = cur[i]
                        self.vetoes += 1
                new[i] = min(cand, cur[i])
        self.desired = new
        act.desired = new.copy()

        # ---- incident tracking (learning from real faults) ----------------------
        self._track_incident(obs)

        # ---- chaos-in-the-loop probes -----------------------------------------------
        if self.chaos:
            self._chaos(obs, act, m, Lf)
        return act

    def _track_incident(self, obs):
        bad = obs.avail < 0.95
        self.inc_loss = 0.8 * self.inc_loss + (1 - obs.avail if bad else 0.0)
        if bad and not self.inc_active and self.probe is None:
            self.inc_active = True
            self.inc_service = None
            self.inc_peak = 0.0
        if self.inc_active:
            # root cause: deepest unhealthy service (graph RCA): lowest s among services whose critical children are healthy
            s_loc = 1 - obs.err
            cand = []
            for i in range(N):
                kids = [c for c in range(N) if T.W[i, c] > 0 and T.CRIT[i, c] > 0]
                if s_loc[i] < 0.85 and all(s_loc[c] >= 0.85 for c in kids):
                    cand.append((s_loc[i], i))
            if cand:
                self.inc_service = min(cand)[1]
            self.inc_peak = max(self.inc_peak, 1 - obs.avail)
            if obs.avail >= 0.97:
                if self.inc_service is not None:
                    i = self.inc_service
                    self.f_inc[i] = min(1.0, self.f_inc[i] + 0.45 * min(1.0, self.inc_peak * 2))
                    self.log["probe"].append((obs.t, i, "incident", float(self.inc_peak)))
                self.inc_active = False
        self.f_inc *= 0.9997          # half-life ~ 2.1 h at 5 s ticks

    def _chaos(self, obs, act, m, Lf):
        t = obs.t
        if self.probe is not None:
            i, t0, pf, pu = self.probe
            pf = max(pf, float(obs.err[i]))
            pu = max(pu, float(obs.util[i] if i not in self.flags["poison"] else 0.0))
            self.probe = (i, t0, pf, pu)
            if t - t0 >= 12:
                r = min(1.0, 4 * pf + 2 * max(0.0, pu - 0.9))
                self.f_chaos[i] = 0.7 * self.f_chaos[i] + 0.3 * r
                self.n_probe[i] += 1
                self.log["probe"].append((t, i, "chaos", r))
                self.probe = None
            return
        healthy = len(self.avail_hist) >= 12 and min(self.avail_hist) >= 0.98 and not self.inc_active \
            and not self.attacking and obs.starting.sum() == 0
        # guardrail: no probes while load is ramping or the cluster quota is nearly used up
        calm = abs(Lf - self.ing.l) <= 0.08 * max(self.ing.l, 1.0) and obs.total_pods <= 0.7 * T.QUOTA
        if self.guard and not calm:
            healthy = False
        if healthy and t - self.last_probe_t >= 120:
            ucb = self.frag + 0.25 * np.sqrt(np.log(self.n_probe.sum() + 1) / self.n_probe)
            for i in np.argsort(-ucb):
                if obs.ready[i] < 2:
                    continue
                # guardrail: predicted utilisation after losing one pod must stay below 0.75
                after = (m[i] * Lf) / ((obs.ready[i] - 1) * self.mu[i])
                if self.guard and after > 0.75:
                    continue
                act.chaos_kill = (int(i), 1)
                self.probe = (int(i), t, 0.0, 0.0)
                self.last_probe_t = t
                break


def make(name, seed=0):
    base = {"Static": Static, "HPA": HPA, "HPA-45": HPA45, "HPA-fast": HPAFast, "PredHPA": PredHPA, "HPA+RL": HPARL}
    if name in base:
        return base[name](seed)
    if name == "ARC":
        return ARC(seed)
    ab = {"ARC-noGraph": dict(graph=False), "ARC-noChaos": dict(chaos=False), "ARC-noSec": dict(sec=False),
          "ARC-noGuard": dict(guard=False), "ARC-noRemed": dict(remed=False)}
    if name in ab:
        return ARC(seed, label=name, **ab[name])
    raise KeyError(name)


ALL = ["Static", "HPA", "HPA-fast", "HPA-45", "PredHPA", "HPA+RL", "ARC"]
ABLATIONS = ["ARC-noGraph", "ARC-noChaos", "ARC-noSec", "ARC-noGuard", "ARC-noRemed"]
