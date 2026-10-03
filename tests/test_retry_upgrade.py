"""Indexed retry bounds and atomic migration of released schema-v1 databases."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from multiprocessing import get_context
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agentledger import BudgetExceeded, IntegrityError, Ledger


def make_legacy(path):
    """Retain real receipts/cache bytes in the released three-column cache schema."""
    ledger = Ledger(path, clock=lambda: 1700000000)
    ledger.create_account('tenant', 100)
    ledger.create_account('session', 80, parent='tenant')
    rid = ledger.reserve('session', 60, key='reserve')['id']
    ledger.start(rid, key='start')
    before = ledger.verify()
    receipts = ledger.receipts()
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.executescript("""
            ALTER TABLE operations RENAME TO operations_v2;
            CREATE TABLE operations (key TEXT PRIMARY KEY, request TEXT NOT NULL, response TEXT NOT NULL);
            INSERT INTO operations SELECT key,request,response FROM operations_v2;
            DROP TABLE operations_v2;
            PRAGMA user_version=1;
        """)
    return before, receipts, rid


def crash_upgrade(path):
    class InterruptedLedger(Ledger):
        def _migrate_v1(self, db):
            super()._migrate_v1(db)
            os._exit(23)
    InterruptedLedger(path)


class CountedLedger(Ledger):
    instructions = 0

    @contextmanager
    def _connection(self):
        with super()._connection() as db:
            def count():
                self.instructions += 100
                return 0
            db.set_progress_handler(count, 100)
            yield db


class RetryUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.db'

    def test_oldest_retry_work_does_not_grow_with_history(self):
        counts = []
        for size in (20, 400):
            ledger = CountedLedger(Path(self.temp.name) / f'{size}.db', clock=lambda: 1700000000)
            ledger.create_account('tenant', 1)
            original = ledger.reserve('tenant', 1, key='oldest')
            for i in range(size):
                with self.assertRaises(BudgetExceeded):
                    ledger.reserve('tenant', 1, key=f'denied:{i}')
            ledger.instructions = 0
            replayed = ledger.reserve('tenant', 1, key='oldest')
            counts.append(ledger.instructions)
            self.assertEqual(replayed['id'], original['id'])
            self.assertTrue(replayed['replayed'])
            self.assertTrue(ledger.verify()['ok'])
        # SQLite VM work, not a timing guarantee or a mirrored SQL assertion.
        self.assertLessEqual(counts[1], counts[0] + 200)
        self.assertLess(counts[1], 1000)

    def test_legacy_upgrade_preserves_receipts_checkpoint_and_unknown_started_hold(self):
        before, receipts, rid = make_legacy(self.path)
        ledger = Ledger(self.path, clock=lambda: 1701000000)
        self.assertEqual(ledger.receipts(), receipts)
        self.assertEqual(ledger.verify(checkpoint=before['checkpoint']), before)
        self.assertEqual(ledger.expire(), 0)
        self.assertEqual(ledger.account('tenant')['held'], 60)
        self.assertTrue(ledger.start(rid, key='start')['replayed'])
        ledger.settle(rid, 110, key='actual')
        self.assertEqual(ledger.account('tenant')['available'], -10)
        self.assertTrue(ledger.verify()['ok'])

    def test_corrupted_legacy_cache_or_projection_does_not_get_migrated(self):
        for corruption in ("UPDATE operations SET response='{}' WHERE key='start'",
                           "DELETE FROM operations WHERE key='start'",
                           "UPDATE accounts SET held=0 WHERE name='tenant'"):
            path = Path(self.temp.name) / f'{len(list(Path(self.temp.name).glob("*.db")))}.db'
            _, receipts, _ = make_legacy(path)
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                db.execute(corruption)
            with self.assertRaises(IntegrityError):
                Ledger(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
                self.assertNotIn('receipt_seq', [r[1] for r in db.execute('PRAGMA table_info(operations)')])
                db.row_factory = sqlite3.Row
                self.assertEqual([dict(r) for r in db.execute('SELECT * FROM receipts ORDER BY seq')], receipts)

    def test_process_exit_during_upgrade_rolls_back_ddl_and_backfill(self):
        before, receipts, _ = make_legacy(self.path)
        process = get_context('spawn').Process(target=crash_upgrade, args=(str(self.path),))
        process.start()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join()
            self.fail('migration interruption timed out')
        self.assertEqual(process.exitcode, 23)
        process.close()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertNotIn('receipt_seq', [r[1] for r in db.execute('PRAGMA table_info(operations)')])
        recovered = Ledger(self.path)
        self.assertEqual(recovered.receipts(), receipts)
        self.assertEqual(recovered.verify(), before)

    def test_concurrent_initializers_upgrade_once_and_preserve_dispatch_claim(self):
        before, receipts, rid = make_legacy(self.path)
        def reopen(_):
            ledger = Ledger(self.path)
            return ledger.start(rid, key='start')['replayed']
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(reopen, range(16))), [True] * 16)
        ledger = Ledger(self.path)
        self.assertEqual(ledger.receipts(), receipts)
        self.assertEqual(ledger.verify(), before)

    def test_redirected_or_missing_receipt_pointer_fails_retry_and_verification(self):
        make_legacy(self.path)
        ledger = Ledger(self.path)
        for pointer in (1, None, 99999):
            with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
                db.execute('UPDATE operations SET receipt_seq=? WHERE key=?', (pointer, 'start'))
            with self.assertRaises(IntegrityError):
                ledger.start(ledger.reserve('session', 60, key='reserve')['id'], key='start')
            with self.assertRaises(IntegrityError):
                ledger.verify()


if __name__ == '__main__':
    unittest.main()
