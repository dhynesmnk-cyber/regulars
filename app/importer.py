"""
CSV import: the alternative to connecting Square.

Two accepted shapes:

1. Square's own dashboard exports - a transactions/item-sales CSV (one row per
   line item) plus, optionally, the customer directory CSV. Headers are matched
   tolerantly, because Square has renamed columns over the years and AU/US
   exports differ.

2. A simple documented format, one row per visit:
   date, time, email, name, items, party_size, channel
   where items is "2x Flat White; 1x Garlic Bread".

Whatever arrives, the result is the same dataset shape data.build() produces,
plus a report listing which fields were found, which were missing, and what
that means for the triggers.
"""

import csv
import io
import re
from datetime import datetime

MENU_HINTS = None


def _norm(h):
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def _find(headers, *candidates):
    """First header whose normalised name contains any candidate string."""
    for h in headers:
        n = _norm(h)
        for c in candidates:
            if c in n:
                return h
    return None


def _parse_when(date_s, time_s=None):
    date_s = (date_s or "").strip()
    if not date_s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            base = datetime.strptime(date_s[:19], fmt)
            break
        except ValueError:
            continue
    else:
        return None
    if time_s and base.hour == 0 and base.minute == 0:
        try:
            hh, mm = str(time_s).strip().split(":")[:2]
            base = base.replace(hour=int(hh), minute=int(mm))
        except (ValueError, IndexError):
            pass
    return base


def _split_mods(cell):
    """A modifiers cell or parenthetical: 'Oat Milk; Extra Shot' / 'Oat Milk, Extra Shot'."""
    return [x.strip() for x in re.split(r"[;|]", cell or "") if x.strip()] \
        if ";" in (cell or "") or "|" in (cell or "") else \
        [x.strip() for x in re.split(r",", cell or "") if x.strip()]


def _peel_mods(name):
    """'Flat White (Oat Milk, Extra Shot)' -> ('Flat White', ['Oat Milk', 'Extra Shot']).

    Square writes modifiers into item names on receipts and some exports even
    when there is no dedicated modifiers column, so both shapes are accepted."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name or "")
    if m:
        return m.group(1).strip(), [x.strip() for x in re.split(r"[;,]", m.group(2)) if x.strip()]
    return (name or "").strip(), []


def _parse_items(text, menu_names):
    """'2x Flat White; 1x Garlic Bread' or 'Flat White, Garlic Bread'.
    Modifier parentheticals ride along: '1x Flat White (Oat Milk)'."""
    out = []
    low = {n.lower(): n for n in menu_names}
    for part in re.split(r"[;\n]|,(?=\s*\d+\s*x)|,(?![^(]*\bx\b)", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*x?\s+(.+)$", part, re.I)
        qty, name = (int(m.group(1)), m.group(2).strip()) if m else (1, part)
        name, mods = _peel_mods(name)
        real = low.get(name.lower())
        if real:
            out.append({"name": real, "qty": qty, "mods": mods})
        elif not menu_names:
            # no menu to validate against - the menu gets built from the file
            out.append({"name": name, "qty": qty, "mods": mods})
    return out


def _daypart(hour):
    if hour < 11:
        return "morning"
    if hour < 15:
        return "midday"
    return "evening"


def _sniff(text):
    head = text.lstrip("\ufeff").splitlines()
    return "square" if any("itemvariationname" == _norm(h) or _norm(h) in
                           ("createdat", "locationid") for h in (head[0].split(",") if head else [])) \
        else "simple"


# ---------------------------------------------------------------------------
def parse_simple(text, menu_names):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return [], [], {"error": "The file had no rows."}
    headers = rows[0].keys()
    h_date = _find(headers, "date")
    h_time = _find(headers, "time")
    h_email = _find(headers, "email")
    h_name = _find(headers, "name") or h_email
    h_items = _find(headers, "items", "products", "order")
    h_party = _find(headers, "party", "covers", "guests")
    h_chan = _find(headers, "channel")
    missing = [n for n, h in (("date", h_date), ("email", h_email), ("items", h_items)) if not h]
    if missing:
        return [], [], {"error": "Simple format needs columns: date, email, items. "
                                 f"Missing: {', '.join(missing)}."}
    visits, people, bad = [], {}, 0
    for r in rows:
        when = _parse_when(r.get(h_date), r.get(h_time) if h_time else None)
        items = _parse_items(r.get(h_items), menu_names)
        email = (r.get(h_email) or "").strip().lower()
        if not when or not items or not email:
            bad += 1
            continue
        try:
            party = int(float(r.get(h_party) or 1))
        except (TypeError, ValueError):
            party = 1
        visits.append({"when": when, "email": email,
                       "name": (r.get(h_name) or email.split("@")[0]).strip(),
                       "items": items, "party": max(1, party),
                       "channel": (r.get(h_chan) or "DINE_IN").strip().upper() or "DINE_IN"})
        people.setdefault(email, (r.get(h_name) or email.split("@")[0]).strip())
    return visits, [{"email": e, "name": n} for e, n in people.items()], \
        {"skipped_rows": bad}


def parse_square(tx_text, cust_text, menu_names):
    rows = list(csv.DictReader(io.StringIO(tx_text)))
    if not rows:
        return [], [], {"error": "The transactions file had no rows."}
    headers = rows[0].keys()
    h_when = _find(headers, "createdat", "created", "date")
    h_item = _find(headers, "itemvariationname", "itemname", "item")
    h_qty = _find(headers, "soldcount", "quantity", "qty")
    h_order = _find(headers, "orderid", "transactionid")
    h_cust = _find(headers, "customerid", "customer")
    h_email = _find(headers, "customeremail", "email")
    h_cov = _find(headers, "covers", "partysize", "guests")
    h_amt = _find(headers, "grossmoneyamount", "gross", "amount", "total")
    h_cat = _find(headers, "categoryname", "category")
    h_mod = _find(headers, "modifier")
    found = {n: bool(h) for n, h in (("timestamp", h_when), ("item", h_item), ("quantity", h_qty),
                                     ("order id", h_order), ("customer link", h_cust or h_email),
                                     ("covers", h_cov), ("amount", h_amt),
                                     ("category", h_cat), ("modifiers", h_mod))}
    if not h_when or not h_item:
        return [], [], {"error": "Could not find a date column and an item column in that "
                                 "file. If it isn't a Square transactions export, use the "
                                 "simple format instead."}

    orders = {}
    for r in rows:
        when = _parse_when(r.get(h_when))
        name = (r.get(h_item) or "").strip()
        if not when or not name:
            continue
        mods = _split_mods(r.get(h_mod)) if h_mod else []
        name, paren_mods = _peel_mods(name)
        mods = mods + [m for m in paren_mods if m not in mods]
        if not name:
            continue
        oid = (r.get(h_order) or f"{when:%Y%m%d%H%M}") if h_order else f"{when:%Y%m%d%H%M}"
        o = orders.setdefault(oid, {"when": when, "items": [], "email": None, "party": None,
                                    "total": 0.0})
        try:
            qty = int(float(r.get(h_qty) or 1)) if h_qty else 1
        except (TypeError, ValueError):
            qty = 1
        price = None
        if h_amt:
            try:
                amt = float(str(r.get(h_amt)).replace("$", "").replace(",", "") or 0)
                price = round(amt / qty, 2) if qty else None
            except (TypeError, ValueError):
                price = None
        o["items"].append({"name": name, "qty": qty, "price": price,
                           "category": (r.get(h_cat) or "").strip() or None,
                           "mods": mods})
        if h_cust:
            o["email"] = (r.get(h_cust) or "").strip().lower() or o["email"]
        if h_email and not o["email"]:
            o["email"] = (r.get(h_email) or "").strip().lower() or None
        if h_cov:
            try:
                o["party"] = max(o["party"] or 1, int(float(r.get(h_cov) or 1)))
            except (TypeError, ValueError):
                pass
        if h_amt:
            try:
                o["total"] += float(str(r.get(h_amt)).replace("$", "").replace(",", "") or 0)
            except (TypeError, ValueError):
                pass

    people = {}
    if cust_text:
        crows = list(csv.DictReader(io.StringIO(cust_text)))
        if crows:
            ch = crows[0].keys()
            c_id = _find(ch, "customerid", "id")
            c_first = _find(ch, "givenname", "firstname", "first")
            c_last = _find(ch, "familyname", "lastname", "last")
            c_email = _find(ch, "emailaddress", "email")
            c_bday = _find(ch, "birthday")
            c_opt = _find(ch, "marketingoptin", "optin", "consent")
            for r in crows:
                email = (r.get(c_email) or "").strip().lower() if c_email else ""
                name = " ".join(x for x in ((r.get(c_first) or "").strip(),
                                            (r.get(c_last) or "").strip()) if x)
                key = (r.get(c_id) or "").strip().lower() if c_id else email
                people[key] = {"email": email, "name": name or email.split("@")[0],
                               "birthday": (r.get(c_bday) or "").strip() if c_bday else None,
                               "optin": (r.get(c_opt) or "").strip().lower()
                                        in ("true", "yes", "1", "opted in") if c_opt else None}

    visits = []
    for o in orders.values():
        email = o["email"]
        if email and email in people:
            email = people[email]["email"] or email
        elif email and any(v["email"] == email for v in people.values()):
            pass
        visits.append({"when": o["when"], "email": email, "name": None,
                       "items": o["items"], "party": o["party"] or 1,
                       "channel": "DINE_IN"})
    visits.sort(key=lambda v: v["when"])
    cust_list = list(people.values())
    report = {"fields_found": [k for k, v in found.items() if v],
              "fields_missing": [k for k, v in found.items() if not v],
              "orders": len(visits), "directory_rows": len(cust_list)}
    return visits, cust_list, report


_SIDE = ("chips", "fries", "side", "slaw", "bread", "salad", "wedge")
_DESSERT = ("brownie", "ice cream", "gelato", "tart", "cake", "dessert", "affogato")
_BAR = ("wine", "beer", "martini", "spritz", "negroni", "cocktail", "cider",
        "prosecco", "whisky", "gin", "vodka", "aperol")
_BRUNCH = ("avo", "benedict", "pancake", "omelette", "granola", "toast", "bircher")
_DRINK = ("coffee", "latte", "flat white", "cappuccino", "tea", "matcha", "juice",
          "smoothie", "soda", "lemonade")


def guess_category(name):
    n = name.lower()
    for hints, cat in ((_DESSERT, "Dessert"), (_SIDE, "Sides"), (_BAR, "Bar"),
                       (_BRUNCH, "Brunch"), (_DRINK, "Drinks")):
        if any(h in n for h in hints):
            return cat
    return "Mains"


def build_menu(visits):
    """Infer a menu from what appears in the file. Categories come from the
    Square export when it has them; otherwise they're guessed by name, and the
    import report says so."""
    agg = {}
    for v in visits:
        for it in v["items"]:
            a = agg.setdefault(it["name"], {"qty": 0, "prices": [], "cat": None, "mods": {}})
            a["qty"] += it["qty"]
            if it.get("price"):
                a["prices"].append(it["price"])
            if it.get("category") and not a["cat"]:
                a["cat"] = it["category"]
            for md in it.get("mods") or []:
                a["mods"][md] = a["mods"].get(md, 0) + it["qty"]
    menu = []
    for name, a in sorted(agg.items(), key=lambda x: -x[1]["qty"]):
        price = round(sum(a["prices"]) / len(a["prices"]), 2) if a["prices"] else 0.0
        cat = a["cat"] or guess_category(name)
        tags = ["alcohol"] if cat == "Bar" else []
        menu.append({"name": name, "category": cat, "price": price,
                     "cost": round(price * 0.3, 2), "tags": tags,
                     "common_mods": sorted(a["mods"].items(), key=lambda x: -x[1])[:5]})
    return menu


# ---------------------------------------------------------------------------
def to_dataset(visits, people, menu, now, attested_consent=False, venue=None):
    """Fold visits into the dataset shape the engine expects."""
    import data as D
    venue = venue or D.VENUE
    by_email = {}
    for v in visits:
        if not v["email"]:
            continue
        by_email.setdefault(v["email"], []).append(v)

    customers = []
    dropped = 0
    known = {m["name"] for m in menu}
    for i, (email, vs) in enumerate(sorted(by_email.items())):
        person = next((p for p in people if p.get("email") == email), None)
        name = (person or {}).get("name") or (vs[0].get("name") or email.split("@")[0])
        orders = []
        for j, v in enumerate(sorted(vs, key=lambda x: x["when"])):
            items = [it for it in v["items"] if it["name"] in known]
            if not items:
                dropped += 1
                continue
            gross = 0.0
            for it in items:
                price = next((m["price"] for m in menu if m["name"] == it["name"]), 0.0)
                cost = next((m["cost"] for m in menu if m["name"] == it["name"]), 0.0)
                gross += price * it["qty"]
            ts = v["when"]
            orders.append({
                "id": f"imp_{i}_{j}", "at": ts.isoformat(), "date": ts.date().isoformat(),
                "weekday": ts.weekday(), "hour": ts.hour,
                "daypart": _daypart(ts.hour), "party_size": v["party"],
                "items": items, "gross": round(gross, 2), "discount": 0.0,
                "net": round(gross, 2), "cost": round(gross * 0.3, 2),
                "channel": v["channel"],
            })
        if not orders:
            continue
        consent = bool(attested_consent) or bool((person or {}).get("optin"))
        customers.append({
            "id": f"imp_{i:05d}", "name": name, "first_name": name.split(" ")[0],
            "email": email, "phone": None, "archetype": "imported",
            "email_consent": consent,
            "unsubscribed": False,
            "consent_source": ("Merchant attested at import" if attested_consent
                               else ("Square marketing opt-in" if (person or {}).get("optin")
                                     else None)),
            "consent_at": now.date().isoformat() if consent else None,
            "birthday": (person or {}).get("birthday") or None,
            "orders": orders, "joined": orders[0]["date"],
        })
    customers.sort(key=lambda c: c["id"])
    return {
        "venue": venue,
        "menu": menu,
        "customers": customers,
        "now": now.isoformat(),
        "stats": {
            "customers": len(customers),
            "orders": sum(len(c["orders"]) for c in customers),
            "revenue": round(sum(o["net"] for c in customers for o in c["orders"]), 2),
        },
        "imported": True,
        "unmatched_line_items": dropped,
    }
