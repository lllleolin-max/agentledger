"""Duplicate JSON properties cannot silently replace an audit input value."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from agentledger import IntegrityError, Ledger, verify_receipts
from agentledger.cli import main, read_json
from agentledger.ledger import canonical


class JsonAmbiguityTests(unittest.TestCase):
    def test_duplicate_outer_property_is_rejected_by_jsonl_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / 'ledger.db')
            ledger.create_account('tenant', 100)
            path = Path(folder) / 'receipts.jsonl'
            row = json.dumps(ledger.receipts()[0])
            path.write_text('{"seq":999,' + row[1:] + '\n', encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(['verify-receipts', str(path), '--jsonl'])
            self.assertEqual((code, out.getvalue()), (2, ''))
            self.assertEqual(json.loads(err.getvalue())['error'], 'ValueError')

    def test_duplicate_hashed_event_property_is_rejected_after_rehash(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / 'ledger.db')
            ledger.create_account('tenant', 100)
            receipts = ledger.receipts()
            row = receipts[0]
            row['body'] = '{"operation":"unknown",' + row['body'][1:]
            row['digest'] = hashlib.sha256(canonical(
                [row['seq'], row['at'], row['previous'], row['body']]).encode()).hexdigest()
            with self.assertRaises(IntegrityError):
                verify_receipts(receipts)

    def test_duplicate_workload_property_is_rejected_before_use(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'workload.json'
            path.write_text('{"tenant_limit":100,"tenant_limit":1}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'duplicate JSON property'):
                read_json(path)


if __name__ == '__main__':
    unittest.main()
