"""Microservice dependency graphs.

``boutique`` (default): 10 services modelled on the Online Boutique demo.
``deep``: a synthetic 24-service, 6-tier graph (select with ``ARC_TOPO=deep``) used to test whether
graph-aware planning matters when demand reaches leaf services several hops after the frontend.

``W[j][c]`` is the expected number of calls a request handled by service ``j`` makes to ``c``;
``CRIT[j][c]`` is 1 when a failed call to ``c`` fails the request at ``j`` and 0 when ``j`` degrades
gracefully. Capacities, latencies and start-up times are round numbers in the range reported for
typical containerised services; they are modelling assumptions, not measurements.
"""
import os

import numpy as np

TOPOLOGY = os.environ.get("ARC_TOPO", "boutique")


def _boutique():
    names = ["frontend", "productcatalog", "recommendation", "ad", "cart",
             "currency", "checkout", "payment", "shipping", "email"]
    n = len(names)
    idx = {s: i for i, s in enumerate(names)}
    mu = np.array([120, 220, 90, 160, 200, 400, 100, 150, 140, 80], float)    # req/s per pod
    lat = np.array([20, 10, 15, 8, 8, 5, 25, 30, 20, 12], float) / 1000        # s
    startup = np.array([25, 30, 40, 20, 30, 15, 35, 35, 30, 20], float)        # s until a pod is ready
    w = np.zeros((n, n))
    crit = np.zeros((n, n))

    def edge(a, b, wt, c=1.0):
        w[idx[a], idx[b]] = wt
        crit[idx[a], idx[b]] = c

    edge("frontend", "productcatalog", 1.0)
    edge("frontend", "recommendation", 0.5, 0.0)
    edge("frontend", "ad", 0.6, 0.0)
    edge("frontend", "cart", 0.7)
    edge("frontend", "currency", 0.8)
    edge("frontend", "checkout", 0.1)
    edge("recommendation", "productcatalog", 1.0)
    edge("checkout", "cart", 1.0)
    edge("checkout", "productcatalog", 1.0)
    edge("checkout", "currency", 1.0)
    edge("checkout", "payment", 1.0)
    edge("checkout", "shipping", 1.0)
    edge("checkout", "email", 1.0, 0.0)
    topo = [idx[s] for s in ["frontend", "recommendation", "ad", "checkout", "productcatalog",
                             "cart", "currency", "payment", "shipping", "email"]]
    pools = dict(
        kill=[idx["productcatalog"], idx["cart"], idx["currency"]],
        gray=[idx["checkout"], idx["productcatalog"], idx["cart"]],
        ddos_kill=[idx["productcatalog"], idx["cart"]],
        poison=[idx["productcatalog"], idx["cart"], idx["currency"], idx["recommendation"]],
        storm=[idx["productcatalog"], idx["cart"], idx["checkout"], idx["payment"]],
    )
    return dict(names=names, mu=mu, lat=lat, startup=startup, w=w, crit=crit, topo=topo, pools=pools,
                core=[0, 1, 4, 5], quota=120, max_rep=np.array([40] + [30] * (n - 1)))


def _deep():
    rng = np.random.default_rng(7)               # fixed: the graph is part of the experimental design
    tiers = [1, 3, 4, 5, 5, 6]
    names, tier_of = [], []
    for t, k in enumerate(tiers):
        for i in range(k):
            names.append("frontend" if t == 0 else f"s{t}_{i}")
            tier_of.append(t)
    n = len(names)
    first = np.cumsum([0] + tiers)
    members = [list(range(first[t], first[t + 1])) for t in range(len(tiers))]
    w = np.zeros((n, n))
    crit = np.zeros((n, n))
    for t in range(len(tiers) - 1):
        nxt = members[t + 1]
        has_parent = set()
        for j in members[t]:
            k = min(len(nxt), int(rng.integers(1, 3)) + (1 if t == 0 else 0))
            for c in rng.choice(nxt, k, replace=False):
                w[j, c] = float(rng.choice([0.6, 0.8, 1.0]))
                crit[j, c] = 1.0
                has_parent.add(int(c))
            if rng.random() < 0.5:                # one graceful-degradation (non-critical) callee
                far = members[t + 1] + (members[t + 2] if t + 2 < len(tiers) else [])
                c = int(rng.choice(far))
                if w[j, c] == 0:
                    w[j, c] = 0.5
                    crit[j, c] = 0.0
                    has_parent.add(c)
        for c in nxt:                              # every service is reachable from the frontend
            if c not in has_parent:
                j = int(rng.choice(members[t]))
                w[j, c] = 0.8
                crit[j, c] = 1.0
    topo = list(range(n))                          # tier order is a valid topological order
    m = np.zeros(n)
    m[0] = 1.0
    for c in topo[1:]:
        m[c] = sum(w[j, c] * m[j] for j in range(n))
    # per-pod capacity chosen so each service needs 3-8 pods at the 600 req/s design peak (60% target)
    mu = np.ceil(m * 600 / (rng.integers(3, 9, n) * 0.6))
    lat = rng.uniform(5, 30, n) / 1000
    startup = rng.choice([15, 20, 25, 30, 35, 40], n).astype(float)
    deep_by_mult = sorted([i for i in range(n) if tier_of[i] in (2, 3, 4)], key=lambda i: -m[i])
    mid = [i for i in range(n) if tier_of[i] in (2, 3, 4)]
    low = [i for i in range(n) if tier_of[i] in (3, 4, 5)]
    pools = dict(kill=deep_by_mult[:3], gray=[int(x) for x in rng.choice(low, 3, replace=False)],
                 ddos_kill=deep_by_mult[:2], poison=[int(x) for x in rng.choice(mid, 4, replace=False)],
                 storm=[int(x) for x in rng.choice(mid, 4, replace=False)])
    init = np.maximum(2, np.ceil(m * 330 / (mu * 0.6)))      # initial sizing at ~330 req/s
    return dict(names=names, mu=mu, lat=lat, startup=startup, w=w, crit=crit, topo=topo, pools=pools,
                core=[0] + members[1], quota=int(np.ceil(3.5 * init.sum())),
                max_rep=np.array([40] + [30] * (n - 1)))


_G = _deep() if TOPOLOGY == "deep" else _boutique()
NAMES = _G["names"]
N = len(NAMES)
IDX = {s: i for i, s in enumerate(NAMES)}
MU = _G["mu"]
BASE_LAT = _G["lat"]
STARTUP = _G["startup"]
MIN_REP = np.full(N, 2)
MAX_REP = _G["max_rep"]
QUOTA = _G["quota"]
W = _G["w"]
CRIT = _G["crit"]
TOPO = _G["topo"]
POOLS = _G["pools"]           # fault-target pools used by scenarios.py
CORE = _G["core"]             # services whose errors indicate user-visible overload
NONCRIT_EDGES = [(j, c) for j in range(N) for c in range(N) if W[j, c] > 0 and CRIT[j, c] == 0]


def call_multipliers(w=W):
    """Expected calls to each service per frontend request (no retries)."""
    m = np.zeros(N)
    m[0] = 1.0
    for c in TOPO[1:]:
        m[c] = sum(w[j, c] * m[j] for j in range(N))
    return m


M = call_multipliers()
