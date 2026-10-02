"""Machine-readable CLI. Domain errors are JSON on stderr with nonzero status."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .ledger import BudgetExceeded, Ledger, LedgerError
from .audit import verify_receipts
from .replay import DEMO, replay


def read_json(path: Path):
    # Read one extra byte to detect oversize input without allocating the file.
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("JSON input exceeds the 16 MiB limit")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("JSON input must be UTF-8 with bounded nesting") from exc


def parser():
    p = argparse.ArgumentParser(description="Local transactional agent spending permits (integer millionths)")
    p.add_argument("--db", default="agentledger.db", help="SQLite file on a local filesystem")
    sub = p.add_subparsers(dest="command", required=True)
    account = sub.add_parser("account", help="create an immutable budget scope")
    account.add_argument("name")
    account.add_argument("ceiling", type=int)
    account.add_argument("--currency", default="USD")
    account.add_argument("--parent")
    reserve = sub.add_parser("reserve", help="reserve a maximum tool cost")
    reserve.add_argument("account")
    reserve.add_argument("amount", type=int)
    reserve.add_argument("--ttl", type=int, default=300)
    reserve.add_argument("--key", required=True)
    for command in ("start", "settle", "cancel", "refund"):
        q = sub.add_parser(command)
        q.add_argument("id")
        q.add_argument("--key", required=True)
        if command in ("settle", "refund"):
            q.add_argument("amount", type=int)
        if command == "cancel":
            q.add_argument("--no-charge", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("account")
    get = sub.add_parser("get")
    get.add_argument("id")
    sub.add_parser("expire")
    verify = sub.add_parser("verify")
    verify.add_argument("--checkpoint", type=Path, help="JSON object with seq and digest")
    sub.add_parser("receipts", help="export append-only receipts as JSON")
    audit = sub.add_parser("verify-receipts", help="audit an exported JSON receipt list without opening a database")
    audit.add_argument("input", type=Path)
    audit.add_argument("--checkpoint", type=Path, help="externally retained JSON object with seq and digest")
    sub.add_parser("demo", help="run deterministic built-in policy ablation")
    report = sub.add_parser("replay", help="run a supplied workload JSON")
    report.add_argument("workload", type=Path)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in ("demo", "replay"):
            result = replay(DEMO if args.command == "demo" else read_json(args.workload))
        elif args.command == "verify-receipts":
            result = verify_receipts(read_json(args.input),
                                    checkpoint=read_json(args.checkpoint) if args.checkpoint else None)
        else:
            ledger = Ledger(args.db)
            if args.command == "account":
                result = ledger.create_account(args.name, args.ceiling, currency=args.currency, parent=args.parent)
            elif args.command == "reserve":
                result = ledger.reserve(args.account, args.amount, key=args.key, ttl=args.ttl)
            elif args.command == "start":
                result = ledger.start(args.id, key=args.key)
            elif args.command == "settle":
                result = ledger.settle(args.id, args.amount, key=args.key)
            elif args.command == "cancel":
                result = ledger.cancel(args.id, key=args.key, no_charge=args.no_charge)
            elif args.command == "refund":
                result = ledger.refund(args.id, args.amount, key=args.key)
            elif args.command == "status":
                result = ledger.account(args.account)
            elif args.command == "get":
                result = ledger.reservation(args.id)
            elif args.command == "expire":
                result = dict(expired=ledger.expire())
            elif args.command == "verify":
                result = ledger.verify(checkpoint=read_json(args.checkpoint) if args.checkpoint else None)
            else:
                result = ledger.receipts()
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except BudgetExceeded as exc:
        print(json.dumps(dict(error=type(exc).__name__, details=exc.result)), file=sys.stderr)
        return 3
    except (LedgerError, ValueError, KeyError, OSError, sqlite3.Error) as exc:
        print(json.dumps(dict(error=type(exc).__name__, message=str(exc))), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
