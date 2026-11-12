"""Deterministic SYNTHETIC Square-style export pair for a fictional venue.

Nothing here is real data. IDs start with DEMO, the venue is "Demo Wine Bar",
staff and customers are placeholders, and URLs use the reserved .invalid TLD.

Column *names* follow the reference header rows in schema.py (seen in US
exports). Value *formats and semantics* are assumptions that have NOT been
checked against a real Australian export:
  * money as "$12.50" / "-$1.25"; discounts and refunds negative
  * GST-inclusive prices (Tax = net / 11), Total Collected = Net Sales + Tip
  * a full refund is a separate "Refund" row reusing the sale's Transaction ID
    and Payment ID, with matching negative lines in the Items file
  * a partial refund is recorded only in the sale row's "Partial Refunds" column
  * Time Zone is an IANA name, dates are ISO
The parser reads values as exported and does not rely on any of these.
"""
from __future__ import annotations

import calendar
import csv
import io
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from . import schema

VENUE = "Demo Wine Bar"
TZ = "Australia/Melbourne"
START = date(2026, 9, 28)  # a Monday
FEE_RATE = 0.019  # illustrative, not a statement about Square's pricing
TX_FILE = "demo-transactions.csv"
ITEMS_FILE = "demo-items.csv"

# weekday (Mon=0) -> (first trading hour, last trading hour + 1). Monday closed.
OPEN = {1: (15, 22), 2: (15, 22), 3: (15, 22), 4: (12, 23), 5: (12, 23), 6: (12, 20)}
TARGET = {1: 38, 2: 48, 3: 72, 4: 130, 5: 150, 6: 90}  # payments per trading day

# category, item, GST-inclusive price in cents, weights for (lunch, arvo, evening, late)
MENU = [
    ("Wine by the Glass", "House Sparkling 150ml", 1400, (3, 3, 5, 3)),
    ("Wine by the Glass", "Sauvignon Blanc 150ml", 1300, (3, 4, 5, 3)),
    ("Wine by the Glass", "Chardonnay 150ml", 1500, (2, 3, 4, 3)),
    ("Wine by the Glass", 'Pét-Nat "Cloudy" 150ml', 1600, (1, 2, 4, 4)),
    ("Wine by the Glass", "Pinot Noir 150ml", 1600, (2, 3, 6, 4)),
    ("Wine by the Glass", "Shiraz 150ml", 1500, (1, 2, 5, 4)),
    ("Wine by the Bottle", "Sauvignon Blanc Bottle", 6200, (1, 1, 4, 2)),
    ("Wine by the Bottle", "Pinot Noir Bottle", 7800, (1, 1, 4, 2)),
    ("Wine by the Bottle", "Sparkling Bottle", 6800, (1, 1, 3, 2)),
    ("Beer & Cider", "Lager Pot", 950, (2, 3, 3, 3)),
    ("Beer & Cider", "Pale Ale Pint", 1300, (1, 2, 3, 3)),
    ("Beer & Cider", "Dry Cider Pint", 1300, (1, 1, 2, 2)),
    ("Spirits & Cocktails", "Spritz", 1800, (3, 4, 4, 2)),
    ("Spirits & Cocktails", "Negroni", 2000, (1, 1, 3, 4)),
    ("Spirits & Cocktails", "Espresso Martini", 2200, (0, 1, 3, 6)),
    ("Share Plates", "Cheese Board, Local (serves 2)", 3600, (3, 3, 5, 1)),
    ("Share Plates", "Marinated Olives", 1200, (2, 2, 3, 1)),
    ("Share Plates", "Whipped Feta, Honey", 1700, (2, 2, 3, 1)),
    ("Share Plates", "Fries, Rosemary Salt", 1100, (2, 2, 3, 2)),
    ("Mains", "Wood-Fired Margherita", 2600, (5, 1, 5, 1)),
    ("Mains", "Wood-Fired Prosciutto", 3000, (4, 1, 4, 1)),
    ("Mains", "Beef Burger, Cheddar", 2800, (4, 1, 3, 1)),
    ("Desserts", "Basque Cheesecake", 1600, (1, 1, 2, 2)),
    ("Non-Alcoholic", "Sparkling Water 750ml", 800, (3, 2, 2, 1)),
    ("Coffee", "Flat White", 450, (5, 3, 1, 0)),
]
BOOZE = {"Wine by the Glass", "Beer & Cider", "Spirits & Cocktails"}
STAFF = [("Demo Staff A", "DEMOSTAFF-A"), ("Demo Staff B", "DEMOSTAFF-B"),
         ("Demo Staff C", "DEMOSTAFF-C"), ("Demo Staff D", "DEMOSTAFF-D")]
REFUND_REASONS = ["Wrong order", "Quality issue", "Customer changed mind", "Duplicate charge"]
CARD_BRANDS = ["Visa", "Mastercard", "American Express", "EFTPOS"]
B62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Planted in rows that the parser must NEVER store (see tests).
SENTINELS = ("Demo Customer", "DEMOCUST", "DEMOREF", "Demo tender note",
             "Demo fulfillment note", "Demo item note")


def money(c: int) -> str:
    return f"{'-' if c < 0 else ''}${abs(c) // 100}.{abs(c) % 100:02d}"


def melbourne_cup(year: int) -> date:  # first Tuesday of November
    first = date(year, 11, 1)
    return first + timedelta(days=(1 - first.weekday()) % 7)


def _next_business_day(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _part(hour: int) -> int:
    return 0 if hour < 15 else 1 if hour < 17 else 2 if hour < 21 else 3


def _hour_weight(hour: int) -> float:
    return {0: 1.0, 1: 0.7, 2: 1.7, 3: 1.0}[_part(hour)]


class _Gen:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.tx: list[tuple] = []     # (sort key, row dict)
        self.items: list[tuple] = []
        self.n = 0                    # global emission counter -> stable ordering
        self.cust = 0

    def rid(self, n: int = 24) -> str:
        return "DEMO" + "".join(self.rng.choice(B62) for _ in range(n))

    def emit_sale(self, dt: datetime, end: datetime) -> None:
        r = self.rng
        dow = dt.weekday()
        part = _part(dt.hour)

        # lines
        lines = []
        for _ in range(1 + min(6, int(r.expovariate(1 / 1.8)))):
            idx = r.choices(range(len(MENU)), weights=[m[3][part] for m in MENU])[0]
            cat, name, price, _w = MENU[idx]
            qty = 2 if r.random() < (0.2 if cat in BOOZE else 0.08) else 1
            lines.append([idx, cat, name, price, qty])

        # discount
        pct, dname = 0, ""
        if dow == 1 and r.random() < 0.25:
            pct, dname = 20, "Industry Night 20%"
        elif dow in (2, 3, 4) and 15 <= dt.hour < 17 and r.random() < 0.4:
            pct, dname = 10, "Happy Hour 10%"
        elif r.random() < 0.015:
            pct, dname = 10, "Owner's Mates 10%"

        calc = []
        for idx, cat, name, price, qty in lines:
            gross = price * qty
            disc = -round(gross * pct / 100)
            net = gross + disc
            calc.append((idx, cat, name, price, qty, gross, disc, net, round(net / 11)))
        gross = sum(c[5] for c in calc)
        disc = sum(c[6] for c in calc)
        net = sum(c[7] for c in calc)
        tax = sum(c[8] for c in calc)

        roll = r.random()
        tender = "cash" if roll < 0.09 else "gift" if roll < 0.12 else "card"
        tip = round(net * r.uniform(0.05, 0.15)) if tender == "card" and r.random() < 0.10 else 0
        total = net + tip
        fee = -round(total * FEE_RATE) if tender == "card" else 0
        partial = -calc[0][7] if r.random() < 0.008 and tender == "card" else 0

        staff = r.choice(STAFF)
        device = f"Demo Terminal {r.randint(1, 2)}"
        dining = "For Here" if r.random() < 0.85 else "To Go"
        table = f"Table {r.randint(1, 12)}" if dining == "For Here" and r.random() < 0.5 else ""
        txid, payid = self.rid(), self.rid()
        cust_id = cust_name = cust_ref = ""
        if r.random() < 0.30:
            self.cust += 1
            cust_id, cust_name = f"DEMOCUST{self.cust:04d}", f"Demo Customer {self.cust}"
            cust_ref = f"DEMOREF{self.cust:04d}" if r.random() < 0.3 else ""
        deposit = _next_business_day(dt.date()) if tender == "card" else None

        common = {
            "Date": dt.date().isoformat(), "Time": dt.strftime("%H:%M:%S"), "Time Zone": TZ,
            "Transaction ID": txid, "Payment ID": payid, "Device Name": device,
            "Event Type": "Payment", "Location": VENUE, "Dining Option": dining,
            "Customer ID": cust_id, "Customer Name": cust_name,
            "Customer Reference ID": cust_ref, "Channel": "In-Person",
        }
        card_amt = total if tender == "card" else 0
        row = {
            **common,
            "Gross Sales": money(gross), "Discounts": money(disc), "Service Charges": money(0),
            "Net Sales": money(net), "Gift Card Sales": money(0), "Tax": money(tax),
            "Tip": money(tip), "Partial Refunds": money(partial),
            "Total Collected": money(total), "Source": "Demo POS",
            "Card": money(card_amt), "Card Entry Methods": "Contactless" if card_amt else "",
            "Cash": money(total if tender == "cash" else 0),
            "Square Gift Card": money(total if tender == "gift" else 0),
            "Other Tender": money(0), "Tender Note": "Demo tender note" if r.random() < 0.03 else "",
            "Fees": money(fee), "Net Total": money(total + partial + fee),
            "Card Brand": r.choice(CARD_BRANDS) if card_amt else "",
            "PAN Suffix": f"{r.randint(0, 9999):04d}" if card_amt else "",
            "Staff Name": staff[0], "Staff ID": staff[1],
            "Details": f"https://example.invalid/demo/{txid}",
            "Description": ", ".join(f"{c[4]} x {c[2]}" for c in calc)[:140],
            "Device Nickname": device, "Third Party Fees": money(0),
            "Deposit ID": f"DEMO-DEP-{deposit:%Y%m%d}" if deposit else "",
            "Deposit Date": deposit.isoformat() if deposit else "",
            "Fee Percentage Rate": f"{FEE_RATE * 100:.1f}%" if card_amt else "",
            "Fee Fixed Rate": money(0) if card_amt else "",
            "Discount Name": dname, "Transaction Status": "Complete",
            "Fulfillment Note": "Demo fulfillment note" if r.random() < 0.02 else "",
            "Unattributed Tips": money(0), "Table Info": table,
        }
        self._emit(dt, row, calc, common, sign=1, staff=staff)

        # Full refund later (separate Refund rows, same transaction/payment IDs)
        if not partial and tender != "gift" and r.random() < 0.012:
            rdt = dt + timedelta(minutes=r.randint(3, 45))
            if rdt.date() != dt.date() or r.random() < 0.4:
                rd = dt.date() + timedelta(days=r.randint(1, 2))
                while rd.weekday() not in OPEN:
                    rd += timedelta(days=1)
                lo, hi = OPEN[rd.weekday()]
                rdt = datetime(rd.year, rd.month, rd.day, r.randint(lo, hi - 1),
                               r.randint(0, 59), r.randint(0, 59))
            if rdt >= end:
                return
            rdeposit = _next_business_day(rdt.date()) if tender == "card" else None
            rcommon = {**common, "Date": rdt.date().isoformat(),
                       "Time": rdt.strftime("%H:%M:%S"), "Event Type": "Refund"}
            rrow = {
                **rcommon,
                "Gross Sales": money(-gross), "Discounts": money(-disc),
                "Service Charges": money(0), "Net Sales": money(-net),
                "Gift Card Sales": money(0), "Tax": money(-tax), "Tip": money(0),
                "Partial Refunds": money(0), "Total Collected": money(-net),
                "Source": "Demo POS",
                "Card": money(-net if tender == "card" else 0),
                "Cash": money(-net if tender == "cash" else 0),
                "Square Gift Card": money(0), "Other Tender": money(0),
                "Fees": money(0), "Net Total": money(-net),
                "Card Brand": row["Card Brand"], "PAN Suffix": row["PAN Suffix"],
                "Staff Name": staff[0], "Staff ID": staff[1],
                "Details": row["Details"], "Description": row["Description"],
                "Device Nickname": device, "Third Party Fees": money(0),
                "Deposit ID": f"DEMO-DEP-{rdeposit:%Y%m%d}" if rdeposit else "",
                "Deposit Date": rdeposit.isoformat() if rdeposit else "",
                "Refund Reason": r.choice(REFUND_REASONS), "Discount Name": dname,
                "Transaction Status": "Complete", "Unattributed Tips": money(0),
            }
            self._emit(rdt, rrow, calc, rcommon, sign=-1, staff=staff)

    def _emit(self, dt, txrow, calc, common, sign, staff):
        r = self.rng
        self.n += 1
        order = self.n
        self.tx.append(((dt, order), txrow))
        for k, (idx, cat, name, price, qty, gross, disc, net, tax) in enumerate(calc):
            mods = "Gluten-free base" if name.startswith("Wood-Fired") and r.random() < 0.1 else ""
            self.items.append(((dt, order, k), {
                **common,
                "Category": cat, "Item": name, "Qty": str(sign * qty),
                "Price Point Name": "Regular", "SKU": f"DEMO-{idx:03d}",
                "Modifiers Applied": mods,
                "Gross Sales": money(sign * gross), "Discounts": money(sign * disc),
                "Net Sales": money(sign * net), "Tax": money(sign * tax),
                "Notes": "Demo item note" if r.random() < 0.02 else "",
                "Details": txrow["Details"], "Itemization Type": "Physical Item",
                "Commission": money(0), "Employee": staff[0],
                "Token": "DEMOTOKEN" + "".join(r.choice(B62) for _ in range(16)),
            }))


def generate(seed: int = 2026, weeks: int = 6, start: date = START) -> tuple[bytes, bytes]:
    """Return (transactions_csv, items_csv) as UTF-8 bytes. Same args, same bytes."""
    gen = _Gen(seed)
    end = datetime.combine(start + timedelta(days=7 * weeks), datetime.min.time())
    cup = melbourne_cup(start.year)
    for offset in range(7 * weeks):
        day = start + timedelta(days=offset)
        if day.weekday() not in OPEN:
            continue
        lo, hi = OPEN[day.weekday()]
        target = 125 if day == cup else TARGET[day.weekday()]
        count = round(target * gen.rng.uniform(0.85, 1.15))
        hours = list(range(lo, hi))
        weights = [_hour_weight(h) for h in hours]
        stamps = sorted(
            datetime(day.year, day.month, day.day, h, gen.rng.randint(0, 59), gen.rng.randint(0, 59))
            for h in gen.rng.choices(hours, weights=weights, k=count)
        )
        for dt in stamps:
            gen.emit_sale(dt, end)

    def render(headers, rows):
        buf = io.StringIO(newline="")
        w = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n", restval="")
        w.writeheader()
        for _key, row in sorted(rows, key=lambda x: x[0]):
            w.writerow(row)
        return buf.getvalue().encode("utf-8")

    return render(schema.TX_HEADERS, gen.tx), render(schema.ITEM_HEADERS, gen.items)


def write_demo(out_dir: str | Path, **kwargs) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tx, items = generate(**kwargs)
    paths = [out / TX_FILE, out / ITEMS_FILE]
    paths[0].write_bytes(tx)
    paths[1].write_bytes(items)
    return paths
