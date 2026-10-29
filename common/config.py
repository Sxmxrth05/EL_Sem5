"""Typed, validated configuration shared by every module (owner: Person 4).

Usage:
    cfg = load_config("configs/default.yaml")
    cfg = load_config("configs/default.yaml", override_files=["configs/sens_x2.yaml"])
    cfg = load_config("configs/default.yaml", overrides={"costs.friction.2": 10})

An override file may also name its base with a top-level ``extends: default.yaml`` key,
so ``load_config("configs/sens_x2.yaml")`` works on its own.

Unknown keys and missing keys are rejected with a message naming the full key path.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

TIERS = ("0", "W", "1", "2", "3")
ENGINE_TIERS = ("0", "1", "2", "3")
ATTACK_TYPES = ("simswap", "screenshare", "collect", "replay", "stolen")
NETWORKS = ("home", "mobile", "public")
WEIGHT_KEYS = (
    "new_device", "net_mobile", "net_public", "new_payee", "amt_2x_10x", "amt_over_10x",
    "velocity", "sim_change", "remote", "far_location", "fri_1", "fri_2", "fri_3",
)

_PROB_TOL = 1e-6


class ConfigError(ValueError):
    """Raised when a config file is missing keys, has unknown keys, or has bad values."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


Prob = Field(ge=0.0, le=1.0)


def _check_dist(values: Iterable[float], what: str) -> None:
    total = sum(values)
    if abs(total - 1.0) > _PROB_TOL:
        raise ValueError(f"{what} probabilities must sum to 1, got {total:.6f}")


# ----------------------------------------------------------------- generator


class NetworkMix(_Strict):
    home: float = Prob
    mobile: float = Prob
    public: float = Prob

    @model_validator(mode="after")
    def _sums_to_one(self) -> NetworkMix:
        _check_dist((self.home, self.mobile, self.public), "network")
        return self


def _fri_dist(v: list[float]) -> list[float]:
    if len(v) != 4:
        raise ValueError("fri must list 4 probabilities (levels 0, 1, 2, 3)")
    if any(p < 0 or p > 1 for p in v):
        raise ValueError("fri probabilities must be in [0, 1]")
    _check_dist(v, "fri")
    return v


class LegitParams(_Strict):
    median_center: float = Field(gt=0)
    median_sigma: float = Field(gt=0)
    median_min: int = Field(gt=0)
    median_max: int = Field(gt=0)
    big_payment_prob: float = Prob
    big_ratio_low: float = Field(gt=0)
    big_ratio_high: float = Field(gt=0)
    ratio_sigma: float = Field(gt=0)
    new_device: float = Prob
    network: NetworkMix
    new_payee: float = Prob
    velocity: float = Prob
    far_location: float = Prob
    true_sim_change: float = Prob
    true_remote: float = Prob
    fri: list[float]

    check_fri = field_validator("fri")(_fri_dist)

    @model_validator(mode="after")
    def _ranges(self) -> LegitParams:
        if self.median_min > self.median_max:
            raise ValueError("median_min must not exceed median_max")
        if self.big_ratio_low > self.big_ratio_high:
            raise ValueError("big_ratio_low must not exceed big_ratio_high")
        return self


class AttackParams(_Strict):
    new_device: float = Prob
    network: NetworkMix
    new_payee: float = Prob
    ratio_center: float = Field(gt=0)
    ratio_sigma: float = Field(ge=0)
    velocity: float = Prob
    true_sim_change: float = Prob
    true_remote: float = Prob
    far_location: float = Prob
    fri: list[float]

    check_fri = field_validator("fri")(_fri_dist)


class AttackTable(_Strict):
    simswap: AttackParams
    screenshare: AttackParams
    collect: AttackParams
    replay: AttackParams
    stolen: AttackParams


class Coverage(_Strict):
    sim_change: float = Prob
    remote: float = Prob


class EvasiveParams(_Strict):
    normal_amount_prob: float = Prob
    fri_zero_prob: float = Prob


class GeneratorConfig(_Strict):
    evasive: bool
    legit: LegitParams
    coverage: Coverage
    min_attack_ratio: float = Field(gt=0)
    attacks: AttackTable
    evasive_params: EvasiveParams


# ----------------------------------------------------------------- policy


class AmountBands(_Strict):
    mid_low: float = Field(gt=0)
    mid_high: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> AmountBands:
        if self.mid_low >= self.mid_high:
            raise ValueError("amount_bands.mid_low must be below mid_high")
        return self


class Weights(_Strict):
    new_device: int = Field(ge=0, le=100)
    net_mobile: int = Field(ge=0, le=100)
    net_public: int = Field(ge=0, le=100)
    new_payee: int = Field(ge=0, le=100)
    amt_2x_10x: int = Field(ge=0, le=100)
    amt_over_10x: int = Field(ge=0, le=100)
    velocity: int = Field(ge=0, le=100)
    sim_change: int = Field(ge=0, le=100)
    remote: int = Field(ge=0, le=100)
    far_location: int = Field(ge=0, le=100)
    fri_1: int = Field(ge=0, le=100)
    fri_2: int = Field(ge=0, le=100)
    fri_3: int = Field(ge=0, le=100)


def check_thresholds(v: list[int]) -> list[int]:
    if len(v) != 3:
        raise ValueError("thresholds must list exactly 3 values")
    if not (v[0] < v[1] < v[2]):
        raise ValueError(f"thresholds must be strictly increasing, got {v}")
    return v


class PolicyConfig(_Strict):
    score_cap: int = Field(gt=0, le=1000)
    amount_bands: AmountBands
    weights: Weights
    thresholds: list[int]
    shift: int = Field(ge=-100, le=100)

    check_thr = field_validator("thresholds")(check_thresholds)


# ----------------------------------------------------------------- costs


class TierValues(_Strict):
    t0: float = Field(alias="0", ge=0)
    W: float = Field(ge=0)
    t1: float = Field(alias="1", ge=0)
    t2: float = Field(alias="2", ge=0)
    t3: float = Field(alias="3", ge=0)

    def as_dict(self) -> dict[str, float]:
        return self.model_dump(by_alias=True)


class StopChances(_Strict):
    simswap: float = Prob
    screenshare: float = Prob
    collect: float = Prob
    replay: float = Prob
    stolen: float = Prob


class StopTable(_Strict):
    t0: StopChances = Field(alias="0")
    W: StopChances
    t1: StopChances = Field(alias="1")
    t2: StopChances = Field(alias="2")
    t3: StopChances = Field(alias="3")

    def as_dict(self) -> dict[str, dict[str, float]]:
        return self.model_dump(by_alias=True)


class CostConfig(_Strict):
    friction: TierValues
    abandon: TierValues
    p_stop: StopTable

    @model_validator(mode="after")
    def _abandon_is_prob(self) -> CostConfig:
        for tier, p in self.abandon.as_dict().items():
            if p > 1:
                raise ValueError(f"abandon[{tier}] must be a probability, got {p}")
        return self


class BaselineConfig(_Strict):
    amount_rule_threshold: float = Field(ge=0)
    fri_block_level: int = Field(ge=0, le=3)
    fri_warn_level: int = Field(ge=0, le=3)


class SweepRange(_Strict):
    start: int
    stop: int
    step: int = Field(gt=0)

    def shifts(self) -> list[int]:
        return list(range(self.start, self.stop + 1, self.step))


class ExperimentConfig(_Strict):
    sweep_shifts: SweepRange
    sensitivity_factors: list[float]


# ----------------------------------------------------------------- service


class SimulateLimits(_Strict):
    max_users: int = Field(gt=0)
    max_payments_per_user: int = Field(gt=0)
    max_attacks_per_type: int = Field(gt=0)


class TierNames(_Strict):
    t0: str = Field(alias="0")
    W: str
    t1: str = Field(alias="1")
    t2: str = Field(alias="2")
    t3: str = Field(alias="3")

    def as_dict(self) -> dict[str, str]:
        return self.model_dump(by_alias=True)


class ServiceConfig(_Strict):
    decision_log_path: str
    max_batch: int = Field(gt=0)
    max_decisions_limit: int = Field(gt=0)
    max_amount: float = Field(gt=0)
    simulate_limits: SimulateLimits
    strictness_range: list[int]
    tier_names: TierNames


# ----------------------------------------------------------------- top level


class Sizes(_Strict):
    n_users: int = Field(gt=0)
    payments_per_user: int = Field(gt=0)
    attacks_per_type: int = Field(ge=0)


class Seeds(_Strict):
    demo: int
    tune: list[int]
    report: list[int]
    parity: list[int]


class Config(_Strict):
    version: int
    sizes: Sizes
    seeds: Seeds
    generator: GeneratorConfig
    policy: PolicyConfig
    costs: CostConfig
    baselines: BaselineConfig
    experiment: ExperimentConfig
    service: ServiceConfig

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)

    def with_overrides(self, overrides: Mapping[str, Any]) -> Config:
        """Return a new validated Config with nested or dotted-key overrides applied."""
        return _validate(_apply_overrides(self.to_dict(), overrides), source="<overrides>")

    def fingerprint(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]


# ----------------------------------------------------------------- loading


def _stringify_keys(obj: Any) -> Any:
    """YAML turns `0:` into an int key. Normalise all mapping keys to strings."""
    if isinstance(obj, dict):
        return {str(k): _stringify_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_keys(v) for v in obj]
    return obj


def _deep_merge(base: dict, extra: Mapping) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _apply_overrides(base: dict, overrides: Mapping[str, Any]) -> dict:
    """Overrides may be nested dicts, dotted keys ("costs.friction.2"), or a mix."""
    nested: dict[str, Any] = {}
    for key, value in _stringify_keys(dict(overrides)).items():
        node = nested
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return _deep_merge(base, nested)


def _read_yaml(path: Path, _seen: tuple[Path, ...] = ()) -> dict:
    path = path.resolve()
    if path in _seen:
        raise ConfigError(f"{path}: circular 'extends' chain")
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    data = _stringify_keys(data)
    parent = data.pop("extends", None)
    if parent is not None:
        base = _read_yaml(path.parent / str(parent), (*_seen, path))
        data = _deep_merge(base, data)
    return data


def _format_errors(err: ValidationError, source: str) -> str:
    lines = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        kind = e["type"]
        if kind == "missing":
            lines.append(f"missing key '{loc}'")
        elif kind == "extra_forbidden":
            lines.append(f"unknown key '{loc}'")
        else:
            where = f"'{loc}'" if loc else "(top level)"
            lines.append(f"bad value at {where}: {e['msg']}")
    return f"{source}: invalid config\n  - " + "\n  - ".join(lines)


def _validate(data: dict, source: str) -> Config:
    try:
        return Config.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_format_errors(e, source)) from None


def load_config(
    path: str | Path,
    override_files: Iterable[str | Path] = (),
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """Loads and validates a YAML config. Raises ConfigError on unknown or missing keys.

    Later override files win over earlier ones; the ``overrides`` dict is applied last.
    """
    data = _read_yaml(Path(path))
    for f in override_files:
        data = _deep_merge(data, _read_yaml(Path(f)))
    if overrides:
        data = _apply_overrides(data, overrides)
    return _validate(data, source=str(path))
