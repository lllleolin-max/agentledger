"""Full retained-history exports, snapshot consistency and hostile stream tails."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from agentledger import BudgetExceeded, IntegrityError, Ledger, verify_receipt_stream, verify_receipts
from agentledger.cli import iter_jsonl, main
from agentledger.ledger import canonical


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.db'
        self.ledger = Ledger(self.path, clock=lambda: 1700000000)
        self.ledger.create_account('租户-Δ-😀', 0)

    def write_lines(self, receipts, *, bom=False):
        path = Path(self.temp.name) / 'receipts.jsonl'
        with path.open('w', encoding='utf-8-sig' if bom else 'utf-8', newline='\n') as output:
            for receipt in receipts:
                output.write(json.dumps(receipt, ensure_ascii=False) + '\n')
        return path

    def cli_audit(self, path, *extra):
        out, err = io.StringIO(), io.StringIO()
        missing = Path(self.temp.name) / 'offline-must-not-exist.db'
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['--db', str(missing), 'verify-receipts', str(path), '--jsonl', *extra])
        self.assertFalse(missing.exists())
        return code, out.getvalue(), err.getvalue()

    def test_snapshot_stays_consistent_while_writer_appends(self):
        self.ledger.create_account('other', 10)
        expected = self.ledger.verify()
        iterator = self.ledger.iter_receipts()
        first = next(iterator)  # Establish the SQLite read snapshot.
        Ledger(self.path).reserve('other', 5, key='concurrent-append')
        exported = [first, *iterator]
        self.assertEqual(verify_receipt_stream(iter(exported)), expected)
        self.assertEqual(len(self.ledger.receipts()), len(exported) + 1)
        self.assertEqual(verify_receipts(exported), expected)

    def test_unicode_bom_and_checkpoint_roundtrip_without_database(self):
        path = self.write_lines(self.ledger.iter_receipts(), bom=True)
        checkpoint = self.ledger.verify()['checkpoint']
        retained = Path(self.temp.name) / 'checkpoint.json'
        retained.write_text(json.dumps(checkpoint), encoding='utf-8')
        code, out, err = self.cli_audit(path, '--checkpoint', str(retained))
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), self.ledger.verify())
        path.write_bytes(b'')
        code, out, err = self.cli_audit(path, '--checkpoint', str(retained))
        self.assertEqual((code, out), (2, ''))
        self.assertEqual(json.loads(err)['error'], 'IntegrityError')

    def test_malformed_or_non_utf8_tail_never_reports_a_success(self):
        for tail in (b'{"seq":', b'\xff\n', b'\n', b'null\n'):
            path = self.write_lines(self.ledger.iter_receipts())
            with path.open('ab') as output:
                output.write(tail)
            code, out, err = self.cli_audit(path)
            self.assertEqual((code, out), (2, ''))
            self.assertIn(json.loads(err)['error'], ('IntegrityError', 'ValueError'))

    def test_rehashed_duplicate_key_is_rejected(self):
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve('租户-Δ-😀', 1, key='denied')
        receipts = self.ledger.receipts()
        duplicate = dict(receipts[-1], seq=receipts[-1]['seq'] + 1, previous=receipts[-1]['digest'])
        duplicate['digest'] = hashlib.sha256(canonical(
            [duplicate['seq'], duplicate['at'], duplicate['previous'], duplicate['body']]).encode()).hexdigest()
        receipts.append(duplicate)
        with self.assertRaisesRegex(IntegrityError, 'duplicate idempotency key'):
            verify_receipt_stream(iter(receipts))

    def test_jsonl_line_bound_and_valid_last_line_without_newline(self):
        path = self.write_lines(self.ledger.iter_receipts())
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        self.assertEqual(self.cli_audit(path)[0], 0)
        cap = 16 * 1024 * 1024
        path.write_bytes(b'{}' + b' ' * (cap - 3) + b'\n')
        self.assertEqual(list(iter_jsonl(path)), [{}])
        path.write_bytes(b'{}' + b' ' * (cap - 2) + b'\n')
        with self.assertRaisesRegex(ValueError, 'exceeds the 16 MiB limit'):
            list(iter_jsonl(path))

    def test_cli_jsonl_export_retains_history_larger_than_legacy_import_limit(self):
        parent = None
        for i in range(80):
            name = f'{i:03d}:' + 'x' * 196
            self.ledger.create_account(name, 0, parent=parent)
            parent = name
        for i in range(700):
            with self.assertRaises(BudgetExceeded):
                self.ledger.reserve(parent, 1, key=f'denied:{i}')
        expected = self.ledger.verify()
        path = Path(self.temp.name) / 'large.jsonl'
        with path.open('w', encoding='utf-8', newline='\n') as output, redirect_stdout(output):
            self.assertEqual(main(['--db', str(self.path), 'receipts', '--jsonl']), 0)
        self.assertGreater(path.stat().st_size, 16 * 1024 * 1024)
        code, out, err = self.cli_audit(path)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), expected)


if __name__ == '__main__':
    unittest.main()
