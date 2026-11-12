import unittest
from datetime import date, time

from regulars import values as v


class MoneyTests(unittest.TestCase):
    def test_formats(self):
        cases = {
            "$12.50": 1250, "-$3.20": -320, "($3.20)": -320, "$1,234.56": 123456,
            "3.2": 320, "A$5": 500, "AUD 5.00": 500, "$-3.20": -320,
            "−$3": -300, "$0.00": 0, ".5": 50, "  $7.00 ": 700, "$0.005": 1,
            "-$0.005": -1, "+$2": 200,
        }
        for raw, cents in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(v.parse_money(raw), cents)

    def test_blank_is_none(self):
        for raw in ("", "   ", None):
            self.assertIsNone(v.parse_money(raw))

    def test_garbage_raises(self):
        for raw in ("abc", "$1.2.3", "(3.20", "3.20)", "1,23", "$", "--5"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                v.parse_money(raw)

    def test_exact_sums(self):
        # the reason money is integer cents
        self.assertEqual(sum(v.parse_money("$0.10") for _ in range(3)), 30)


class OtherValueTests(unittest.TestCase):
    def test_pct(self):
        self.assertEqual(v.parse_pct("1.75%"), 1.75)
        self.assertEqual(v.parse_pct("2"), 2.0)
        self.assertIsNone(v.parse_pct(""))
        with self.assertRaises(ValueError):
            v.parse_pct("x%")

    def test_qty(self):
        self.assertEqual(v.parse_qty("2"), 2.0)
        self.assertEqual(v.parse_qty("0.500"), 0.5)
        self.assertEqual(v.parse_qty("-1"), -1.0)
        self.assertIsNone(v.parse_qty(""))
        with self.assertRaises(ValueError):
            v.parse_qty("two")

    def test_dates_are_day_first_for_slashes(self):
        self.assertEqual(v.parse_date("2026-10-02"), date(2026, 10, 2))
        self.assertEqual(v.parse_date("14/03/2026"), date(2026, 3, 14))
        self.assertEqual(v.parse_date("05/04/2026"), date(2026, 4, 5))  # 5 April, not 4 May
        with self.assertRaises(ValueError):
            v.parse_date("03/14/2026")  # month-first is refused, not guessed
        with self.assertRaises(ValueError):
            v.parse_date("yesterday")

    def test_times(self):
        for raw in ("16:05:09", "4:05:09 PM", "4:05:09 pm"):
            self.assertEqual(v.parse_time(raw), time(16, 5, 9))
        self.assertEqual(v.parse_time("16:05"), time(16, 5))
        with self.assertRaises(ValueError):
            v.parse_time("25:00")


if __name__ == "__main__":
    unittest.main()
