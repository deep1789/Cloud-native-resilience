"""Generic service replica ("pod") for the container testbed. Stdlib only; runs inside a container.

An idle worker waits in the pool. /admin/assign turns it into a replica of one microservice with a fixed
processing time, a single worker slot (capacity = 1 / service time), a bounded wait queue, and downstream calls
with per-call timeouts and retries. Downstream endpoints, retry caps and circuit breakers are read from a shared
config file (/cfg/config.json) that the orchestrator rewrites every tick.
"""
import asyncio
import json
import os
import random
import time
from urllib.parse import parse_qs

PORT = int(os.environ["PORT"])
CFG = "/cfg/config.json"
CALL_TIMEOUT = float(os.environ.get("CALL_TIMEOUT", "1.0"))


class State:
    def reset(self):
        self.name = None
        self.svc_time = 0.05
        self.qcap = 1.5
        self.slow = 1.0
        self.callees = []            # (name, weight, critical)
        self.sem = asyncio.Semaphore(1)
        self.arrivals = self.served = self.rejected = 0
        self.svc_sum = 0.0
        self.q_len = 0
        self.qint = 0.0                # time integral of queue length (request-seconds)
        self.qt = time.perf_counter()
        self.calls = {}              # callee -> [attempts, ok, f503, f502, fother]


S = State()
S.reset()
CONFIG = {"endpoints": {}, "retries": {}, "breakers": []}
_mtime = [0.0]


async def poll_config():
    while True:
        try:
            m = os.stat(CFG).st_mtime
            if m != _mtime[0]:
                _mtime[0] = m
                with open(CFG) as fh:
                    CONFIG.update(json.load(fh))
        except Exception:
            pass
        await asyncio.sleep(0.1)


async def http_get(port, path, timeout):
    async def go():
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.0\r\n\r\n".encode())
        await w.drain()
        line = await r.readline()
        code = int(line.split()[1]) if line else 0
        await r.read()
        w.close()
        return code
    try:
        return await asyncio.wait_for(go(), timeout)
    except asyncio.TimeoutError:
        return 0
    except Exception:
        return -1


async def call(callee):
    st = S.calls.setdefault(callee, [0, 0, 0, 0, 0])
    retries = int(CONFIG["retries"].get(S.name, 2))
    for _ in range(retries + 1):
        eps = CONFIG["endpoints"].get(callee, [])
        if not eps:
            st[0] += 1
            st[4] += 1
            continue
        st[0] += 1
        code = await http_get(random.choice(eps), "/req", CALL_TIMEOUT)
        if code == 200:
            st[1] += 1
            return True
        if code == 503:
            st[2] += 1
        elif code == 502:
            st[3] += 1
        else:
            st[4] += 1
    return False


def qchange(delta):
    now = time.perf_counter()
    S.qint += S.q_len * (now - S.qt)
    S.qt = now
    S.q_len += delta


async def handle_req():
    if S.name is None:
        return 503
    S.arrivals += 1
    qchange(+1)
    try:
        await asyncio.wait_for(S.sem.acquire(), S.qcap)
    except asyncio.TimeoutError:
        qchange(-1)
        S.rejected += 1
        return 503
    qchange(-1)
    t = S.svc_time * S.slow * random.uniform(0.8, 1.2)
    try:
        await asyncio.sleep(t)
    finally:
        S.sem.release()
    S.svc_sum += t
    S.served += 1
    brk = {tuple(b) for b in CONFIG["breakers"]}
    jobs = []
    for (c, w, crit) in S.callees:
        if (S.name, c) in brk:
            continue
        if random.random() < min(1.0, w):
            jobs.append((crit, asyncio.ensure_future(call(c))))
    ok = True
    for crit, j in jobs:
        res = await j
        if crit and not res:
            ok = False
    return 200 if ok else 502


async def handler(reader, writer):
    try:
        line = await reader.readline()
        if not line:
            return
        parts = line.decode().split()
        path = parts[1] if len(parts) > 1 else "/"
        while True:
            h = await reader.readline()
            if h in (b"\r\n", b"\n", b""):
                break
        route, _, qs = path.partition("?")
        q = {k: v[0] for k, v in parse_qs(qs).items()}
        code, body = 200, b"ok"
        if route == "/req":
            code = await handle_req()
        elif route == "/metrics":
            body = json.dumps(dict(name=S.name, arrivals=S.arrivals, served=S.served, rejected=S.rejected,
                                   svc_sum=S.svc_sum, q_len=S.q_len, qint=(qchange(0) or S.qint), calls=S.calls)).encode()
        elif route == "/admin/assign":
            S.reset()
            S.name = q["name"]
            S.svc_time = float(q["svc_time"])
            S.qcap = float(q.get("qcap", 1.5))
            S.callees = json.loads(q.get("callees", "[]"))
        elif route == "/admin/slow":
            S.slow = float(q["f"])
        elif route == "/admin/reset":
            S.reset()
        writer.write(f"HTTP/1.0 {code} X\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    asyncio.ensure_future(poll_config())
    server = await asyncio.start_server(handler, "0.0.0.0", PORT, backlog=2048)
    async with server:
        await server.serve_forever()


asyncio.run(main())
