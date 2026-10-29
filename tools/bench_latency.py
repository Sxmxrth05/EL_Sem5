"""Latency benchmark for the decision service.

Sends many concurrent POST /score requests and reports throughput and p50/p95/p99 latency,
then times the engine on 31,500 payments in batch. Writes results/latency.{json,md}.

    python -m tools.bench_latency                       # starts its own server
    python -m tools.bench_latency --url http://127.0.0.1:8000 --requests 5000 --concurrency 64

Target (guide B4.6): p95 under 50 ms for one payment on a laptop. This is a measurement,
not a promise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
P95_TARGET_MS = 50.0

_OFF = dict(new_device=False, new_payee=False, velocity=False, sim_change=False,
            remote=False, far_location=False, fri=0)
PAYLOADS = [
    {**_OFF, "amount": 288, "median": 400, "network": "mobile"},
    {**_OFF, "amount": 45000, "median": 250, "network": "home", "new_payee": True},
    {**_OFF, "amount": 45000, "median": 250, "network": "public", "new_device": True,
     "new_payee": True, "sim_change": True},
    {**_OFF, "amount": 45000, "median": 250, "network": "home", "new_payee": True,
     "remote": True},
    {**_OFF, "amount": 45000, "median": 250, "network": "home", "new_payee": True,
     "velocity": True, "far_location": True},
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def local_server():
    port = _free_port()
    log = Path(tempfile.mkdtemp()) / "bench_decisions.jsonl"
    env = {**os.environ, "UPI_DECISION_LOG": str(log)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "service.api:app", "--port", str(port),
         "--log-level", "warning"],
        cwd=ROOT, env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(url + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError("service did not start within 30 s")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def _load(url: str, n: int, concurrency: int
                ) -> tuple[list[float], list[float], int, float]:
    latencies: list[float] = []
    server: list[float] = []
    errors = 0
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(n):
        queue.put_nowait(i)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(base_url=url, limits=limits, timeout=30) as client:
        for p in PAYLOADS:  # warm-up
            await client.post("/score", json=p)

        async def worker() -> None:
            nonlocal errors
            while True:
                try:
                    i = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                t = time.perf_counter()
                r = await client.post("/score", json=PAYLOADS[i % len(PAYLOADS)])
                latencies.append((time.perf_counter() - t) * 1000)
                server.append(float(r.headers.get("x-process-time-ms", "nan")))
                if r.status_code != 200:
                    errors += 1

        t0 = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        wall = time.perf_counter() - t0
    return latencies, server, errors, wall


async def _sequential(url: str, n: int) -> tuple[list[float], list[float]]:
    out, server = [], []
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        await client.post("/score", json=PAYLOADS[0])
        for i in range(n):
            t = time.perf_counter()
            r = await client.post("/score", json=PAYLOADS[i % len(PAYLOADS)])
            out.append((time.perf_counter() - t) * 1000)
            server.append(float(r.headers.get("x-process-time-ms", "nan")))
    return out, server


def _pct(xs: list[float]) -> dict[str, float]:
    a = np.asarray(xs)
    return {f"p{q}": round(float(np.percentile(a, q)), 2) for q in (50, 95, 99)} | {
        "mean": round(float(a.mean()), 2), "max": round(float(a.max()), 2)}


def engine_batch_timing(repeats: int = 7) -> dict:
    from common.config import load_config
    from policy.engine import make_engine
    from sim.generate import generate

    cfg = load_config(ROOT / "configs" / "default.yaml")
    legit, attacks = generate(cfg, cfg.seeds.demo)
    both = pd.concat([legit, attacks], ignore_index=True)
    p = cfg.policy
    pol = make_engine(p.weights.model_dump(), p.shift, p.thresholds)
    pol(both)
    times = []
    for _ in range(repeats):
        t = time.perf_counter()
        pol(both)
        times.append((time.perf_counter() - t) * 1000)
    return {"rows": len(both), "repeats": repeats,
            "median_ms": round(statistics.median(times), 2), "min_ms": round(min(times), 2)}


def run(url: str | None, n: int, concurrency: int, sequential: int) -> dict:
    def go(u: str) -> dict:
        lat, srv, errors, wall = asyncio.run(_load(u, n, concurrency))
        seq, seq_srv = asyncio.run(_sequential(u, sequential))
        return {
            "concurrent": {"requests": n, "concurrency": concurrency, "errors": errors,
                           "wall_s": round(wall, 3), "rps": round(n / wall, 1),
                           "latency_ms": _pct(lat), "server_ms": _pct(srv)},
            "sequential": {"requests": sequential, "latency_ms": _pct(seq),
                           "server_ms": _pct(seq_srv)},
        }

    if url:
        http = go(url)
    else:
        with local_server() as u:
            http = go(u)
    seq_p95 = http["sequential"]["latency_ms"]["p95"]
    return {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": {"platform": platform.platform(), "python": platform.python_version(),
                    "processor": platform.processor() or platform.machine(),
                    "cpus": os.cpu_count()},
        "server": url or "local uvicorn, 1 worker, decision log on",
        "http": http,
        "engine_batch": engine_batch_timing(),
        "target": {"p95_single_ms": P95_TARGET_MS, "sequential_p95_ms": seq_p95,
                   "met": seq_p95 < P95_TARGET_MS},
    }


def _row(*cells) -> str:
    return "| " + " | ".join(str(x) for x in cells) + " |"


def _q(d: dict) -> list:
    return [d["p50"], d["p95"], d["p99"], d["max"]]


def to_markdown(r: dict) -> str:
    c, s, e = r["http"]["concurrent"], r["http"]["sequential"], r["engine_batch"]
    m = r["machine"]
    t = r["target"]
    verdict = "met" if t["met"] else "not met"
    e2e_seq = _row("Sequential (1 in flight)", s["requests"], "-", *_q(s["latency_ms"]), "-")
    e2e_con = _row(f"Concurrent ({c['concurrency']} in flight)", c["requests"], c["rps"],
                   *_q(c["latency_ms"]), c["errors"])
    srv_seq = _row("Sequential", *_q(s["server_ms"]))
    srv_con = _row("Concurrent", *_q(c["server_ms"]))
    return f"""# Decision service latency report

Generated {r['timestamp']} by `python -m tools.bench_latency`. Server: {r['server']}.
Machine: {m['platform']}, Python {m['python']}, {m['cpus']} logical CPUs ({m['processor']}).
Client and server ran on the same machine, so the numbers include client overhead.

## POST /score, one payment per request

| Load | Requests | rps | p50 ms | p95 ms | p99 ms | max ms | errors |
|---|---|---|---|---|---|---|---|
{e2e_seq}
{e2e_con}

Target: p95 under {t['p95_single_ms']:.0f} ms for one payment.
Sequential p95 = {t['sequential_p95_ms']} ms -> **{verdict}**.
Under concurrent load, latency includes queueing behind other requests on a single worker.

### Server-side processing time (X-Process-Time-Ms header, excludes network and client)

| Load | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
{srv_seq}
{srv_con}

The gap between end-to-end and server-side time is HTTP transport plus the benchmark client,
which shares the CPU with the server in this setup. With 32 requests in flight on one
worker, the server-side span also includes time spent interleaved with other requests.
Throughput here is limited by the single-process Python client, not by the server.

## Engine, batch scoring

{e['rows']:,} payments scored by the vectorised engine: median {e['median_ms']} ms,
best {e['min_ms']} ms over {e['repeats']} runs (target in guide B2: under 200 ms).
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="benchmark a running service instead of starting one")
    ap.add_argument("--requests", type=int, default=3000)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--sequential", type=int, default=500)
    ap.add_argument("--out", default=str(ROOT / "results"))
    args = ap.parse_args()

    report = run(args.url, args.requests, args.concurrency, args.sequential)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "latency.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = to_markdown(report)
    (out / "latency.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
