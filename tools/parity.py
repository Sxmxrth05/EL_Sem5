"""Python side of the parity harness: golden scores and aggregate results per policy.

    python -m tools.parity --seeds 1 2 3 4 5 6 7 8 9 10
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

from baselines import get_baselines
from common.config import Config, load_config
from eval.metrics import evaluate
from policy.engine import make_engine, score, tier_for

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_JS = ROOT / "reference" / "upi_reference_core.js"
RUNNER_JS = ROOT / "tools" / "run_reference.js"

# Published reference numbers (guide B4.5): seed 7, default config, shift 0.
PARITY_TARGETS = {
    "pin_only": {"prevented_pct": 0.0, "friction": 0, "extra": 0},
    "always_strict": {"prevented_pct": 20.2, "friction": 1000, "extra": 1000},
    "amount_rule": {"prevented_pct": 13.9, "friction": 27, "extra": 27},
    "fri_only": {"prevented_pct": 14.6, "friction": 23, "extra": 25},
    "engine": {"prevented_pct": 79.2, "friction": 195, "extra": 169},
}
PCT_TOL = 3.0        # percentage points on fraud stopped
FRICTION_REL = 0.10  # relative tolerance on friction
FRICTION_ABS = 3.0   # absolute floor so near-zero values do not fail on noise

# Golden cases in A3 form (guide B2). ratio given directly.
_OFF = dict(new_device=False, new_payee=False, velocity=False, sim_change=False,
            remote=False, far_location=False, fri=0)
GOLDEN_TX = {
    "A_chai": ({**_OFF, "network": "mobile", "ratio": 0.72}, 5, "0"),
    "B_rent": ({**_OFF, "network": "home", "new_payee": True, "ratio": 180}, 40, "1"),
    "C_simswap": ({**_OFF, "network": "public", "new_device": True, "new_payee": True,
                   "sim_change": True, "ratio": 180}, 100, "3"),
    "D_screenshare": ({**_OFF, "network": "home", "remote": True, "new_payee": True,
                       "ratio": 180}, 70, "3"),
    "E_stolen": ({**_OFF, "network": "home", "new_payee": True, "velocity": True,
                  "far_location": True, "ratio": 180}, 65, "2"),
}


def policies(cfg: Config) -> dict:
    p = cfg.policy
    out = dict(get_baselines(cfg))
    out["engine"] = make_engine(p.weights.model_dump(), p.shift, p.thresholds, p.score_cap,
                                (p.amount_bands.mid_low, p.amount_bands.mid_high))
    return out


def python_golden(cfg: Config) -> dict[str, dict]:
    p = cfg.policy
    w = p.weights.model_dump()
    bands = (p.amount_bands.mid_low, p.amount_bands.mid_high)
    out = {}
    for name, (tx, _, _) in GOLDEN_TX.items():
        s, _ = score(tx, w, p.score_cap, bands)
        out[name] = {"score": s, "tier": tier_for(s, p.thresholds, 0)}
    return out


def python_aggregates(cfg: Config, seeds: list[int]) -> dict[str, dict]:
    """Mean of each metric over seeds, per policy."""
    from sim.generate import generate

    pols = policies(cfg)
    acc: dict[str, dict[str, list[float]]] = {k: {} for k in pols}
    for seed in seeds:
        legit, attacks = generate(cfg, seed)
        for name, pol in pols.items():
            m = evaluate(pol, legit, attacks, cfg.costs)
            for key in ("prevented_pct", "friction", "extra"):
                acc[name].setdefault(key, []).append(m[key])
    return {
        name: {k: float(np.mean(v)) for k, v in d.items()}
        | {f"{k}_sd": float(np.std(v)) for k, v in d.items()}
        for name, d in acc.items()
    }


def within_tolerance(got: dict, want: dict) -> list[str]:
    """Returns a list of human-readable failures (empty means parity holds)."""
    fails = []
    for name, tgt in want.items():
        g = got[name]
        if abs(g["prevented_pct"] - tgt["prevented_pct"]) > PCT_TOL:
            fails.append(f"{name}: fraud stopped {g['prevented_pct']:.1f}% vs "
                         f"{tgt['prevented_pct']}% (tol {PCT_TOL} pp)")
        tol = max(FRICTION_ABS, FRICTION_REL * tgt["friction"])
        if abs(g["friction"] - tgt["friction"]) > tol:
            fails.append(f"{name}: friction {g['friction']:.1f} vs {tgt['friction']} "
                         f"(tol {tol:.1f})")
    return fails


def reference_available() -> str | None:
    """None if the JS reference can be run, else the reason it cannot."""
    if shutil.which("node") is None:
        return "node is not installed"
    if not REFERENCE_JS.is_file():
        return f"{REFERENCE_JS.relative_to(ROOT)} is not in the repository"
    return None


def run_reference(cfg: Config, seeds: list[int], timeout: float = 300) -> dict:
    """Runs tools/run_reference.js and returns its parsed JSON output."""
    proc = subprocess.run(
        ["node", str(RUNNER_JS), "--reference", str(REFERENCE_JS),
         "--seeds", ",".join(map(str, seeds)), "--config-json", "-"],
        input=json.dumps(cfg.to_dict()), capture_output=True, text=True, timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"run_reference.js failed ({proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "configs" / "default.yaml"))
    ap.add_argument("--seeds", type=int, nargs="+")
    args = ap.parse_args()
    cfg = load_config(args.config)
    seeds = args.seeds or cfg.seeds.parity
    report = {
        "seeds": seeds,
        "golden": python_golden(cfg),
        "python": python_aggregates(cfg, seeds),
        "targets": PARITY_TARGETS,
    }
    report["failures_vs_targets"] = within_tolerance(report["python"], PARITY_TARGETS)
    why = reference_available()
    report["reference"] = run_reference(cfg, seeds) if why is None else {"skipped": why}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
