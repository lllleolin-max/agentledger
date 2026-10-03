"""Portable UTF-8 JSONL export and offline audit: python -m examples.stream_audit.

Supply --db, --output and an independently retained --checkpoint. No shell
transcoding or provider requests occur. Partial output is never an audit success.
"""

import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new JSONL file; existing files are never overwritten')
    parser.add_argument('--checkpoint', required=True, type=Path)
    args = parser.parse_args()
    with args.output.open('xb') as output:
        subprocess.run([sys.executable, '-m', 'agentledger', '--db', str(args.db),
                        'receipts', '--jsonl'], stdout=output, check=True)
    subprocess.run([sys.executable, '-m', 'agentledger', 'verify-receipts',
                    str(args.output), '--jsonl', '--checkpoint', str(args.checkpoint)], check=True)


if __name__ == '__main__':
    main()
