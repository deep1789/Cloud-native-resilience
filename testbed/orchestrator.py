#!/usr/bin/env python3
"""Container testbed: real Docker containers as pods, real queuing / timeouts / retries, real trace replay.

What is real: every replica is a Docker container running worker.py with a real request queue, real per-call
timeouts and retries over TCP, real process kills (`docker kill`) and a real open-loop load generator replaying the
NASA HTTP trace. What is emulated: (i) pod start-up delay (a container from a warm pool is assigned to a service after
the nominal start-up delay), (ii) gray failure (a slowdown factor set on the replicas), (iii) service work is a
`sleep`, so capacity is bounded by a single worker slot per replica, (iv) no Kubernetes: the ReplicaSet and the quota
are implemented here. The *same controller code* as in the simulator (arcsim.controllers) drives it.

Scaling: request rates are divided by K (ingress p95 = 600/K req/s) and per-pod capacity by K, so utilisation is
unchanged; controller / pod-lifecycle time is compressed by CT (one 5 s control tick = 5/CT real seconds).
"""
import argparse
import asyncio
import json
import math
import os
import random
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from arcsim import controllers as C          # noqa: E402
from arcsim import topology as T             # noqa: E402
from arcsim import workload as W             # noqa: E402
from arcsim.sim import Actions, Fault, Obs, DT, AVAIL_SLO   # noqa: E402

K = 5.0                  # rate / capacity scale-down
CT = 8.0                 # time compression of control + pod lifecycle
TICK = DT / CT           # real seconds per control tick
SLO_LAT = 1.0            # s, end-to-end latency objective (real time)
CLIENT_TO = 2.0          # s, user timeout
QCAP = 1.5               # s, max queue wait before a replica rejects (503)
WARM = 60                # ticks excluded from metrics
IMAGE = "mirror.gcr.io/library/python:3.11-slim"
POOL = T.QUOTA + 20
BASE_PORT = 21000
N = T.N
SVC_TIME = 1.0 / (T.MU / K)          # seconds of processing per request (single slot)
CFG_PATH = os.path.join(HERE, "cfg", "config.json")


async def sh(*args):
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    return p.returncode, out.decode(), err.decode()


async def http_get(port, path, timeout):
    async def go():
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.0\r\n\r\n".encode())
        await w.drain()
        line = await r.readline()
        code = int(line.split()[1]) if line else 0
        raw = await r.read()
        w.close()
        return code, raw.split(b"\r\n\r\n", 1)[-1]
    try:
        return await asyncio.wait_for(go(), timeout)
    except asyncio.TimeoutError:
        return 0, b""
    except Exception:
        return -1, b""


class Rep:
    __slots__ = ("idx", "port", "svc", "state", "ready_tick", "prev")

    def __init__(self, idx):
        self.idx, self.port, self.svc, self.state, self.ready_tick, self.prev = idx, BASE_PORT + idx, None, "idle", 0, None


async def ensure_pool():
    rc, out, _ = await sh("docker", "ps", "-a", "--filter", "name=tb-w", "--format", "{{.Names}} {{.State}}")
    have = {l.split()[0]: l.split()[1] for l in out.strip().splitlines() if l.strip()}
    sem = asyncio.Semaphore(8)

    async def make(i):
        async with sem:
            name = f"tb-w{i}"
            if name in have:
                if have[name] != "running":
                    await sh("docker", "start", name)
                return
            await sh("docker", "run", "-d", "--network", "host", "--name", name, "--cpus", "0.5", "--memory", "96m",
                     "-e", f"PORT={BASE_PORT + i}", "-v", f"{HERE}:/app:ro", "-v", f"{os.path.join(HERE, 'cfg')}:/cfg:ro",
                     IMAGE, "python", "-u", "/app/worker.py")
    await asyncio.gather(*[make(i) for i in range(POOL)])
    for _ in range(100):
        res = await asyncio.gather(*[http_get(BASE_PORT + i, "/health", 1.0) for i in range(POOL)])
        if all(c == 200 for c, _ in res):
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("pool did not come up")


def write_config(endpoints, retries, breakers):
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(dict(endpoints=endpoints, retries=retries, breakers=breakers), fh)
    os.replace(tmp, CFG_PATH)


def make_faults(name, rng):
    t0 = int(rng.integers(200, 260))
    if name == "baseline":
        return []
    if name == "flash_step":
        return [Fault("flash", t0, 180, mag=3.0, ramp=1)]
    if name == "kill_cascade":
        return [Fault("kill", t0, svc=int(rng.choice(T.POOLS["kill"])), mag=0.7)]
    if name == "gray":
        return [Fault("gray", t0, 180, svc=int(rng.choice(T.POOLS["gray"])), mag=0.3)]
    raise KeyError(name)


class Episode:
    def __init__(self, ctrl_name, scenario, seed, minutes=40):
        self.ctrl_name, self.scenario, self.seed = ctrl_name, scenario, seed
        rng = np.random.default_rng(50_000 + seed)
        base = W.window(seed % len(W.WINDOWS), minutes=minutes)
        self.load = base * np.exp(rng.normal(0, 0.03, len(base)))
        self.n_ticks = len(self.load)
        self.faults = make_faults(scenario, rng)
        self.rng = random.Random(seed)
        self.ctrl = C.make(ctrl_name, seed)
        self.reps = [Rep(i) for i in range(POOL)]
        self.idle = list(self.reps)
        self.svc = [[] for _ in range(N)]
        self.desired = np.zeros(N)
        self.retries = np.full(N, 2.0)
        self.breakers = []
        self.shed = 0.0
        self.health = np.ones(N)
        self.restore_at = {}
        self.events, self.chaos_events = [], []
        self.arr = np.zeros(self.n_ticks)
        self.good = np.zeros(self.n_ticks)
        self.shedn = np.zeros(self.n_ticks)
        self.lat = []
        self.pods = np.zeros(self.n_ticks)
        self.fail_hist = {}
        self.att_s = np.zeros(N)
        self.bad_s = np.zeros(N)
        self.prev_t = None
        self.tasks = set()

    # ------------------------------------------------------------- pod lifecycle
    def ready_reps(self, i):
        return [r for r in self.svc[i] if r.state == "ready"]

    def starting_reps(self, i):
        return [r for r in self.svc[i] if r.state == "starting"]

    async def assign(self, rep, i):
        callees = [[T.NAMES[c], float(T.W[i, c]), bool(T.CRIT[i, c] > 0)] for c in range(N) if T.W[i, c] > 0]
        q = f"/admin/assign?name={T.NAMES[i]}&svc_time={SVC_TIME[i]:.5f}&qcap={QCAP}&callees={json.dumps(callees)}".replace(" ", "")
        await http_get(rep.port, q, 2.0)
        if self.health[i] < 1.0:
            await http_get(rep.port, f"/admin/slow?f={1.0 / self.health[i]:.3f}", 2.0)
        rep.prev = None
        rep.state = "ready"

    async def release(self, rep):
        await http_get(rep.port, "/admin/reset", 2.0)
        rep.state, rep.svc, rep.prev = "idle", None, None
        self.idle.append(rep)

    async def kill(self, rep):
        i = rep.svc
        if rep in self.svc[i]:
            self.svc[i].remove(rep)
        rep.state, rep.svc = "dead", None

        async def revive():
            await sh("docker", "kill", f"tb-w{rep.idx}")
            await asyncio.sleep(0.5)
            await sh("docker", "start", f"tb-w{rep.idx}")
            for _ in range(40):
                c, _ = await http_get(rep.port, "/health", 0.5)
                if c == 200:
                    break
                await asyncio.sleep(0.25)
            rep.state = "idle"
            self.idle.append(rep)
        t = asyncio.ensure_future(revive())
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def init_pods(self):
        l0 = float(self.load[:12].mean())
        init = np.maximum(T.MIN_REP, np.ceil(T.M * l0 / (T.MU * 0.6))).astype(int)
        if init.sum() > T.QUOTA:
            init = np.maximum(T.MIN_REP, np.floor(init * T.QUOTA / init.sum())).astype(int)
        self.desired = init.astype(float)
        jobs = []
        for i in range(N):
            for _ in range(int(init[i])):
                rep = self.idle.pop()
                rep.svc, rep.state = i, "starting"
                self.svc[i].append(rep)
                jobs.append(self.assign(rep, i))
        await asyncio.gather(*jobs)

    def total(self):
        return sum(len(self.ready_reps(i)) + len(self.starting_reps(i)) for i in range(N))

    async def reconcile(self, tick):
        # starting -> ready
        jobs = []
        for i in range(N):
            for r in self.starting_reps(i):
                if r.ready_tick <= tick and r.state == "starting":
                    r.state = "assigning"
                    jobs.append(self.assign(r, i))
        if jobs:
            await asyncio.gather(*jobs)
        # scale down / cancel surplus
        for i in range(N):
            want = int(self.desired[i])
            ready, starting = self.ready_reps(i), self.starting_reps(i)
            excess = len(ready) + len(starting) - want
            if excess > 0:
                for r in starting[:excess]:
                    self.svc[i].remove(r)
                    r.state, r.svc = "idle", None
                    self.idle.append(r)
                excess -= min(excess, len(starting))
                for r in ready[:max(0, min(excess, len(ready) - 1))]:
                    self.svc[i].remove(r)
                    r.state = "releasing"
                    asyncio.ensure_future(self.release(r))
        # scale up, one pod at a time to the most under-provisioned service (fair), within quota
        total = self.total()
        while total < T.QUOTA and self.idle:
            best, ratio = -1, 2.0
            for i in range(N):
                want = int(self.desired[i])
                have = len(self.ready_reps(i)) + len(self.starting_reps(i))
                if have < want and have / max(want, 1) < ratio:
                    best, ratio = i, have / max(want, 1)
            if best < 0:
                break
            rep = self.idle.pop()
            rep.svc, rep.state = best, "starting"
            rep.ready_tick = tick + max(1, int(round(T.STARTUP[best] * self.rng.uniform(0.8, 1.3) / DT)))
            self.svc[best].append(rep)
            total += 1

    def publish(self):
        eps = {T.NAMES[i]: [r.port for r in self.ready_reps(i)] for i in range(N)}
        rt = {T.NAMES[i]: int(self.retries[i]) for i in range(N)}
        brk = [[T.NAMES[j], T.NAMES[c]] for (j, c) in self.breakers]
        write_config(eps, rt, brk)

    # ----------------------------------------------------------------- faults
    async def apply_faults(self, tick):
        for f in self.faults:
            if f.t0 == tick:
                if f.kind == "kill":
                    ready = self.ready_reps(f.svc)
                    k = max(0, min(int(math.ceil(f.mag * len(ready))), len(ready)))
                    for r in self.rng.sample(ready, k):
                        await self.kill(r)
                elif f.kind == "gray":
                    self.health[f.svc] = f.mag
                    for r in self.ready_reps(f.svc):
                        await http_get(r.port, f"/admin/slow?f={1.0 / f.mag:.3f}", 2.0)
                if f.kind != "flash":
                    self.events.append((tick, f.kind, f.svc))
                else:
                    self.events.append((tick, f.kind, -1))
            if f.kind == "gray" and tick == f.t0 + f.dur and f.svc not in self.restore_at:
                await self.set_health(f.svc, 1.0)
        for s, tr in list(self.restore_at.items()):
            if tick >= tr:
                await self.set_health(s, 1.0)
                del self.restore_at[s]

    async def set_health(self, s, h):
        self.health[s] = h
        for r in self.ready_reps(s):
            await http_get(r.port, f"/admin/slow?f={1.0 / h:.3f}", 2.0)

    def flash_mult(self, tick):
        m = 1.0
        for f in self.faults:
            if f.kind == "flash" and f.t0 <= tick < f.t0 + f.dur:
                up = min(1.0, (tick - f.t0 + 1) / max(f.ramp, 1))
                down = min(1.0, (f.t0 + f.dur - tick) / max(f.ramp, 1))
                m *= 1.0 + (f.mag - 1.0) * min(up, down)
        return m

    # ----------------------------------------------------------------- load
    async def one_request(self, tick):
        eps = [r.port for r in self.ready_reps(0)]
        if not eps:
            self.fail_hist[("fe", "att")] = self.fail_hist.get(("fe", "att"), 0) + 1
            self.fail_hist[("fe", "bad")] = self.fail_hist.get(("fe", "bad"), 0) + 1
            return
        t0 = time.perf_counter()
        code, _ = await http_get(self.rng.choice(eps), "/req", CLIENT_TO)
        lat = time.perf_counter() - t0
        self.fail_hist[("fe", "att")] = self.fail_hist.get(("fe", "att"), 0) + 1
        if code == 200 and lat <= SLO_LAT:
            self.good[tick] += 1
        if code != 200:
            self.fail_hist[("fe", "bad")] = self.fail_hist.get(("fe", "bad"), 0) + 1
        if code == 200:
            self.lat.append(lat)

    async def spawn_load(self, tick):
        rate = self.load[tick] / K * self.flash_mult(tick)          # real req/s
        n = np.random.default_rng(self.seed * 100003 + tick).poisson(rate * TICK)
        self.arr[tick] = n
        offs = sorted(self.rng.random() * TICK for _ in range(n))

        async def fire(off):
            await asyncio.sleep(off)
            if self.rng.random() < self.shed:
                self.shedn[tick] += 1
                return
            await self.one_request(tick)
        for off in offs:
            t = asyncio.ensure_future(fire(off))
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)

    # -------------------------------------------------------------- telemetry
    async def scrape(self, tick, elapsed):
        reps = [r for i in range(N) for r in self.ready_reps(i)]
        res = await asyncio.gather(*[http_get(r.port, "/metrics", 1.0) for r in reps])
        agg = [dict(arr=0.0, srv=0.0, busy=0.0, q=0.0, att=0.0, bad=0.0, n=0) for _ in range(N)]
        for r, (code, body) in zip(reps, res):
            if code != 200:
                continue
            m = json.loads(body)
            prev = r.prev or dict(arrivals=0, served=0, svc_sum=0.0, qint=0.0)
            a = agg[r.svc]
            a["arr"] += m["arrivals"] - prev["arrivals"]
            a["srv"] += m["served"] - prev["served"]
            a["busy"] += m["svc_sum"] - prev["svc_sum"]
            a["q"] += m["qint"] - prev["qint"]
            a["n"] += 1
            r.prev = dict(arrivals=m["arrivals"], served=m["served"], svc_sum=m["svc_sum"], qint=m["qint"], calls=m["calls"])
            # callee-side failure statistics reported by this caller, attributed to the callee
            pc = prev.get("calls", {}) if isinstance(prev, dict) else {}
            for callee, st in m["calls"].items():
                ci = T.IDX[callee]
                p = pc.get(callee, [0, 0, 0, 0, 0])
                agg[ci]["att"] += st[0] - p[0]
                agg[ci]["bad"] += (st[2] - p[2]) + (st[4] - p[4])
        # frontend failures as seen by the load generator
        fa, fb = self.fail_hist.pop(("fe", "att"), 0), self.fail_hist.pop(("fe", "bad"), 0)
        agg[0]["att"] += fa
        agg[0]["bad"] += fb
        ready = np.array([a["n"] for a in agg], float)
        starting = np.array([len(self.starting_reps(i)) + sum(1 for r in self.svc[i] if r.state == "assigning") for i in range(N)], float)
        util = np.zeros(N)
        queue = np.zeros(N)
        err = np.zeros(N)
        for i, a in enumerate(agg):
            if a["n"] == 0:
                util[i], queue[i] = 1.5, 1.0
                err[i] = 1.0
                continue
            busy = a["busy"] / max(elapsed * a["n"], 1e-9)
            qpr = a["q"] / max(elapsed * a["n"], 1e-9)         # time-averaged queue length per replica
            util[i] = min(1.5, busy + 0.5 * qpr)
            queue[i] = min(1.0, qpr / max(QCAP / SVC_TIME[i], 1.0))
            self.att_s[i] = 0.5 * self.att_s[i] + a["att"]            # light smoothing of counts: few calls per tick
            self.bad_s[i] = 0.5 * self.bad_s[i] + a["bad"]
            err[i] = self.bad_s[i] / self.att_s[i] if self.att_s[i] > 0 else 0.0
        served = np.array([a["srv"] for a in agg]) * K / max(elapsed, 1e-9)
        offered = np.array([a["arr"] for a in agg]) * K / max(elapsed, 1e-9)
        lag = tick - 3
        avail = float(self.good[lag] / self.arr[lag]) if lag >= 0 and self.arr[lag] > 0 else 1.0
        ing = self.arr[tick] / TICK * K
        return Obs(t=tick, ingress=float(ing), suspect=float(np.clip(0.05 + self.rng.gauss(0, 0.03), 0, 1)),
                   ready=ready, starting=starting, desired=self.desired.copy(), util=util, queue=queue, err=np.clip(err, 0, 1),
                   served=served, offered=offered, avail=avail, total_pods=int(ready.sum() + starting.sum()))

    async def apply_actions(self, a: Actions, tick):
        if a.desired is not None:
            self.desired = np.clip(np.round(a.desired), T.MIN_REP, T.MAX_REP)
        if a.retries is not None:
            self.retries = np.asarray(a.retries, float)
        self.breakers = sorted(a.breaker)
        self.shed = float(np.clip(a.shed_legit, 0, 0.95))
        for s in a.remediate:
            if s not in self.restore_at and self.health[s] < 1.0:
                self.restore_at[s] = tick + int(math.ceil(T.STARTUP[s] / DT))
        if a.chaos_kill is not None:
            svc, k = a.chaos_kill
            ready = self.ready_reps(svc)
            kk = max(0, min(k, len(ready) - 1))
            for r in self.rng.sample(ready, kk):
                await self.kill(r)
            if kk:
                self.chaos_events.append((tick, svc))

    # -------------------------------------------------------------------- run
    async def run(self):
        await asyncio.gather(*[http_get(r.port, "/admin/reset", 2.0) for r in self.reps])
        await self.init_pods()
        self.publish()
        await asyncio.sleep(0.5)
        t_begin = time.perf_counter() + 0.2
        last = t_begin
        obs = None
        for tick in range(self.n_ticks):
            target = t_begin + tick * TICK
            now = time.perf_counter()
            if target > now:
                await asyncio.sleep(target - now)
            await self.apply_faults(tick)
            await self.reconcile(tick)
            self.publish()
            await self.spawn_load(tick)
            self.pods[tick] = self.total()
            # control step at the end of the interval
            end = t_begin + (tick + 1) * TICK - 0.03
            now = time.perf_counter()
            if end > now:
                await asyncio.sleep(end - now)
            now = time.perf_counter()
            obs = await self.scrape(tick, max(now - last, 1e-3))
            last = now
            act = self.ctrl.step(obs)
            await self.apply_actions(act, tick)
        await asyncio.sleep(CLIENT_TO + 0.5)         # let in-flight requests finish
        for t in list(self.tasks):
            t.cancel()
        return self.metrics()

    def metrics(self):
        a, g, p = self.arr[WARM:], self.good[WARM:], self.pods[WARM:]
        avail = np.where(a > 0, g / np.maximum(a, 1), 1.0)
        viol = avail < AVAIL_SLO
        out = dict(controller=self.ctrl_name, scenario=self.scenario, seed=self.seed,
                   slo_viol_s=float(viol.sum() * DT), loss_pct=float(100 * (1 - g.sum() / max(a.sum(), 1))),
                   shed_pct=float(100 * self.shedn[WARM:].sum() / max(a.sum(), 1)),
                   replica_hours=float(p.sum() * DT / 3600), p95_lat_s=float(np.percentile(self.lat, 95)) if self.lat else float("nan"),
                   min_avail=float(avail.min()), n_chaos=len(self.chaos_events), requests=int(a.sum()))
        mt = []
        for (t0, kind, svc) in self.events:
            t0 = max(t0 - WARM, 0)
            seg = avail[t0:t0 + 240]
            bad = np.where(seg < AVAIL_SLO)[0]
            if len(bad) == 0:
                mt.append(0.0)
                continue
            rec = None
            for k in range(bad[0], len(seg) - 2):
                if (seg[k:k + 3] >= AVAIL_SLO).all():
                    rec = k
                    break
            mt.append(float(((rec if rec is not None else len(seg)) - bad[0]) * DT))
        out["mttr_s"] = float(np.mean(mt)) if mt else 0.0
        out["_avail"] = [round(float(x), 3) for x in avail]
        out["_pods"] = [int(x) for x in p]
        return out


async def amain(args):
    os.makedirs(os.path.join(HERE, "cfg"), exist_ok=True)
    write_config({}, {}, [])
    await ensure_pool()
    outdir = os.path.join(HERE, "..", "results")
    os.makedirs(outdir, exist_ok=True)
    done = set()
    path = os.path.join(outdir, args.out)
    if os.path.exists(path):
        for line in open(path):
            d = json.loads(line)
            done.add((d["controller"], d["scenario"], d["seed"]))
    for seed in args.seeds:
        for scen in args.scenarios:
            for ctrl in args.controllers:
                if (ctrl, scen, seed) in done:
                    continue
                t0 = time.time()
                ep = Episode(ctrl, scen, seed, minutes=args.minutes)
                m = await ep.run()
                # bring killed containers back before the next run
                for _ in range(60):
                    if all(r.state in ("idle", "releasing") or r.svc is not None for r in ep.reps):
                        break
                    await asyncio.sleep(0.25)
                m["wall_s"] = round(time.time() - t0, 1)
                with open(path, "a") as fh:
                    fh.write(json.dumps(m) + "\n")
                print(f"{ctrl:12s} {scen:13s} seed {seed}: viol {m['slo_viol_s']:6.0f}s loss {m['loss_pct']:5.1f}% "
                      f"cost {m['replica_hours']:5.1f} mttr {m['mttr_s']:5.0f}s ({m['wall_s']}s)", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--controllers", default="HPA-45,HPA-45-r0,ARC")
    ap.add_argument("--scenarios", default="kill_cascade,gray,flash_step")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--minutes", type=int, default=40)
    ap.add_argument("--out", default="testbed.jsonl")
    a = ap.parse_args()
    a.controllers, a.scenarios = a.controllers.split(","), a.scenarios.split(",")
    a.seeds = [int(x) for x in a.seeds.split(",")]
    asyncio.run(amain(a))
