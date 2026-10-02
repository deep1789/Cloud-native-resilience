# Pre-registration: proactive-retry ARC and fair retry-limited baselines

Written and committed **before** running any of the experiments below. Seeds 300-339 (10-service graphs) and
400-429 (24-service graph) have not been used for development or any earlier analysis.

## Motivation
Earlier results (paper §6.2) showed that most of ARC's gain over default-retry autoscalers is retry-amplification control,
and that a retry-limited HPA matches or beats ARC on several scenarios. ARC's retry control is reactive (caps start at 2).
We test whether ARC with a *proactive* low default retry cap, keeping its shedding / guardrail / chaos learning,
beats the strongest simple baselines.

## Controllers (fixed)
Baselines, all with limited retries: `HPA-r0`, `HPA-fast-r0`, `HPA-45-r0`, `PredHPA-r0`, `PredHPA-r1`.
ARC variants: `ARC` (original, default cap 2), `ARC-r0` (default cap 0), `ARC-r1` (default cap 1).
For repeated stress only: `ARC-r0-noChaos`.

## Runs (fixed)
1. NASA trace, 10-service graph: 10 scenarios x 8 controllers x seeds 300-339.
2. ClarkNet trace, 10-service graph: same, seeds 300-339.
3. NASA trace, 24-service graph: same controllers, seeds 400-429.
4. Surge / DDoS sweeps (20 seeds, 300-319): HPA-45-r0, PredHPA-r1, ARC, ARC-r0.
5. Repeated stress, identical load (30 seeds, 300-329): HPA-45-r0, PredHPA-r1, ARC, ARC-r0, ARC-r0-noChaos.

## Hypotheses
* **H1 (reliability at equal cost).** On the mean over the nine fault scenarios, ARC-r0 has SLO-violation time not
  higher than the best of {HPA-r0, HPA-fast-r0, HPA-45-r0} and cost at most 5% higher than that baseline, on both
  10-service runs (1, 2) and on the 24-service run (3).
* **H2 (cost vs forecasting).** ARC-r0 has at least 15% lower cost than PredHPA-r1 with SLO-violation time within
  the 95% CI of PredHPA-r1's.
* **H3 (poisoning).** In `poison_flash`, ARC-r0 has lower violation time than HPA-45-r0.
* **H4 (antifragility).** In repeated stress, ARC-r0 improves between epochs 1-3 and 8-11 (Wilcoxon p < 0.05) while
  ARC-r0-noChaos does not.

## Analysis plan (fixed)
Primary metric: SLO-violation time (s), mean over the nine fault scenarios, per-seed means compared with paired
Wilcoxon tests. Secondary: loss %, cost (replica-hours), MTTR. Per-scenario paired Wilcoxon with Holm correction over
scenarios. **All results are reported, including any hypothesis that fails.** No controller parameters are changed
after seeing these results; if a variant is changed it is reported as a new, exploratory variant on new seeds.

---
# Addendum: container testbed protocol (written before the testbed experiment is run)

Testbed: `testbed/` (real Docker containers as pods; real queuing, timeouts, retries, process kills, trace replay;
emulated start-up delay and gray failure; no Kubernetes). Settings fixed after a debugging phase on seed 1 only
(rate/capacity scale K=5, time compression 8, 40-minute episodes, 10-minute warm-up excluded).

* Controllers: `HPA-45`, `HPA-45-r0`, `PredHPA-r0`, `ARC`, `ARC-r0` (same controller code as the simulator).
* Scenarios: `kill_cascade`, `gray`, `flash_step`. Seeds 1-3 (all runs reported; no run is dropped).
* **T1 (retry amplification is real).** In `kill_cascade` and `flash_step`, HPA-45 has higher SLO-violation time than
  HPA-45-r0 (direction only; 3 seeds do not support significance tests).
* **T2 (simulator ranking transfers).** The ordering of controllers by mean SLO-violation time over the three scenarios
  agrees with the simulator's ordering on the same scenarios for at least 3 of 5 controllers (rank agreement).
* **T3 (cost).** ARC and ARC-r0 cost within 10% of HPA-45-r0 and at least 10% less than PredHPA-r0.
Results are descriptive (n = 3). Any discrepancy with the simulator is reported as a finding about the simulator.
