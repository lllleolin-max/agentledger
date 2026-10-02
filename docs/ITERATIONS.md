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
