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
Recorded results will be added after the final corrected build is measured.
