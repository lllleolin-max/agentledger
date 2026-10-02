from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import closing
from multiprocessing import get_context
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agentledger import BudgetExceeded, Conflict, IntegrityError, InvalidState, Ledger
from agentledger.ledger import MAX_INT
from agentledger.replay import DEMO, replay


def reserve_in_process(args):
    path, i = args
    ledger = Ledger(path)
    try:
        return ledger.reserve("session", 10, key=f"worker-{i}")['id']
    except BudgetExceeded:
        return None


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.db"
        self.now = 1700000000
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.ledger.create_account("tenant", 100)
        self.ledger.create_account("session", 80, parent="tenant")

    def permit(self, amount=60, key="reserve"):
        return self.ledger.reserve("session", amount, key=key)['id']

    def started(self, amount=60):
        rid = self.permit(amount)
        self.ledger.start(rid, key="start")
        return rid

    def test_actual_releases_hold_and_propagates_to_parent(self):
        rid = self.started()
        self.assertEqual(self.ledger.settle(rid, 40, key="settle")['released'], 20)
        for name in ("session", "tenant"):
            account = self.ledger.account(name)
            self.assertEqual((account['spent'], account['held']), (40, 0))
        self.assertTrue(self.ledger.verify()['ok'])

    def test_parent_blocks_sibling_oversubscription(self):
        self.ledger.create_account("sibling", 80, parent="tenant")
        self.permit(60)
        with self.assertRaises(BudgetExceeded) as cm:
            self.ledger.reserve("sibling", 50, key="sibling")
        self.assertEqual(cm.exception.result['scope'], "tenant")
        self.assertEqual(self.ledger.account("sibling")['held'], 0)
        self.assertEqual(self.ledger.account("tenant")['held'], 60)

    def test_retry_is_idempotent_and_parameter_conflict_is_rejected(self):
        first = self.ledger.reserve("session", 60, key="same")
        again = self.ledger.reserve("session", 60, key="same")
        self.assertEqual(first['id'], again['id'])
        self.assertTrue(again['replayed'])
        self.assertEqual(self.ledger.account("tenant")['held'], 60)
        with self.assertRaises(Conflict):
            self.ledger.reserve("session", 59, key="same")
        with self.assertRaises(Conflict):
            self.ledger.start(first['id'], key="same")

    def test_denial_remains_idempotent_after_refund(self):
        rid = self.permit(60)
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve("session", 30, key="denied")
        self.ledger.cancel(rid, key="cancel")
        with self.assertRaises(BudgetExceeded) as cm:
            self.ledger.reserve("session", 30, key="denied")
        self.assertTrue(cm.exception.result['replayed'])
        self.ledger.reserve("session", 30, key="new-attempt")

    def test_pending_expires_at_exact_boundary(self):
        rid = self.ledger.reserve("session", 60, key="reserve", ttl=10)['id']
        self.now += 9
        self.assertEqual(self.ledger.expire(), 0)
        self.now += 1
        self.assertEqual(self.ledger.expire(), 1)
        self.assertEqual(self.ledger.reservation(rid)['state'], "expired")
        self.assertEqual(self.ledger.account("tenant")['held'], 0)
        with self.assertRaises(InvalidState):
            self.ledger.start(rid, key="too-late")

    def test_running_work_does_not_expire_on_reopen(self):
        rid = self.started()
        self.now += 100000
        reopened = Ledger(self.path, clock=lambda: self.now)
        self.assertEqual(reopened.expire(), 0)
        self.assertEqual(reopened.account("tenant")['held'], 60)
        reopened.settle(rid, 55, key="recover")
        self.assertEqual(reopened.account("session")['spent'], 55)

    def test_start_claim_and_replayed_dispatch_are_distinguishable(self):
        rid = self.permit()
        self.assertFalse(self.ledger.start(rid, key="start")['replayed'])
        self.assertTrue(self.ledger.start(rid, key="start")['replayed'])
        with self.assertRaises(InvalidState):
            self.ledger.start(rid, key="another-worker")

    def test_actual_overrun_is_recorded_then_blocks_admission(self):
        rid = self.started()
        self.assertEqual(self.ledger.settle(rid, 110, key="settle")['overrun'], 50)
        self.assertEqual(self.ledger.account("tenant")['available'], -10)
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve("session", 1, key="blocked")
        self.assertTrue(self.ledger.verify()['ok'])

    def test_partial_refunds_are_idempotent_and_cannot_exceed_actual(self):
        rid = self.started()
        self.ledger.settle(rid, 50, key="settle")
        self.ledger.refund(rid, 20, key="refund")
        self.ledger.refund(rid, 20, key="refund")
        with self.assertRaises(InvalidState):
            self.ledger.refund(rid, 31, key="too-much")
        self.ledger.refund(rid, 30, key="rest")
        self.assertEqual(self.ledger.account("tenant")['spent'], 0)

    def test_started_cancel_requires_no_charge_attestation(self):
        rid = self.started()
        with self.assertRaises(InvalidState):
            self.ledger.cancel(rid, key="cancel")
        self.ledger.cancel(rid, key="cancel", no_charge=True)
        self.assertEqual(self.ledger.account("tenant")['held'], 0)
        with self.assertRaises(InvalidState):
            self.ledger.settle(rid, 1, key="late")

    def test_invalid_numbers_and_currencies_cannot_mutate_ledger(self):
        before = self.ledger.verify()
        for amount in (-1, 0, 1.5, True, float('inf'), float('nan'), MAX_INT + 1, "1"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.ledger.reserve("session", amount, key="bad")
        with self.assertRaises(ValueError):
            self.ledger.create_account("eur", 10, currency="EUR", parent="tenant")
        self.assertEqual(before, self.ledger.verify())

    def test_currency_roots_are_independent(self):
        self.ledger.create_account("euro", 100, currency="EUR")
        self.ledger.reserve("euro", 100, key="euro-reserve")
        self.assertEqual(self.ledger.account("tenant")['held'], 0)

    def test_database_integer_overflow_rolls_back_entire_settlement(self):
        self.ledger.create_account("large", MAX_INT)
        r1 = self.ledger.reserve("large", 1, key="large-1")['id']
        r2 = self.ledger.reserve("large", 1, key="large-2")['id']
        self.ledger.start(r1, key="start-1")
        self.ledger.start(r2, key="start-2")
        self.ledger.settle(r1, MAX_INT, key="settle-1")
        with self.assertRaises(ValueError):
            self.ledger.settle(r2, 1, key="settle-2")
        self.assertEqual(self.ledger.reservation(r2)['state'], "started")
        self.assertEqual(self.ledger.account("large")['held'], 1)

    def test_append_only_trigger_and_projection_corruption_detection(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM receipts")
            db.execute("UPDATE accounts SET spent=1 WHERE name='tenant'")
        with self.assertRaises(IntegrityError):
            self.ledger.verify()

    def test_chain_tampering_and_external_checkpoint(self):
        checkpoint = self.ledger.verify()['checkpoint']
        self.permit()
        self.assertTrue(self.ledger.verify(checkpoint=checkpoint)['ok'])
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("DROP TRIGGER receipts_no_update")
            db.execute("UPDATE receipts SET at=at+1 WHERE seq=1")
        with self.assertRaises(IntegrityError):
            self.ledger.verify(checkpoint=checkpoint)

    def test_unknown_schema_is_not_silently_migrated(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("PRAGMA user_version=999")
        with self.assertRaises(IntegrityError):
            Ledger(self.path)

    def test_thread_race_cannot_exceed_budget(self):
        def reserve(i):
            try:
                self.ledger.reserve("session", 10, key=f"thread-{i}")
                return True
            except BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(32)))
        self.assertEqual(sum(results), 8)
        self.assertEqual(self.ledger.account("session")['held'], 80)
        self.assertTrue(self.ledger.verify()['ok'])

    def test_spawn_process_race_cannot_exceed_budget(self):
        with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
            results = list(pool.map(reserve_in_process, [(str(self.path), i) for i in range(24)]))
        self.assertEqual(sum(r is not None for r in results), 8)
        self.assertEqual(self.ledger.account("tenant")['held'], 80)
        self.assertTrue(self.ledger.verify()['ok'])


class ReplayTests(unittest.TestCase):
    def test_ablation_exposes_parent_limit_and_posthoc_race(self):
        report = replay(DEMO)
        policies = report['policies']
        self.assertEqual(policies['hierarchical_reservations']['actual_spend'], 8500)
        self.assertEqual(policies['hierarchical_reservations']['tenant_overspend'], 0)
        self.assertEqual(policies['post_hoc']['tenant_overspend'], 11000)
        self.assertEqual(policies['session_only']['tenant_overspend'], 11000)
        self.assertEqual(report, replay(DEMO))

    def test_arbitrary_workload_can_falsify_upper_bound_assumption(self):
        report = replay(dict(currency="EUR", tenant_limit=100, sessions={"x": 100},
                             batches=[[dict(id="x", session="x", estimate=20, actual=120)]]))
        self.assertEqual(report['policies']['hierarchical_reservations']['tenant_overspend'], 20)
        self.assertEqual(report['avoided_overspend']['post_hoc'], 0)


if __name__ == "__main__":
    unittest.main()

