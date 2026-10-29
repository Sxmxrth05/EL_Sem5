# Decision service latency report

Generated 2026-10-29T15:02:21+00:00 by `python -m tools.bench_latency`. Server: local uvicorn, 1 worker, decision log on.
Machine: Windows-11-10.0.26200-SP0, Python 3.14.2, 18 logical CPUs (Intel64 Family 6 Model 170 Stepping 4, GenuineIntel).
Client and server ran on the same machine, so the numbers include client overhead.

## POST /score, one payment per request

| Load | Requests | rps | p50 ms | p95 ms | p99 ms | max ms | errors |
|---|---|---|---|---|---|---|---|
| Sequential (1 in flight) | 500 | - | 6.38 | 8.1 | 10.11 | 27.67 | - |
| Concurrent (32 in flight) | 3000 | 85.5 | 209.51 | 1186.17 | 1973.82 | 3292.94 | 0 |

Target: p95 under 50 ms for one payment.
Sequential p95 = 8.1 ms -> **met**.
Under concurrent load, latency includes queueing behind other requests on a single worker.

### Server-side processing time (X-Process-Time-Ms header, excludes network and client)

| Load | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
| Sequential | 1.72 | 2.28 | 2.98 | 22.38 |
| Concurrent | 11.58 | 18.57 | 27.25 | 108.95 |

The gap between end-to-end and server-side time is HTTP transport plus the benchmark client,
which shares the CPU with the server in this setup. With 32 requests in flight on one
worker, the server-side span also includes time spent interleaved with other requests.
Throughput here is limited by the single-process Python client, not by the server.

## Engine, batch scoring

31,500 payments scored by the vectorised engine: median 6.38 ms,
best 5.38 ms over 7 runs (target in guide B2: under 200 ms).
