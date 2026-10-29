# upi-stepup

Adaptive step-up authentication for UPI-style payments: a simulator, an explainable
rule-based policy engine, an evaluation pipeline, and a decision service with a web app.
All data is synthetic, and all numbers are version 1 placeholders, not real UPI fraud rates.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -e ".[dev]"

pytest                            # config, API, golden and parity tests
uvicorn service.api:app --port 8000
# open http://127.0.0.1:8000/     (API docs at /docs)
```

Docker:

```bash
docker build -t upi-stepup .
docker run --rm -p 8000:8000 upi-stepup
```

Everything at once: `make all`, or on Windows
`powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1`.

## Configuration

```python
from common.config import load_config
cfg = load_config("configs/default.yaml")
cfg = load_config("configs/default.yaml", override_files=["configs/my_override.yaml"])
cfg = load_config("configs/default.yaml", overrides={"costs.friction.2": 10})
cfg2 = cfg.with_overrides({"generator.evasive": True})
```

An override file can contain only the keys it changes and can name its base:

```yaml
extends: default.yaml
costs:
  friction: {"2": 10, "3": 20}
```

Unknown or missing keys raise `ConfigError` with the full key path, for example
`unknown key 'policy.weigths'` or `missing key 'policy.weights.remote'`.

## Decision service API

| Endpoint | Does |
|---|---|
| `GET /health` | status, policy version, config fingerprint |
| `GET /policy` | weights, base and effective thresholds, shift |
| `POST /policy` | partial update `{"weights": {...}, "shift": -10, "thresholds": [..]}` or `{"reset": true}` |
| `POST /score` | score one payment, with breakdown |
| `POST /score/batch` | `{"payments": [...]}` (max 1000) |
| `POST /compare` | engine result plus each baseline's tier |
| `POST /simulate` | small seeded simulation, returns the trade-off curve and baseline points |
| `GET /decisions?limit=50` | recent decision-log entries, newest first |
| `/ui/` | the web app |

Example:

```bash
curl -s localhost:8000/score -H "Content-Type: application/json" -d '{"amount":45000,"median":250,"network":"home","new_device":false,"new_payee":true,"velocity":false,"sim_change":false,"remote":true,"far_location":false,"fri":0}'
# {"score":70,"raw_total":70,"tier":"3","tier_name":"Block and call back",
#  "breakdown":[{"signal":"new_payee","points":15},{"signal":"amt_over_10x","points":25},
#               {"signal":"remote","points":30}],"thresholds":[20,50,70],"policy_version":"..."}
```

Environment variables: `UPI_CONFIG` (config path) and `UPI_DECISION_LOG` (log path).

## Tools

* `python -m tools.parity` prints the golden scores, the Python aggregates over 10 seeds, a
  comparison with the reference table, and the JS reference results when
  `reference/upi_reference_core.js` is present.
* `node tools/run_reference.js --seeds 1,2,3` runs the JS reference on its own.
* `python -m tools.bench_latency` measures `/score` latency and throughput plus batch engine
  time, and writes `results/latency.md` and `results/latency.json`.

## Ownership

| Part | Owner | Status in this repo |
|---|---|---|
| `common/config.py`, `configs/default.yaml`, `service/*`, `tools/*`, CI, Docker, packaging, `tests/test_config.py`, `tests/test_api.py`, `tests/test_parity.py` | Person 4 | done |
| `sim/*` | Person 1 | interim stand-in from the guide spec (marked in file) |
| `policy/*` | Person 2 | interim stand-in from the guide spec (marked in file) |
| `baselines/*`, `eval/*` | Person 3 | interim stand-in: `get_baselines`, `evaluate`, `sweep` |
| `reference/upi_reference_core.js` | shared, read-only | not yet added; the JS parity test skips until it is |
