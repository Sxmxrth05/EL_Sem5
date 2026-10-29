# System design and deployment (draft, Person 4)

*Draft section for the paper. Numbers in brackets are measured by
`python -m tools.bench_latency` and must be refreshed from `results/latency.md` before
submission.*

## Where the engine sits

A UPI payment passes from the payer's app (the payment service provider, PSP) to the
payer's bank (issuer) through NPCI's switch. The RBI Authentication Mechanisms Directions,
2025 [rbi_auth_2025] keep two-factor authentication as a floor for every digital payment
and let payment participants apply additional, risk-based checks. Our engine is designed for
that position. It runs at the issuer or the PSP after the payment request is formed and before
the PIN is verified, and its output can only *add* steps on top of the PIN:

| Tier | Effect | Relation to the mandated floor |
|---|---|---|
| 0 | PIN only | equals the floor |
| 1 | PIN + biometric or OTP | above the floor |
| 2 | PIN + biometric + 30-minute hold with confirmation | above the floor |
| 3 | Block until bank call-back | above the floor |

The engine never removes a factor, so no strictness setting can drop a payment below the
regulatory minimum. The FRI-style baseline uses the Department of Telecommunications'
Financial Fraud Risk Indicator [dot_fri_2025], which labels mobile numbers as medium, high or
very high risk. In our model that label is just one more signal (`fri`) rather than the
whole decision.

## Service architecture

The decision service (`service/api.py`) is a stateless FastAPI application around the
policy engine:

* `POST /score` and `POST /score/batch` validate the request, compute `ratio = amount /
  median`, score with the additive points table, map the score to a tier, and return the
  per-signal breakdown together with the policy version.
* `POST /compare` reports what each baseline would do for the same payment.
* `POST /simulate` runs a size-capped simulation and returns the fraud-stopped versus
  friction curve, for demonstrations.
* `GET/POST /policy` read and change the weights and strictness shift. Every change
  produces a new *policy version*, a hash of the weights and effective thresholds.
* `GET /decisions` returns recent entries from the decision log.

All inputs are validated with typed schemas, and malformed requests are rejected with a
message naming the field. The configuration loader applies the same rule to the YAML files:
an unknown or missing key stops the service from starting, so a typo cannot silently fall
back to a default.

## Explainability and the decision log

Each scored payment is appended to a JSON-lines log with its timestamp, inputs, score,
per-signal breakdown, tier, effective thresholds and policy version. Because the policy is
an additive points table, the breakdown *is* the decision. No post-hoc explainer is
needed, and the explanation costs nothing to compute. This contrasts with machine-learning
fraud models, where explainers such as SHAP add runtime cost and their choice of background
data affects the explanation [psychoula_2021]. The policy version lets an auditor
reconstruct the exact rule behind any historical decision, even after the strictness
slider has moved.

## Latency

Production fraud scoring runs inside tight latency budgets on the payment path
[branco_2020]. We set ourselves a target of p95 under 50 ms for one payment on a laptop and
measured it with `tools/bench_latency.py`. The scoring step itself takes microseconds.
End-to-end time is dominated by HTTP handling and the decision-log write.

* Sequential `/score` requests: end-to-end p95 [8.1] ms. Server-side processing p95 [2.3] ms
  natively on Windows and [1.7] ms in the Linux Docker container, measured by an
  `X-Process-Time-Ms` header.
* Batch scoring of 31,500 payments by the vectorised engine: [≈6] ms.
* Under 32 concurrent clients on one worker, with client and server on the same Windows
  laptop, end-to-end tail latency grows through queueing and transport overhead. Server-side
  time stays in single-digit milliseconds. A deployment would run several workers behind a
  load balancer and send the log to an asynchronous sink.

These are laptop measurements on synthetic requests, not a claim about production
performance.

## Reproducibility

The repository installs with `pip install -e .`, and continuous integration runs `ruff` and
`pytest` on every pull request, including golden-score and parity tests. CI also builds the
Docker image and smoke-tests the running service. `make all` (or `scripts/run_all.ps1`) runs
the tests and the default experiment, then starts the service.

## Open points for the team

1. The hold prompt for tier 2 must reach the owner on a channel the thief does not hold
   (guide A9.4). The service records the decision but does not deliver the prompt. Person 3
   decides the delivery assumption and the matching `stolen` stop chance.
2. The NPCI circular withdrawing P2P collect requests (effective 1 October 2025) has so far
   been confirmed only through news coverage [npci_collect_2025]. The primary PDF must be
   cited before submission.
