from contextlib import closing
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agentledger import IntegrityError, Ledger
from agentledger.replay import replay
from agentledger.cli import main, read_json


class AuditCacheRegression(unittest.TestCase):
    def test_corrupted_cached_response_fails_verification_and_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.db"
            ledger = Ledger(path)
            ledger.create_account("tenant", 100)
            original = ledger.reserve("tenant", 50, key="job")
            corrupted = dict(original, amount=1)
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                db.execute("UPDATE operations SET response=? WHERE key='job'", (json.dumps(corrupted),))
            with self.assertRaises(IntegrityError):
                ledger.verify()
            with self.assertRaises(IntegrityError):
                ledger.reserve("tenant", 50, key="job")

    def test_deleted_idempotency_cache_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.db"
            ledger = Ledger(path)
            ledger.create_account("tenant", 100)
            ledger.reserve("tenant", 50, key="job")
            with closing(sqlite3.connect(path, isolation_level=None)) as db:
                db.execute("DELETE FROM operations")
            with self.assertRaises(IntegrityError):
                ledger.verify()


class ReplayBoundaryRegression(unittest.TestCase):
    def test_valid_user_ids_do_not_collide_with_internal_operation_names(self):
        workload = dict(currency="USD", tenant_limit=100, sessions={"one": 100},
                        batches=[[dict(id=name, session="one", estimate=10, actual=5)
                                  for name in ("x", "start:x", "settle:x", "a" * 200)]])
        report = replay(workload)
        self.assertEqual(report['policies']['hierarchical_reservations']['actual_spend'], 20)

    def test_malformed_shapes_and_unsupported_fields_are_validation_errors(self):
        cases = [None, [], dict(currency="USD", tenant_limit=100, sessions={"s": 100}, batches=None),
                 dict(currency="USD", tenant_limit=100, sessions={"s": 100}, batches=[[None]]),
                 dict(currency="USD", tenant_limit=100, sessions={"s": 100}, batches=[], budget=1)]
        for workload in cases:
            with self.subTest(workload=workload), self.assertRaises(ValueError):
                replay(workload)

    def test_full_length_session_identifier_is_usable(self):
        session = "s" * 200
        report = replay(dict(currency="USD", tenant_limit=10, sessions={session: 10},
                             batches=[[dict(id="x", session=session, estimate=10, actual=5)]]))
        self.assertEqual(report['policies']['hierarchical_reservations']['actual_spend'], 5)

    def test_workload_bounds_are_checked_before_simulation(self):
        workload = dict(currency="USD", tenant_limit=100, sessions={"s": 100},
                        batches=[[dict(id=str(i), session="s", estimate=1, actual=1)
                                  for i in range(10001)]])
        with self.assertRaisesRegex(ValueError, "10000 calls"):
            replay(workload)

    def test_cli_json_error_exit_and_windows_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_text("null", encoding="utf-8-sig")
            err, out = io.StringIO(), io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                code = main(["replay", str(path)])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(err.getvalue())['error'], "ValueError")
            self.assertEqual(out.getvalue(), "")
            path.write_text('{"hello": "world"}', encoding="utf-8-sig")
            self.assertEqual(read_json(path), {"hello": "world"})


if __name__ == "__main__":
    unittest.main()
