"""Disclosed local latency benchmark, not a provider cost or scale claim."""
import argparse
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import tempfile
import time

from agentledger import Ledger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations", type=int, default=300)
    args = parser.parse_args()
    if args.operations < 1:
        parser.error("operations must be positive")
    with tempfile.TemporaryDirectory(prefix="agentledger-bench-") as folder:
        dbpath = Path(folder) / "bench.db"
        ledger = Ledger(dbpath)
        ledger.create_account("tenant", args.operations * 100)
        ledger.create_account("session", args.operations * 100, parent="tenant")
        samples = []
        for i in range(args.operations):
            begin = time.perf_counter()
            permit = ledger.reserve("session", 100, key=f"r:{i}")
            ledger.start(permit['id'], key=f"s:{i}")
            ledger.settle(permit['id'], 80, key=f"c:{i}")
            samples.append((time.perf_counter() - begin) * 1000)
        started = time.perf_counter()
        verified = ledger.verify()
        verification_ms = (time.perf_counter() - started) * 1000
        print(json.dumps(dict(python=platform.python_version(), platform=platform.system(),
                              sqlite=sqlite3.sqlite_version, cycles=args.operations,
                              transactions_per_cycle=3, concurrency=1, hierarchy_depth=2,
                              sqlite_synchronous="FULL", cycle_median_ms=statistics.median(samples),
                              cycle_p95_ms=sorted(samples)[min(len(samples)-1, int(len(samples)*0.95))],
                              elapsed_cycles_ms=sum(samples), verify_ms=verification_ms,
                              database_bytes=dbpath.stat().st_size, receipts=verified['receipts']), indent=2))


if __name__ == "__main__":
    main()
