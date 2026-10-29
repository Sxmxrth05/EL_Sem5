"""Comparison policies.

INTERIM STAND-IN (owner: Person 3). Written by Person 4 from guide section B3 so /compare and
/simulate work. Person 3's version (with baselines/simple.py and the optional ML baseline)
replaces this; the interface must stay:  get_baselines(cfg) -> dict[str, Policy]
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common.config import Config
from policy.engine import Policy


def get_baselines(cfg: Config) -> dict[str, Policy]:
    """Keys: pin_only, always_strict, amount_rule, fri_only."""
    b = cfg.baselines

    def pin_only(df: pd.DataFrame) -> pd.Series:
        return pd.Series("0", index=df.index, dtype=object)

    def always_strict(df: pd.DataFrame) -> pd.Series:
        return pd.Series("1", index=df.index, dtype=object)

    def amount_rule(df: pd.DataFrame) -> pd.Series:
        hit = df["amount"].to_numpy() > b.amount_rule_threshold
        return pd.Series(np.where(hit, "1", "0").astype(object), index=df.index)

    def fri_only(df: pd.DataFrame) -> pd.Series:
        fri = df["fri"].to_numpy()
        out = np.where(
            fri >= b.fri_block_level, "3", np.where(fri >= b.fri_warn_level, "W", "0")
        )
        return pd.Series(out.astype(object), index=df.index)

    return {
        "pin_only": pin_only,
        "always_strict": always_strict,
        "amount_rule": amount_rule,
        "fri_only": fri_only,
    }
