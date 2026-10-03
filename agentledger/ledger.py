"""SQLite spending permits. All amounts are integer millionths of a currency.

One fresh SQLite connection per operation: Ledger can be shared across threads,
and separate processes can open the same local file. No connection is forked.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable
import uuid

MAX_INT = 2**63 - 1
ZERO_HASH = "0" * 64


class LedgerError(Exception):
    """Base class for actionable domain errors."""


class BudgetExceeded(LedgerError):
    def __init__(self, result: dict):
        self.result = result
        super().__init__(f"budget denied by {result['scope']}: requested "
                         f"{result['requested']}, available {result['available']}")


class Conflict(LedgerError):
    """An idempotency key or account name has incompatible parameters."""


class InvalidState(LedgerError):
    """The permit does not allow the requested transition."""


class IntegrityError(LedgerError):
    """A receipt, checkpoint, or materialized balance is inconsistent."""


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def integer(value: int, name: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_INT:
        raise ValueError(f"{name} must be an integer in [{minimum}, {MAX_INT}]")
    return value


def identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or "\x00" in value:
        raise ValueError(f"{name} must contain 1..200 characters, without NUL")
    return value


SCHEMA = (
"""CREATE TABLE IF NOT EXISTS accounts (
 name TEXT PRIMARY KEY, parent TEXT REFERENCES accounts(name), currency TEXT NOT NULL,
 ceiling INTEGER NOT NULL CHECK(ceiling>=0), spent INTEGER NOT NULL CHECK(spent>=0),
 held INTEGER NOT NULL CHECK(held>=0))""",
"""CREATE TABLE IF NOT EXISTS reservations (
 id TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(name),
 amount INTEGER NOT NULL CHECK(amount>0), expires_at INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','started','settled','cancelled','expired')),
 actual INTEGER NOT NULL DEFAULT 0 CHECK(actual>=0),
 refunded INTEGER NOT NULL DEFAULT 0 CHECK(refunded>=0 AND refunded<=actual))""",
"CREATE INDEX IF NOT EXISTS reservations_expiry ON reservations(state,expires_at)",
"""CREATE TABLE IF NOT EXISTS receipts (
 seq INTEGER PRIMARY KEY, at INTEGER NOT NULL, body TEXT NOT NULL,
 previous TEXT NOT NULL, digest TEXT NOT NULL UNIQUE)""",
"""CREATE TABLE IF NOT EXISTS operations (
 key TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL,
 receipt_seq INTEGER NOT NULL UNIQUE REFERENCES receipts(seq))""",
"""CREATE TRIGGER IF NOT EXISTS receipts_no_update BEFORE UPDATE ON receipts
 BEGIN SELECT RAISE(ABORT,'receipts are append-only'); END""",
"""CREATE TRIGGER IF NOT EXISTS receipts_no_delete BEFORE DELETE ON receipts
 BEGIN SELECT RAISE(ABORT,'receipts are append-only'); END""",
)


class Ledger:
    """Durable local ledger; construction initializes a new database if needed.

    ``clock`` returns integer Unix seconds. Inject it only for deterministic tests.
    ``timeout`` is SQLite's bounded lock wait in seconds; lock errors fail closed.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], int] | None = None,
                 timeout: float = 10.0):
        if str(path) == ":memory:":
            raise ValueError("a file path is required for durable, shared state")
        self.path = str(Path(path).resolve())
        self.clock = clock or (lambda: int(time.time()))
        self.timeout = timeout
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, 2):
                    raise IntegrityError(f"unsupported database schema {version}")
                if version == 0 and db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name IN ('accounts','reservations','operations','receipts')").fetchone():
                    raise IntegrityError("unversioned database already contains ledger tables")
                if version == 1:
                    self._migrate_v1(db)
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute("PRAGMA user_version=2")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _migrate_v1(self, db):
        # Audit before trusting a legacy cache or assigning receipt pointers.
        # DDL, backfill and version marker share the initialization transaction.
        _, operations = self._verify_db(db, legacy=True)
        db.execute("ALTER TABLE operations ADD COLUMN receipt_seq INTEGER REFERENCES receipts(seq)")
        db.executemany("UPDATE operations SET receipt_seq=? WHERE key=?",
                       ((row['receipt_seq'], key) for key, row in operations.items()))
        db.execute("CREATE UNIQUE INDEX operations_receipt ON operations(receipt_seq)")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self, *, write: bool = True):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _now(self) -> int:
        return integer(self.clock(), "clock")

    @staticmethod
    def _account(db, name: str) -> dict:
        row = db.execute("SELECT * FROM accounts WHERE name=?", (name,)).fetchone()
        if row is None:
            raise KeyError(f"unknown account: {name}")
        return dict(row)

    @staticmethod
    def _reservation(db, rid: str) -> dict:
        row = db.execute("SELECT * FROM reservations WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(f"unknown reservation: {rid}")
        return dict(row)

    def _path(self, db, name: str) -> list[dict]:
        path, seen = [], set()
        while name is not None:
            if name in seen:
                raise IntegrityError("account hierarchy contains a cycle")
            seen.add(name)
            row = self._account(db, name)
            path.append(row)
            name = row['parent']
        return path

    def _adjust(self, db, name: str, *, spent: int = 0, held: int = 0):
        for row in self._path(db, name):
            new_spent = integer(row['spent'] + spent, "accumulated spend")
            new_held = integer(row['held'] + held, "accumulated holds")
            db.execute("UPDATE accounts SET spent=?,held=? WHERE name=?",
                       (new_spent, new_held, row['name']))

    def _append(self, db, now: int, op: str, params: dict, result: dict,
                *, account: str | None = None, rid: str | None = None,
                key: str | None = None):
        tail = db.execute("SELECT seq,digest FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
        seq, previous = (tail['seq'] + 1, tail['digest']) if tail else (1, ZERO_HASH)
        event = dict(operation=op, parameters=params, result=result, key=key,
                     accounts=self._path(db, account) if account else [],
                     reservations=[self._reservation(db, rid)] if rid else [])
        body = canonical(event)
        digest = hashlib.sha256(canonical([seq, now, previous, body]).encode()).hexdigest()
        db.execute("INSERT INTO receipts VALUES (?,?,?,?,?)", (seq, now, body, previous, digest))
        return seq

    def _expire(self, db, now: int) -> int:
        rows = db.execute("SELECT * FROM reservations WHERE state='reserved' AND expires_at<=? "
                          "ORDER BY id", (now,)).fetchall()
        for row in rows:
            self._adjust(db, row['account'], held=-row['amount'])
            db.execute("UPDATE reservations SET state='expired' WHERE id=?", (row['id'],))
            self._append(db, now, "expire", {"id": row['id']}, {"state": "expired"},
                         account=row['account'], rid=row['id'])
        return len(rows)

    def _operate(self, key: str, op: str, params: dict, action) -> dict:
        identifier(key, "idempotency key")
        request = canonical(dict(operation=op, parameters=params))
        with self._transaction() as db:
            now = self._now()
            prior = db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
            if prior:
                self._check_cached_receipt(db, key, prior)
                if prior['request'] != request:
                    raise Conflict("idempotency key was already used with different parameters")
                response = json.loads(prior['response'])
                response['replayed'] = True
            else:
                self._expire(db, now)
                response, account, rid = action(db, now)
                response['replayed'] = False
                seq = self._append(db, now, op, params, response, account=account, rid=rid, key=key)
                db.execute("INSERT INTO operations (key,request,response,receipt_seq) VALUES (?,?,?,?)",
                           (key, request, canonical(response), seq))
        if response.get('state') == 'denied':
            raise BudgetExceeded(response)
        return response

    @staticmethod
    def _check_cached_receipt(db, key: str, cached):
        row = db.execute("SELECT * FROM receipts WHERE seq=?", (cached['receipt_seq'],)).fetchone()
        if row is None:
            raise IntegrityError("cached operation has no receipt")
        try:
            body = json.loads(row['body'])
            expected = hashlib.sha256(canonical([row['seq'], row['at'], row['previous'], row['body']]).encode()).hexdigest()
            request = canonical(dict(operation=body['operation'], parameters=body['parameters']))
            if (body['key'] != key or expected != row['digest'] or request != cached['request']
                    or canonical(body['result']) != cached['response']):
                raise IntegrityError("cached operation differs from its receipt")
        except (KeyError, TypeError, ValueError, RecursionError) as exc:
            raise IntegrityError("cached operation has an invalid receipt") from exc

    def create_account(self, name: str, ceiling: int, *, currency: str = "USD",
                       parent: str | None = None) -> dict:
        identifier(name, "account name")
        integer(ceiling, "ceiling")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("currency must be a three-letter uppercase currency code")
        if parent is not None:
            identifier(parent, "parent")
        with self._transaction() as db:
            existing = db.execute("SELECT * FROM accounts WHERE name=?", (name,)).fetchone()
            if existing:
                if (existing['ceiling'], existing['currency'], existing['parent']) != (ceiling, currency, parent):
                    raise Conflict("account exists with different parameters")
                return dict(existing)
            if parent is not None and self._account(db, parent)['currency'] != currency:
                raise ValueError("parent and child currencies must match")
            db.execute("INSERT INTO accounts VALUES (?,?,?,?,0,0)", (name, parent, currency, ceiling))
            result = self._account(db, name)
            self._append(db, self._now(), "create_account", dict(name=name, ceiling=ceiling,
                         currency=currency, parent=parent), result, account=name)
            return result

    def reserve(self, account: str, amount: int, *, key: str, ttl: int = 300) -> dict:
        identifier(account, "account")
        integer(amount, "amount", 1)
        integer(ttl, "ttl", 1)
        params = dict(account=account, amount=amount, ttl=ttl)

        def action(db, now):
            expires = integer(now + ttl, "expires_at")
            for scope in self._path(db, account):
                available = scope['ceiling'] - scope['spent'] - scope['held']
                if amount > available:
                    return dict(state="denied", scope=scope['name'], requested=amount,
                                available=available), account, None
            rid = uuid.uuid4().hex
            self._adjust(db, account, held=amount)
            db.execute("INSERT INTO reservations (id,account,amount,expires_at,state) "
                       "VALUES (?,?,?,?,'reserved')", (rid, account, amount, expires))
            return self._reservation(db, rid), account, rid
        return self._operate(key, "reserve", params, action)

    def start(self, rid: str, *, key: str) -> dict:
        """Claim dispatch once. NEVER dispatch again if result['replayed'] is True."""
        def action(db, now):
            row = self._reservation(db, rid)
            if row['state'] != 'reserved':
                raise InvalidState(f"cannot start a {row['state']} reservation")
            db.execute("UPDATE reservations SET state='started' WHERE id=?", (rid,))
            return self._reservation(db, rid), row['account'], rid
        return self._operate(key, "start", dict(id=rid), action)

    def settle(self, rid: str, actual: int, *, key: str) -> dict:
        """Record truth, including overruns; release the complete previous hold."""
        integer(actual, "actual")
        def action(db, now):
            row = self._reservation(db, rid)
            if row['state'] != 'started':
                raise InvalidState(f"cannot settle a {row['state']} reservation")
            self._adjust(db, row['account'], spent=actual, held=-row['amount'])
            db.execute("UPDATE reservations SET state='settled',actual=? WHERE id=?", (actual, rid))
            result = self._reservation(db, rid)
            result['overrun'] = max(0, actual - row['amount'])
            result['released'] = max(0, row['amount'] - actual)
            return result, row['account'], rid
        return self._operate(key, "settle", dict(id=rid, actual=actual), action)

    def cancel(self, rid: str, *, key: str, no_charge: bool = False) -> dict:
        """For started work, caller must explicitly attest that no cost occurred."""
        if type(no_charge) is not bool:
            raise ValueError("no_charge must be bool")
        def action(db, now):
            row = self._reservation(db, rid)
            if row['state'] not in ('reserved', 'started'):
                raise InvalidState(f"cannot cancel a {row['state']} reservation")
            if row['state'] == 'started' and not no_charge:
                raise InvalidState("started work needs no_charge=True or actual settlement")
            self._adjust(db, row['account'], held=-row['amount'])
            db.execute("UPDATE reservations SET state='cancelled' WHERE id=?", (rid,))
            return self._reservation(db, rid), row['account'], rid
        return self._operate(key, "cancel", dict(id=rid, no_charge=no_charge), action)

    def refund(self, rid: str, amount: int, *, key: str) -> dict:
        """Record a confirmed provider refund, bounded by remaining actual charge."""
        integer(amount, "refund", 1)
        def action(db, now):
            row = self._reservation(db, rid)
            if row['state'] != 'settled' or amount > row['actual'] - row['refunded']:
                raise InvalidState("refund exceeds remaining settled charge")
            self._adjust(db, row['account'], spent=-amount)
            db.execute("UPDATE reservations SET refunded=refunded+? WHERE id=?", (amount, rid))
            return self._reservation(db, rid), row['account'], rid
        return self._operate(key, "refund", dict(id=rid, amount=amount), action)

    def expire(self) -> int:
        """Release only undispatched holds. Running work never expires automatically."""
        with self._transaction() as db:
            return self._expire(db, self._now())

    def account(self, name: str) -> dict:
        with self._transaction(write=False) as db:
            result = self._account(db, name)
            result['available'] = result['ceiling'] - result['spent'] - result['held']
            return result

    def reservation(self, rid: str) -> dict:
        with self._transaction(write=False) as db:
            return self._reservation(db, rid)

    def receipts(self) -> list[dict]:
        with self._transaction(write=False) as db:
            return [dict(row) for row in db.execute("SELECT * FROM receipts ORDER BY seq")]

    def verify(self, *, checkpoint: dict | None = None) -> dict:
        """Check chain and projections; optionally verify an externally retained tail.

        Hashes detect edits, not authorship. A database administrator can rewrite
        both history and hashes. Retain checkpoints separately to detect rewrites.
        """
        with self._transaction(write=False) as db:
            return self._verify_db(db, checkpoint=checkpoint)[0]

    @staticmethod
    def _verify_db(db, *, checkpoint=None, legacy=False):
        from .audit import audit_receipts
        summary, accounts, reservations, operations = audit_receipts(
            (dict(row) for row in db.execute("SELECT * FROM receipts ORDER BY seq")), checkpoint)
        actual_accounts = {r['name']: dict(r) for r in db.execute("SELECT * FROM accounts")}
        actual_reservations = {r['id']: dict(r) for r in db.execute("SELECT * FROM reservations")}
        actual_operations = {r['key']: dict(r) for r in db.execute("SELECT * FROM operations")}
        expected_operations = ({key: {k: v for k, v in row.items() if k != 'receipt_seq'}
                                for key, row in operations.items()} if legacy else operations)
        if accounts != actual_accounts or reservations != actual_reservations:
            raise IntegrityError("materialized state differs from receipt history")
        if expected_operations != actual_operations:
            raise IntegrityError("idempotency cache differs from receipt history")
        return summary, operations
