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
