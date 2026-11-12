import unittest
from datetime import date
from pathlib import Path

from regulars import demo, schema

DEMO = Path(__file__).resolve().parent.parent / "demo"


class DemoDataTests(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(demo.generate(), demo.generate())
        self.assertNotEqual(demo.generate(seed=1), demo.generate(seed=2))

    def test_committed_files_match_generator(self):
        tx, items = demo.generate()
        self.assertEqual((DEMO / demo.TX_FILE).read_bytes(), tx, "run: python -m regulars demo --out demo")
        self.assertEqual((DEMO / demo.ITEMS_FILE).read_bytes(), items, "run: python -m regulars demo --out demo")

    def test_headers_are_the_reference_rows(self):
        self.assertEqual((len(schema.TX_HEADERS), len(schema.ITEM_HEADERS)), (54, 33))
        for name, ref in ((demo.TX_FILE, schema.TX_HEADERS), (demo.ITEMS_FILE, schema.ITEM_HEADERS)):
            first = (DEMO / name).read_text().splitlines()[0]
            self.assertEqual(first.split(","), ref)

    def test_every_reference_header_is_accounted_for(self):
        for ref, fields in ((schema.TX_HEADERS, schema.TX_FIELDS), (schema.ITEM_HEADERS, schema.ITEM_FIELDS)):
            for h in ref:
                n = schema.norm(h)
                self.assertTrue(n in fields or n in schema.PRIVATE or n in schema.IGNORED, h)

    def test_obviously_synthetic(self):
        text = (DEMO / demo.TX_FILE).read_text()
        self.assertIn("Demo Wine Bar", text)
        self.assertIn("example.invalid", text)
        self.assertNotIn("squareup.com", text)

    def test_contains_the_edge_cases_the_parser_must_survive(self):
        tx = (DEMO / demo.TX_FILE).read_text()
        items = (DEMO / demo.ITEMS_FILE).read_text()
        self.assertIn(",Refund,", tx)
        self.assertIn("Industry Night 20%", tx)
        self.assertIn("-$", items)                      # negative refund/discount lines
        self.assertIn('"Cheese Board, Local (serves 2)"', items)   # comma inside a quoted field
        self.assertIn('""Cloudy""', items)              # embedded quotes
        self.assertIn("Pét-Nat", items)                 # non-ASCII

    def test_melbourne_cup_is_first_tuesday_of_november(self):
        self.assertEqual(demo.melbourne_cup(2026), date(2026, 11, 3))
        self.assertEqual(demo.melbourne_cup(2026).weekday(), 1)


if __name__ == "__main__":
    unittest.main()
