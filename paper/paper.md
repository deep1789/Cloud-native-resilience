# ARC: Closing the Loop Between Chaos Engineering and Autonomic Control for Antifragile Cloud-Native Systems

*Draft manuscript for the special issue on resilience-by-design for cloud-native systems. All numbers are produced by the code in this repository (`results/tables.md`); the evaluation is trace-driven **simulation**, not a real-cluster study (see §8).*

## Abstract

Cloud-native platforms fail through emergent behaviour — retry storms, cascading overload, gray failures and poisoned telemetry — that reactive autoscaling and post-failure recovery handle poorly. Chaos engineering, AIOps and autoscaling are normally built and evaluated separately. We present **ARC**, an Antifragile Resilience Controller that closes the loop between them. ARC (i) plans capacity from the service dependency graph, (ii) learns per-service fragility by running low-blast-radius chaos probes on itself, selected by a bandit, (iii) separates attacks from flash crowds and detects poisoned telemetry by checking reported utilisation against flow conservation on the graph, (iv) remediates gray failures and degrades gracefully with circuit breakers and retry budgets, and (v) vets risky actions through a safe-adaptation guardrail. We evaluate ARC on a ten-service Online-Boutique-style topology driven by a real HTTP trace (1.89 M requests, NASA, July 1995) under ten stress scenarios, two intensity sweeps and a repeated-stress experiment, against five baselines including a Kubernetes-style HPA, a tuned HPA, a cost-matched HPA and a forecasting autoscaler (40 seeds, paired tests). Averaged over nine fault scenarios, ARC cuts SLO-violation time from 128–205 s (HPA variants) to 13 s and recovery time from 99–140 s to 7.6 s at equal cost to the cost-matched HPA; it is statistically indistinguishable from, or slightly worse than, a simple forecasting autoscaler on several scenarios, while using 18% fewer replica-hours and handling gray failures far better. Ablations show that circuit-breaking/remediation and the guardrail carry most of the benefit, whereas graph planning, the security layer and chaos learning show no measurable effect in the main scenarios; chaos learning is, however, the only mechanism that makes the system improve under repeated identical stress (−24% violation time, p = 5·10⁻⁵, flat for every other controller). We report negative results and failure modes, including a chaos-probe incident that motivated the guardrail.

## 1. Introduction

Mission-critical services increasingly run as microservices on elastic, orchestrated infrastructure. Outages in such systems rarely come from a single broken component; they come from interactions: a lost dependency triggers timeouts, timeouts trigger retries, retries overload the survivors, and the autoscaler reacts only after metrics have moved and new pods have started. Adversaries make this worse, either by volumetric application-layer attacks that are indistinguishable from legitimate surges at the resource level, or by corrupting the telemetry that autoscalers trust.

Three bodies of work address parts of this problem and are rarely combined: *autoscaling and autonomic control* (reactive or predictive), *AIOps* (detection, root-cause analysis, remediation), and *chaos engineering* (injecting faults to find weaknesses). In practice chaos experiments are run by people, offline, and their findings reach the controller only through human-written configuration.

**Contributions.**

1. **ARC**, a controller that uses chaos experiments as an *online learning signal* for the autoscaler: a UCB bandit chooses one-pod probes, and the observed stress margin updates a per-service fragility estimate that sets redundancy headroom (§4.2).
2. A **flow-conservation consistency check** that detects under-reporting telemetry exporters from the reports of their (honest) callers, and a noisy-classifier-based attack/flash-crowd discriminator with selective shedding (§4.3).
3. A **safe-adaptation guardrail** that vets scale-down and chaos actions against an internal, imperfect model, and graceful-degradation machinery (circuit breakers, retry budgets, gray-failure remediation) (§4.4–4.5).
4. A **stress-oriented evaluation**: real-trace-driven, ten scenarios, intensity sweeps beyond cluster quota, repeated-exposure experiment, five baselines incl. a cost-matched one, ablations, paired statistics, held-out seeds, and an open simulator.
5. An **honest account of what does not work**: which mechanisms show no measurable effect, where a simple forecaster is as good, and a failure mode of naive chaos probing.

## 2. Background and related work

*[TODO before submission: extend and verify all citations; this section is intentionally short.]*

**Autonomic and elastic control.** The autonomic-computing vision (Kephart & Chess, 2003) frames self-healing and self-optimisation as MAPE-K loops. The Kubernetes Horizontal Pod Autoscaler scales on observed utilisation with stabilisation windows; Autopilot (Rzadzca et al., EuroSys 2020) shows learned vertical/horizontal scaling at Google scale. **Chaos engineering.** Basiri et al. (IEEE Software, 2016) describe principles of chaos engineering as practised at Netflix; Taleb (2012) coined *antifragility*, which we use in the operational sense that measured resilience improves with exposure to stressors. **Microservice benchmarks and traces.** DeathStarBench (Gan et al., ASPLOS 2019) and Google's Online Boutique are standard benchmarks; Alibaba's microservice traces (Luo et al., SoCC 2021) characterise real call graphs. **Resilience patterns.** Circuit breakers and bulkheads (Nygard, *Release It!*, 2007) and SRE practice (Beyer et al., 2016) motivate our breakers, retry budgets and SLO-based metrics.

ARC differs from this body of work by treating chaos experiments as a *training signal for the controller itself*, bounded by a guardrail, and by evaluating the controller under combined resilience, performance, cost and security stress.

## 3. System and threat model

**System.** A microservice application with *N = 10* services (frontend, product catalogue, recommendation, ads, cart, currency, checkout, payment, shipping, e-mail) and call weights `W[j][c]` (expected calls from *j* to *c* per request). Each service is a pool of pods with per-pod capacity μ, a bounded queue, a nominal start-up delay of 15–40 s (±30% jitter), and 2–30 replicas under a 120-pod cluster quota. Calls time out after 2 s and are retried up to twice by default, producing retry amplification `1 + F + F²` for a callee with failure probability *F*. Recommendation, ads and e-mail are non-critical (callers degrade gracefully); all other edges are critical. The ReplicaSet replaces lost pods automatically.

**SLO.** A 5-s tick violates the SLO when legitimate goodput / legitimate load < 95% (availability and a 1 s latency objective combined).

**Faults and attacks** (§5.2): pod kills, node failure, gray failure (pods pass liveness but serve at 30% capacity), flash crowds, L7 volumetric DDoS, and *telemetry poisoning* (a compromised exporter reports 25% of its true utilisation, queue depth and error rate).

**Controller view.** Controllers see noisy (5%) telemetry; ARC plans with an *imperfect* capacity model (each μ mis-estimated by up to ±20%).

## 4. ARC design

### 4.1 Overview
Every tick ARC receives telemetry and emits desired replicas, ingress shedding fractions, retry caps, open circuit breakers, remediation orders and, rarely, a chaos probe.

### 4.2 Chaos-in-the-loop fragility learning
ARC keeps a fragility score `f_i ∈ [0,1]` per service, `f_i = max(f_chaos_i, f_inc_i)`.

* **Probes.** Every ≥10 min, when the system is healthy (availability ≥ 98% for 1 min, no attack, no scaling in flight, **load stable and ≤70% of quota**), ARC kills one pod of the service with highest `f_i + 0.25·sqrt(ln n / n_i)` (UCB), provided the model predicts post-kill utilisation ≤ 0.75. Over the next 60 s it measures peak failure fraction and utilisation and sets reward `r = min(1, 4·peak_fail + 2·max(0, peak_util − 0.9))`; `f_chaos_i ← 0.7·f_chaos_i + 0.3·r`.
* **Real incidents.** When availability recovers after an incident, graph-based root-cause analysis (the deepest unhealthy service whose critical callees are healthy) raises `f_inc_i` by `0.45·min(1, 2·peak_loss)`; it decays with a ≈2 h half-life.
* **Use.** Feed-forward capacity is multiplied by `(1 + 0.8·f_i)`: fragile services (single points of failure, slow start-up, repeatedly hit) get N+k redundancy, robust ones are right-sized.

### 4.3 Security-aware orchestration
*Attack vs. flash crowd.* An L7 classifier (modelled as noisy, TPR≈0.8 / FPR≈0.05) yields a suspect share; an attack is declared after two consecutive ticks above threshold. Then ARC sheds 85% of suspect traffic with 3% collateral shedding and plans capacity for the residual legitimate load. A flash crowd shows no suspect signal and is scaled out, not shed.
*Telemetry poisoning.* For every non-ingress service ARC estimates offered load from its callers' reported *served* rates and call weights (flow conservation). A service whose reported utilisation is below half the graph-derived value is flagged and its utilisation replaced by the graph estimate.

### 4.4 Capacity planning and the safe-adaptation guardrail
Ingress load is forecast with Holt's double exponential smoothing (60 s horizon) and propagated through the graph, `m = (I − Wᵀ)⁻¹e₀`, to per-service demand; desired replicas are `max(feed-forward, feedback on robust utilisation)` at a 65% target. **Guardrail:** scale-down is applied only if (a) the model-predicted post-action utilisation ≤ 0.8, (b) no incident is active and availability ≥ 97%, and it is limited to −20% per step with a 2-minute hold-down; chaos probes are vetoed unless post-kill utilisation ≤ 0.75, load is stable and free quota exists.

### 4.5 Graceful degradation and remediation
*Circuit breakers* cut non-critical edges (recommendation, ads, e-mail) for 60 s when the system is overloaded and the callee is saturated or failing. *Retry budgets* lower the retry cap of a caller from 2 to 1 or 0 as its callees' failure rate rises (preventing retry storms). *Gray-failure remediation:* if a service is saturated by flow conservation yet serves <65% of its offered load while the model says it should have spare capacity, for three ticks, ARC reschedules its pods and meanwhile over-provisions it.

## 5. Evaluation methodology

### 5.1 Simulator and workload
A trace-driven fluid simulator (`arcsim/`, 5 s tick) implements the system of §3. The **workload is real**: the NASA Kennedy Space Center HTTP trace of July 1995 (1,891,714 requests) is converted to a request-rate series; each episode is a 2-h window (six windows, weekdays and weekends) rescaled so its 95th percentile is 600 req/s. Only the amplitude is rescaled; burstiness and daily structure come from the trace. The first 10 min of each run are excluded from all metrics.

### 5.2 Scenarios (fixed before the first run, plus `flash_step`)
`baseline` (no fault); `flash` (3× surge, 1-min ramp); `flash_step` (3× abrupt); `kill_cascade` (70% of a shared dependency's pods lost at peak); `node_failure` (40% of the pods of four services); `gray` (one service at 30% capacity for 20 min); `ddos` (volumetric L7 attack, 2.5 units); `ddos_kill`; `poison_flash` (three exporters under-report at 25% during a 3× surge); `storm` (kill → surge → gray failure → DDoS in sequence). Timing and targets are randomised per seed.

### 5.3 Baselines
**Static** (initial sizing); **HPA** (Kubernetes algorithm: 15 s sync, 30 s window, 10% tolerance, 300 s scale-down stabilisation); **HPA-fast** (5 s sync, 10 s window); **HPA-45** (HPA at a 45% target — a *cost-matched* baseline with standing headroom); **PredHPA** (HPA plus a per-service Holt forecast of its own offered load); **HPA+RL** (HPA plus a fixed ingress rate limiter that cannot tell attack from surge). All baselines get the ReplicaSet's automatic pod replacement and the same default retry/timeout settings.

### 5.4 Metrics and statistics
SLO-violation time (s), legitimate-request loss (% of offered load, *including* requests ARC sheds deliberately), cost (replica-hours), mean time to recover from each injected fault, p95 end-to-end latency, probe overhead, DDoS detection delay, poisoning-detection F1. Means with 95% CIs over **40 seeds**; paired Wilcoxon signed-rank tests (ARC vs. each baseline per scenario), Holm-corrected across the ten scenarios. Development used seeds 0–19; **all reported results use unseen seeds 100–139** (sweeps: 20 seeds; repeated stress: 30 seeds). Because seeds map to six trace windows, seeds are not fully independent draws of the workload (§8).

## 6. Results

### 6.1 Main comparison
SLO-violation time (s; mean ± 95% CI; lower is better; bold = best):

| Scenario | HPA | HPA-fast | HPA-45 | PredHPA | HPA+RL | ARC |
|---|---|---|---|---|---|---|
| baseline | 6.2 ± 3.7 | 1.0 ± 0.6 | **0.0 ± 0.0** | 0.0 ± 0.0 | 4.8 ± 1.8 | 6.8 ± 4.8 |
| flash | 167.6 ± 42.7 | 74.5 ± 26.2 | 80.4 ± 41.9 | **2.4 ± 1.5** | 555.0 ± 29.8 | 10.1 ± 4.5 |
| flash_step | 174.1 ± 45.4 | 138.9 ± 40.3 | 101.0 ± 43.5 | **18.4 ± 14.3** | 599.6 ± 31.6 | 21.0 ± 6.3 |
| kill_cascade | 44.0 ± 7.5 | 36.6 ± 6.1 | 30.1 ± 5.8 | **17.2 ± 4.8** | 43.5 ± 7.4 | 19.2 ± 5.3 |
| node_failure | 24.8 ± 8.0 | 7.2 ± 4.5 | 2.8 ± 2.2 | **0.0 ± 0.0** | 23.6 ± 7.7 | 9.6 ± 5.2 |
| gray | 201.8 ± 71.6 | 164.8 ± 70.4 | 133.0 ± 57.6 | 103.4 ± 61.4 | 201.1 ± 72.2 | **22.8 ± 7.1** |
| ddos | 45.4 ± 13.1 | 13.8 ± 7.4 | 0.1 ± 0.2 | **0.0 ± 0.0** | 203.2 ± 34.8 | 6.1 ± 4.3 |
| ddos_kill | 62.1 ± 12.5 | 44.8 ± 9.4 | 1.9 ± 3.4 | **0.1 ± 0.2** | 225.1 ± 33.3 | 7.9 ± 4.8 |
| poison_flash | 1048.6 ± 50.6 | 1092.0 ± 36.8 | 789.4 ± 43.3 | **2.4 ± 1.5** | 1024.8 ± 44.4 | 10.9 ± 4.8 |
| storm | 77.0 ± 28.1 | 128.5 ± 70.7 | 11.8 ± 10.7 | **3.4 ± 4.2** | 220.8 ± 21.6 | 10.4 ± 3.9 |

Mean over the nine fault scenarios:

| Controller | SLO viol. (s) | Loss (%) | Cost (replica-h) | MTTR (s) | p95 latency (s) |
|---|---|---|---|---|---|
| Static | 2916.8 | 48.92 | 54.5 | 649.2 | 5.80 |
| HPA | 205.0 | 5.94 | 93.2 | 139.8 | 0.71 |
| HPA-fast | 189.0 | 5.34 | 89.0 | 129.7 | 0.75 |
| HPA-45 | 127.8 | 4.31 | 110.3 | 99.1 | 0.63 |
| PredHPA | 16.4 | 0.29 | 134.8 | 16.1 | 0.21 |
| HPA+RL | 344.1 | 6.80 | 87.8 | 132.5 | 0.65 |
| **ARC** | **13.1** | 0.32 | 110.0 | **7.6** | **0.08** |

*Findings.* (1) ARC outperforms the default HPA and HPA+RL in all nine fault scenarios (paired Wilcoxon, Holm-corrected p ≤ 0.003), the tuned HPA-fast in seven (n.s. in node_failure and ddos), and the cost-matched HPA-45 in five (flash, flash_step, kill_cascade, gray, poison_flash); at the same cost (110 replica-hours) ARC's violation time is ~10× lower. (2) **ARC does not dominate.** HPA-45 is better in `ddos` and `ddos_kill` (p ≈ 0.03–0.04) and in the fault-free case, and **PredHPA is as good or better on SLO violation in most scenarios** (significantly better in flash, node_failure, ddos, ddos_kill, poison_flash and storm; ARC significantly better only in gray, p = 0.047; no significant difference in flash_step and kill_cascade). PredHPA's advantage is bought with standing capacity: it uses **18% more replica-hours than ARC** (134.8 vs. 110.0). (3) ARC has the lowest recovery time and p95 latency because circuit breakers and retry budgets prevent retry storms from forming. (4) Poisoned telemetry blinds every utilisation-driven autoscaler (29–34% of requests lost) but not ARC (0.1%) nor PredHPA, whose forecast uses request rates rather than the poisoned signals. (5) **Resilience costs money in quiet times**: in the fault-free scenario ARC runs 103.2 replica-hours vs. 73.8 for HPA (+40%).

### 6.2 Cost–reliability trade-off
`figures/fig_pareto.png` plots mean violation time against cost over the fault scenarios. ARC and HPA-45 have the same cost; ARC is an order of magnitude lower in violations. PredHPA reaches similar violation time but at the highest cost. On the aggregate plane no tested controller dominates ARC: it has lower violation time *and* lower cost than PredHPA, and ~10× lower violation time than HPA-45 at equal cost. The default HPA family is cheaper in absolute terms (88–93 replica-hours) but 14–26× worse on violations. Per scenario the picture is less favourable (§6.1): PredHPA wins several individual scenarios.

### 6.3 Ablation
SLO-violation time (s):

| Scenario | ARC | −Graph | −Chaos | −Sec | −Guard | −Remed |
|---|---|---|---|---|---|---|
| baseline | 6.8 ± 4.8 | 6.2 ± 4.4 | 6.5 ± 4.5 | 6.8 ± 4.8 | 368.6 ± 27.7 | 11.5 ± 8.0 |
| flash_step | 21.0 ± 6.3 | 21.6 ± 6.3 | 21.5 ± 6.1 | 20.9 ± 6.3 | 426.9 ± 30.9 | 47.4 ± 21.9 |
| kill_cascade | 19.2 ± 5.3 | 18.1 ± 4.9 | 18.9 ± 5.1 | 19.8 ± 5.6 | 383.9 ± 26.6 | 30.0 ± 8.6 |
| gray | 22.8 ± 7.1 | 22.1 ± 7.1 | 24.4 ± 8.0 | 23.2 ± 7.4 | 893.9 ± 102.6 | 148.5 ± 65.5 |
| poison_flash | 10.9 ± 4.8 | 11.4 ± 5.3 | 11.4 ± 5.1 | 10.9 ± 4.8 | 447.4 ± 34.2 | 17.0 ± 8.5 |
| storm | 10.4 ± 3.9 | 11.4 ± 4.4 | 13.0 ± 4.5 | 10.9 ± 4.6 | 625.9 ± 95.5 | 79.1 ± 67.0 |

(all ten scenarios: `results/tables.md`). *Remediation/graceful degradation* matters (gray 6.5×, storm 7.6×, flash_step 2.3×). *Guardrail* matters most, but note that the `−Guard` variant removes both the model check and the scale-down hold-down, so it oscillates and violates the SLO even without faults while using 27% fewer replica-hours (80.0 vs. 109.3); this ablation shows that an unguarded predictive controller is unsafe rather than isolating the model check alone, and its probes cause 45.2 s of SLO harm per run vs. 1.0 s for ARC. **Graph planning, the security layer and chaos learning are within confidence intervals of full ARC in every main scenario.** The security layer's measurable value is operational: DDoS detection after 7.4 ± 0.7 s with 0.43% collateral shedding of legitimate requests, poisoning detection F1 = 0.67 ± 0.03, and 1.7 fewer replica-hours per run on average. It does not change SLO outcomes because ARC's feed-forward planning is anchored on ingress rate, which the modelled poisoning does not touch.

### 6.4 Stress sweeps
Legitimate-request loss (%) as surge size and attack volume grow past the point where the 120-pod quota binds (20 seeds):

| Surge (× load) | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|
| HPA | 1.6 | 6.3 | 22.0 | 37.0 | 45.3 |
| HPA-45 | 0.3 | 2.2 | 22.0 | 34.5 | 45.7 |
| PredHPA | 0.0 | 0.1 | 5.9 | 19.4 | 30.2 |
| **ARC** | 0.1 | 0.1 | **1.1** | **5.9** | **12.2** |

| Attack (units) | 1 | 2 | 3 | 5 | 8 |
|---|---|---|---|---|---|
| HPA | 0.0 | 0.3 | 1.0 | 12.7 | 14.3 |
| PredHPA | 0.0 | 0.0 | 0.0 | 0.3 | 9.0 |
| **ARC** | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 |

ARC degrades gracefully: at a 6× surge it loses 12% of requests vs. 30–46% for the baselines, and its loss under DDoS is constant at the 0.5% collateral-shedding rate while its cost rises only from 103 to 109 replica-hours (PredHPA: 125 → 146). We did not run ablations inside the sweeps, so we attribute the high-load advantage to graceful degradation (breakers, retry budgets, shedding) only by consistency with §6.3. At low intensity ARC is *slightly worse* (0.5% vs. 0.0%) because it sheds legitimate traffic as collateral during attacks.

### 6.5 Antifragility under repeated stress
The same three pod-kill faults hit every 30 min for 6 h. With **identical load in every epoch** (isolating learning from workload drift):

| Controller | epochs 1–3 (s) | epochs 8–11 (s) | change |
|---|---|---|---|
| HPA | 98.9 ± 11.4 | 97.5 ± 11.0 | −1% (n.s.) |
| HPA-fast | 85.5 ± 15.0 | 88.7 ± 14.6 | +4% (n.s.) |
| HPA-45 | 76.5 ± 9.8 | 75.6 ± 8.6 | −1% (n.s.) |
| PredHPA | 38.1 ± 9.1 | 36.5 ± 9.3 | −4% (n.s.) |
| ARC without chaos learning | 33.1 ± 5.8 | 33.5 ± 5.6 | +1% (n.s.) |
| **ARC** | 29.6 ± 5.8 | **22.5 ± 5.6** | **−24% (p = 5·10⁻⁵)** |

Only full ARC improves with exposure. The effect is real but moderate (−24%, not elimination of the violations). In absolute terms ARC's late-epoch level (22.5 s) is below PredHPA's (36.5 s) while using fewer pods on average (61 vs. 81). *(With the real, non-stationary load, ARC improved by 51% but HPA-45 and PredHPA also improved by 31–36% because the trace itself drifts; we therefore report the stationary experiment as the clean test and the drifting one only for transparency, `results/tables.md`.)*

## 7. Discussion

**What carries the benefit.** Most of ARC's advantage over HPA-family autoscalers comes from (i) acting on a forecast of ingress load propagated through the dependency graph rather than on lagging per-service utilisation, and (ii) not amplifying failures: retry budgets, circuit breakers and gray-failure remediation. The *graph* part of (i) is not separable from local forecasting in our ten-service topology, where PredHPA, which has no graph, performs as well on most scenarios; deeper call graphs, where demand reaches leaf services several hops later, are the natural place to test it (future work).

**Chaos probes need guardrails.** In development, a probe killed one of two payment pods during a surge that had already exhausted the cluster quota; the replacement could not be scheduled and checkout failed repeatedly for about 200 s of SLO violation. Two lessons: probes must be conditioned on stable load and free quota, and the simulated scheduler's index-order bias (which starved late services) had been unfairly penalising *every* controller. Both were fixed before the final evaluation (README, development log).

**Cost of resilience.** ARC is not free: 40% more replica-hours than HPA when nothing fails. Whether this is acceptable depends on the SLO's price; the cost-matched HPA-45 comparison shows that ARC's reliability gain is not just spare capacity, while PredHPA shows that spare capacity alone *can* buy comparable reliability on many scenarios.

**Security.** Telemetry poisoning is a real weakness of utilisation-based autoscaling (29–34% request loss in our scenario). In ARC its measured effect is absorbed by ingress-anchored planning; the conservation check detects the poisoned services (F1 0.67) but its marginal SLO value is not demonstrated here, and a stronger adversary (e.g. poisoning the ingress rate or the honest callers) is out of scope.

## 8. Threats to validity

* **Simulation, not a cluster.** Capacities, start-up delays, queueing, retry behaviour and fault effects are modelling assumptions, not measurements; absolute numbers will differ on a real Kubernetes cluster. We did not run a real-cluster validation (no container runtime was available). This is the most important limitation; a kind/minikube + Online Boutique + Chaos Mesh replication is the first follow-up.
* **Workload realism.** The trace is 1995 web traffic and supplies arrival patterns only; it has no microservice call graph. Six 2-h windows limit workload diversity and make seeds non-independent across windows. A second modern trace (e.g. Azure Functions, Alibaba) is future work.
* **Authored faults and baselines.** We designed the faults and re-implemented the baselines; the HPA is a faithful model of the documented algorithm, not Kubernetes itself. Scenarios were fixed before the first run (only `flash_step` added), and the strongest/cost-matched baselines were added after seeing preliminary results, which we disclose.
* **Model mismatch.** ARC's internal model errs by ±20% per service; larger or structural mismatch (not tested) could hurt the guardrail.
* **Small topology and cluster.** Ten services and a 120-pod quota. Scaling behaviour of the controller (e.g. graph inversion, probing cadence) on hundreds of services is untested.
* **Tuning.** ARC's hyper-parameters were set informally on development seeds; baselines were not tuned beyond the HPA-fast/HPA-45 variants.
* **Multiple comparisons.** Holm correction is applied within each baseline across scenarios, not across baselines.

## 9. Conclusion

Closing the loop between chaos engineering and autonomic control is feasible and, in simulation on a real HTTP trace, yields controllers that recover an order of magnitude faster than Kubernetes-style autoscalers at equal cost, degrade gracefully beyond cluster capacity, resist poisoned telemetry, and — uniquely — improve with repeated exposure to the same faults. The gains come mainly from graceful degradation and safe adaptation; a plain forecasting autoscaler can match ARC's reliability on many scenarios but at 18% higher cost, and most of ARC's individual mechanisms (graph planning, security layer) show no measurable benefit in our scenarios. Validating on a real cluster, richer topologies and adversaries, and learning-based rather than hand-designed policies are the next steps.

## Reproducibility

Everything (simulator, controllers, scenarios, seeds, analysis, tests) is in this repository; see `README.md`. Full tables: `results/tables.md`. Figures: `figures/`.

## References (verify before submission)

* Beyer, B., Jones, C., Petoff, J., Murphy, N. R. *Site Reliability Engineering.* O'Reilly, 2016.
* Basiri, A. et al. Chaos Engineering. *IEEE Software* 33(3), 2016.
* Gan, Y. et al. An open-source benchmark suite for microservices and their hardware-software implications for cloud & edge systems (DeathStarBench). *ASPLOS* 2019.
* Kephart, J. O., Chess, D. M. The vision of autonomic computing. *IEEE Computer* 36(1), 2003.
* Luo, S. et al. Characterizing microservice dependency and performance: Alibaba trace analysis. *SoCC* 2021.
* Nygard, M. *Release It!* Pragmatic Bookshelf, 2007.
* Rzadca, K. et al. Autopilot: workload autoscaling at Google. *EuroSys* 2020.
* Taleb, N. N. *Antifragile: Things That Gain from Disorder.* Random House, 2012.
* The Internet Traffic Archive. NASA-HTTP trace (July 1995). ita.ee.lbl.gov/html/contrib/NASA-HTTP.html
* Google Cloud Platform. Online Boutique (microservices-demo). github.com/GoogleCloudPlatform/microservices-demo
