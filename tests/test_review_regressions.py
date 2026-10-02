from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from agentledger import IntegrityError, Ledger


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


if __name__ == "__main__":
    unittest.main()
