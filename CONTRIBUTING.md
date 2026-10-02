# Contributing

Run `python -m unittest discover -s tests -v` and `python -m agentledger demo`.
Use Python 3.11-compatible standard library APIs. CI covers Linux and Windows;
explicitly close SQLite connections and use spawn-safe multiprocessing helpers.

For ledger changes, provide a failing regression reproducing a violated invariant,
the corrected behavior and a clear discussion of transaction boundaries. Preserve
truthful actual costs, ancestor conservation, idempotency and unknown-charge holds.
Avoid changing a receipt format without a schema/version and compatibility plan.
Do not weaken assertions simply to pass a test. Performance claims need a command,
environment, workload and raw result. Documentation should distinguish evidence,
synthetic scenarios and hypotheses. New dependencies need a concrete reason.

Issues and pull requests are welcome. Contributions are under the MIT license.
Scope is one-machine durable spending control; distributed serving, billing and
provider pricing are intentionally delegated to integrations or other projects.
