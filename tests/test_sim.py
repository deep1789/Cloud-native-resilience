import numpy as np

from arcsim import controllers as C, run, topology as T, workload as W
from arcsim.sim import CloudSim, Fault, Actions


def _load(n=400, rps=300.0):
    return np.full(n, rps)


def test_trace_window_shape_and_scale():
    w = W.window(0)
    assert len(w) == 1440
    assert abs(np.percentile(w, 95) - 600.0) < 1e-6
    assert (w >= 0).all()


def test_topology_is_a_dag_in_topo_order():
    pos = {s: k for k, s in enumerate(T.TOPO)}
    for j in range(T.N):
        for c in range(T.N):
            if T.W[j, c] > 0:
                assert pos[j] < pos[c]


def test_deterministic_given_seed():
    a = run.run_episode("ARC", "storm", 7)
    b = run.run_episode("ARC", "storm", 7)
    assert a == {k: b[k] for k in a} or all(
        (np.isnan(a[k]) and np.isnan(b[k])) or a[k] == b[k] for k in a)


def test_healthy_cluster_serves_everything():
    sim = CloudSim(_load(), [], seed=1)
    sim.initial_obs()
    for _ in range(300):
        sim.step(None)
    assert min(sim.log["avail"]) > 0.99


def test_quota_never_exceeded_and_all_services_keep_a_pod():
    sim = CloudSim(_load(300, 3000.0), [], seed=1)
    ctrl = C.make("HPA", 1)
    obs = sim.initial_obs()
    for _ in range(299):
        obs = sim.step(ctrl.step(obs))
        assert obs.total_pods <= T.QUOTA
        assert (obs.ready >= 1).all()


def test_pod_kill_is_replaced_by_replicaset():
    sim = CloudSim(_load(200), [Fault("kill", 20, svc=1, mag=0.7)], seed=1)
    sim.initial_obs()
    before = sim.ready[1]
    for _ in range(21):
        sim.step(None)
    assert sim.ready[1] < before
    for _ in range(40):
        sim.step(None)
    assert sim.ready[1] == before


def test_controller_actions_are_within_bounds():
    for name in C.ALL + C.ABLATIONS:
        _, sim, _ = run.run_episode(name, "storm", 3, keep=True)
        assert (sim.desired >= T.MIN_REP).all() and (sim.desired <= T.MAX_REP).all()


def test_breaker_removes_noncritical_load():
    sim = CloudSim(_load(60), [], seed=1)
    sim.initial_obs()
    sim.step(None)
    base = sim.offered[T.IDX["recommendation"]]
    a = Actions(breaker={(0, T.IDX["recommendation"])})
    sim.step(a)
    assert sim.offered[T.IDX["recommendation"]] < 0.05 * base + 1e-9
