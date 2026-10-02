"""Controlled policy ablation; no provider traffic and no invented customer data."""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from .ledger import BudgetExceeded, Ledger, identifier, integer


DEMO = {
    "currency": "USD", "tenant_limit": 10000,
    "sessions": {"research": 12000, "coding": 12000},
    "batches": [
        [{"id": f"tool-{i}", "session": "research" if i % 2 == 0 else "coding",
          "estimate": 4000, "actual": 3500} for i in range(6)],
        [{"id": "follow-up", "session": "research", "estimate": 2000, "actual": 1500}],
    ],
}


def validate_workload(workload: dict) -> None:
    if not isinstance(workload, dict) or set(workload) != {"currency", "tenant_limit", "sessions", "batches"}:
        raise ValueError("workload requires exactly currency, tenant_limit, sessions and batches")
    if not isinstance(workload['currency'], str) or not re.fullmatch(r"[A-Z]{3}", workload['currency']):
        raise ValueError("currency must be a three-letter uppercase code")
    integer(workload['tenant_limit'], "tenant_limit")
    sessions = workload['sessions']
    if not isinstance(sessions, dict) or not 1 <= len(sessions) <= 1000:
        raise ValueError("sessions must be an object with 1..1000 entries")
    for session, limit in sessions.items():
        identifier(session, "session")
        integer(limit, "session limit")
    seen = set()
    if not isinstance(workload['batches'], list) or len(workload['batches']) > 1000:
        raise ValueError("batches must be a list of at most 1000 batches")
    for batch in workload['batches']:
        if not isinstance(batch, list):
            raise ValueError("each batch must be a list")
        if len(seen) + len(batch) > 10000:
            raise ValueError("workload exceeds 10000 calls")
        for call in batch:
            if not isinstance(call, dict) or set(call) != {"id", "session", "estimate", "actual"}:
                raise ValueError("each call requires exactly id, session, estimate and actual")
            identifier(call['id'], "call id")
            if call['id'] in seen:
                raise ValueError("call ids must be unique")
            seen.add(call['id'])
            identifier(call['session'], "session")
            if call['session'] not in sessions:
                raise ValueError("unknown session")
            integer(call['estimate'], "estimate", 1)
            integer(call['actual'], "actual")


def _token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def replay(workload: dict) -> dict:
    """Run identical batches against reservation, post-hoc and leaf-only policies.

    All admission decisions in a batch occur before any actual-cost report. The
    supplied denied actuals are hypothetical, never inferred from real receipts.
    """
    validate_workload(workload)
    sessions = workload['sessions']
    account_names = {session: f"session:{_token(session)}" for session in sessions}
    scope_labels = {value: f"session:{name}" for name, value in account_names.items()}
    outcomes = {}
    for policy in ("hierarchical_reservations", "post_hoc", "session_only"):
        with tempfile.TemporaryDirectory(prefix="agentledger-replay-") as folder:
            ledger = Ledger(Path(folder) / "replay.db", clock=lambda: 1700000000)
            ledger.create_account("tenant", workload['tenant_limit'], currency=workload['currency'])
            for session, limit in sessions.items():
                ledger.create_account(account_names[session], limit, currency=workload['currency'],
                                      parent="tenant" if policy == "hierarchical_reservations" else None)
            accepted, denied, spend, leaf_spend = [], [], 0, dict.fromkeys(sessions, 0)
            for batch in workload['batches']:
                pending = []
                for call in batch:
                    token = _token(call['id'])
                    if policy == "post_hoc":
                        # A defined naive baseline, NOT an emulation of LiteLLM/OpenMeter.
                        if spend >= workload['tenant_limit'] or leaf_spend[call['session']] >= sessions[call['session']]:
                            denied.append(dict(id=call['id'], scope="reported_spend"))
                            continue
                        pending.append((call, None))
                    else:
                        try:
                            permit = ledger.reserve(account_names[call['session']], call['estimate'], key=f"reserve:{token}")
                        except BudgetExceeded as exc:
                            scope = exc.result['scope']
                            denied.append(dict(id=call['id'], scope=scope_labels.get(scope, scope)))
                            continue
                        ledger.start(permit['id'], key=f"start:{token}")
                        pending.append((call, permit['id']))
                    accepted.append(call['id'])
                for call, rid in pending:
                    spend += call['actual']
                    leaf_spend[call['session']] += call['actual']
                    if rid:
                        ledger.settle(rid, call['actual'], key=f"settle:{_token(call['id'])}")
            outcomes[policy] = dict(accepted=accepted, denied=denied, actual_spend=spend,
                                    tenant_overspend=max(0, spend - workload['tenant_limit']),
                                    receipt_count=ledger.verify()['receipts'])
    controlled = outcomes['hierarchical_reservations']
    return dict(currency=workload['currency'], unit="millionth", tenant_limit=workload['tenant_limit'],
                assumptions="Synthetic actuals are supplied for every call; all calls in a batch overlap. "
                            "No provider benchmark or observed monetary savings.",
                policies=outcomes,
                avoided_overspend={name: outcome['tenant_overspend'] - controlled['tenant_overspend']
                                   for name, outcome in outcomes.items() if name != 'hierarchical_reservations'})
