# Assumptions

Every assumption is recorded here as it is made (team rule A7). Person 3 owns this file and
the cost-model section; each person adds the entries for their own part.

## Platform and service (Person 4)

1. **Config is the single source of numbers.** `configs/default.yaml` holds every value from
   the team guide. `common/config.py` rejects unknown and missing keys and checks ranges
   (probabilities in [0, 1], distributions summing to 1, thresholds strictly increasing,
   weights 0 to 100). Tier codes are strings (`"0"`, `"W"`, `"1"`, `"2"`, `"3"`). Unquoted
   YAML keys such as `0:` are normalised to strings so either spelling works.
2. **Attack amounts are parametrised by their median ratio.** The guide writes `mu = ln 25`.
   The config stores `ratio_center: 25` and the generator takes the log, which keeps the
   file readable. The same applies to the genuine `median_center: 400`.
3. **Policy version** is the first 12 hex characters of SHA-256 over the weights and the
   *effective* thresholds (base + shift). Moving the strictness slider therefore changes
   the version, so every logged decision can be tied to the exact rule that made it.
4. **Thresholds in API responses are effective thresholds** (base + shift). `GET /policy`
   returns both the base thresholds and the shift.
5. **Score requests**: `ratio` is computed by the service as `amount / median`. Amount and
   median must be greater than 0. Amount is capped by `service.max_amount` (Rs 1 crore,
   a sanity limit, not a UPI rule). Booleans must be real JSON booleans (`"yes"` and `1`
   are rejected), and unknown fields are rejected so typos do not pass silently.
6. **Policy updates are in memory only.** `POST /policy` changes the running service and is
   lost on restart. `{"reset": true}` returns to the config values. A real deployment
   would version policies in a store with an approval step.
7. **Decision log** is one JSON line per scored payment (single and batch), written by a
   single process with a lock. Rejected (invalid) requests are not logged. Multiple
   uvicorn workers would need a shared log sink; the demo runs one worker.
8. **`/simulate` sizes are capped** (`service.simulate_limits`: 300 users, 30 payments each,
   100 attacks per type) so a browser click cannot tie up the service. Full-size runs
   belong in `eval.run_experiment`.
9. **Parity acceptance** follows guide B4.5: golden scores must match exactly; aggregates
   averaged over seeds 1 to 10 must be within 3 percentage points of the reference on
   fraud stopped and within 10% on friction (with an absolute floor of 3 friction points
   for values near zero). The published targets are single-seed (seed 7) numbers, so this
   compares a 10-seed mean with one seed. The engine's fraud stopped is the closest call
   (76.3% vs 79.2%, a gap of 2.9 pp). See `docs/paper/system_design.md`.
10. **Collect requests.** NPCI withdrew P2P collect requests from 1 October 2025 (circular of
    29 July 2025, confirmed through news coverage so far). The `collect` attack should be
    read as a merchant-style collect request. Person 1 and Person 3 should confirm this
    framing.
11. **Interim modules.** `sim/generate.py`, `policy/engine.py`, `baselines/__init__.py`
    and `eval/metrics.py` are interim stand-ins written by Person 4 from the guide so
    the service and tests run. Each file says so at the top and will be replaced by
    its owner's version without changing the interface.
