import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from regulars import cli, db, demo, reader
from tests.helpers import ITEM_MIN, TX_MIN, item_row, make_csv, tx_row

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"


def new_db(case):
    conn = db.connect(":memory:")
    case.addCleanup(conn.close)
    return conn


def load_bytes(conn, name, data, venue="v"):
    return db.load_parsed(conn, reader.parse_csv_bytes(name, data), venue)


class DemoLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = db.connect(":memory:")
        cls.addClassCleanup(cls.conn.close)
        cls.parsed = list(reader.parse_path(DEMO))
        cls.results = [db.load_parsed(cls.conn, pf, "Demo Wine Bar") for pf in cls.parsed]

    def test_everything_loaded_without_issues(self):
        self.assertEqual(sorted(r.kind for r in self.results), ["items", "transactions"])
        for pf, res in zip(self.parsed, self.results):
            self.assertEqual(res.status, "loaded")
            self.assertEqual(res.inserted, res.rows)
            self.assertGreater(res.rows, 3000)
            self.assertEqual(pf.issues, [])
            self.assertEqual(pf.unmapped, [])

    def test_no_customer_data_anywhere_in_the_database(self):
        dump = "\n".join(self.conn.iterdump())
        for sentinel in demo.SENTINELS:
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, dump)
        for table in ("tx", "item"):
            cols = [r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")]
            self.assertFalse([c for c in cols if "customer" in c or "pan" in c or "note" in c], cols)

    def test_source_actually_contained_the_sentinels(self):
        # guards the previous test against passing vacuously
        text = b"".join(p.read_bytes() for p in DEMO.glob("*.csv")).decode()
        for sentinel in demo.SENTINELS:
            self.assertIn(sentinel, text)

    def test_join_does_not_multiply_money(self):
        q = lambda sql: self.conn.execute(sql).fetchone()[0]
        self.assertEqual(q("SELECT SUM(net_sales_c) FROM v_txn_items"), q("SELECT SUM(net_sales_c) FROM tx"))
        self.assertEqual(q("SELECT COUNT(*) FROM v_txn_items"), q("SELECT COUNT(*) FROM (SELECT 1 FROM tx GROUP BY venue, transaction_id, is_refund)"))
        # every refund pairs with its own negative item lines, never the original sale's
        bad = q("""SELECT COUNT(*) FROM v_txn_items
                   WHERE item_lines IS NOT NULL AND items_net_c != net_sales_c""")
        self.assertEqual(bad, 0)
        self.assertGreater(q("SELECT COUNT(*) FROM tx WHERE is_refund = 1"), 0)

    def test_views_agree_with_base_tables(self):
        q = lambda sql: self.conn.execute(sql).fetchone()[0]
        total = q("SELECT SUM(net_sales_c) FROM tx")
        self.assertEqual(q("SELECT SUM(net_sales_c) FROM v_hourly"), total)
        self.assertEqual(q("SELECT SUM(net_sales_c) FROM v_daily"), total)
        self.assertEqual(q("SELECT SUM(net_sales_c) FROM v_category_hourly"), q("SELECT SUM(net_sales_c) FROM item"))

    def test_local_time_fields(self):
        r = self.conn.execute("SELECT local_ts, local_date, local_hour, local_dow FROM tx LIMIT 1").fetchone()
        self.assertEqual(r["local_ts"][:10], r["local_date"])
        self.assertEqual(int(r["local_ts"][11:13]), r["local_hour"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tx WHERE local_dow = 0").fetchone()[0], 0)  # Mondays closed

    def test_demo_reconciles_cleanly(self):
        rep = db.reconcile(self.conn)
        counts = rep["counts"]
        self.assertEqual(counts["net_mismatch"], 0)
        self.assertEqual(counts["tx_without_items"] + counts["items_without_tx"] + counts["multi_payment_unchecked"], 0)
        self.assertGreater(counts["ok"], 3000)

    def test_summary_mentions_period_and_fees(self):
        text = db.summary(self.conn)
        self.assertIn("Period", text)
        self.assertIn("% of card volume", text)


class IdempotencyTests(unittest.TestCase):
    def test_same_file_twice(self):
        conn = new_db(self)
        data = make_csv(TX_MIN, [tx_row("T1"), tx_row("T2")])
        self.assertEqual(load_bytes(conn, "a.csv", data).status, "loaded")
        self.assertEqual(load_bytes(conn, "a-again.csv", data).status, "already_loaded")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tx").fetchone()[0], 2)

    def test_overlapping_exports_dedupe(self):
        conn = new_db(self)
        rows = [tx_row(f"T{i}", time=f"1{i % 10}:00:00") for i in range(10)]
        load_bytes(conn, "first.csv", make_csv(TX_MIN, rows[:7]))
        res = load_bytes(conn, "second.csv", make_csv(TX_MIN, rows[4:]))  # 3 rows overlap
        self.assertEqual((res.rows, res.inserted, res.duplicates), (6, 3, 3))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tx").fetchone()[0], 10)

    def test_duplicate_item_lines_in_one_sale_both_kept(self):
        conn = new_db(self)
        load_bytes(conn, "i.csv", make_csv(ITEM_MIN, [item_row(), item_row()]))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM item").fetchone()[0], 2)

    def test_whole_demo_overlap(self):
        conn = new_db(self)
        raw = (DEMO / demo.TX_FILE).read_text().splitlines()
        head, body = raw[0], raw[1:]
        mid = len(body) // 2
        part1 = ("\n".join([head] + body[: mid + 300]) + "\n").encode()
        part2 = ("\n".join([head] + body[mid - 300:]) + "\n").encode()
        load_bytes(conn, "p1.csv", part1)
        load_bytes(conn, "p2.csv", part2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tx").fetchone()[0], len(body))


class StreamingTests(unittest.TestCase):
    def test_streamed_rows_equal_eager_rows(self):
        data = (DEMO / demo.TX_FILE).read_bytes()
        eager = reader.parse_csv_bytes("t", data)
        streamed = reader.parse_csv_bytes("t", data, stream=True)
        self.assertNotIsInstance(streamed.rows, list)
        self.assertEqual(list(streamed.rows), eager.rows)
        self.assertEqual(streamed.issues, eager.issues)

    def test_tiny_chunks_give_identical_database(self):
        data = make_csv(TX_MIN, [tx_row(f"T{i}", time=f"{10 + i % 12}:00:00") for i in range(25)])
        a, b = new_db(self), new_db(self)
        load_bytes(a, "t.csv", data)
        old, db.CHUNK_ROWS = db.CHUNK_ROWS, 7
        try:
            res = db.load_parsed(b, reader.parse_csv_bytes("t.csv", data, stream=True), "v")
        finally:
            db.CHUNK_ROWS = old
        self.assertEqual((res.rows, res.inserted), (25, 25))
        q = "SELECT row_key, net_sales_c FROM tx ORDER BY row_key"
        self.assertEqual([tuple(r) for r in a.execute(q)], [tuple(r) for r in b.execute(q)])
        self.assertEqual(b.execute("SELECT rows FROM source_file").fetchone()[0], 25)

    def test_failure_midway_leaves_nothing_behind(self):
        conn = new_db(self)
        pf = reader.parse_csv_bytes("t.csv", make_csv(TX_MIN, [tx_row(f"T{i}") for i in range(10)]))

        good_rows = pf.rows

        def exploding():
            for i, r in enumerate(good_rows):
                if i == 6:
                    raise RuntimeError("disk on fire")
                yield r

        pf.rows = exploding()
        old, db.CHUNK_ROWS = db.CHUNK_ROWS, 3
        try:
            with self.assertRaises(RuntimeError):
                db.load_parsed(conn, pf, "v")
        finally:
            db.CHUNK_ROWS = old
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tx").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0], 0)
        # ...so the same file can simply be loaded again afterwards
        self.assertEqual(load_bytes(conn, "t.csv", make_csv(TX_MIN, [tx_row(f"T{i}") for i in range(10)])).status, "loaded")


class ReconcileTests(unittest.TestCase):
    def test_flags_every_kind_of_disagreement(self):
        conn = new_db(self)
        load_bytes(conn, "t.csv", make_csv(TX_MIN, [
            tx_row("OK"),
            tx_row("OFF", net="$10.00"),
            tx_row("NOITEMS"),
            tx_row("SPLIT", pay="P1"), tx_row("SPLIT", pay="P2"),
        ]))
        load_bytes(conn, "i.csv", make_csv(ITEM_MIN, [
            item_row("OK"),
            item_row("OFF", net="$5.00"),
            item_row("SPLIT"),
            item_row("ORPHAN"),
        ]))
        c = db.reconcile(conn)["counts"]
        self.assertEqual(c, {"ok": 1, "net_mismatch": 1, "multi_payment_unchecked": 1,
                             "missing_amounts": 0, "tx_without_items": 1, "items_without_tx": 1})

    def test_one_cent_tolerance(self):
        conn = new_db(self)
        load_bytes(conn, "t.csv", make_csv(TX_MIN, [tx_row("A", net="$10.01")]))
        load_bytes(conn, "i.csv", make_csv(ITEM_MIN, [item_row("A", net="$10.00")]))
        self.assertEqual(db.reconcile(conn)["counts"]["ok"], 1)
        self.assertEqual(db.reconcile(conn, tolerance_c=0)["counts"]["net_mismatch"], 1)

    def test_refund_does_not_attach_to_original_sale(self):
        conn = new_db(self)
        load_bytes(conn, "t.csv", make_csv(TX_MIN, [
            tx_row("A"), tx_row("A", event="Refund", gross="-$10.00", net="-$10.00", date="2026-10-03")]))
        load_bytes(conn, "i.csv", make_csv(ITEM_MIN, [
            item_row("A"), item_row("A", event="Refund", qty="-1", gross="-$10.00", net="-$10.00", date="2026-10-03")]))
        self.assertEqual(db.reconcile(conn)["counts"]["ok"], 2)
        rows = conn.execute("SELECT is_refund, net_sales_c, items_net_c FROM v_txn_items ORDER BY is_refund").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(0, 1000, 1000), (1, -1000, -1000)])


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_load_check_summary(self):
        with tempfile.TemporaryDirectory() as d:
            dbp = str(Path(d) / "t.db")
            code, out, _ = self.run_cli("load", str(DEMO), "--db", dbp, "--venue", "Demo Wine Bar")
            self.assertEqual(code, 0)
            self.assertIn("never stored: ", out)
            code, out, _ = self.run_cli("load", str(DEMO), "--db", dbp)
            self.assertEqual(code, 0)
            self.assertEqual(out.count("already_loaded"), 2)
            self.assertEqual(self.run_cli("check", "--db", dbp)[0], 0)
            self.assertIn("Top categories", self.run_cli("summary", "--db", dbp)[1])

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            dbp = str(Path(d) / "t.db")
            bad = Path(d) / "bad.csv"
            bad.write_bytes(make_csv(["Date", "Time", "Gross Sales", "Total Collected"], [["2026-10-02", "10:00:00", "$1", "$1"]]))
            self.assertEqual(self.run_cli("load", str(bad), "--db", dbp)[0], 1)  # missing Transaction ID
            junk = Path(d) / "junk.csv"
            junk.write_bytes(make_csv(["a", "b"], [["1", "2"]]))
            self.assertEqual(self.run_cli("load", str(junk), "--db", dbp)[0], 1)  # nothing loadable
            self.assertEqual(self.run_cli("load", str(Path(d) / "nope.csv"), "--db", dbp)[0], 1)


if __name__ == "__main__":
    unittest.main()
