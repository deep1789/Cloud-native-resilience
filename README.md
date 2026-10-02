# Cloud-native resilience: chaos-in-the-loop autonomic control (ARC) — and what it buys beyond retry limits

Code, experiments and paper draft for a submission to the special issue on **resilience-by-design for
cloud-native systems** (stress-oriented evaluation of self-healing, AIOps, chaos engineering and
security-aware orchestration).

* **Paper draft:** [`paper/paper.md`](paper/paper.md) (PDF: [`paper/paper.pdf`](paper/paper.pdf))
* **All numbers:** [`results/tables.md`](results/tables.md) · **figures:** [`figures/`](figures/)

## What was built

**ARC** (Antifragile Resilience Controller) closes the loop between chaos engineering and autoscaling:
(1) dependency-graph capacity planning, (2) chaos probes that ARC runs on itself, scheduled by a bandit,
whose stress margin sets per-service redundancy headroom, (3) attack-vs-flash-crowd discrimination and a
flow-conservation check for poisoned telemetry, (4) circuit breakers, retry budgets and gray-failure
remediation, (5) a safe-adaptation guardrail. It is evaluated in a trace-driven simulator against
Kubernetes-style autoscalers and stronger baselines.

## Headline results (and honest caveats)

| Finding | Evidence |
|---|---|
| Against default-retry (cap 2) Kubernetes-style autoscalers ARC cuts SLO-violation time ~10x at equal cost (13.1 s vs 127.8 s, NASA trace; same pattern on ClarkNet) | paper §6.1, §6.5 |
| **But a retry-limited baseline closes most of that gap.** Excluding the poisoning scenario an HPA with retries disabled matches ARC at equal cost (12.0 s vs 13.4 s); PredHPA with 1 retry beats ARC on SLO at +22% cost; on a 24-service graph HPA-45 with retries disabled **beats ARC** (91 s vs 456 s) | paper §6.2 |
| ARC's distinctive wins: surges beyond quota (12.2% vs 19.5-23.4% loss at 6x), 13-25% lower cost under volumetric DDoS, poisoned utilisation telemetry (shared with forecast-based autoscalers), and the only controller that **improves under repeated stress** (-24%, p=5e-5; identical load each epoch) | paper §6.3 |
| Graph-aware planning, the security layer and chaos learning show **no measurable effect** in the main scenarios (chaos learning shows up only under repeated exposure); remediation/graceful degradation and the guardrail carry the benefit | paper §6.4, §6.5 |
| **Pre-registered test (seeds never used before):** ARC-r0 (proactive retry cap 0) passes H1-H4 as registered, but H1/H3 pass only because of the poisoning scenario; a forecasting autoscaler without retries is more reliable at +19-25% cost | paper §6.6, `experiments/PREREGISTRATION.md` |
| **Container testbed (real Docker pods, queues, retries, kills; not Kubernetes):** confirms the direction of the retry effect and PredHPA-r0's reliability, but ranks ARC-r0 last (pre-registered T2 failed): real chaos probes cause visible failures when retries are off, which the simulator does not model | paper §6.7, `testbed/` |

## What is real and what is simulated

| | |
|---|---|
| Workloads | **Real**: NASA-HTTP (Jul 1995, 1,891,714 requests) and ClarkNet-HTTP (28 Aug-3 Sep 1995, 1,654,882 requests) from the Internet Traffic Archive. Only the amplitude is rescaled. |
| Cluster, services, queues, scaling lag, faults, attacks | **Simulated** (`arcsim/`). Parameters are modelling assumptions, not measurements. |
| Topologies | 10-service Online-Boutique-style graph; synthetic 24-service 6-tier graph (fixed generator seed). |
| Container testbed | **Real software, not Kubernetes** (`testbed/`): Docker containers, real queues/timeouts/retries/kills, real trace replay; start-up delay and gray failure emulated. |
| Real Kubernetes validation | **Not done.** Main remaining limitation. |

## Layout

```
arcsim/            simulator, topologies, workload loader, controllers, scenarios, metrics
experiments/       run_main.py, run_sweeps.py, run_antifragile.py, analyze.py
results/           CSVs and tables.md
figures/           PNG figures
paper/             paper.md, build_pdf.py, paper.pdf
testbed/           container testbed (worker.py, orchestrator.py, analyze_testbed.py)
tests/             pytest invariants for the simulator
data/download.sh   fetches the two traces
```

## Reproduce

```
pip install numpy pandas matplotlib scipy tabulate pytest markdown
sh data/download.sh
python -m pytest -q tests
cd experiments
python run_main.py 40 100                                   # NASA, 10-service: 10 scenarios x 12 controllers x 40 seeds (~9 min on 4 cores)
ARC_TRACE=clarknet python run_main.py 40 100 "Static,HPA,HPA-fast,HPA-45,PredHPA,HPA+RL,ARC"
ARC_TOPO=deep python run_main.py 30 200 "HPA,HPA-45,PredHPA,ARC,ARC-noGraph,ARC-bf"
ARC_TOPO=deep ARC_TAG=_deep_abl python run_main.py 30 200 "ARC-noRemed,ARC-noGuard"
ARC_TAG=_retry python run_main.py 40 100 "HPA-45-r1,HPA-45-r0,PredHPA-r1"
ARC_TOPO=deep ARC_TAG=_deep_retry python run_main.py 30 200 "HPA-45-r1,HPA-45-r0,PredHPA-r1"
python run_sweeps.py 20 100
ARC_CTRL="HPA-45-r0,HPA-45-r1,PredHPA-r1,ARC" ARC_TAG=_retry python run_sweeps.py 20 100
python run_antifragile.py 30 100                            # drifting real load
python run_antifragile.py 30 100 stationary                 # identical load every epoch
ARC_CTRL="HPA-45-r0,HPA-45-r1,PredHPA-r1,ARC" ARC_TAG=_retry python run_antifragile.py 30 100 stationary
python analyze.py                                           # tables.md + figures
python analyze_prereg.py                                    # pre-registered scorecard (results/prereg_tables.md)
# pre-registered runs: see experiments/PREREGISTRATION.md for the exact commands (seeds 300-339 / 400-429)
# container testbed (needs Docker): cd ../testbed && python orchestrator.py && python analyze_testbed.py
cd ../paper && python build_pdf.py                          # paper.pdf (needs Chromium)
```

## Development log (disclosure)

Development used seeds 0-19. Reported results use seeds 100-139 (main, ClarkNet, sweeps from 100, repeated stress) and
200-229 (deep graph). Changes made after looking at results, in order:

1. Metrics exclude the first 10 min of every run (cold-start transient of the autoscalers).
2. Added stronger / cost-matched baselines (HPA-fast, HPA-45) and an abrupt-surge scenario (`flash_step`), because the
   first results flattered ARC (ramped surges are easy to extrapolate; ARC used more replicas).
3. A chaos probe fired while a surge had exhausted the pod quota and the killed pod could not be replaced (seed 18).
   Fixes: the simulator's scheduler now serves the most under-provisioned service first (it previously favoured
   low-index services, unfair to every controller), and the probe guardrail now requires stable load and free quota.
4. Initial pod sizing is capped at the cluster quota (found by a unit test; no effect on reported runs).
5. Added a second trace (ClarkNet) and a synthetic 24-service graph to test robustness and the graph-planning claim.
   My first deep graph was infeasible (a service needed ~66 pods against a cap of 30) - a design bug, fixed by sizing
   per-pod capacity from the call multiplier. A "bounded feedback" variant (`ARC-bf`) was tried as a possible fix for
   quota runaway; it did not help and is reported as an exploratory variant. An early explanation based on two seeds
   (100, 101) did not survive the 30-seed run.
6. **Retry-limited baselines (HPA-45-r1/r0, PredHPA-r1) were added after the deep-graph ablation showed that ARC's
   advantage came from retry/breaker control.** They reverse much of the headline result (paper §6.2). They were run
   on seeds already used for the main comparison on the 10-service graph.

Scenario definitions and fault magnitudes were fixed before the first run (only `flash_step` was added). Retry-limited
baselines were not run on ClarkNet. The references were checked against publisher/author pages (see paper); books and
datasets should be re-checked before submission.
