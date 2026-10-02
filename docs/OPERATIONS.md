# Operational contract

## State machine

```mermaid
stateDiagram-v2
  [*] --> reserved: reserve / atomic ancestor holds
  reserved --> started: start before expiry
  reserved --> expired: sweep deadline
  reserved --> cancelled: cancel
  started --> settled: confirmed actual charge
  started --> cancelled: explicit no-charge attestation
  settled --> settled: confirmed partial refund
```

For an account, `spent` includes all net settled descendant costs; `held` includes
all reserved and started descendant estimates. `available = ceiling - spent - held`.
Parents can also be charged directly. A child ceiling may exceed its parent's;
the effective ceiling is still the tightest remaining ancestor capacity.

Every operation creates a fresh connection, enables foreign keys and FULL
synchronous writes, begins an immediate transaction, updates all projections,
appends a receipt and persists its idempotent response, then commits. A lock timeout,
disk-full error or failed statement raises rather than returning an allowed permit.
The caller must treat every exception as no authorization. An uncertain commit
can be resolved by retrying exactly the same durable operation key.

There is no remote I/O inside transactions. A start claim prevents two compliant
workers from dispatching one permit. It does not establish exactly-once execution
at a remote provider: persist a provider request key and query provider outcome
after an ambiguous timeout. Unresolved started work remains held indefinitely.
Do not release it just to restore availability. For a known zero-cost failure,
settle actual 0, or cancel with `no_charge=True`.

Amounts and timestamps range from 0 through 2^63−1, with strictly positive holds,
refunds and TTLs. Bool, floating point, infinity and NaN are rejected. An aggregate
that exceeds SQLite's integer range fails atomically and preserves its unresolved
hold for operator recovery; split an unreasonable accounting lifetime into scopes.
Currencies are three uppercase letters treated as labels, never converted.

Keys are global to the database, max 200 characters. Failed budget admissions
are durably idempotent; use a new key for an intentional later attempt. Invalid
state/validation errors do not consume a key. Account creation is idempotent by
name and exact configuration. Account limits and ancestry are immutable in v0.1.

System wall-clock time controls expiry; a backward clock adjustment prolongs an
undispatched hold, while a forward jump can expire it early. This cannot release
started work. `clock=` exists for deterministic testing, not client-supplied time.

## Integrity and retention

Each receipt hashes its sequence, timestamp, previous digest and canonical JSON
body. Its body contains operation arguments, result and affected row snapshots.
`verify()` checks the chain and materialized state in one consistent read snapshot.
Keep `verify()['checkpoint']` in separate protected storage. Pass it to
`verify(checkpoint=...)` or CLI `verify --checkpoint checkpoint.json` on a later
copy. An older checkpoint allows a longer history but rejects truncation below it.

The chain establishes consistency, not provider truth or signer identity. Local
OS access is the trust boundary. Direct SQL changes bypass the SDK. Never place
credentials, prompts, PII or payloads in operation keys/account names. There is no
pruning API: budget capacity and receipt retention are different responsibilities.
For backup, quiesce all writers and copy the database, or use SQLite's online
backup API; copying a live `.db` file without its WAL is unsafe.

## Replay input

```json
{
  "currency": "USD",
  "tenant_limit": 10000,
  "sessions": {"research": 8000, "coding": 8000},
  "batches": [
    [
      {"id": "search-1", "session": "research", "estimate": 6000, "actual": 5000},
      {"id": "render-1", "session": "coding", "estimate": 6000, "actual": 4500}
    ]
  ]
}
```

IDs must be unique across batches. Input is bounded to 1000 sessions, 1000 batches and 10000 calls;
the CLI accepts at most 16 MiB of UTF-8 JSON, with an optional BOM. Unknown fields
are errors, so a misspelled policy setting cannot be silently ignored. Call IDs
and session names allow 200 characters; internal keys use distinct operation
namespaces and fixed-length SHA-256 encodings to avoid accidental collisions.

All admissions in a batch precede all its
settlements. The hierarchical policy uses the real SDK; session-only ablates
parent links but keeps reservations. Post-hoc checks only already reported spend
against tenant and session limits, so an overlapping batch can overrun them.
Neither baseline emulates LiteLLM or OpenMeter. Declaring actuals above estimates
can falsify the hard-bound premise; the report displays resulting overspend.
Actual values for denied work are counterfactual input, not observations. Policy
admission order follows input order; it is not a throughput optimizer or fairness
scheduler.
