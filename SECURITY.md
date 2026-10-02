# Security and trust boundary

AgentLedger is an in-process local ledger, not an authentication or network
security boundary. A caller with database write access can rewrite data, triggers
and hashes; an external checkpoint detects earlier-history changes only if kept
independently. Restrict file permissions and tool credentials to trusted workers.

Do not put credentials, user prompts, personal identifiers or private payloads in
keys and account names: those values intentionally become durable receipts.
Prices, estimates, actual charges and no-charge attestations come from callers.
They must be validated at your integration boundary. The library cannot verify
an external invoice or stop an uninstrumented provider call.

Report a reproducible security problem privately through the repository's GitHub
Security Advisories when available. Do not publish secrets or database files in
issues. If private reporting is unavailable, open a minimal issue requesting a
private contact channel without exploit data. This early release has no guaranteed
response time. Only the latest main branch is currently maintained.
