"""Real-trace workloads (Internet Traffic Archive HTTP logs, 1995).

  nasa      NASA Kennedy Space Center WWW server, July 1995 (1,891,714 requests)  [default]
  clarknet  ClarkNet commercial ISP WWW server, 28 Aug - 3 Sep 1995 (1,654,882 requests)

Select with the ``ARC_TRACE`` environment variable. The raw log is converted once into a
per-5-second request-count series and cached. Episodes are 2-hour windows cut from that series,
rescaled so that the window's 95th-percentile rate equals ``p95_rps``: the trace fixes only the
*shape* of the load (daily level, burstiness, short-term variability), not its amplitude.
"""
import gzip
import os
import re
from datetime import datetime

import numpy as np

DT = 5.0  # seconds per simulation tick
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
TRACE = os.environ.get("ARC_TRACE", "nasa")

_MON = {"Jul": 7, "Aug": 8, "Sep": 9}
_TS = re.compile(r"\[(\d\d)/(Jul|Aug|Sep)/1995:(\d\d):(\d\d):(\d\d)")

TRACES = {
    "nasa": dict(file="NASA_access_log_Jul95.gz", origin=datetime(1995, 7, 1), days=31,
                 # (days since origin, start hour): weekday and weekend daytime windows that avoid
                 # the Jul 28 - Aug 3 outage in the original log
                 windows=[(4, 10), (10, 13), (12, 9), (17, 14), (19, 11), (24, 15)]),
    "clarknet": dict(file="clarknet_access_log_Aug28.gz", origin=datetime(1995, 8, 28), days=7,
                     windows=[(1, 10), (2, 13), (3, 9), (4, 14), (5, 15), (6, 11)]),
}
WINDOWS = TRACES[TRACE]["windows"]
RAW = os.path.join(DATA, TRACES[TRACE]["file"])
CACHE = os.path.join(DATA, f"{TRACE}_5s.npy")


def load_counts():
    """Per-5s request counts (index 0 = trace origin, local server time)."""
    if os.path.exists(CACHE):
        return np.load(CACHE)
    if not os.path.exists(RAW):
        raise FileNotFoundError("run data/download.sh first")
    cfg = TRACES[TRACE]
    n = cfg["days"] * 24 * 3600 // int(DT)
    counts = np.zeros(n, dtype=np.float32)
    o = cfg["origin"]
    base = {}
    with gzip.open(RAW, "rt", errors="ignore") as fh:
        for line in fh:
            m = _TS.search(line)
            if not m:
                continue
            d, mon, h, mi, s = m.groups()
            key = (int(d), mon)
            if key not in base:
                base[key] = (datetime(1995, _MON[mon], int(d)) - o).days * 86400
            idx = int((base[key] + int(h) * 3600 + int(mi) * 60 + int(s)) // DT)
            if 0 <= idx < n:
                counts[idx] += 1
    np.save(CACHE, counts)
    return counts


def window(win_id, p95_rps=600.0, minutes=120):
    """Request-rate series (req/s per tick) for one evaluation window."""
    counts = load_counts()
    day, hour = WINDOWS[win_id % len(WINDOWS)]
    start = int((day * 24 + hour) * 3600 // DT)
    n = int(minutes * 60 // DT)
    rate = counts[start:start + n].astype(float) / DT
    # light smoothing: the raw 5 s counts are very spiky for a ~10 rps service
    rate = np.convolve(rate, np.ones(3) / 3.0, mode="same")
    scale = p95_rps / max(np.percentile(rate, 95), 1e-9)
    return rate * scale


def summary():
    c = load_counts()
    r = c / DT
    return {
        "trace": TRACE,
        "requests": int(c.sum()),
        "mean_rps": float(r.mean()),
        "p99_rps": float(np.percentile(r, 99)),
        "burstiness_p99_over_mean": float(np.percentile(r, 99) / r.mean()),
    }


if __name__ == "__main__":
    print(summary())
    for i in range(len(WINDOWS)):
        w = window(i)
        print(i, WINDOWS[i], "mean %.0f p95 %.0f max %.0f" % (w.mean(), np.percentile(w, 95), w.max()))
