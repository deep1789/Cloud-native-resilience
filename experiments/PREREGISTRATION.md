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
