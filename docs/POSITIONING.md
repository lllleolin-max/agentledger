# Positioning and commercial-use evidence

Primary sources checked 2026-10-03; the date is a source-verification date, not a
release timestamp. No absolute novelty, market-share or competitor absence claims.

| Existing project | Documented capability | AgentLedger's scoped choice |
|---|---|---|
| [LiteLLM budgets](https://docs.litellm.ai/docs/proxy/users#budget-reservation) | Default maximum-cost reservations, replacement with actual costs, hierarchical budgets and optional fail-closed enforcement | Embed a local Python ledger for arbitrary paid tools; no model routing, provider registry, Redis or PostgreSQL service |
| [OpenMeter entitlements](https://openmeter.io/docs/billing/entitlements/entitlement) | Metered balance/limit-based access and usage enforcement | Explicit per-operation pending/started/settled lifecycle plus local audit receipts; no billing products, subscriptions or grants |

These mature projects address many of the same concerns. We did not run their
deployments or assert missing capabilities from documentation silence. The
executable baselines are named algorithmic ablations, not competitor simulations.

## Pilot user and bounded value

Candidate user: the engineer maintaining a single-host agent worker pool for an
internal research or coding team. One job can incur paid searches, temporary VM
charges and model calls. Existing per-session counters allow sibling workers to
consume the same tenant headroom, while a timeout leaves uncertain charges.
The buyer hypothesis is reduced budget incidents and easier internal reconciliation.
This is a reasoned hypothesis, not interviews or a validated sales forecast.

`python -m agentledger demo` demonstrates a $0.01 shared budget with overlapping
synthetic requests. Full hierarchy records $0.0085 actual spend; post-hoc and
session-only policies each record $0.021, or $0.011 overspend. Multiplying this
synthetic example into an annual ROI claim would be unsupported. The benefit is
the changed admission decision and recorded recovery evidence, not a promised
percentage saving. Test your real concurrency, upper bounds and charge latency.

A useful pilot is one instrumented tool adapter, persisted job identifiers, two
session scopes, confirmed provider charge ingestion, a reconciliation runbook and
an external checkpoint. Measure denied-work correctness, unresolved-started age,
reservation-to-actual ratios, lock contention and the time to explain a disputed
charge. Reject the pilot if provider costs have no dependable upper bound or
workers can bypass the adapter. A multi-host fleet should use a service designed
for distributed coordination instead of a shared SQLite file.

MIT permits commercial incorporation and redistribution subject to its notice.
This project has no runtime dependency licenses to aggregate. Actual adoption,
customers, willingness to pay and revenue are **unknown**. There is no SLA or
support contract; the source and regression evidence are the current deliverable.
