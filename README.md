# Cloud-native resilience: chaos-in-the-loop antifragile control (ARC)

Code, experiments and paper draft for a submission to the special issue on **resilience-by-design for
cloud-native systems** (stress-oriented evaluation of self-healing, AIOps, chaos engineering and
security-aware orchestration).

**Idea.** Existing autoscalers, AIOps and chaos tooling are evaluated and run separately. ARC closes the
loop: a controller that (1) plans capacity from the *service dependency graph*, (2) learns per-service
fragility by running *low-blast-radius chaos probes on itself* (a bandit chooses what to probe), (3)
tells attacks from flash crowds and detects *poisoned telemetry* with a flow-conservation check,
(4) remediates gray failures and degrades gracefully (circuit breakers, retry budgets), and
(5) model-checks every risky action through a *safe-adaptation guardrail*.

## What is real and what is simulated

| | |
|---|---|
| Workload | **Real**: NASA Kennedy Space Center HTTP trace, July 1995 (1,891,714 requests). Rescaled in amplitude only. |
| Cluster, services, queues, scaling lag, faults, attacks | **Simulated** (trace-driven fluid simulator, `arcsim/`). Parameters are modelling assumptions, not measurements. |
| Real Kubernetes validation | **Not done** (no container runtime in the authoring environment). This is the main limitation. |

## Layout

```
arcsim/            simulator, topology, workload loader, controllers, scenarios, metrics
experiments/       run_main.py, run_sweeps.py, run_antifragile.py, analyze.py
results/           CSVs, tables.md (all numbers quoted in the paper)
figures/           PNG figures
paper/paper.md     manuscript draft
tests/             pytest invariants for the simulator
data/download.sh   fetches the NASA trace
```

## Reproduce

```
pip install numpy pandas matplotlib scipy tabulate pytest
sh data/download.sh
python -m pytest -q tests
cd experiments
python run_main.py 40 100        # 10 scenarios x 12 controllers x 40 seeds (~10 min on 4 cores)
python run_sweeps.py 20 100
python run_antifragile.py 30 100
python analyze.py                # writes results/tables.md and figures/*.png
```

## Development log (disclosure)

Development used seeds 0-19; all reported results use **fresh seeds 100+** that were not looked at while
tuning. Changes made after looking at development results:

1. Metrics exclude the first 10 min of every run (cold-start transient of the autoscalers).
2. Added stronger / cost-matched baselines (HPA-fast, HPA-45) and an abrupt-surge scenario (`flash_step`)
   because the first results were flattering to ARC (ramped surges are easy to extrapolate; ARC used more replicas).
3. A chaos probe fired while a surge had exhausted the pod quota and the killed pod could not be replaced
   (seed 18). Fixes: the simulator's scheduler now serves the most under-provisioned service first (it
   previously favoured low-index services, which was unfair to every controller), and the probe guardrail
   now requires stable load and free quota.
4. Initial pod sizing is capped at the cluster quota (found by a unit test; no effect on reported runs).

Scenario definitions and fault magnitudes were fixed before the first run and not changed afterwards
(only `flash_step` was added).
