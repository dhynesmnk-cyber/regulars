"""
Synthetic Square data for one Melbourne restaurant.

Deliberately small and legible: the customer base is built from named
behavioural archetypes so you can look at any generated message and trace it
back to a real pattern in the data. That traceability is the product.

Swap this file for the Square connector and nothing else changes.
"""

import random
from datetime import datetime, timedelta

SEED = 40
RNG = random.Random(SEED)

VENUE = {
    "name": "Marlow & Finch",
    "suburb": "Fitzroy, Melbourne",
    "abn": "12 345 678 901",
    "type": "Neighbourhood restaurant + bar",
    "timezone": "Australia/Melbourne",
}

# name, category, price, cost, tags
MENU = [
    ("Flat White",          "Coffee",   5.50, 1.20, ["hot", "cafe"]),
    ("Long Black",          "Coffee",   5.20, 1.00, ["hot", "cafe"]),
    ("Oat Latte",           "Coffee",   6.50, 1.60, ["hot", "cafe"]),
    ("Fresh OJ",            "Drinks",   8.00, 2.20, ["cold"]),
    ("Lemonade",            "Drinks",   6.50, 1.40, ["cold"]),
    ("Smashed Avo",         "Brunch",  23.00, 6.80, ["veg"]),
    ("Eggs Benedict",       "Brunch",  25.00, 8.10, []),
    ("Buttermilk Pancakes", "Brunch",  20.00, 4.40, ["veg", "sweet"]),
    ("Acai Bowl",           "Brunch",  19.00, 5.30, ["veg", "sweet"]),
    ("Burger & Fries",      "Mains",   26.00, 8.20, []),
    ("Margherita Pizza",    "Mains",   27.00, 6.40, ["veg"]),
    ("Fish & Chips",        "Mains",   31.00, 12.10, []),
    ("Pappardelle Ragu",    "Mains",   30.00, 9.10, []),
    ("Duck Fat Chips",      "Sides",   13.00, 3.10, ["veg"]),
    ("Caesar Salad",        "Sides",   17.00, 4.60, []),
    ("Garlic Bread",        "Sides",   10.00, 2.10, ["veg"]),
    ("House Wine",          "Bar",     14.00, 3.60, ["alcohol"]),
    ("Craft Beer",          "Bar",     15.00, 4.30, ["alcohol"]),
    ("Espresso Martini",    "Bar",     22.00, 5.40, ["alcohol", "signature"]),
    ("Negroni",             "Bar",     21.00, 5.10, ["alcohol"]),
    ("Chocolate Brownie",   "Dessert", 15.00, 3.40, ["sweet"]),
    ("Vanilla Gelato",      "Dessert", 11.00, 2.00, ["sweet"]),
    ("Catering Platter",    "Catering",145.00, 44.00, ["group"]),
    ("Group Set Menu",      "Catering", 65.00, 19.00, ["group"]),
    ("Gift Card $50",       "Gift Card",50.00, 0.00, []),
]
MENU_BY_NAME = {m[0]: m for m in MENU}

# Square "modifiers": per-item customisations applied at checkout - milks,
# toppings, removals ("no onion"), special requests. They show up in Square's
# sales exports, and they are where real specificity lives: "your usual flat
# white" is nice, "your usual flat white (oat milk)" is unmistakably theirs.
MODIFIER_SETS = {
    "Flat White":          ["Oat Milk", "Almond Milk", "Lactose-Free Milk", "Extra Shot",
                            "Decaf", "Extra Hot"],
    "Long Black":          ["Extra Shot", "Decaf", "Cold Water on the Side"],
    "Oat Latte":           ["Extra Shot", "Decaf", "No Sugar"],
    "Smashed Avo":         ["No Feta", "Extra Chilli", "Gluten-Free Base", "Add Poached Egg"],
    "Eggs Benedict":       ["No Ham", "Add Bacon", "Sauce on the Side", "Gluten-Free Muffin"],
    "Buttermilk Pancakes": ["No Cream", "Extra Maple", "Add Bacon"],
    "Burger & Fries":      ["No Onion", "Extra Bacon", "Add Cheese", "Gluten-Free Bun"],
    "Margherita Pizza":    ["Extra Cheese", "No Basil", "Gluten-Free Base", "Add Nduja"],
    "Fish & Chips":        ["No Tartare", "Extra Lemon", "Add Salad"],
    "Pappardelle Ragu":    ["No Parmesan", "Extra Ragu", "Add Truffle Oil"],
    "Duck Fat Chips":      ["Extra Salt", "No Aioli"],
    "Garlic Bread":        ["Extra Cheese", "Add Nduja"],
    "Espresso Martini":    ["Extra Shot", "No Sugar", "Double"],
    "Negroni":             ["Extra Dry", "No Orange"],
    "Chocolate Brownie":   ["Add Gelato", "Extra Warm"],
}

FIRST = ["Ava", "Liam", "Mia", "Noah", "Olivia", "Jack", "Isla", "Thomas", "Charlotte", "Ethan",
         "Amelia", "Grace", "Cooper", "Ella", "Riley", "Zoe", "Chloe", "Ruby", "Arlo", "Sienna",
         "Harper", "Angus", "Imogen", "Luca", "Willow", "Priya", "Anh", "Sofia", "Declan", "Marco",
         "Eleni", "Tane", "Kirra", "Bianca", "Hamish", "Simone", "Ravi", "Mei", "Hassan", "Aoife"]
LAST = ["Nguyen", "Smith", "Patel", "Williams", "Chen", "Brown", "Rossi", "Jones", "Kim", "Taylor",
        "O'Brien", "Singh", "Martin", "Kaur", "Wilson", "Zhang", "Anderson", "Ali", "Thompson", "Lee",
        "Papadakis", "Walker", "Silva", "Hughes", "Kelly", "Kowalski", "Santos", "White", "Duong", "Gray"]

# ---------------------------------------------------------------------------
# Archetypes. Each one produces a recognisable customer, which means each one
# should produce a recognisable MESSAGE. That's the test.
# ---------------------------------------------------------------------------
ARCHETYPES = [
    # key, count, visits range, gap days, party, daypart, weekday bias,
    # signature item(s), attach rate, has email, has phone, email consent
    ("daily_coffee",   70, (18, 60), (4, 8),   (1, 2), "morning",  [0,1,2,3,4],
     ["Flat White"], 0.15, 0.88, 0.55, 0.82),
    ("brunch_regular", 55, (10, 30), (10, 18), (2, 3), "morning",  [5,6,0],
     ["Smashed Avo", "Eggs Benedict"], 0.45, 0.85, 0.50, 0.80),
    ("friday_group",   45, (8, 22),  (12, 20), (4, 7), "evening",  [4,5],
     ["Burger & Fries", "Margherita Pizza"], 0.70, 0.80, 0.62, 0.76),
    ("dinner_couple",  60, (6, 20),  (14, 26), (2, 2), "evening",  [3,4,5],
     ["Pappardelle Ragu", "Fish & Chips"], 0.55, 0.82, 0.44, 0.78),
    ("cocktail_crowd", 35, (5, 16),  (16, 30), (3, 5), "evening",  [4,5],
     ["Espresso Martini", "Negroni"], 0.65, 0.78, 0.58, 0.74),
    ("lunch_no_extras",40, (8, 24),  (9, 16),  (1, 2), "midday",   [0,1,2,3,4],
     ["Burger & Fries", "Margherita Pizza", "Fish & Chips"], 0.05, 0.84, 0.40, 0.80),
    # normally a pair, but occasionally turns up with a crowd - the functions lead
    ("sometimes_big",  25, (5, 14),   (16, 30), (2, 3), "evening",  [2,3,4,5],
     ["Pappardelle Ragu", "Margherita Pizza"], 0.50, 0.84, 0.48, 0.80),
    ("lapsed_vip",     30, (14, 40), (6, 12),  (2, 4), "evening",  [3,4,5],
     ["Pappardelle Ragu", "Espresso Martini"], 0.60, 0.90, 0.70, 0.86),
    ("brand_new",      50, (1, 2),   (14, 30), (1, 3), "any",      [0,1,2,3,4,5,6],
     [], 0.30, 0.72, 0.38, 0.70),
    ("occasional",     70, (3, 7),   (35, 70), (2, 3), "any",      [0,1,2,3,4,5,6],
     ["Margherita Pizza"], 0.35, 0.66, 0.34, 0.62),
    ("card_only",      45, (4, 14),  (12, 30), (2, 4), "any",      [0,1,2,3,4,5,6],
     ["Fish & Chips"], 0.40, 0.00, 0.00, 0.00),   # no contact details at all
]

DAYPART_HOURS = {"morning": (8, 11), "midday": (11, 15), "evening": (17, 22), "any": (8, 22)}

NOW = datetime(2026, 11, 4, 9, 0)


def _contact(has_email, has_phone, i):
    first, last = RNG.choice(FIRST), RNG.choice(LAST)
    name = f"{first} {last}"
    dom = RNG.choice(["gmail.com", "outlook.com", "hotmail.com", "icloud.com", "yahoo.com.au"])
    email = f"{first.lower()}.{last.lower()}{RNG.randint(1, 99)}@{dom}" if has_email else None
    phone = f"+614{RNG.randint(10000000, 99999999)}" if has_phone else None
    return name, email, phone


def _consent_source():
    return RNG.choices(
        ["POS receipt prompt", "Loyalty signup", "Website form", "Booking", "Old import (unverified)"],
        weights=[44, 26, 14, 10, 6])[0]


def _items_for(sig, attach_rate, party, daypart):
    """Build a ticket around the customer's signature order."""
    items = []
    if sig:
        main = RNG.choice(sig)
    else:
        pool = [m[0] for m in MENU if m[1] in ("Brunch", "Mains")]
        main = RNG.choice(pool)
    items.append({"name": main, "qty": 1})
    if party >= 2:
        items.append({"name": RNG.choice(sig or [m[0] for m in MENU if m[1] == "Mains"]),
                      "qty": party - 1})
    # drinks
    if daypart == "morning":
        drink = RNG.choice(["Flat White", "Long Black", "Oat Latte", "Fresh OJ"])
    elif daypart == "evening":
        drink = RNG.choice(["House Wine", "Craft Beer", "Espresso Martini", "Negroni", "Lemonade"])
    else:
        drink = RNG.choice(["Lemonade", "Fresh OJ", "Craft Beer", "Flat White"])
    items.append({"name": drink, "qty": max(1, party if RNG.random() < 0.6 else 1)})
    # attach behaviour — this is what the "never orders extras" trigger reads
    if RNG.random() < attach_rate:
        side = RNG.choice(["Duck Fat Chips", "Garlic Bread", "Caesar Salad"])
        items.append({"name": side, "qty": 1 if party < 3 else 2})
    if RNG.random() < attach_rate * 0.7:
        items.append({"name": RNG.choice(["Chocolate Brownie", "Vanilla Gelato"]), "qty": 1})
    # occasional group booking
    if party >= 6 and RNG.random() < 0.12:
        items.append({"name": RNG.choice(["Catering Platter", "Group Set Menu"]), "qty": 1})
    if RNG.random() < 0.02:
        items.append({"name": "Gift Card $50", "qty": 1})
    return items


def _build_customer(arch, idx):
    (key, _n, vrange, grange, prange, dp, wdays, sig, attach,
     p_email, p_phone, p_consent) = arch

    has_email = RNG.random() < p_email
    has_phone = RNG.random() < p_phone
    name, email, phone = _contact(has_email, has_phone, idx)
    consent = bool(email) and RNG.random() < p_consent
    unsubscribed = bool(email) and not consent and RNG.random() < 0.25

    n_visits = RNG.randint(*vrange)
    gap_lo, gap_hi = grange
    party_lo, party_hi = prange
    # A fading customer's gaps widen towards the present. brand_new has too few
    # visits to show a trend, and lapsed_vip has already gone.
    fading = RNG.random() < FADING_SHARE and key not in ("brand_new", "lapsed_vip")
    span = max(1, n_visits - 1)

    # when did their history start / end
    if key == "brand_new":
        last = NOW - timedelta(days=RNG.randint(6, 45))
    elif key == "lapsed_vip":
        # was coming regularly, then went quiet 4-8 months ago
        last = NOW - timedelta(days=RNG.randint(125, 245))
    elif key == "occasional":
        last = NOW - timedelta(days=RNG.randint(45, 140))
    else:
        last = NOW - timedelta(days=RNG.randint(1, int(gap_hi * 1.4)))

    orders = []
    d = last
    for k in range(n_visits):
        dp_use = dp if dp != "any" else RNG.choice(["morning", "midday", "evening"])
        lo, hi = DAYPART_HOURS[dp_use]
        wd = RNG.choice(wdays)
        # walk backwards from the most recent visit
        hour = RNG.randint(lo, hi - 1)
        ts = d.replace(hour=hour, minute=RNG.choice([5, 15, 25, 40, 50]))
        party = RNG.randint(party_lo, party_hi)
        if key == "sometimes_big" and RNG.random() < 0.10:
            party = RNG.randint(6, 8)
        items = _items_for(sig, attach, party, dp_use)
        gross = sum(MENU_BY_NAME[i["name"]][2] * i["qty"] for i in items)
        cost = sum(MENU_BY_NAME[i["name"]][3] * i["qty"] for i in items)
        disc = round(gross * RNG.choice([0.10, 0.15, 0.20]), 2) if RNG.random() < 0.08 else 0.0
        orders.append({
            "id": f"ord_{idx}_{k}",
            "at": ts.isoformat(),
            "date": ts.date().isoformat(),
            "weekday": ts.weekday(),
            "hour": hour,
            "daypart": dp_use,
            "party_size": party,
            "items": items,
            "gross": round(gross, 2),
            "discount": disc,
            "net": round(gross - disc, 2),
            "cost": round(cost, 2),
            "channel": RNG.choices(["DINE_IN", "TAKEAWAY"], weights=[82, 18])[0],
        })
        mult = FADING_RECENT + (FADING_OLD - FADING_RECENT) * (k / span) if fading else 1.0
        d = d - timedelta(days=max(1, int(round(RNG.randint(gap_lo, gap_hi) * mult))))

    orders.sort(key=lambda o: o["at"])
    # Birthday is assigned in a separate pass (see _assign_birthdays). Drawing
    # it here would consume from the same RNG stream that generates orders, so
    # any change to the birthday capture rate would silently reshuffle every
    # customer's order history after it.

    return {
        "id": f"cust_{idx:05d}",
        "name": name,
        "first_name": name.split(" ")[0],
        "email": email,
        "phone": phone,
        "archetype": key,
        "email_consent": consent,
        "unsubscribed": unsubscribed,
        "consent_source": _consent_source() if (consent or unsubscribed) else None,
        "consent_at": (NOW - timedelta(days=RNG.randint(30, 700))).date().isoformat()
                      if (consent or unsubscribed) else None,
        "birthday": None,
        "orders": orders,
        "joined": (NOW - timedelta(days=RNG.randint(60, 900))).date().isoformat(),
    }


FADING_SHARE = 0.30
"""Share of established customers whose visits get further apart over time.

The single most common real pattern in hospitality, and the one worth catching
early. Uniform gaps would make every customer permanently stationary, and a
churn-detection trigger would have nothing to find."""

FADING_RECENT = 1.9   # multiplier on their normal gap for the most recent visits
FADING_OLD = 0.7      # ... and for their oldest visits, so the average still holds

BIRTHDAY_CAPTURE_RATE = 0.55
"""Share of consented customers whose birthday Square actually holds. Realistic
for a venue running Square Loyalty, where birthday is a signup field. Square
never has it for someone who only ever tapped a card."""


def _assign_birthdays(customers):
    """Separate RNG stream so birthday tuning can't perturb the order data."""
    rng = random.Random(SEED + 7)
    n = 0
    for c in customers:
        if not (c["email"] and c["email_consent"] and not c["unsubscribed"]):
            continue
        if rng.random() < BIRTHDAY_CAPTURE_RATE:
            c["birthday"] = f"19{rng.randint(60, 99)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            n += 1
    return n


def _assign_mods(customers):
    """Modifier habits, applied as a post-pass on its own RNG stream (same
    reason as birthdays: tuning them must not reshuffle order histories).
    Each customer gets a stable preference per item they order - real people
    do not randomise their milk - plus occasional one-offs and second mods."""
    rng = random.Random(SEED + 11)
    for c in customers:
        prefs = {}
        for item in sorted({i["name"] for o in c["orders"] for i in o["items"]}):
            if item in MODIFIER_SETS and rng.random() < 0.6:
                prefs[item] = rng.choice(MODIFIER_SETS[item])
        for o in c["orders"]:
            for it in o["items"]:
                opts = MODIFIER_SETS.get(it["name"])
                if not opts:
                    continue
                if it["name"] in prefs and rng.random() < 0.85:
                    mods = [prefs[it["name"]]]
                    if rng.random() < 0.25:
                        mods.append(rng.choice([m for m in opts if m != mods[0]]))
                    it["mods"] = mods
                elif rng.random() < 0.15:
                    it["mods"] = [rng.choice(opts)]
                else:
                    it["mods"] = []


def build(scale=1):
    """The demo venue. `scale` multiplies every archetype's head-count, for
    benchmarking at real-venue sizes; scale=1 is the 525-customer demo."""
    RNG.seed(SEED)
    customers = []
    i = 0
    for arch in ARCHETYPES:
        for _ in range(arch[1] * scale):
            customers.append(_build_customer(arch, i))
            i += 1
    _assign_birthdays(customers)
    _assign_mods(customers)
    total_orders = sum(len(c["orders"]) for c in customers)
    return {
        "venue": VENUE,
        "menu": [{"name": m[0], "category": m[1], "price": m[2], "cost": m[3], "tags": m[4]}
                 for m in MENU],
        "customers": customers,
        "now": NOW.isoformat(),
        "stats": {
            "customers": len(customers),
            "orders": total_orders,
            "revenue": round(sum(o["net"] for c in customers for o in c["orders"]), 2),
        },
    }


# ---------------------------------------------------------------------------
# Merchant settings — what the onboarding wizard collects
# ---------------------------------------------------------------------------

DEFAULT_GOALS = {
    # Times they want busier
    "push_times": [
        {"daypart": "evening", "weekday": 1, "label": "Tuesday dinner"},
        {"daypart": "evening", "weekday": 2, "label": "Wednesday dinner"},
    ],
    # Products they want to sell more of
    "push_products": ["Espresso Martini", "Catering Platter", "Chocolate Brownie"],
    # What they're optimising for
    "priority": "repeat",          # repeat | new_customers | bigger_basket | fill_quiet | high_margin
    # The offer catalogue. Offers live in categories because "an oat milk
    # upgrade" is the right nudge for a coffee regular and nonsense for a
    # Friday-night group. The merchant edits this list and picks per trigger.
    "catalog": [
        {"id": "upg_oat",     "cat": "upgrade",   "label": "an oat milk upgrade on us",      "cost": 0.60},
        {"id": "upg_pour",    "cat": "upgrade",   "label": "a top-shelf pour in your usual", "cost": 2.20},
        {"id": "upg_seat",    "cat": "upgrade",   "label": "the good seat by the window held for you", "cost": 0.00},
        {"id": "free_side",   "cat": "free_item", "label": "a free side with your main",     "cost": 3.10},
        {"id": "free_dessert","cat": "free_item", "label": "your first one on us",           "cost": 5.40},
        {"id": "free_round",  "cat": "free_item", "label": "the first round on us",          "cost": 14.00},
        {"id": "off_bogo",    "cat": "money_off", "label": "buy one, get one free",          "cost": 9.00},
        {"id": "off_15",      "cat": "money_off", "label": "$15 off your bill",              "cost": 15.00},
        {"id": "off_10",      "cat": "money_off", "label": "10% off the table",              "cost": 7.50},
        {"id": "grp_wine",    "cat": "group",     "label": "a free bottle of house wine",    "cost": 18.00},
        {"id": "grp_table",   "cat": "group",     "label": "the window table held for your group", "cost": 0.00},
    ],
    # Which catalogue offer each trigger uses by default. Swappable per trigger.
    "trigger_offer": {
        "signature_overdue": "upg_oat",
        "birthday": "grp_wine",
        "lapsed_big_spender": "off_15",
        "social_hub": "free_round",
        "big_table": "grp_table",
        "wrong_time": "off_bogo",
        "never_adds_extras": "free_side",
        "habit_breaker": "free_dessert",
        "new_second_visit": "off_bogo",
        "drifting": "free_side",
    },
    # Unsubscribe merge tag, rewritten per sending tool on the Sending screen.
    "unsub_tag": "{{unsubscribe_url}}",
    # Sending rules
    "min_days_between_sends": 14,
    "skip_if_visited_within_days": 4,
    "channel": "email",
    # Voice of the messages: warm | punchy | polished | playful.
    # Tone changes greeting, sign-off and connective phrasing only - the
    # evidence rules, offer matching and compliance footers never change.
    "tone": "warm",
}


if __name__ == "__main__":
    d = build()
    print(d["venue"]["name"], "-", d["venue"]["suburb"])
    print(f'  customers: {d["stats"]["customers"]}')
    print(f'  orders:    {d["stats"]["orders"]:,}')
    print(f'  revenue:   ${d["stats"]["revenue"]:,.0f}')
    from collections import Counter
    c = Counter(x["archetype"] for x in d["customers"])
    for k, v in c.most_common():
        reach = sum(1 for x in d["customers"] if x["archetype"] == k
                    and x["email"] and x["email_consent"] and not x["unsubscribed"])
        print(f"    {k:16s} {v:4d}  ({reach} email-sendable)")
    tot = sum(1 for x in d["customers"] if x["email"] and x["email_consent"] and not x["unsubscribed"])
    print(f"  TOTAL email-sendable: {tot} of {len(d['customers'])}")


# ---------------------------------------------------------------------------
# Sample exports - so the CSV upload path can be tried without a Square account
# ---------------------------------------------------------------------------

def sample_simple(rows=700, per_customer=8):
    """Our documented fallback format: one row per visit.

    The slice takes each customer's MOST RECENT visits and spreads across the
    whole directory, the way a real date-range export looks. Taking the first
    N rows of the file instead would give a handful of customers with ancient
    histories, which imports cleanly and produces almost nothing.
    """
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["date", "time", "email", "name", "items", "party_size", "channel"])
    n = 0
    for c in build()["customers"]:
        if not c["email"]:
            continue
        for o in c["orders"][-per_customer:]:
            items = "; ".join(
                f'{i["qty"]}x {i["name"]}'
                + (f' ({", ".join(i["mods"])})' if i.get("mods") else "")
                for i in o["items"])
            w.writerow([o["date"], f'{o["hour"]:02d}:15', c["email"], c["name"], items,
                        o["party_size"], o["channel"]])
            n += 1
            if n >= rows:
                return buf.getvalue()
    return buf.getvalue()


SQUARE_TXN_HEADERS = ["Id", "Created at", "Location id", "Order id", "Customer id",
                      "Item variation name", "Category name", "Item modifier names",
                      "Sold count", "Gross money amount", "Covers"]
SQUARE_CUST_HEADERS = ["Customer ID", "Given name", "Family name", "Email address",
                       "Phone number", "Birthday", "Marketing opt-in"]


def sample_square(customers=40, ds=None):
    """Two files shaped like Square dashboard exports: transactions (one row per
    line item) and the customer directory."""
    import csv as _csv
    import io as _io
    ds = ds or build()
    tx, cu = _io.StringIO(), _io.StringIO()
    wt, wc = _csv.writer(tx), _csv.writer(cu)
    wt.writerow(SQUARE_TXN_HEADERS)
    wc.writerow(SQUARE_CUST_HEADERS)
    for c in ds["customers"][:customers]:
        wc.writerow([c["id"], c["first_name"], c["name"].split(" ", 1)[-1],
                     c["email"] or "", c.get("phone") or "", c.get("birthday") or "",
                     "true" if c["email_consent"] else "false"])
        for o in c["orders"]:
            for i in o["items"]:
                cat = MENU_BY_NAME[i["name"]][1]
                wt.writerow([f"txn_{o['id']}", o["at"], "LOC_1", o["id"], c["id"],
                             i["name"], cat, "; ".join(i.get("mods") or []), i["qty"],
                             round(MENU_BY_NAME[i["name"]][2] * i["qty"], 2),
                             o["party_size"]])
    return tx.getvalue(), cu.getvalue()
