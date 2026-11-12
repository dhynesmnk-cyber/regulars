"""SQLite storage, loading, reconciliation and a quick summary.

Money is stored as integer cents in columns ending ``_c``. Times are the venue's
local wall-clock time. Customer-identifying columns are never created.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from itertools import islice
from datetime import datetime, timezone

from . import schema
from .reader import ParsedFile

CHUNK_ROWS = 5000
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SQLTYPE = {"money": "INTEGER", "pct": "REAL", "qty": "REAL", "text": "TEXT"}
_BASE = [
    ("row_key", "TEXT PRIMARY KEY"),
    ("source_id", "INTEGER NOT NULL REFERENCES source_file(id)"),
    ("venue", "TEXT NOT NULL"),
    ("local_ts", "TEXT NOT NULL"),
    ("local_date", "TEXT NOT NULL"),
    ("local_hour", "INTEGER NOT NULL"),
    ("local_dow", "INTEGER NOT NULL"),
    ("is_refund", "INTEGER NOT NULL"),
]


def _field_cols(fields: dict) -> dict[str, str]:
    cols: dict[str, str] = {}
    for key, kind in fields.values():
        if kind not in ("date", "time"):
            cols[key] = _SQLTYPE[kind]
    return cols


TABLES = {
    "tx": (schema.KIND_TRANSACTIONS, _field_cols(schema.TX_FIELDS)),
    "item": (schema.KIND_ITEMS, _field_cols(schema.ITEM_FIELDS)),
}
TABLE_FOR_KIND = {kind: name for name, (kind, _) in TABLES.items()}


def _ddl() -> str:
    parts = [
        "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);",
        """CREATE TABLE IF NOT EXISTS source_file(
               id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
               sha256 TEXT NOT NULL UNIQUE, venue TEXT NOT NULL,
               rows INTEGER NOT NULL, loaded_at TEXT NOT NULL);""",
    ]
    for table, (_, cols) in TABLES.items():
        body = [f"{n} {t}" for n, t in _BASE] + [f"{n} {t}" for n, t in cols.items()]
        parts.append(f"CREATE TABLE IF NOT EXISTS {table}(\n  " + ",\n  ".join(body) + "\n);")
        parts.append(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_txn ON {table}(venue, transaction_id, is_refund);"
        )
        parts.append(f"CREATE INDEX IF NOT EXISTS ix_{table}_date ON {table}(venue, local_date);")
    return "\n".join(parts)


_VIEWS = """
DROP VIEW IF EXISTS v_hourly;
DROP VIEW IF EXISTS v_daily;
DROP VIEW IF EXISTS v_category_hourly;
DROP VIEW IF EXISTS v_txn_items;

-- Sales by day-of-week x hour. Refund rows are included (their amounts are
-- negative as exported) so net_sales_c is net of refunds; `transactions`
-- counts distinct payment transactions only.
CREATE VIEW v_hourly AS
SELECT venue, location, local_dow, local_hour,
       COUNT(DISTINCT CASE WHEN is_refund = 0 THEN transaction_id END) AS transactions,
       SUM(net_sales_c) AS net_sales_c,
       SUM(tip_c)       AS tip_c,
       SUM(fees_c)      AS fees_c
FROM tx
GROUP BY venue, location, local_dow, local_hour;

CREATE VIEW v_daily AS
SELECT venue, location, local_date,
       COUNT(DISTINCT CASE WHEN is_refund = 0 THEN transaction_id END) AS transactions,
       SUM(net_sales_c) AS net_sales_c,
       SUM(tip_c)       AS tip_c,
       SUM(fees_c)      AS fees_c
FROM tx
GROUP BY venue, location, local_date;

-- What sold when, from the Items file.
CREATE VIEW v_category_hourly AS
SELECT venue, location, category, local_dow, local_hour,
       SUM(qty)         AS qty,
       SUM(net_sales_c) AS net_sales_c
FROM item
GROUP BY venue, location, category, local_dow, local_hour;

-- One row per transaction with BOTH sides pre-aggregated, then joined on
-- (venue, transaction_id, is_refund). Aggregating first is what stops the
-- one-to-many join from multiplying money. Joining on is_refund keeps a
-- refund from attaching to the original sale's item lines.
-- Caveat: if a sale was split across several payments (payment_rows > 1) and
-- Square repeats sale-level values on each payment row, the SUMs here repeat too.
CREATE VIEW v_txn_items AS
SELECT t.venue, t.transaction_id, t.is_refund,
       t.local_ts, t.payment_rows, t.net_sales_c, t.tip_c, t.fees_c,
       i.item_lines, i.items_net_c, i.categories
FROM (SELECT venue, transaction_id, is_refund,
             MIN(local_ts) AS local_ts, COUNT(*) AS payment_rows,
             SUM(net_sales_c) AS net_sales_c, SUM(tip_c) AS tip_c, SUM(fees_c) AS fees_c
      FROM tx GROUP BY venue, transaction_id, is_refund) t
LEFT JOIN (SELECT venue, transaction_id, is_refund,
                  COUNT(*) AS item_lines, SUM(net_sales_c) AS items_net_c,
                  group_concat(DISTINCT category) AS categories
           FROM item GROUP BY venue, transaction_id, is_refund) i
  ON i.venue = t.venue AND i.transaction_id = t.transaction_id AND i.is_refund = t.is_refund;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.executescript(_ddl())
        conn.executescript(_VIEWS)
        conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version', '1')")
    return conn


@dataclass
class LoadResult:
    name: str
    kind: str
    status: str  # loaded | already_loaded | skipped | failed
    rows: int = 0
    inserted: int = 0
    message: str = ""

    @property
    def duplicates(self) -> int:
        return self.rows - self.inserted


def load_parsed(conn: sqlite3.Connection, pf: ParsedFile, venue: str) -> LoadResult:
    if pf.fatal:
        harmless = pf.kind == schema.KIND_ITEMS_SUMMARY or pf.kind == schema.KIND_UNKNOWN
        return LoadResult(pf.source, pf.kind, "skipped" if harmless else "failed", message=pf.fatal)
    if conn.execute("SELECT 1 FROM source_file WHERE sha256 = ?", (pf.sha256,)).fetchone():
        return LoadResult(pf.source, pf.kind, "already_loaded",
                          message="identical file already loaded")

    table = TABLE_FOR_KIND[pf.kind]
    cols = [n for n, _ in _BASE if n not in ("source_id", "venue")] + list(TABLES[table][1])
    all_cols = ["source_id", "venue"] + cols
    sql = (
        f"INSERT OR IGNORE INTO {table}({','.join(all_cols)}) "
        f"VALUES({','.join(':' + c for c in all_cols)})"
    )
    blank = {c: None for c in all_cols}
    total = 0
    with conn:  # one transaction per file: a failure part-way leaves nothing behind
        cur = conn.execute(
            "INSERT INTO source_file(name, kind, sha256, venue, rows, loaded_at) VALUES(?,?,?,?,0,?)",
            (pf.source, pf.kind, pf.sha256, venue,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        source_id = cur.lastrowid
        before = conn.total_changes
        rows = iter(pf.rows)
        while chunk := list(islice(rows, CHUNK_ROWS)):  # bounded memory for huge files
            total += len(chunk)
            conn.executemany(
                sql, ({**blank, **r, "source_id": source_id, "venue": venue} for r in chunk)
            )
        inserted = conn.total_changes - before
        conn.execute("UPDATE source_file SET rows = ? WHERE id = ?", (total, source_id))
    return LoadResult(pf.source, pf.kind, "loaded", rows=total, inserted=inserted)


# --- Reconciliation -----------------------------------------------------------

def reconcile(conn: sqlite3.Connection, venue: str | None = None,
              tolerance_c: int = 1, example_limit: int = 15) -> dict:
    """Compare Transactions Net Sales with the sum of Items Net Sales per sale.

    This reports disagreements; it does not decide which file is right. Expect
    some on real data (gift-card sales, partial refunds, split payments), which
    is exactly why the check exists.
    """
    q = """SELECT venue, transaction_id, is_refund, COUNT(*) AS n,
                  SUM(net_sales_c) AS net, COUNT(net_sales_c) AS nn
           FROM {t}
           WHERE transaction_id IS NOT NULL AND (?1 IS NULL OR venue = ?1)
           GROUP BY venue, transaction_id, is_refund"""
    tx = {(r[0], r[1], r[2]): r for r in conn.execute(q.format(t="tx"), (venue,))}
    it = {(r[0], r[1], r[2]): r for r in conn.execute(q.format(t="item"), (venue,))}

    counts = {"ok": 0, "net_mismatch": 0, "multi_payment_unchecked": 0,
              "missing_amounts": 0, "tx_without_items": 0, "items_without_tx": 0}
    examples: dict[str, list] = {k: [] for k in counts if k != "ok"}

    def note(status, key, detail=""):
        counts[status] += 1
        if status != "ok" and len(examples[status]) < example_limit:
            examples[status].append((key[1], "refund" if key[2] else "payment", detail))

    for key, t in tx.items():
        i = it.get(key)
        if i is None:
            note("tx_without_items", key)
        elif t["n"] > 1:
            note("multi_payment_unchecked", key, f"{t['n']} payment rows")
        elif not t["nn"] or not i["nn"]:
            note("missing_amounts", key)
        elif abs(t["net"] - i["net"]) <= tolerance_c:
            note("ok", key)
        else:
            note("net_mismatch", key, f"transactions {t['net']/100:.2f} vs items {i['net']/100:.2f}")
    for key in it.keys() - tx.keys():
        note("items_without_tx", key)
    return {"counts": counts, "examples": examples}


# --- Summary ------------------------------------------------------------------

def dollars(cents: int | None) -> str:
    c = cents or 0
    return f"{'-' if c < 0 else ''}${abs(c) / 100:,.2f}"


def summary(conn: sqlite3.Connection, venue: str | None = None) -> str:
    v = (venue,)
    out: list[str] = []
    row = conn.execute(
        """SELECT MIN(local_date), MAX(local_date),
                  COUNT(DISTINCT CASE WHEN is_refund=0 THEN transaction_id END),
                  COUNT(DISTINCT CASE WHEN is_refund=1 THEN transaction_id END),
                  SUM(net_sales_c), SUM(tip_c), SUM(fees_c),
                  SUM(CASE WHEN is_refund=0 THEN card_c END)
           FROM tx WHERE (?1 IS NULL OR venue = ?1)""", v).fetchone()
    if not row[2] and not row[3]:
        return "No transactions loaded."
    first, last, pays, refunds, net, tips, fees, card = row
    out.append(f"Period            {first} to {last}")
    out.append(f"Transactions      {pays:,} payments, {refunds:,} refunds")
    out.append(f"Net sales         {dollars(net)} (after refunds)")
    out.append(f"Tips              {dollars(tips)}")
    fee_line = f"Card fees         {dollars(abs(fees or 0))}"
    if card:
        fee_line += f" ({abs(fees or 0) / card * 100:.2f}% of card volume)"
    out.append(fee_line)

    out.append("\nTop categories by net sales")
    for cat, n in conn.execute(
        """SELECT COALESCE(category, '(none)'), SUM(net_sales_c) FROM item
           WHERE (?1 IS NULL OR venue = ?1) GROUP BY 1 ORDER BY 2 DESC LIMIT 6""", v):
        out.append(f"  {cat:<24}{dollars(n):>12}")

    out.append("\nBusiest day/hour slots (payments, whole period)")
    for dow, hr, n, s in conn.execute(
        """SELECT local_dow, local_hour, SUM(transactions), SUM(net_sales_c) FROM v_hourly
           WHERE (?1 IS NULL OR venue = ?1) GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 5""", v):
        out.append(f"  {DAYS[dow]} {hr:02d}:00{'':<10}{n:>6,} payments{dollars(s):>14}")
    return "\n".join(out)
