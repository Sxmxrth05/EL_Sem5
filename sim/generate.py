"""Synthetic payment generator.

INTERIM STAND-IN (owner: Person 1). Written by Person 4 from guide section B1 so the service's
/simulate endpoint and the parity tests can run. Person 1's version (with sim/rng.py,
sim/attacks.py, the immutable lists and the learned attacker) replaces this file; the
interface must stay:

    generate(cfg, seed) -> (legit_df, attacks_df)   # both in the A3 format
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common.config import ATTACK_TYPES, Config

COLUMNS = [
    "kind", "attack_type", "median", "ratio", "amount", "new_device", "network", "new_payee",
    "velocity", "sim_change", "remote", "far_location", "fri", "u",
]
_NETS = np.array(["home", "mobile", "public"], dtype=object)


def _network(rng: np.random.Generator, mix, n: int) -> np.ndarray:
    return _NETS[rng.choice(3, size=n, p=[mix.home, mix.mobile, mix.public])]


def _frame(**cols) -> pd.DataFrame:
    df = pd.DataFrame(cols)[COLUMNS]
    df["median"] = df["median"].astype(int)
    df["amount"] = df["amount"].astype(int)
    df["fri"] = df["fri"].astype(int)
    return df


def generate(
    cfg: Config, seed: int, n_users: int | None = None, payments_per_user: int | None = None,
    attacks_per_type: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (legit_df, attacks_df), both in the A3 format. Sizes default to cfg.sizes."""
    g = cfg.generator
    lp = g.legit
    n_users = cfg.sizes.n_users if n_users is None else n_users
    per_user = cfg.sizes.payments_per_user if payments_per_user is None else payments_per_user
    n_att = cfg.sizes.attacks_per_type if attacks_per_type is None else attacks_per_type
    rng = np.random.default_rng(seed)

    # --- genuine payments
    user_median = np.clip(
        np.round(rng.lognormal(np.log(lp.median_center), lp.median_sigma, n_users)),
        lp.median_min, lp.median_max,
    ).astype(int)
    n = n_users * per_user
    median = np.repeat(user_median, per_user)
    big = rng.random(n) < lp.big_payment_prob
    ratio = np.where(
        big,
        rng.uniform(lp.big_ratio_low, lp.big_ratio_high, n),
        np.exp(lp.ratio_sigma * rng.standard_normal(n)),
    )
    true_sim = rng.random(n) < lp.true_sim_change
    true_remote = rng.random(n) < lp.true_remote
    legit = dict(
        kind="legit", attack_type=None, median=median, ratio=ratio,
        amount=np.round(median * ratio),
        new_device=rng.random(n) < lp.new_device,
        network=_network(rng, lp.network, n),
        new_payee=rng.random(n) < lp.new_payee,
        velocity=rng.random(n) < lp.velocity,
        sim_change=true_sim & (rng.random(n) < g.coverage.sim_change),
        remote=true_remote & (rng.random(n) < g.coverage.remote),
        far_location=rng.random(n) < lp.far_location,
        fri=rng.choice(4, size=n, p=lp.fri),
    )
    legit["u"] = rng.random(n)  # drawn last
    legit_df = _frame(**legit)

    # --- attacks
    parts = []
    for t in ATTACK_TYPES:
        ap = getattr(g.attacks, t)
        m = n_att
        med = user_median[rng.integers(0, n_users, m)]
        r = np.maximum(
            g.min_attack_ratio,
            np.exp(np.log(ap.ratio_center) + ap.ratio_sigma * rng.standard_normal(m)),
        )
        cols = dict(
            kind="attack", attack_type=t, median=med, ratio=r,
            new_device=rng.random(m) < ap.new_device,
            network=_network(rng, ap.network, m),
            new_payee=rng.random(m) < ap.new_payee,
            velocity=rng.random(m) < ap.velocity,
            sim_change=(rng.random(m) < ap.true_sim_change)
            & (rng.random(m) < g.coverage.sim_change),
            remote=(rng.random(m) < ap.true_remote) & (rng.random(m) < g.coverage.remote),
            far_location=rng.random(m) < ap.far_location,
            fri=rng.choice(4, size=m, p=ap.fri),
        )
        if g.evasive:
            ep = g.evasive_params
            normal = rng.random(m) < ep.normal_amount_prob
            cols["ratio"] = np.where(normal, 1 + rng.random(m), cols["ratio"])
            cols["velocity"] = np.zeros(m, dtype=bool)
            cols["network"] = np.where(cols["network"] == "public", "mobile", cols["network"])
            cols["fri"] = np.where(rng.random(m) < ep.fri_zero_prob, 0, cols["fri"])
        cols["amount"] = np.round(med * cols["ratio"])
        cols["u"] = rng.random(m)  # drawn last
        parts.append(_frame(**cols))
    attacks_df = pd.concat(parts, ignore_index=True)
    return legit_df, attacks_df
