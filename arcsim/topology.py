"""Microservice dependency graph modelled on the Online Boutique demo (10 services).

``W[j][c]`` is the expected number of calls a request handled by service ``j``
makes to service ``c``; ``CRIT[j][c]`` is 1 when a failed call to ``c`` fails the
request at ``j`` and 0 when ``j`` degrades gracefully (recommendations, ads, e-mail).
Capacities, latencies and start-up times are round numbers in the range reported
for typical containerised Go/Java services; they are modelling assumptions, not
measurements (see paper, Threats to Validity).
"""
import numpy as np

NAMES = ["frontend", "productcatalog", "recommendation", "ad", "cart",
         "currency", "checkout", "payment", "shipping", "email"]
N = len(NAMES)
IDX = {n: i for i, n in enumerate(NAMES)}

MU = np.array([120, 220, 90, 160, 200, 400, 100, 150, 140, 80], float)   # req/s per pod
BASE_LAT = np.array([20, 10, 15, 8, 8, 5, 25, 30, 20, 12], float) / 1000  # s
STARTUP = np.array([25, 30, 40, 20, 30, 15, 35, 35, 30, 20], float)       # s until a new pod is ready
MIN_REP = np.full(N, 2)
MAX_REP = np.array([40] + [30] * (N - 1))
QUOTA = 120  # cluster-wide pod quota

W = np.zeros((N, N))
CRIT = np.zeros((N, N))


def _edge(a, b, w, crit=1.0):
    W[IDX[a], IDX[b]] = w
    CRIT[IDX[a], IDX[b]] = crit


_edge("frontend", "productcatalog", 1.0)
_edge("frontend", "recommendation", 0.5, 0.0)
_edge("frontend", "ad", 0.6, 0.0)
_edge("frontend", "cart", 0.7)
_edge("frontend", "currency", 0.8)
_edge("frontend", "checkout", 0.1)
_edge("recommendation", "productcatalog", 1.0)
_edge("checkout", "cart", 1.0)
_edge("checkout", "productcatalog", 1.0)
_edge("checkout", "currency", 1.0)
_edge("checkout", "payment", 1.0)
_edge("checkout", "shipping", 1.0)
_edge("checkout", "email", 1.0, 0.0)

# topological order (parents before children)
TOPO = [IDX[n] for n in ["frontend", "recommendation", "ad", "checkout", "productcatalog",
                         "cart", "currency", "payment", "shipping", "email"]]
NONCRIT_EDGES = [(j, c) for j in range(N) for c in range(N) if W[j, c] > 0 and CRIT[j, c] == 0]


def call_multipliers(w=W):
    """Expected calls to each service per frontend request (no retries)."""
    m = np.zeros(N)
    m[0] = 1.0
    for c in TOPO[1:]:
        m[c] = sum(w[j, c] * m[j] for j in range(N))
    return m


M = call_multipliers()
