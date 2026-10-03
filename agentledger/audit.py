"""Offline semantic replay of receipt history, independent of stored snapshots."""

from __future__ import annotations

import hashlib
import json
import re

from .ledger import IntegrityError, ZERO_HASH, canonical, identifier, integer

PARAMETERS = {
    'create_account': {'name', 'ceiling', 'currency', 'parent'},
    'reserve': {'account', 'amount', 'ttl'},
    'start': {'id'},
    'settle': {'id', 'actual'},
    'cancel': {'id', 'no_charge'},
    'expire': {'id'},
    'refund': {'id', 'amount'},
}


def audit_receipts(receipts, checkpoint=None):
    if checkpoint is not None:
        if (type(checkpoint) is not dict or set(checkpoint) != {'seq', 'digest'}
                or type(checkpoint['seq']) is not int or checkpoint['seq'] < 0
                or not isinstance(checkpoint['digest'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', checkpoint['digest'])):
            raise ValueError("checkpoint requires integer seq>=0 and a lowercase SHA-256 digest")
    accounts, reservations, operations = {}, {}, {}
    previous, seq = ZERO_HASH, 0
    checkpoint_seen = checkpoint is None or checkpoint == dict(seq=0, digest=ZERO_HASH)

    def path(name):
        result, seen = [], set()
        while name is not None:
            if name in seen or name not in accounts:
                raise IntegrityError("invalid account ancestry in receipt")
            seen.add(name)
            result.append(accounts[name])
            name = accounts[name]['parent']
        return result

    def adjust(name, *, spent=0, held=0):
        for account in path(name):
            account['spent'] = integer(account['spent'] + spent, "replayed spend")
            account['held'] = integer(account['held'] + held, "replayed holds")

    for row in receipts:
        seq += 1
        try:
            if set(row) != {'seq', 'at', 'body', 'previous', 'digest'}:
                raise IntegrityError("unexpected receipt fields")
            integer(row['seq'], "receipt sequence", 1)
            integer(row['at'], "receipt time")
            digest = hashlib.sha256(canonical([row['seq'], row['at'], row['previous'], row['body']]).encode()).hexdigest()
            if row['seq'] != seq or row['previous'] != previous or row['digest'] != digest:
                raise IntegrityError("receipt chain mismatch")
            body = json.loads(row['body'])
            if set(body) != {'operation', 'parameters', 'result', 'key', 'accounts', 'reservations'}:
                raise IntegrityError("unexpected event fields")
            op, p, now = body['operation'], body['parameters'], row['at']
            if op not in PARAMETERS or type(p) is not dict or set(p) != PARAMETERS[op]:
                raise IntegrityError("unknown operation or unexpected parameter fields")
            account, rid = None, None
            if op == 'create_account':
                account = identifier(p['name'], "account")
                integer(p['ceiling'], "ceiling")
                if account in accounts or not re.fullmatch(r'[A-Z]{3}', p['currency']):
                    raise IntegrityError("invalid account creation")
                if p['parent'] is not None and accounts[p['parent']]['currency'] != p['currency']:
                    raise IntegrityError("currency mismatch")
                accounts[account] = dict(name=account, parent=p['parent'], currency=p['currency'],
                                         ceiling=p['ceiling'], spent=0, held=0)
                expected = dict(accounts[account])
            elif op == 'reserve':
                account = identifier(p['account'], "account")
                integer(p['amount'], "amount", 1)
                integer(p['ttl'], "ttl", 1)
                expires = integer(now + p['ttl'], "expiry")
                blocked = next((a for a in path(account)
                                if a['spent'] + a['held'] + p['amount'] > a['ceiling']), None)
                if blocked is not None:
                    expected = dict(state='denied', scope=blocked['name'], requested=p['amount'],
                                    available=blocked['ceiling']-blocked['spent']-blocked['held'])
                else:
                    rid = identifier(body['result']['id'], "reservation id")
                    if rid in reservations:
                        raise IntegrityError("reservation id reused")
                    reservations[rid] = dict(id=rid, account=account, amount=p['amount'],
                        expires_at=expires, state='reserved', actual=0, refunded=0)
                    adjust(account, held=p['amount'])
                    expected = dict(reservations[rid])
            elif op in ('start', 'settle', 'cancel', 'expire', 'refund'):
                rid = identifier(p['id'], "reservation id")
                reservation = reservations[rid]
                account = reservation['account']
                state = reservation['state']
                if op == 'start':
                    if state != 'reserved' or reservation['expires_at'] <= now:
                        raise IntegrityError("dispatch without live reservation")
                    reservation['state'] = 'started'
                elif op == 'settle':
                    integer(p['actual'], "actual")
                    if state != 'started':
                        raise IntegrityError("settlement without dispatch")
                    adjust(account, spent=p['actual'], held=-reservation['amount'])
                    reservation.update(state='settled', actual=p['actual'])
                elif op == 'cancel':
                    if type(p['no_charge']) is not bool or state not in ('reserved', 'started'):
                        raise IntegrityError("invalid cancellation")
                    if state == 'started' and not p['no_charge']:
                        raise IntegrityError("cancellation without no-charge attestation")
                    adjust(account, held=-reservation['amount'])
                    reservation['state'] = 'cancelled'
                elif op == 'expire':
                    if state != 'reserved' or reservation['expires_at'] > now:
                        raise IntegrityError("invalid expiry")
                    adjust(account, held=-reservation['amount'])
                    reservation['state'] = 'expired'
                else:
                    integer(p['amount'], "refund", 1)
                    if state != 'settled' or p['amount'] > reservation['actual'] - reservation['refunded']:
                        raise IntegrityError("invalid refund")
                    adjust(account, spent=-p['amount'])
                    reservation['refunded'] += p['amount']
                expected = dict(reservation) if op != 'expire' else dict(state='expired')
                if op == 'settle':
                    expected.update(overrun=max(0, p['actual']-reservation['amount']),
                                    released=max(0, reservation['amount']-p['actual']))
            else:
                raise IntegrityError(f"unknown operation: {op}")
            key = body['key']
            if op not in ('expire', 'create_account'):
                identifier(key, "operation key")
                expected['replayed'] = False
                if key in operations:
                    raise IntegrityError("duplicate idempotency key")
                operations[key] = dict(key=key, request=canonical(dict(operation=op, parameters=p)),
                                       response=canonical(expected), receipt_seq=seq)
            elif key is not None:
                raise IntegrityError("unexpected key on system event")
            if canonical(body['result']) != canonical(expected):
                raise IntegrityError("result does not match replayed transition")
            if (canonical(body['accounts']) != canonical(path(account))
                    or canonical(body['reservations']) != canonical([reservations[rid]] if rid else [])):
                raise IntegrityError("snapshot violates replayed conservation")
            previous = row['digest']
            if checkpoint is not None and seq == checkpoint['seq']:
                if previous != checkpoint['digest']:
                    raise IntegrityError("checkpoint digest mismatch")
                checkpoint_seen = True
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise IntegrityError(f"invalid receipt {seq}: {exc}") from exc
    if not checkpoint_seen:
        raise IntegrityError("checkpoint is missing (history may be truncated)")
    summary = dict(ok=True, receipts=seq, checkpoint=dict(seq=seq, digest=previous))
    return summary, accounts, reservations, operations


def verify_receipts(receipts, *, checkpoint: dict | None = None) -> dict:
    """Verify an exported JSON receipt list without access to the SQLite database."""
    if type(receipts) is not list:
        raise ValueError("exported receipts must be a JSON list")
    return audit_receipts(receipts, checkpoint)[0]


def verify_receipt_stream(receipts, *, checkpoint: dict | None = None) -> dict:
    """Audit an iterable without retaining the input history.

    Replay still retains reconstructed accounts, reservations and operation keys.
    The entire stream must finish successfully before the result is trusted.
    """
    return audit_receipts(receipts, checkpoint)[0]
