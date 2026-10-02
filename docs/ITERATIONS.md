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
