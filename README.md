# AgentLedger

**Spending permits for tools that cost money. Local, transactional, inspectable.**

AgentLedger is a Python SDK and CLI for a platform engineer running several agent
workers on one machine. Reserve a bounded tool cost against both a tenant and a
session, claim dispatch, then reconcile the actual charge. Every transition writes
an integrity-linked receipt in the same SQLite transaction as the money movement.
It works for paid searches, rendering jobs, sandboxes and model calls; it never
calls a provider or guesses current prices.

**中文：为智能体的付费工具提供本地预算许可。** 在调用前同时预占租户与会话额度，
开始执行后保留未结算费用，调用完成再按实际费用结算并释放多余额度。
不需要服务端、API Key 或第三方运行依赖。完整账本保存在本地 SQLite。

## Try it in one minute / 一分钟运行

Python 3.11+; on Windows, replace `python` with `py -3`.

```sh
python -m pip install .
python -m agentledger demo
python -m unittest discover -s tests -v
```

The demo replays overlapping synthetic tool calls through three executable policies:

| Policy | Accepted calls | Actual units | Tenant overspend |
|---|---:|---:|---:|
| Hierarchical reservations | 3 | 8,500 | 0 |
| Post-hoc reported-spend check | 6 | 21,000 | 11,000 |
| Session-only reservations | 6 | 21,000 | 11,000 |

Each unit is **one millionth of the account currency**: 10,000 USD units is $0.01.
The toy amounts make arithmetic inspectable. This is a controlled counterfactual,
not measured savings or a benchmark against a vendor. All hypothetical actual
costs are supplied in advance. `python -m agentledger replay workload.json` accepts
your own batches; see [workload format](docs/OPERATIONS.md#replay-input).

## Python integration / Python 集成

```python
from agentledger import Ledger, BudgetExceeded

ledger = Ledger("costs.db")
ledger.create_account("acme", 5_000_000)  # $5 tenant ceiling
ledger.create_account("acme/research", 1_000_000, parent="acme")

# Persist a unique business-operation key in your job queue before this call.
permit = ledger.reserve("acme/research", 200_000, key="job-42:reserve")
claim = ledger.start(permit["id"], key="job-42:dispatch")
if not claim["replayed"]:
    # Invoke your paid tool HERE, using a provider idempotency key where available.
    # This runnable example supplies a synthetic confirmed charge.
    actual_cost = 125_000
    ledger.settle(permit["id"], actual_cost, key="job-42:settle")

print(ledger.account("acme"))  # spent=125000, held=0, available=4875000
print(ledger.verify())        # preserve its checkpoint separately for later audits
```

**A replayed dispatch is not permission to execute again.** Retrying a key returns
its original response with `replayed=True`; `reservation(id)` gives the current
state. A duplicate response does not undo or replay an external tool side effect.
After an ambiguous provider timeout or process crash, reconcile with the provider
before settlement or a new dispatch. There is no distributed transaction with an
external API.

**中文：** 金额必须为整数；`200_000` 表示 0.20 美元。相同幂等键与参数返回原结果，
不同参数会报错。`start` 返回 `replayed=True` 时不能再次调用付费工具。
进程崩溃或工具超时不代表免费，已开始的预占会保留，直到确认实际费用后结算。

## CLI / 命令行

```sh
python -m agentledger --db costs.db account tenant 1000000
python -m agentledger --db costs.db account session 500000 --parent tenant
python -m agentledger --db costs.db reserve session 100000 --key request-1
python -m agentledger --db costs.db status tenant
python -m agentledger --db costs.db verify
```

Audit a UTF-8 receipt export without the database:

```python
import json
from pathlib import Path
from agentledger import Ledger, verify_receipts

ledger = Ledger("costs.db")
Path("receipts.json").write_text(json.dumps(ledger.receipts()), encoding="utf-8")
print(verify_receipts(json.loads(Path("receipts.json").read_text(encoding="utf-8"))))
```

Or run `python -m agentledger verify-receipts receipts.json`; add
`--checkpoint checkpoint.json` for a checkpoint retained earlier outside the
ledger. The auditor reconstructs every state transition and ancestor balance,
so a later snapshot cannot hide an earlier balance edit.

For a retained history larger than 16 MiB, stream JSON lines through the same
semantic auditor:

```sh
agentledger --db costs.db receipts --jsonl > receipts.jsonl
agentledger verify-receipts receipts.jsonl --jsonl --checkpoint checkpoint.json
```

JSONL has no total-file limit; each UTF-8 line is limited to 16 MiB. Audit success
is reported only after the complete stream and checkpoint pass. Use binary output
redirection when your shell transcodes native output; [the portable example](examples/stream_audit.py)
writes UTF-8 bytes directly. The SDK exposes `iter_receipts()` and
`verify_receipt_stream()`; reconstructed state still consumes memory as the
number of accounts, permits and operation keys grows.

Use the returned `id` with `start ID --key dispatch-1`, then
`settle ID 65000 --key settle-1`. `refund ID 10000 --key refund-1` records a confirmed
refund. `cancel ID --key cancel-1` releases undispatched work. Started work requires
`--no-charge` and a reliable confirmation that no charge occurred. All commands
produce JSON; budget denial exits 3, other handled errors exit 2, success exits 0.
JSON output escapes Unicode characters, so identifiers survive strict legacy
Windows encodings. JSON parsers reconstruct the original names.

## Upgrade to v0.2

Back up the local ledger before the first open with v0.2. A schema-v1 database is
fully audited and upgraded in one transaction; failed or interrupted migration
preserves its original schema and data. Existing receipt bodies, digests and
checkpoints remain valid. The migration holds the writer lock while replaying
history, so quiesce workers for a large ledger. Verify against your retained
external checkpoint before resuming them. Older v0.1 clients cannot open schema v2.

Durable retries now locate their originating receipt by indexed sequence rather
than scanning historical receipts. New admissions retain the same ancestor
accounting and unknown-charge holds. See [the changelog](CHANGELOG.md) and
[the measured correction record](docs/ITERATIONS.md#v02-reliability-and-scale).

## Guarantees and boundaries

- SQLite `BEGIN IMMEDIATE` serializes admission across processes. All ancestors
  are checked and adjusted atomically; no read/check/write race in the SDK.
- Integer amounts, immutable hierarchy, consistent currency per tree, durable
  idempotency, actual reconciliation, bounded refunds and explicit lifecycle.
- Undispatched reservations expire at `expires_at <= now`; started work never
  expires automatically. Expiration is swept during new operations or `expire`.
  `status` is a conservative stored snapshot and does not sweep.
- If actual costs stay within reservations, compliant callers stay within every
  ceiling. **Underestimates can cause real overspend.** The ledger records that
  truth and denies future work; it cannot revoke an already incurred charge.
- Append-only SQL triggers plus SHA-256 receipts detect accidental edits; an
  external checkpoint detects truncation and history rewrites before that point.
  This is not a signature or protection against an administrator rewriting the
  database and checkpoint together. Protect the database with OS permissions.
- One local machine, local disk, SQLite's single writer. No network filesystem,
  distributed cluster, monthly rollover, exchange rates, billing/invoicing,
  authentication or provider price registry. Start a new scope for a new period.

## Why this project / 项目定位

Budget reservations are established practice. [LiteLLM documents default cost
reservation, reconciliation and fail-closed budget enforcement](https://docs.litellm.ai/docs/proxy/users#budget-reservation).
[OpenMeter documents metered entitlements and usage enforcement](https://openmeter.io/docs/billing/entitlements/entitlement).
AgentLedger targets a smaller embedding boundary: arbitrary tool costs, a portable
local file, explicit unknown-charge recovery, and audit/replay evidence without
deploying a gateway or billing service. It does not claim to outperform or replace
those products. [Comparison and commercial hypothesis](docs/POSITIONING.md).

中文：本项目面向单机多进程智能体工作流，提供可嵌入、可审计、可复算的预算控制。
它不是首创预算预占，也不替代已有网关和计费系统；演示数据为合成数据，没有实际客户、
收入或真实节省金额的声明。

## Evidence and maintenance

[Operational semantics](docs/OPERATIONS.md) · [review iterations](docs/ITERATIONS.md)
· [benchmark](docs/BENCHMARK.md) · [contributing](CONTRIBUTING.md) · [security](SECURITY.md)

MIT licensed, including commercial use. Runtime dependencies: Python standard
library only. CI runs Ubuntu/Windows × Python 3.11/3.14; a configured workflow is
not a claim that hosted CI has already run. v0.2 is an early pilot, without an SLA.
