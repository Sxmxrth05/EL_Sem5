"""Request and response models for the decision service."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

Network = Literal["home", "mobile", "public"]
Tier = Literal["0", "W", "1", "2", "3"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Payment(_In):
    """One payment, A3 fields without the labels (kind, attack_type) and without u."""

    amount: float = Field(gt=0, description="rupees")
    median: float = Field(gt=0, description="the user's usual payment in rupees")
    network: Network
    new_device: StrictBool
    new_payee: StrictBool
    velocity: StrictBool
    sim_change: StrictBool
    remote: StrictBool
    far_location: StrictBool
    fri: StrictInt = Field(ge=0, le=3)

    def to_record(self) -> dict:
        d = self.model_dump()
        d["ratio"] = self.amount / self.median
        return d


class SignalPoints(BaseModel):
    signal: str
    points: int


class ScoreResponse(BaseModel):
    score: int
    raw_total: int
    tier: Tier
    tier_name: str
    breakdown: list[SignalPoints]
    thresholds: list[int]
    policy_version: str


class BatchRequest(_In):
    payments: list[Payment] = Field(min_length=1)


class BatchResponse(BaseModel):
    results: list[ScoreResponse]
    policy_version: str


class CompareResponse(BaseModel):
    engine: ScoreResponse
    baselines: dict[str, Tier]
    tier_names: dict[str, str]


class PolicyView(BaseModel):
    weights: dict[str, int]
    base_thresholds: list[int]
    thresholds: list[int]
    shift: int
    score_cap: int
    policy_version: str
    strictness_range: list[int]


class PolicyUpdate(_In):
    """Partial update. Unknown weight names and out-of-range values are rejected."""

    weights: dict[str, StrictInt] | None = None
    shift: StrictInt | None = Field(default=None, ge=-100, le=100)
    thresholds: list[StrictInt] | None = None
    reset: StrictBool = False

    @model_validator(mode="after")
    def _not_empty(self) -> PolicyUpdate:
        if not self.reset and self.weights is None and self.shift is None \
                and self.thresholds is None:
            raise ValueError("give at least one of: weights, shift, thresholds, reset")
        return self


class SimulateRequest(_In):
    seed: StrictInt = 7
    n_users: StrictInt = Field(default=200, gt=0)
    payments_per_user: StrictInt = Field(default=30, gt=0)
    attacks_per_type: StrictInt = Field(default=100, gt=0)
    evasive: StrictBool = False
    shift_start: StrictInt = -30
    shift_stop: StrictInt = 60
    shift_step: StrictInt = Field(default=3, gt=0)

    @model_validator(mode="after")
    def _range(self) -> SimulateRequest:
        if self.shift_start > self.shift_stop:
            raise ValueError("shift_start must not exceed shift_stop")
        if (self.shift_stop - self.shift_start) // self.shift_step > 200:
            raise ValueError("too many sweep points (max 200)")
        return self


class CurvePoint(BaseModel):
    shift: int
    friction: float
    prevented_pct: float
    extra: float


class BaselinePoint(BaseModel):
    name: str
    friction: float
    prevented_pct: float
    extra: float


class SimulateResponse(BaseModel):
    curve: list[CurvePoint]
    baselines: list[BaselinePoint]
    current: CurvePoint
    n_legit: int
    n_attacks: int
    seed: int
    evasive: bool
    policy_version: str
    elapsed_ms: float


class HealthResponse(BaseModel):
    status: str
    policy_version: str
    config_fingerprint: str
