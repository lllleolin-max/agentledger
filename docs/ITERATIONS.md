# Implementation and review record

This file records completed review/correction rounds with observed results.
Initial implementation is not counted as one of the three required rounds.
Commit hashes are recorded by a subsequent documentation commit, so each listed
code commit can be independently checked out and rerun.

## Initial implementation

Implemented transactional ancestor holds, explicit dispatch, reconciliation,
refunds, expiration, receipt chain/projection verification, CLI, SDK, synthetic
policy ablations and 20 tests. First local test execution exposed Windows cleanup
errors in test-only raw SQLite connections; explicit `contextlib.closing` fixed
them before the initial implementation commit. This is not counted as a review
round. `py -3 -m unittest discover -s tests -v`: **20 tests, OK**, Windows/Python
3.14.3, observed 2026-10-03. Hosted CI is not yet observed.

## Round 1 — idempotency cache corruption

Before: `4aa80ea832c0e0585df914f604bcd88cc0b202b1`.
Self-review found that `verify()` compared only account/reservation projections;
altered or deleted cached responses were not covered. A changed response could
also be returned on an SDK retry. Added two corruption regressions, then ran
`py -3 -m unittest discover -s tests -p test_review_regressions.py -v` on the old
implementation: **2 tests, FAILED (failures=2)**, both `IntegrityError not raised`.

Correction: reconstruct the full idempotency cache from receipt events and compare
it in the same verification snapshot; reject duplicate historical keys; validate
a retried cached response against its originating receipt before returning it.
Retries scan the receipt chain (documented O(receipts)); new admissions retain
their existing transaction path. Full external verification remains necessary
after restoring a potentially corrupted database; hash consistency is not an
authorization boundary against database administrators.

Verification: `py -3 -m unittest discover -s tests -v`: **22 tests, OK** (2.686s).

After round 1: `151f138e02c8f1a06d2924e10b370c1e914760af`.

## Round 2 — user identifiers and replay input boundaries

Before: `151f138e02c8f1a06d2924e10b370c1e914760af`.
Review of the general-purpose replay API found that valid call IDs `x` and
`start:x` collided with internal dispatch keys. A 200-character session name
also passed validation but failed after adding the internal prefix. Malformed
JSON shapes raised uncaught TypeError and unknown policy fields were ignored.
Added three regressions. After correcting an indentation mistake in the new test
file, `py -3 -m unittest discover -s tests -p test_review_regressions.py -v`
on unchanged implementation ran **5 tests, FAILED (failures=1, errors=6)**
(subtests account for the six errors).

Correction: separate internal operation namespaces and hash user identifiers to
fixed-width internal keys; preserve human-readable scope labels in reports.
Validate exact workload/call shapes and bound session/batch/call cardinality before
creating a database. The CLI reads a bounded 16 MiB UTF-8 JSON input and supports
a Windows UTF-8 BOM. This fixes general replay inputs, beyond the built-in demo.

Verification: added input-cardinality and CLI JSON/BOM checks; `py -3 -m unittest discover -s tests -q`: **27 tests, OK** (3.382s). Also corrected the setuptools build requirement to >=77.0.3 for SPDX metadata, based on root review; this packaging change is not counted as an independent cycle.

After round 2: `1ef189db13fb97b99a86ec8e2cc7f3d9be997031`.

## Round 3 — conservation cannot be replaced by last snapshots

Before: `1ef189db13fb97b99a86ec8e2cc7f3d9be997031`.
Self-review found that the verifier treated the latest account snapshot as the
expected balance. Starting with a 100-unit ceiling, reserve 50, manually change
`held` to 0, then ordinarily reserve 60: the second receipt includes the altered
balance, and the old verifier accepts a ledger whose live permits sum to 110.
The receipt hashes themselves are intact. This is an audit correctness defect,
not a claim that direct SQL maintenance is an authorized admission path.

Preserved the pending regression tests and independently ran them against an
archive of the exact before commit. `git archive 1ef189d` was extracted into a
temporary directory; the current `tests/test_review_regressions.py` was copied
into that archive and `py -3 -m unittest discover -s tests -p
test_review_regressions.py -v` ran there. Observed **9 tests, FAILED
(failures=1, errors=5)**, 0.624s. The laundering regression was the failure
(`IntegrityError not raised`). Five malformed-checkpoint subtests exposed the
old unsupported validation behavior; these are not five separate review rounds.

Correction: replay account creation, atomic reserve/denial, dispatch, actual
settlement, partial refund, cancellation and expiry from an empty model. Compare
each event result and intermediate ancestor/permit snapshot against the replayed
transition before comparing final materialized rows and cached responses. Validate
checkpoint fields explicitly. Expose the same semantic auditor as
`verify_receipts` and the `verify-receipts` CLI, usable without any database.
Use a visited set for linear ancestry traversal. Include MIT license-file metadata.

Added probes for rehashed contradictory results, unknown/extra operation fields,
external checkpoints and rewritten/truncated histories, malformed/reordered
exports, all lifecycle transitions, independent seeded descendant sums, and real
spawned-process termination before/after commit. The interruption test uses
`os._exit(23)` after dirty permit/hold writes but before receipt insertion, then
after a completed reservation: reopening rolls back the first case entirely;
the second retries with `replayed=True` and one conserved hold.

Verification: `py -3 -m unittest discover -s tests -v`: **37 tests, OK** (4.737s),
including the existing spawned-process and thread admission races. Clean virtual
environment: `py -3 -m venv .venv`, then
`.venv/Scripts/python.exe -m pip install --no-cache-dir .` built and installed
`agentledger-0.1.0-py3-none-any.whl`. From a separate temporary working directory,
`.venv/Scripts/python.exe -m unittest discover -s <absolute-repo>/tests -v`:
**37 tests, OK** (5.492s). Import path was `.venv/Lib/site-packages/agentledger`,
metadata reported `License-Expression: MIT` and `License-File: LICENSE`.
The installed `agentledger.exe demo` exited 0 and reproduced spend 8,500/21,000/
21,000 and overspend 0/11,000/11,000 for hierarchy/post-hoc/session-only.
`py -3 -m examples.benchmark --operations 300` produced 902 verified receipts;
measured latencies and environment are recorded in `docs/BENCHMARK.md`.
`git diff --check` passed. Observed 2026-10-03 on Windows/Python 3.14.3.

After round 3: `da2c1d78f7c9c4209c6dd072a852951d32971bf4`.

Remaining boundary: hashes and state replay establish consistency, not provider
truth or signer identity. A consistent rewrite is accepted without a previously
retained external checkpoint. New admissions do not scan the full history;
verify after restoring or manually editing a database before workers resume.
Actual costs above estimates can overspend and are recorded honestly. The crash
probe exercises local SQLite transaction recovery, not all hardware power-loss
failure modes. Hosted Ubuntu/Windows CI has not yet been observed; independent
scores, adoption, customers and revenue are not asserted by this builder.
