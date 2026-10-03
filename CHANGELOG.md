# Changelog

## 0.2.0

- Indexed originating receipts for durable retries remove the reverse history
  scan while retaining cached-response corruption checks. Schema-v1 migration
  audits all receipts and projections before atomic backfill; interrupted
  migration preserves the original data and receipt/checkpoint format.
- `Ledger.iter_receipts()`, `verify_receipt_stream()` and the CLI `--jsonl`
  export/audit path process histories beyond the legacy 16 MiB JSON-file limit.
  Input lines remain bounded, and the auditor retains reconstructed state.
- ASCII-safe CLI JSON preserves Unicode names on legacy Windows streams after
  a committed mutation. Duplicate JSON properties are rejected in inputs and
  receipt event bodies rather than silently replacing a value.
- Concurrent first opens retry the WAL-mode race within the configured timeout;
  genuine lock timeouts continue to fail closed.

Schema 2 is incompatible with old v0.1 writers. Back up and quiesce workers before
upgrading; verify a separately retained checkpoint before resuming. No provider
execution or automatic release of uncertain started work was added.

## 0.1.0

Initial local hierarchical budget permits, durable idempotency, explicit dispatch,
actual-charge settlement, bounded refunds, semantic receipt verification and
offline JSON-list auditing.
