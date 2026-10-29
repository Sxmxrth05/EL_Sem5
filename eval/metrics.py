"""Evaluation metrics.

INTERIM STAND-IN (owner: Person 3). Written by Person 4 from guide section B3 so /simulate and
the parity tests work. Person 3's version (with ablation, sensitivity, plots and
run_experiment) replaces this; evaluate() and sweep() must keep these signatures.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from common.config import ATTACK_TYPES, TIERS, CostConfig
from policy.engine import DEFAULT_THRESHOLDS, Policy, make_engine


def evaluate(policy: Policy, legit: pd.DataFrame, attacks: pd.DataFrame,
             assume: CostConfig) -> dict:
    friction = assume.friction.as_dict()
    abandon = assume.abandon.as_dict()
    p_stop = assume.p_stop.as_dict()

    tier_idx = {t: i for i, t in enumerate(TIERS)}
    type_idx = {t: i for i, t in enumerate(ATTACK_TYPES)}
    fr_tab = np.array([friction[t] for t in TIERS])
    ab_tab = np.array([abandon[t] for t in TIERS])
    ps_tab = np.array([[p_stop[t][a] for a in ATTACK_TYPES] for t in TIERS])

    lt = policy(legit).to_numpy()
    n = len(legit)
    per_k = 1000.0 / n if n else 0.0
    li = pd.Series(lt).map(tier_idx).to_numpy(dtype=int)
    fr = fr_tab[li]
    ab = ab_tab[li]

    at = policy(attacks).to_numpy()
    types = attacks["attack_type"].to_numpy()
    ai = pd.Series(at).map(tier_idx).to_numpy(dtype=int)
    ps = ps_tab[ai, pd.Series(types).map(type_idx).to_numpy(dtype=int)]
    stopped = attacks["u"].to_numpy() < ps
    amt = attacks["amount"].to_numpy(dtype=float)
    total = amt.sum()
    prevented = amt[stopped].sum()

    return {
        "friction": float(fr.sum() * per_k),
        "extra": float((lt != "0").sum() * per_k),
        "abandoned": float((legit["u"].to_numpy() < ab).sum() * per_k),
        "holds": float((lt == "2").sum() * per_k),
        "blocks": float((lt == "3").sum() * per_k),
        "prevented_pct": float(100.0 * prevented / total) if total else 0.0,
        "lost": float(total - prevented),
        "per_type_stopped": {
            t: float(stopped[types == t].mean()) if (types == t).any() else 0.0
            for t in ATTACK_TYPES
        },
    }


def sweep(weights: Mapping[str, int], legit: pd.DataFrame, attacks: pd.DataFrame,
          assume: CostConfig, shifts: Iterable[int],
          thresholds: Sequence[int] = DEFAULT_THRESHOLDS) -> list[dict]:
    out = []
    for d in shifts:
        m = evaluate(make_engine(weights, d, thresholds), legit, attacks, assume)
        out.append({"shift": int(d), **m})
    return out
