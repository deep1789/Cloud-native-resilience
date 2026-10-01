import os, sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
os.makedirs(RES, exist_ok=True)


def pmap(fn, jobs, procs=4):
    with Pool(procs) as p:
        return p.map(fn, jobs, chunksize=4)
