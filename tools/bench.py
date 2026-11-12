"""
How fast is Regulars at real-venue sizes?

    python3 tools/bench.py                    # 2,000 and 20,000 customers
    python3 tools/bench.py --sizes 2000,50000

Each size runs in its own process (so peak memory is that size's alone) on
the synthetic demo venue scaled up. It times:

  engine run    E.run over every customer, as every page view does today
  setup page    the Setup screen, end to end
  inbox page    the Messages screen, end to end (all messages on one page)
  import        a Square-format transactions + customers CSV for every
                customer, through the same parse -> menu -> dataset steps
                as an upload

and reports the CSV size and the process's peak memory. Nothing is written
outside a temporary directory.
"""

import argparse
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
import time

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
DEMO_CUSTOMERS = 525


def measure(size):
    tmp = tempfile.mkdtemp(prefix="regulars-bench-")
    os.environ["REGULARS_STATE"] = os.path.join(tmp, "state.json")
    os.environ["REGULARS_AUDIT_LOG"] = os.path.join(tmp, "audit.log")
    sys.path.insert(0, APP)
    import data as D
    import engine as E
    import importer as IMP
    import state
    from pages import messages_page, setup_page

    out = {"target": size}
    t = time.perf_counter()
    ds = D.build(scale=max(1, math.ceil(size / DEMO_CUSTOMERS)))
    out["build_s"] = time.perf_counter() - t
    out["customers"] = ds["stats"]["customers"]
    out["orders"] = ds["stats"]["orders"]
    out["line_items"] = sum(len(o["items"]) for c in ds["customers"] for o in c["orders"])

    state.STATE["dataset"] = ds
    state.STATE["connected"] = True
    t = time.perf_counter()
    r = state.result()
    out["engine_s"] = time.perf_counter() - t
    out["messages"] = r["summary"]["ready"]

    t = time.perf_counter()
    setup_page.page_setup()
    out["setup_page_s"] = time.perf_counter() - t
    t = time.perf_counter()
    html = messages_page.page_inbox()
    out["inbox_page_s"] = time.perf_counter() - t
    out["inbox_page_mb"] = len(html.encode()) / 1e6

    tx, cu = D.sample_square(customers=len(ds["customers"]), ds=ds)
    out["csv_mb"] = (len(tx.encode()) + len(cu.encode())) / 1e6
    del ds, r, html
    state.STATE["dataset"] = None
    t = time.perf_counter()
    visits, people, _rep = IMP.parse_square(tx, cu, [])
    menu = IMP.build_menu(visits)
    imported = IMP.to_dataset(visits, people, menu, E._dt(D.NOW.isoformat()),
                              attested_consent=True)
    out["import_s"] = time.perf_counter() - t
    out["imported_customers"] = imported["stats"]["customers"]
    out["peak_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(json.dumps(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", default="2000,20000",
                    help="comma-separated customer counts (default 2000,20000)")
    ap.add_argument("--one", type=int, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.one:
        measure(args.one)
        return
    rows = []
    for size in (int(s) for s in args.sizes.split(",")):
        res = subprocess.run([sys.executable, os.path.abspath(__file__), "--one", str(size)],
                             capture_output=True, text=True)
        if res.returncode:
            sys.stderr.write(res.stderr)
            sys.exit(f"benchmark failed at {size} customers")
        rows.append(json.loads(res.stdout.strip().splitlines()[-1]))

    print("| Customers | Orders | Line items | Messages | Engine run | Setup page | "
          "Inbox page | Square CSV | Import | Peak RAM |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print(f"| {r['customers']:,} | {r['orders']:,} | {r['line_items']:,} | "
              f"{r['messages']:,} | {r['engine_s']:.2f} s | {r['setup_page_s']:.2f} s | "
              f"{r['inbox_page_s']:.2f} s ({r['inbox_page_mb']:.1f} MB) | "
              f"{r['csv_mb']:.0f} MB | {r['import_s']:.1f} s | {r['peak_mb']:.0f} MB |")


if __name__ == "__main__":
    main()
