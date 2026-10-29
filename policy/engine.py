"""Explainable scoring engine.

INTERIM STAND-IN (owner: Person 2). Written by Person 4 directly from guide section B2 so the
decision service, parity harness and CI can run before Person 2's module lands. Person 2's
version replaces this file; the public interface (A5) must stay the same:

    SIGNAL_GROUPS, score(tx, weights), make_engine(weights, shift=0)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

Policy = Callable[[pd.DataFrame], pd.Series]

# Base values from guide B2; configs/default.yaml (policy.*) is the source of truth and the
# service always passes these explicitly.
DEFAULT_THRESHOLDS = (20, 50, 70)
DEFAULT_CAP = 100
DEFAULT_BANDS = (2.0, 10.0)

SIGNAL_GROUPS: dict[str, list[str]] = {
    "device": ["new_device"],
    "network": ["net_mobile", "net_public"],
    "payee": ["new_payee"],
    "amount": ["amt_2x_10x", "amt_over_10x"],
    "velocity": ["velocity"],
    "sim": ["sim_change"],
    "remote": ["remote"],
    "location": ["far_location"],
    "fri": ["fri_1", "fri_2", "fri_3"],
}


def _active_signals(tx: Mapping, bands: Sequence[float]) -> list[str]:
    lo, hi = bands
    on: list[str] = []
    if tx.get("new_device"):
        on.append("new_device")
    net = tx.get("network")
    if net == "mobile":
        on.append("net_mobile")
    elif net == "public":
        on.append("net_public")
    if tx.get("new_payee"):
        on.append("new_payee")
    ratio = float(tx["ratio"])
    if lo <= ratio <= hi:
        on.append("amt_2x_10x")
    elif ratio > hi:
        on.append("amt_over_10x")
    for flag in ("velocity", "sim_change", "remote", "far_location"):
        if tx.get(flag):
            on.append(flag)
    fri = int(tx.get("fri", 0))
    if fri in (1, 2, 3):
        on.append(f"fri_{fri}")
    return on


def score(
    tx: Mapping, weights: Mapping[str, int], cap: int = DEFAULT_CAP,
    bands: Sequence[float] = DEFAULT_BANDS,
) -> tuple[int, list[tuple[str, int]]]:
    """Returns (score 0..cap, [(signal_name, points), ...]) so every decision is explainable."""
    breakdown = [(s, int(weights[s])) for s in _active_signals(tx, bands) if weights[s]]
    total = sum(p for _, p in breakdown)
    return min(total, cap), breakdown


def tier_for(score_value: int, thresholds: Sequence[int], shift: int = 0) -> str:
    t1, t2, t3 = (t + shift for t in thresholds)
    if score_value >= t3:
        return "3"
    if score_value >= t2:
        return "2"
    if score_value >= t1:
        return "1"
    return "0"


def score_frame(
    df: pd.DataFrame, weights: Mapping[str, int], cap: int = DEFAULT_CAP,
    bands: Sequence[float] = DEFAULT_BANDS,
) -> np.ndarray:
    """Vectorised score for a whole A3 DataFrame."""
    lo, hi = bands
    w = weights
    ratio = df["ratio"].to_numpy(dtype=float)
    net = df["network"].to_numpy()
    fri = df["fri"].to_numpy(dtype=int)
    s = (
        w["new_device"] * df["new_device"].to_numpy(dtype=bool)
        + w["net_mobile"] * (net == "mobile")
        + w["net_public"] * (net == "public")
        + w["new_payee"] * df["new_payee"].to_numpy(dtype=bool)
        + w["amt_2x_10x"] * ((ratio >= lo) & (ratio <= hi))
        + w["amt_over_10x"] * (ratio > hi)
        + w["velocity"] * df["velocity"].to_numpy(dtype=bool)
        + w["sim_change"] * df["sim_change"].to_numpy(dtype=bool)
        + w["remote"] * df["remote"].to_numpy(dtype=bool)
        + w["far_location"] * df["far_location"].to_numpy(dtype=bool)
        + w["fri_1"] * (fri == 1)
        + w["fri_2"] * (fri == 2)
        + w["fri_3"] * (fri == 3)
    )
    return np.minimum(s.astype(int), cap)


def make_engine(
    weights: Mapping[str, int], shift: int = 0,
    thresholds: Sequence[int] = DEFAULT_THRESHOLDS, cap: int = DEFAULT_CAP,
    bands: Sequence[float] = DEFAULT_BANDS,
) -> Policy:
    """Returns a vectorised policy: DataFrame of payments -> Series of tier codes."""
    weights = dict(weights)
    edges = np.array([t + shift for t in thresholds])
    codes = np.array(["0", "1", "2", "3"], dtype=object)

    def policy(df: pd.DataFrame) -> pd.Series:
        s = score_frame(df, weights, cap, bands)
        return pd.Series(codes[np.searchsorted(edges, s, side="right")], index=df.index)

    return policy
