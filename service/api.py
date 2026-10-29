"""Decision service: the policy engine behind a FastAPI app, plus the web UI.

Run:  uvicorn service.api:app --port 8000      then open http://127.0.0.1:8000/
Env:  UPI_CONFIG        path to the YAML config (default configs/default.yaml)
      UPI_DECISION_LOG  path to the JSON-lines decision log (default from config)
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from baselines import get_baselines
from common.config import WEIGHT_KEYS, Config, check_thresholds, load_config
from eval.metrics import evaluate, sweep
from policy.engine import make_engine, score, tier_for
from service.decision_log import DecisionLog
from service.schemas import (
    BaselinePoint,
    BatchRequest,
    BatchResponse,
    CompareResponse,
    CurvePoint,
    HealthResponse,
    Payment,
    PolicyUpdate,
    PolicyView,
    ScoreResponse,
    SimulateRequest,
    SimulateResponse,
)

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent / "web"


def policy_version(weights: dict[str, int], thresholds: list[int]) -> str:
    blob = json.dumps({"weights": weights, "thresholds": thresholds}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class PolicyState:
    """Immutable snapshot. Updates swap the whole object, so readers never see half a change."""

    weights: dict[str, int]
    base_thresholds: list[int]
    shift: int
    cap: int
    bands: tuple[float, float]
    version: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", policy_version(self.weights, self.thresholds))

    @property
    def thresholds(self) -> list[int]:
        return [t + self.shift for t in self.base_thresholds]

    @classmethod
    def from_config(cls, cfg: Config) -> PolicyState:
        p = cfg.policy
        return cls(
            weights=p.weights.model_dump(), base_thresholds=list(p.thresholds), shift=p.shift,
            cap=p.score_cap, bands=(p.amount_bands.mid_low, p.amount_bands.mid_high),
        )


class ProcessTimeMiddleware:
    """Pure ASGI middleware adding X-Process-Time-Ms (server-side time, no network)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                ms = f"{(time.perf_counter() - t0) * 1000:.3f}".encode()
                message.setdefault("headers", []).append((b"x-process-time-ms", ms))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def create_app(config_path: str | Path | None = None,
               log_path: str | Path | None = None) -> FastAPI:
    cfg = load_config(_resolve(config_path or os.environ.get("UPI_CONFIG",
                                                             "configs/default.yaml")))
    log = DecisionLog(_resolve(log_path or os.environ.get("UPI_DECISION_LOG",
                                                          cfg.service.decision_log_path)))
    tier_names = cfg.service.tier_names.as_dict()
    baselines = get_baselines(cfg)
    lock = threading.Lock()
    state = {"policy": PolicyState.from_config(cfg)}

    app = FastAPI(title="UPI step-up decision service", version="0.1.0")
    app.add_middleware(ProcessTimeMiddleware)
    app.state.config = cfg
    app.state.log = log

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        summary = "; ".join(f"{e['field'] or 'body'}: {e['message']}" for e in errors)
        return JSONResponse(status_code=422, content={"detail": summary, "errors": errors})

    def current() -> PolicyState:
        return state["policy"]

    def check_amount(p: Payment) -> None:
        if p.amount > cfg.service.max_amount:
            raise HTTPException(422, f"amount: must be at most {cfg.service.max_amount:g}")

    def score_one(p: Payment, pol: PolicyState) -> dict:
        s, breakdown = score(p.to_record(), pol.weights, pol.cap, pol.bands)
        tier = tier_for(s, pol.base_thresholds, pol.shift)
        return {
            "score": s,
            "raw_total": sum(pts for _, pts in breakdown),
            "tier": tier,
            "tier_name": tier_names[tier],
            "breakdown": [{"signal": n, "points": pts} for n, pts in breakdown],
            "thresholds": pol.thresholds,
            "policy_version": pol.version,
        }

    def policy_view(pol: PolicyState) -> PolicyView:
        return PolicyView(
            weights=pol.weights, base_thresholds=pol.base_thresholds,
            thresholds=pol.thresholds, shift=pol.shift, score_cap=pol.cap,
            policy_version=pol.version, strictness_range=cfg.service.strictness_range,
        )

    # ------------------------------------------------------------- endpoints

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", policy_version=current().version,
                              config_fingerprint=cfg.fingerprint())

    @app.get("/policy", response_model=PolicyView)
    def get_policy() -> PolicyView:
        return policy_view(current())

    @app.post("/policy", response_model=PolicyView)
    def update_policy(upd: PolicyUpdate) -> PolicyView:
        with lock:
            pol = PolicyState.from_config(cfg) if upd.reset else current()
            weights = dict(pol.weights)
            if upd.weights:
                unknown = sorted(set(upd.weights) - set(WEIGHT_KEYS))
                if unknown:
                    raise HTTPException(422, f"weights: unknown signal(s) {unknown}; "
                                             f"allowed: {list(WEIGHT_KEYS)}")
                bad = {k: v for k, v in upd.weights.items() if not 0 <= v <= 100}
                if bad:
                    raise HTTPException(422, f"weights: values must be 0..100, got {bad}")
                weights.update(upd.weights)
            base = pol.base_thresholds
            if upd.thresholds is not None:
                try:
                    base = check_thresholds(list(upd.thresholds))
                except ValueError as e:
                    raise HTTPException(422, f"thresholds: {e}") from None
            shift = pol.shift if upd.shift is None else upd.shift
            state["policy"] = PolicyState(weights=weights, base_thresholds=list(base),
                                          shift=shift, cap=pol.cap, bands=pol.bands)
            return policy_view(state["policy"])

    @app.post("/score", response_model=ScoreResponse)
    async def score_payment(p: Payment) -> dict:
        check_amount(p)
        result = score_one(p, current())
        log.append(DecisionLog.entry(p.model_dump(), result))
        return result

    @app.post("/score/batch", response_model=BatchResponse)
    async def score_batch(req: BatchRequest) -> dict:
        if len(req.payments) > cfg.service.max_batch:
            raise HTTPException(422, f"payments: at most {cfg.service.max_batch} per batch")
        for p in req.payments:
            check_amount(p)
        pol = current()
        results = [score_one(p, pol) for p in req.payments]
        log.append_many(DecisionLog.entry(p.model_dump(), r)
                        for p, r in zip(req.payments, results, strict=True))
        return {"results": results, "policy_version": pol.version}

    @app.post("/compare", response_model=CompareResponse)
    async def compare(p: Payment) -> dict:
        check_amount(p)
        pol = current()
        df = pd.DataFrame([p.to_record()])
        return {
            "engine": score_one(p, pol),
            "baselines": {name: str(fn(df).iloc[0]) for name, fn in baselines.items()},
            "tier_names": tier_names,
        }

    @app.post("/simulate", response_model=SimulateResponse)
    def simulate(req: SimulateRequest) -> dict:
        lim = cfg.service.simulate_limits
        for name, value, cap in (
            ("n_users", req.n_users, lim.max_users),
            ("payments_per_user", req.payments_per_user, lim.max_payments_per_user),
            ("attacks_per_type", req.attacks_per_type, lim.max_attacks_per_type),
        ):
            if value > cap:
                raise HTTPException(422, f"{name}: at most {cap} in the service "
                                         f"(run eval.run_experiment for full-size runs)")
        t0 = time.perf_counter()
        pol = current()
        sim_cfg = cfg.with_overrides({"generator.evasive": req.evasive})
        from sim.generate import generate  # local import: Person 1 may swap this module

        legit, attacks = generate(sim_cfg, req.seed, req.n_users, req.payments_per_user,
                                  req.attacks_per_type)
        shifts = list(range(req.shift_start, req.shift_stop + 1, req.shift_step))
        pts = sweep(pol.weights, legit, attacks, cfg.costs, shifts, pol.base_thresholds)
        keep = ("friction", "prevented_pct", "extra")
        curve = [{"shift": r["shift"], **{k: r[k] for k in keep}} for r in pts]
        cur = evaluate(make_engine(pol.weights, pol.shift, pol.base_thresholds),
                       legit, attacks, cfg.costs)
        base_pts = []
        for name, fn in baselines.items():
            m = evaluate(fn, legit, attacks, cfg.costs)
            base_pts.append(BaselinePoint(name=name, **{k: m[k] for k in keep}))
        return {
            "curve": [CurvePoint(**c) for c in curve],
            "baselines": base_pts,
            "current": CurvePoint(shift=pol.shift, **{k: cur[k] for k in keep}),
            "n_legit": len(legit), "n_attacks": len(attacks), "seed": req.seed,
            "evasive": req.evasive, "policy_version": pol.version,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    @app.get("/decisions")
    def decisions(limit: int = Query(50, ge=1)) -> dict:
        cap = cfg.service.max_decisions_limit
        if limit > cap:
            raise HTTPException(422, f"limit: at most {cap}")
        return {"decisions": log.recent(limit)}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    if WEB_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=WEB_DIR, html=True), name="ui")

    return app


app = create_app()
