"""Command line: python -m regulars {load,check,summary,demo}"""
from __future__ import annotations

import argparse
import contextlib
import sys
import zipfile

from . import db, demo, reader


def _load(args) -> int:
    with contextlib.closing(db.connect(args.db)) as conn:
        return _load_into(conn, args)


def _load_into(conn, args) -> int:
    failed = loaded = 0
    for path in args.paths:
        seen = 0
        try:
            for pf in reader.parse_path(path):
                seen += 1
                res = db.load_parsed(conn, pf, args.venue)
                loaded += res.status in ("loaded", "already_loaded")
                failed += res.status == "failed"
                _report(pf, res, args.verbose)
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            print(f"{path}: {e}", file=sys.stderr)
            failed += 1
            continue
        if not seen:
            print(f"{path}: no CSV files found", file=sys.stderr)
            failed += 1
    if not loaded and not failed:
        print("nothing loaded", file=sys.stderr)
    return 1 if failed or not loaded else 0


def _report(pf, res, verbose) -> None:
    print(f"{pf.source}  [{pf.kind}]  {res.status}", end="")
    if res.status == "loaded":
        print(f"  rows={res.rows:,} inserted={res.inserted:,} "
              f"duplicates={res.duplicates:,} issues={len(pf.issues)}")
    else:
        print(f"  {res.message}")
    if pf.dropped_private:
        print(f"    never stored: {', '.join(pf.dropped_private)}")
    if pf.unmapped:
        print(f"    unmapped columns (new from Square?): {', '.join(pf.unmapped)}")
    shown = pf.issues if verbose else pf.issues[:5]
    for i in shown:
        print(f"    line {i.line} {i.severity}: {i.column or '-'}: {i.message}")
    if len(pf.issues) > len(shown):
        print(f"    ... {len(pf.issues) - len(shown)} more (use -v)")


def _check(args) -> int:
    with contextlib.closing(db.connect(args.db)) as conn:
        rep = db.reconcile(conn, args.venue, example_limit=args.limit)
    print("Transactions vs Items reconciliation (per sale, Net Sales)")
    for k, n in rep["counts"].items():
        print(f"  {k:<26}{n:>9,}")
    for status, rows in rep["examples"].items():
        if rows:
            print(f"\n{status} (first {len(rows)})")
            for txid, kind, detail in rows:
                print(f"  {txid}  {kind:<8}{detail}")
    return 1 if rep["counts"]["net_mismatch"] else 0


def _summary(args) -> int:
    with contextlib.closing(db.connect(args.db)) as conn:
        print(db.summary(conn, args.venue))
    return 0


def _demo(args) -> int:
    for p in demo.write_demo(args.out, seed=args.seed, weeks=args.weeks):
        print(f"wrote {p} ({p.stat().st_size:,} bytes)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="regulars", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("load", help="load Square export CSV/ZIP/folder into SQLite")
    s.add_argument("paths", nargs="+")
    s.add_argument("--db", default="regulars.db")
    s.add_argument("--venue", default="default", help="label for this venue (default: %(default)s)")
    s.add_argument("-v", "--verbose", action="store_true", help="print every row issue")
    s.set_defaults(fn=_load)

    s = sub.add_parser("check", help="reconcile Transactions against Items")
    s.add_argument("--db", default="regulars.db")
    s.add_argument("--venue")
    s.add_argument("--limit", type=int, default=15, help="examples per category")
    s.set_defaults(fn=_check)

    s = sub.add_parser("summary", help="headline numbers")
    s.add_argument("--db", default="regulars.db")
    s.add_argument("--venue")
    s.set_defaults(fn=_summary)

    s = sub.add_parser("demo", help="write the synthetic demo CSV pair")
    s.add_argument("--out", default="demo")
    s.add_argument("--seed", type=int, default=2026)
    s.add_argument("--weeks", type=int, default=6)
    s.set_defaults(fn=_demo)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
