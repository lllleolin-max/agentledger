"""A successful mutation must return valid JSON on legacy Windows streams."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from agentledger import Ledger
from agentledger.cli import main


class CliEncodingTests(unittest.TestCase):
    def test_unicode_identifiers_commit_and_return_json_on_strict_legacy_streams(self):
        for encoding in ('cp1252', 'cp936', 'ascii'):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'ledger.db'
                raw_out, raw_err = io.BytesIO(), io.BytesIO()
                out = io.TextIOWrapper(raw_out, encoding=encoding, errors='strict')
                err = io.TextIOWrapper(raw_err, encoding=encoding, errors='strict')
                try:
                    with redirect_stdout(out), redirect_stderr(err):
                        code = main(['--db', str(path), 'account', '租户-Δ-😀', '100'])
                    out.flush()
                    err.flush()
                    self.assertEqual(code, 0)
                    self.assertEqual(raw_err.getvalue(), b'')
                    response = json.loads(raw_out.getvalue().decode('ascii'))
                    self.assertEqual(response['name'], '租户-Δ-😀')
                    ledger = Ledger(path)
                    self.assertEqual(ledger.account(response['name'])['ceiling'], 100)
                    self.assertEqual(ledger.verify()['receipts'], 1)
                finally:
                    out.close()
                    err.close()


if __name__ == '__main__':
    unittest.main()
