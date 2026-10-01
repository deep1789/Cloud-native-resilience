"""Main comparison: 10 scenarios x 6 controllers + 5 ablations x N seeds."""
import os, sys, time
import pandas as pd
from common import pmap, RES
from arcsim import controllers as C, scenarios as S, run

SEEDS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
CTRL = sys.argv[3].split(",") if len(sys.argv) > 3 else C.ALL + C.ABLATIONS
TAG = os.environ.get("ARC_TAG", ("_" + os.environ["ARC_TRACE"] if os.environ.get("ARC_TRACE", "nasa") != "nasa" else "")
                     + ("_" + os.environ["ARC_TOPO"] if os.environ.get("ARC_TOPO", "boutique") != "boutique" else ""))
S0 = int(sys.argv[2]) if len(sys.argv) > 2 else 100   # evaluation seeds start at 100 (development used 0-19)


def job(a):
    return run.run_episode(*a)


if __name__ == "__main__":
    t0 = time.time()
    jobs = [(c, sc, s) for sc in S.SCENARIOS for c in CTRL for s in range(S0, S0 + SEEDS)]
    rows = pmap(job, jobs)
    df = pd.DataFrame(rows)
    df.to_csv(f"{RES}/main{TAG}.csv", index=False)
    print(len(df), "runs in %.0fs" % (time.time() - t0))
