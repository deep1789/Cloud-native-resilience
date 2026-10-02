# Container testbed (real software, no Kubernetes)

A small, real-software validation of the simulator's central findings.

* **Real:** every replica is a Docker container running `worker.py` (an asyncio HTTP service with a real request
  queue, per-call timeouts and retries over TCP); pod loss is a real `docker kill`; load is a real open-loop
  generator replaying the real NASA HTTP trace; telemetry is scraped over HTTP; the controller code is the same
  `arcsim.controllers` code used in the simulator.
* **Emulated:** pod start-up delay (a warm-pool container is assigned to a service after the nominal delay), gray
  failure (a slowdown factor set on the replicas), service work (a `sleep`, one slot per replica), the ReplicaSet and
  quota (implemented in `orchestrator.py`), and **time compression** (one 5 s control tick = 5/12 s real) with
  request rates and per-pod capacity divided by 12 so utilisation is unchanged.
* **Not Kubernetes.** No kubelet, scheduler, service mesh, CPU contention modelling or real autoscaler.

Run: `python orchestrator.py --controllers HPA-45,HPA-45-r0,ARC --scenarios kill_cascade,gray,flash_step --seeds 1,2,3`
(needs a Docker daemon and the image `mirror.gcr.io/library/python:3.11-slim`; results are appended to
`results/testbed.jsonl`, summarised by `analyze_testbed.py`).
