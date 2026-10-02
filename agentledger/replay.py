"""Controlled policy ablation; no provider traffic and no invented customer data."""

from __future__ import annotations

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


def replay(workload: dict) -> dict:
    """Run identical batches against reservation, post-hoc and leaf-only policies.

    All admission decisions in a batch occur before any actual-cost report. The
    supplied denied actuals are hypothetical, never inferred from real receipts.
    """
    integer(workload['tenant_limit'], "tenant_limit")
    sessions = workload['sessions']
    if not isinstance(sessions, dict) or not sessions:
        raise ValueError("sessions must be a nonempty object")
    for session, limit in sessions.items():
        identifier(session, "session")
        integer(limit, "session limit")
    seen = set()
    for batch in workload['batches']:
        for call in batch:
            identifier(call['id'], "call id")
            if call['id'] in seen:
                raise ValueError("call ids must be unique")
            seen.add(call['id'])
            if call['session'] not in sessions:
                raise ValueError("unknown session")
            integer(call['estimate'], "estimate", 1)
            integer(call['actual'], "actual")
    outcomes = {}
    for policy in ("hierarchical_reservations", "post_hoc", "session_only"):
        with tempfile.TemporaryDirectory(prefix="agentledger-replay-") as folder:
            ledger = Ledger(Path(folder) / "replay.db", clock=lambda: 1700000000)
            ledger.create_account("tenant", workload['tenant_limit'], currency=workload['currency'])
            for session, limit in sessions.items():
                ledger.create_account(f"session:{session}", limit, currency=workload['currency'],
                                      parent="tenant" if policy == "hierarchical_reservations" else None)
            accepted, denied, spend, leaf_spend = [], [], 0, dict.fromkeys(sessions, 0)
            for batch in workload['batches']:
                pending = []
                for call in batch:
                    if policy == "post_hoc":
                        # A defined naive baseline, NOT an emulation of LiteLLM/OpenMeter.
                        if spend >= workload['tenant_limit'] or leaf_spend[call['session']] >= sessions[call['session']]:
                            denied.append(dict(id=call['id'], scope="reported_spend"))
                            continue
                        pending.append((call, None))
                    else:
                        try:
                            permit = ledger.reserve(f"session:{call['session']}", call['estimate'], key=call['id'])
                        except BudgetExceeded as exc:
                            denied.append(dict(id=call['id'], scope=exc.result['scope']))
                            continue
                        ledger.start(permit['id'], key=f"start:{call['id']}")
                        pending.append((call, permit['id']))
                    accepted.append(call['id'])
                for call, rid in pending:
                    spend += call['actual']
                    leaf_spend[call['session']] += call['actual']
                    if rid:
                        ledger.settle(rid, call['actual'], key=f"settle:{call['id']}")
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
