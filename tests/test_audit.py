"""Semantic receipt, conservation and real process-interruption probes."""

from contextlib import closing, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from multiprocessing import get_context
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest

from agentledger import BudgetExceeded, IntegrityError, Ledger, verify_receipts
from agentledger.cli import main
from agentledger.ledger import ZERO_HASH, canonical


def crash_reservation(path, committed):
    ledger = Ledger(path)
    if not committed:
        # reserve() has updated ancestor holds and inserted its permit before
        # appending the receipt. Terminate the process at that transaction cut.
        ledger._append = lambda *args, **kwargs: os._exit(23)
    ledger.reserve("session", 30, key="interrupted")
    os._exit(23)


def rehash(receipts):
    """Model a writer that recomputes hashes; hashes alone cannot prove semantics."""
    previous = ZERO_HASH
    for row in receipts:
        row['previous'] = previous
        row['digest'] = hashlib.sha256(canonical(
            [row['seq'], row['at'], previous, row['body']]).encode()).hexdigest()
        previous = row['digest']


class ReceiptAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.db'
        self.now = 1700000000
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.ledger.create_account('tenant', 200)
        self.ledger.create_account('session', 150, parent='tenant')

    def test_export_roundtrip_includes_refund_cancel_expiry_and_denial(self):
        first = self.ledger.reserve('session', 100, key='reserve')['id']
        self.ledger.start(first, key='start')
        self.ledger.settle(first, 80, key='settle')
        self.ledger.refund(first, 20, key='refund')
        second = self.ledger.reserve('session', 50, key='second')['id']
        self.ledger.cancel(second, key='cancel')
        self.ledger.reserve('session', 60, key='expire', ttl=1)
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve('session', 40, key='denied')
        self.now += 1
        self.ledger.expire()
        checkpoint = self.ledger.verify()['checkpoint']
        exported = json.loads(json.dumps(self.ledger.receipts()))
        original = copy.deepcopy(exported)
        self.assertEqual(verify_receipts(exported, checkpoint=checkpoint), self.ledger.verify())
        self.assertEqual(exported, original)

    def test_semantic_replay_rejects_rehashed_snapshot_laundering(self):
        self.ledger.reserve('session', 50, key='first')
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute('UPDATE accounts SET held=0')
        self.ledger.reserve('session', 60, key='second')
        with self.assertRaisesRegex(IntegrityError, 'conservation'):
            verify_receipts(self.ledger.receipts())

    def test_rehashed_invalid_result_and_unknown_operation_are_rejected(self):
        self.ledger.reserve('session', 50, key='first')
        for change in ('result', 'operation', 'parameters'):
            receipts = self.ledger.receipts()
            body = json.loads(receipts[-1]['body'])
            if change == 'result':
                body['result']['amount'] = 1
            elif change == 'operation':
                body['operation'] = 'invented'
            else:
                body['parameters']['ignored'] = True
            receipts[-1]['body'] = canonical(body)
            rehash(receipts)
            with self.subTest(change=change), self.assertRaises(IntegrityError):
                verify_receipts(receipts)

    def test_external_checkpoint_detects_truncation_and_rewritten_prefix(self):
        checkpoint = self.ledger.verify()['checkpoint']
        self.ledger.reserve('session', 30, key='later')
        receipts = self.ledger.receipts()
        self.assertTrue(verify_receipts(receipts, checkpoint=checkpoint)['ok'])
        with self.assertRaises(IntegrityError):
            verify_receipts(receipts[:1], checkpoint=checkpoint)
        receipts[0]['at'] += 1
        rehash(receipts)
        # The semantically identical rewrite is consistent without an external
        # commitment; the retained prior prefix still exposes that rewrite.
        self.assertTrue(verify_receipts(receipts)['ok'])
        with self.assertRaises(IntegrityError):
            verify_receipts(receipts, checkpoint=checkpoint)

    def test_reorder_tamper_and_malformed_exports_fail(self):
        receipts = self.ledger.receipts()
        modified = copy.deepcopy(receipts)
        modified[0]['at'] += 1
        for invalid in (modified, list(reversed(receipts)), [None], [dict(receipts[0], body='null')]):
            with self.subTest(invalid=invalid), self.assertRaises(IntegrityError):
                verify_receipts(invalid)
        for invalid in (None, {}, 'receipts'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                verify_receipts(invalid)

    def test_offline_cli_uses_no_database_and_reports_json_error(self):
        filename = Path(self.temp.name) / 'receipts.json'
        database = Path(self.temp.name) / 'must-not-exist.db'
        filename.write_text(json.dumps(self.ledger.receipts()), encoding='utf-8')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['--db', str(database), 'verify-receipts', str(filename)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['receipts'], 2)
        self.assertFalse(database.exists())
        filename.write_text('null', encoding='utf-8')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['verify-receipts', str(filename)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err.getvalue())['error'], 'ValueError')

    def test_seeded_lifecycle_matches_independent_descendant_sums(self):
        ledger = self.ledger
        ledger.create_account('other', 150, parent='tenant')
        rng, ids = random.Random(42), []
        for i in range(50):
            account = rng.choice(('tenant', 'session', 'other'))
            try:
                rid = ledger.reserve(account, rng.randint(1, 20), key=f'r:{i}', ttl=1)['id']
            except BudgetExceeded:
                continue
            ids.append(rid)
            mode = rng.randrange(4)
            if mode == 0:
                ledger.cancel(rid, key=f'c:{i}')
            elif mode == 1:
                self.now += 1
                ledger.expire()
            elif mode == 2:
                ledger.start(rid, key=f's:{i}')
                actual = rng.randint(0, 25)
                ledger.settle(rid, actual, key=f't:{i}')
                if actual:
                    ledger.refund(rid, rng.randint(1, actual), key=f'f:{i}')
            else:
                ledger.start(rid, key=f's:{i}')
        rows = [ledger.reservation(rid) for rid in ids]
        for name in ('tenant', 'session', 'other'):
            included = [r for r in rows if name == 'tenant' or r['account'] == name]
            spent = sum(r['actual'] - r['refunded'] for r in included)
            held = sum(r['amount'] for r in included if r['state'] in ('reserved', 'started'))
            self.assertEqual((ledger.account(name)['spent'], ledger.account(name)['held']), (spent, held))
        self.assertTrue(ledger.verify()['ok'])

    def test_process_exit_before_and_after_commit_preserves_atomicity(self):
        ctx = get_context('spawn')
        for committed in (False, True):
            with self.subTest(committed=committed):
                before = self.ledger.verify()
                process = ctx.Process(target=crash_reservation, args=(str(self.path), committed))
                process.start()
                process.join(20)
                if process.is_alive():
                    process.terminate()
                    process.join()
                    self.fail('interruption probe timed out')
                self.assertEqual(process.exitcode, 23)
                process.close()
                reopened = Ledger(self.path, clock=lambda: self.now)
                if not committed:
                    self.assertEqual(reopened.verify(), before)
                    self.assertEqual(reopened.account('tenant')['held'], 0)
                else:
                    self.assertTrue(reopened.verify()['ok'])
                    self.assertEqual(reopened.account('tenant')['held'], 30)
                response = reopened.reserve('session', 30, key='interrupted')
                self.assertEqual(response['replayed'], committed)
                self.assertEqual(reopened.account('tenant')['held'], 30)
                # Reset the fixture between the two genuinely separate crashes.
                reopened.cancel(response['id'], key=f'cleanup:{committed}')
                if not committed:
                    # Keep history/cache valid: use a fresh database for the
                    # second cut instead of manually deleting an operation.
                    self.path = Path(self.temp.name) / 'committed.db'
                    self.ledger = Ledger(self.path, clock=lambda: self.now)
                    self.ledger.create_account('tenant', 200)
                    self.ledger.create_account('session', 150, parent='tenant')


if __name__ == '__main__':
    unittest.main()
