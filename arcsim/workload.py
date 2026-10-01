"""Real-trace workload: NASA Kennedy Space Center HTTP log (July 1995).

The raw log is converted once into a per-5-second request-count series and cached.
Episodes are 2-hour windows cut from that series, rescaled so that the window's
95th-percentile rate equals ``p95_rps`` (the trace only fixes the *shape* of the
load: diurnal level, burstiness, short-term variability).
"""
import gzip
import os
import re
from datetime import datetime, timezone, timedelta

import numpy as np

DT = 5.0  # seconds per simulation tick
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
RAW = os.path.join(DATA, "NASA_access_log_Jul95.gz")
CACHE = os.path.join(DATA, "nasa_5s.npy")

_TS = re.compile(r"\[(\d\d)/Jul/1995:(\d\d):(\d\d):(\d\d)")

# (day-of-July, start hour) of the 2-hour evaluation windows: weekday and weekend
# daytime periods that avoid the Jul 28 - Aug 3 server outage in the original log.
WINDOWS = [(5, 10), (11, 13), (13, 9), (18, 14), (20, 11), (25, 15)]


def load_counts():
    """Per-5s request counts for July 1995 (index 0 = 1 Jul 00:00 local)."""
    if os.path.exists(CACHE):
        return np.load(CACHE)
    if not os.path.exists(RAW):
        raise FileNotFoundError("run data/download.sh first")
    n = 31 * 24 * 3600 // int(DT)
    counts = np.zeros(n, dtype=np.float32)
    with gzip.open(RAW, "rt", errors="ignore") as fh:
        for line in fh:
            m = _TS.search(line)
            if not m:
                continue
            d, h, mi, s = map(int, m.groups())
            sec = ((d - 1) * 24 + h) * 3600 + mi * 60 + s
            idx = int(sec // DT)
            if 0 <= idx < n:
                counts[idx] += 1
    np.save(CACHE, counts)
    return counts


def window(win_id, p95_rps=600.0, minutes=120):
    """Request-rate series (req/s per tick) for one evaluation window."""
    counts = load_counts()
    day, hour = WINDOWS[win_id % len(WINDOWS)]
    start = int(((day - 1) * 24 + hour) * 3600 // DT)
    n = int(minutes * 60 // DT)
    rate = counts[start:start + n].astype(float) / DT
    # light smoothing: the raw 5 s counts are very spiky for a ~10 rps service
    k = np.ones(3) / 3.0
    rate = np.convolve(rate, k, mode="same")
    scale = p95_rps / max(np.percentile(rate, 95), 1e-9)
    return rate * scale


def summary():
    c = load_counts()
    r = c / DT
    return {
        "ticks": int(len(c)),
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
