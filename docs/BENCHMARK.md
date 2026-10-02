# Local benchmark

Run `python -m examples.benchmark --operations 300` for one process performing
reserve → start → settle cycles against a two-level account tree on a temporary
local SQLite file. FULL synchronous writes and one connection per operation are
included. Setup and final verification are excluded from cycle latency; verification
is reported separately. Output includes Python/SQLite/platform, median and p95,
database bytes and receipt count. No network, provider call or model inference.

Latency varies with storage, antivirus, filesystem and competing workloads. This
microbenchmark is not a throughput guarantee, multi-host benchmark or comparison
with a deployed competitor. CI smoke-runs 100 cycles; no latency threshold is used.
Observed on 2026-10-03 at the round-3 implementation
`da2c1d78f7c9c4209c6dd072a852951d32971bf4`:

| Measurement | Observed value |
|---|---:|
| Python / SQLite / platform | 3.14.3 / 3.50.4 / Windows |
| Cycles / transactions / concurrency / depth | 300 / 900 / 1 / 2 |
| Cycle median | 22.510 ms |
| Cycle p95 | 33.427 ms |
| Sum of measured cycle durations | 7,098.665 ms |
| Full semantic verification | 50.790 ms |
| Database bytes | 1,150,976 |
| Verified receipts, including account creation | 902 |

Command: `py -3 -m examples.benchmark --operations 300`. One observed run,
temporary local storage; setup and verification are excluded from cycle timing.
The p95 uses sorted sample index `floor(0.95*n)` (zero-based), capped at `n-1`.
These numbers are a reproducibility reference, not an SLA or a scale claim.
