"""Golden and parity tests (guide B4.5).

1. Golden scores (5, 40, 100, 70, 65) must match exactly.
2. Python aggregates over 10 seeds must be within tolerance of the published reference table
   (within 3 pp on fraud stopped, within about 10% on friction).
3. If node and reference/upi_reference_core.js are present, the JS reference is run through
   tools/run_reference.js and compared with Python on the same seeds.
"""

import time

import pytest

from policy.engine import score, tier_for
from tools import parity
from tools.parity import GOLDEN_TX, PARITY_TARGETS


@pytest.mark.parametrize("name", list(GOLDEN_TX))
def test_golden_scores_exact(cfg, name):
    tx, want_score, want_tier = GOLDEN_TX[name]
    s, breakdown = score(tx, cfg.policy.weights.model_dump())
    assert s == want_score
    assert tier_for(s, cfg.policy.thresholds, 0) == want_tier
    assert all(points > 0 for _, points in breakdown)


def test_golden_screenshare_breakdown(cfg):
    _, breakdown = score(GOLDEN_TX["D_screenshare"][0], cfg.policy.weights.model_dump())
    assert breakdown == [("new_payee", 15), ("amt_over_10x", 25), ("remote", 30)]


def test_vectorised_engine_matches_single_score(cfg):
    from sim.generate import generate

    legit, attacks = generate(cfg, 7, n_users=50, payments_per_user=10, attacks_per_type=50)
    pol = parity.policies(cfg)["engine"]
    w = cfg.policy.weights.model_dump()
    for df in (legit, attacks):
        tiers = pol(df)
        for i, row in df.iterrows():
            s, _ = score(row.to_dict(), w)
            assert tiers[i] == tier_for(s, cfg.policy.thresholds, 0)


def test_engine_is_fast_enough(cfg):
    import pandas as pd

    from sim.generate import generate

    legit, attacks = generate(cfg, 7)
    both = pd.concat([legit, attacks], ignore_index=True)
    assert len(both) == 31_500
    pol = parity.policies(cfg)["engine"]
    best = min(_timed(pol, both) for _ in range(3))
    assert best < 0.2, f"scoring 31,500 payments took {best:.3f}s"


def _timed(fn, arg) -> float:
    t = time.perf_counter()
    fn(arg)
    return time.perf_counter() - t


@pytest.fixture(scope="module")
def python_aggs(cfg):
    return parity.python_aggregates(cfg, cfg.seeds.parity)


def test_python_matches_reference_table(python_aggs):
    fails = parity.within_tolerance(python_aggs, PARITY_TARGETS)
    assert not fails, "parity outside tolerance:\n" + "\n".join(fails)


def test_extra_checks_close_to_reference(python_aggs):
    for name, tgt in PARITY_TARGETS.items():
        tol = max(3.0, 0.10 * tgt["extra"])
        assert abs(python_aggs[name]["extra"] - tgt["extra"]) <= tol, name


def test_run_reference_harness_with_mock(cfg):
    """Checks the Node harness plumbing with a toy reference (not a parity check)."""
    import json
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    mock = parity.ROOT / "tests" / "fixtures" / "mock_reference.js"
    proc = subprocess.run(
        ["node", str(parity.RUNNER_JS), "--reference", str(mock), "--seeds", "1,2",
         "--config-json", "-"],
        input=json.dumps(cfg.to_dict()), capture_output=True, text=True, timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["seeds"] == [1, 2]
    assert {k: v["score"] for k, v in out["golden"].items()} == {
        k: g[1] for k, g in GOLDEN_TX.items()}
    assert set(out["aggregates"]) == {"pin_only", "engine"}


def test_run_reference_reports_missing_functions(tmp_path):
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    bad = tmp_path / "empty.js"
    bad.write_text("function unrelated() {}\n", encoding="utf-8")
    proc = subprocess.run(["node", str(parity.RUNNER_JS), "--reference", str(bad)],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 2
    assert "could not find score" in proc.stderr and "unrelated" in proc.stderr


def test_reference_js_parity(cfg, python_aggs):
    why = parity.reference_available()
    if why:
        pytest.skip(f"JS reference not runnable: {why}")
    ref = parity.run_reference(cfg, cfg.seeds.parity)
    for name, (_, want_score, want_tier) in GOLDEN_TX.items():
        assert ref["golden"][name] == {"score": want_score, "tier": want_tier}
    fails = parity.within_tolerance(python_aggs, {
        k: v for k, v in ref["aggregates"].items() if k in PARITY_TARGETS
    })
    assert not fails, "Python vs JS reference:\n" + "\n".join(fails)
