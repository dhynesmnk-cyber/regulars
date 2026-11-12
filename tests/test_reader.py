import collections.abc
import tempfile
import unittest
import zipfile
from pathlib import Path

from regulars import reader, schema
from tests.helpers import ITEM_MIN, TX_MIN, item_row, make_csv, tx_row


class ColumnMatchingTests(unittest.TestCase):
    def test_column_order_does_not_matter(self):
        rows = [tx_row("T1"), tx_row("T2", gross="$22.00", net="$20.00")]
        a = reader.parse_csv_bytes("a.csv", make_csv(TX_MIN, rows))
        order = list(reversed(range(len(TX_MIN))))
        b = reader.parse_csv_bytes(
            "b.csv",
            make_csv([TX_MIN[i] for i in order], [[r[i] for i in order] for r in rows]),
        )
        self.assertEqual(a.kind, schema.KIND_TRANSACTIONS)
        self.assertEqual(a.rows, b.rows)

    def test_header_spelling_variants(self):
        headers = ["date", "TIME", "transaction_id", "Gross  Sales", "Net Sales ", "TOTAL_COLLECTED"]
        pf = reader.parse_csv_bytes("x.csv", make_csv(headers, [["2026-10-02", "18:30:00", "T1", "$5.00", "$5.00", "$5.00"]]))
        self.assertIsNone(pf.fatal)
        self.assertEqual(pf.rows[0]["gross_sales_c"], 500)

    def test_unknown_columns_are_reported_not_fatal(self):
        pf = reader.parse_csv_bytes("x.csv", make_csv(TX_MIN + ["Brand New Column"], [tx_row() + ["?"]]))
        self.assertIsNone(pf.fatal)
        self.assertEqual(pf.unmapped, ["Brand New Column"])

    def test_missing_optional_columns_are_none(self):
        headers = ["Date", "Time", "Transaction ID", "Gross Sales", "Total Collected"]
        pf = reader.parse_csv_bytes("x.csv", make_csv(headers, [["2026-10-02", "18:30:00", "T1", "$5.00", "$5.00"]]))
        self.assertIsNone(pf.fatal)
        self.assertNotIn("fees_c", pf.rows[0])

    def test_missing_required_column_is_fatal(self):
        headers = ["Date", "Time", "Gross Sales", "Total Collected"]  # no Transaction ID
        pf = reader.parse_csv_bytes("x.csv", make_csv(headers, [["2026-10-02", "18:30:00", "$5.00", "$5.00"]]))
        self.assertEqual(pf.kind, schema.KIND_TRANSACTIONS)
        self.assertIn("transaction id", pf.fatal)


class FileKindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(reader.parse_csv_bytes("t", make_csv(TX_MIN, [tx_row()])).kind, "transactions")
        self.assertEqual(reader.parse_csv_bytes("i", make_csv(ITEM_MIN, [item_row()])).kind, "items")

    def test_items_summary_is_skipped_with_reason(self):
        pf = reader.parse_csv_bytes("s", make_csv(["Item Name", "Items Sold", "Net Sales"], [["Shiraz", "3", "$30.00"]]))
        self.assertEqual(pf.kind, schema.KIND_ITEMS_SUMMARY)
        self.assertIn("aggregated", pf.fatal)
        self.assertEqual(pf.rows, [])

    def test_unrecognised_and_empty(self):
        self.assertEqual(reader.parse_csv_bytes("u", make_csv(["a", "b"], [["1", "2"]])).kind, "unknown")
        self.assertEqual(reader.parse_csv_bytes("e", b"").fatal, "file is empty")


class EncodingTests(unittest.TestCase):
    def test_bom_and_cp1252(self):
        row = item_row(item="Café Latte")
        for enc, bom in (("utf-8", True), ("utf-8", False), ("cp1252", False)):
            with self.subTest(enc=enc, bom=bom):
                pf = reader.parse_csv_bytes("i", make_csv(ITEM_MIN, [row], encoding=enc, bom=bom))
                self.assertEqual(pf.headers[0], "Date")  # BOM did not glue itself to the header
                self.assertEqual(pf.rows[0]["item"], "Café Latte")


class RowProblemTests(unittest.TestCase):
    def test_bad_date_skips_only_that_row(self):
        pf = reader.parse_csv_bytes("t", make_csv(TX_MIN, [tx_row("T1"), tx_row("T2", date="soon"), tx_row("T3")]))
        self.assertEqual([r["transaction_id"] for r in pf.rows], ["T1", "T3"])
        self.assertEqual(pf.errors, 1)

    def test_bad_money_blanks_cell_keeps_row(self):
        pf = reader.parse_csv_bytes("t", make_csv(TX_MIN, [tx_row(fees="oops")]))
        self.assertEqual(len(pf.rows), 1)
        self.assertIsNone(pf.rows[0]["fees_c"])
        self.assertEqual((pf.errors, len(pf.issues)), (0, 1))

    def test_ragged_rows_are_padded_and_reported(self):
        data = make_csv(TX_MIN, [tx_row()]) + b"2026-10-02,18:31:00,T9\n"
        pf = reader.parse_csv_bytes("t", data)
        self.assertEqual(len(pf.rows), 2)
        self.assertTrue(any("fields" in i.message for i in pf.issues))

    def test_blank_lines_ignored(self):
        pf = reader.parse_csv_bytes("t", make_csv(TX_MIN, [tx_row()]) + b"\n\n")
        self.assertEqual(len(pf.rows), 1)


class PrivacyTests(unittest.TestCase):
    def test_customer_columns_never_reach_rows(self):
        pf = reader.parse_csv_bytes("t", make_csv(TX_MIN, [tx_row(cust="Jane Citizen")]))
        self.assertIn("Customer Name", pf.dropped_private)
        for rec in pf.rows:
            self.assertNotIn("Jane Citizen", repr(rec))
            self.assertFalse([k for k in rec if "customer" in k])


class IdentityTests(unittest.TestCase):
    def test_identical_lines_in_one_sale_stay_distinct(self):
        pf = reader.parse_csv_bytes("i", make_csv(ITEM_MIN, [item_row(), item_row()]))
        self.assertEqual(len({r["row_key"] for r in pf.rows}), 2)

    def test_keys_are_stable_across_parses(self):
        data = make_csv(ITEM_MIN, [item_row(), item_row(txid="T2")])
        a = [r["row_key"] for r in reader.parse_csv_bytes("a", data).rows]
        b = [r["row_key"] for r in reader.parse_csv_bytes("b", data).rows]
        self.assertEqual(a, b)

    def test_payment_and_refund_with_same_ids_are_distinct(self):
        pf = reader.parse_csv_bytes("t", make_csv(TX_MIN, [
            tx_row(), tx_row(event="Refund", gross="-$10.00", net="-$10.00", total="-$10.00", date="2026-10-03")]))
        self.assertEqual([r["is_refund"] for r in pf.rows], [0, 1])
        self.assertEqual(len({r["row_key"] for r in pf.rows}), 2)


class PathTests(unittest.TestCase):
    def test_zip_and_directory(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tx = make_csv(TX_MIN, [tx_row()])
            with zipfile.ZipFile(d / "export.zip", "w") as zf:
                zf.writestr("2026-09/transactions.csv", tx)
                zf.writestr("2026-10/transactions.csv", make_csv(TX_MIN, [tx_row("T2")]))
                zf.writestr("__MACOSX/._transactions.csv", b"junk")
                zf.writestr("readme.txt", b"not a csv")
            self.assertEqual(len(list(reader.parse_path(d / "export.zip"))), 2)
            (d / "sub").mkdir()
            (d / "sub" / "items.csv").write_bytes(make_csv(ITEM_MIN, [item_row()]))
            kinds = sorted(pf.kind for pf in reader.parse_path(d))
            self.assertIsInstance(reader.parse_path(d), collections.abc.Iterator)
            self.assertEqual(kinds, ["items", "transactions", "transactions"])

    def test_missing_path(self):
        with self.assertRaises(FileNotFoundError):
            list(reader.parse_path("/nonexistent/path.csv"))


class CsvDialectTests(unittest.TestCase):
    def test_crlf_and_newline_inside_quoted_field(self):
        raw = ("Date,Time,Transaction ID,Event Type,Category,Item,Qty,Gross Sales,Net Sales\r\n"
               '2026-10-02,18:30:00,T1,Payment,Wine,"Shiraz\r\n(special)",1,$10.00,$10.00\r\n'
               "2026-10-02,18:31:00,T2,Payment,Wine,Pinot,1,$12.00,$12.00\r\n").encode()
        pf = reader.parse_csv_bytes("i", raw)
        self.assertEqual([r["transaction_id"] for r in pf.rows], ["T1", "T2"])
        self.assertIn("special", pf.rows[0]["item"])
        self.assertEqual(pf.issues, [])


if __name__ == "__main__":
    unittest.main()
