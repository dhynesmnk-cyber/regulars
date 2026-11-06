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


def _find(headers, *candidates, exact=False):
    """The header for a field. An exact (normalised) name wins, in candidate
    order; only then the first header containing a candidate. Exact first
    stops "Reference ID" standing in for an ID or "Last Visit" for a surname
    when the real column is there too."""
    named = [(h, _norm(h)) for h in headers]
    for c in candidates:
        for h, n in named:
            if n == c:
                return h
    if exact:
        return None
    for h, n in named:
        for c in candidates:
            if c in n:
                return h
    return None


_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def _email(value):
    """A usable email address, or None. A Square customer ID is not one."""
    v = (value or "").strip().lower()
    return v if _EMAIL.match(v) else None


def _slash_order(values, region="AU"):
    """'dmy' or 'mdy' for dates written with slashes. The file decides when
    it can (a first part over 12 is a day; a second part over 12 is a day);
    otherwise the market's habit does."""
    for v in values:
        m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/\d{2,4}", v or "")
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 >= b:
            return "dmy"
        if b > 12 >= a:
            return "mdy"
    return "mdy" if region == "US" else "dmy"


def _parse_time(time_s):
    """'18:42', '18:42:10' or '6:42 pm' -> (18, 42); None if unreadable."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*([ap]\.?m\.?)?\s*$",
                 str(time_s or ""), re.I)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    ampm = (m.group(3) or "").lower().replace(".", "")
    if ampm == "pm" and hh < 12:
        hh += 12
    elif ampm == "am" and hh == 12:
        hh = 0
    return (hh, mm) if hh < 24 and mm < 60 else None


_FORMATS = {
    "dmy": ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
            "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d",
            "%m/%d/%Y", "%d/%m/%y", "%m/%d/%y"),
    "mdy": ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
            "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d",
            "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y"),
}


def _parse_when(date_s, time_s=None, order="dmy"):
    date_s = (date_s or "").strip()
    if not date_s:
        return None
    for fmt in _FORMATS[order]:
        try:
            base = datetime.strptime(date_s[:19], fmt)
            break
        except ValueError:
            continue
    else:
        return None
    if time_s and base.hour == 0 and base.minute == 0:
        hm = _parse_time(time_s)
        if hm:
            base = base.replace(hour=hm[0], minute=hm[1])
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


# Headers that only a Square export has: the API-style names, and the
# Dashboard's Items Detail CSV (Transactions > Export > Items Detail).
_SQUARE_MARKS = ("itemvariationname", "createdat", "locationid", "orderid", "soldcount",
                 "transactionid", "grosssales", "modifiersapplied", "pricepointname",
                 "itemizationtype")


def header_row(text):
    """The first line of a CSV, parsed properly (quoted commas and all)."""
    first = (text or "").lstrip("\ufeff").splitlines()[:1]
    return next(csv.reader(first), []) if first else []


def is_square(headers):
    norm = {_norm(h) for h in headers}
    return bool(norm & set(_SQUARE_MARKS)) or {"item", "qty"} <= norm


# ---------------------------------------------------------------------------
def parse_simple(text, menu_names, region="AU"):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return [], [], {"error": "The file had no rows."}
    headers = rows[0].keys()
    h_date = _find(headers, "date")
    h_time = _find(headers, "time", exact=True)
    h_email = _find(headers, "email")
    h_name = _find(headers, "name") or h_email
    h_items = _find(headers, "items", "products", "order")
    h_party = _find(headers, "party", "covers", "guests")
    h_chan = _find(headers, "channel")
    missing = [n for n, h in (("date", h_date), ("email", h_email), ("items", h_items)) if not h]
    if missing:
        return [], [], {"error": "Simple format needs columns: date, email, items. "
                                 f"Missing: {', '.join(missing)}."}
    order = _slash_order((r.get(h_date) for r in rows[:2000]), region)
    visits, people, bad = [], {}, 0
    for r in rows:
        when = _parse_when(r.get(h_date), r.get(h_time) if h_time else None, order)
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


def _name_key(name):
    return " ".join((name or "").lower().split())


def _subscription(value):
    """A directory's consent column -> (opted in, unsubscribed). Square's
    customer export says subscribed / unsubscribed; older files say true or
    false. Unsubscribed is permanent: no attestation overrides it."""
    v = _name_key(value)
    if v in ("true", "yes", "y", "1", "opted in", "subscribed"):
        return True, False
    if v.startswith("unsub") or v in ("opted out", "opted-out"):
        return False, True
    return False, False


def _directory(cust_text):
    """The customer export -> (people by key, name -> key, names shared by
    more than one customer). A person's key is their Square customer ID, or
    their email when the file has no IDs."""
    people, by_name, shared = {}, {}, set()
    crows = list(csv.DictReader(io.StringIO(cust_text or "")))
    if not crows:
        return people, by_name, shared
    ch = crows[0].keys()
    c_id = (_find(ch, "squarecustomerid", "customerid") or _find(ch, "id", exact=True)
            or _find(ch, "referenceid", exact=True))
    c_first = _find(ch, "givenname", "firstname")
    c_last = _find(ch, "familyname", "lastname", "surname")
    c_email = _find(ch, "emailaddress", "email")
    c_bday = _find(ch, "birthday")
    c_opt = _find(ch, "marketingoptin", "emailsubscriptionstatus", "optin", "consent",
                  "subscription")
    for r in crows:
        email = _email(r.get(c_email)) if c_email else None
        name = " ".join(x for x in ((r.get(c_first) or "").strip() if c_first else "",
                                    (r.get(c_last) or "").strip() if c_last else "") if x)
        cid = (r.get(c_id) or "").strip().lower() if c_id else ""
        key = cid or email or (("name:" + _name_key(name)) if name else "")
        if not key:
            continue
        optin, unsub = _subscription(r.get(c_opt)) if c_opt else (None, False)
        people[key] = {"key": key, "email": email,
                       "name": name or (email or "").split("@")[0],
                       "birthday": (r.get(c_bday) or "").strip() if c_bday else None,
                       "optin": optin, "unsubscribed": unsub}
        nk = _name_key(name)
        if nk:
            if by_name.get(nk, key) != key:
                shared.add(nk)
            by_name[nk] = key
    return people, by_name, shared


def parse_square(tx_text, cust_text, menu_names, region="AU"):
    """Square's exports: the line-item file (the Dashboard's Items Detail CSV,
    or the API-style names) plus, optionally, the customer export, which is
    where the email addresses and consent live."""
    rows = list(csv.DictReader(io.StringIO(tx_text)))
    if not rows:
        return [], [], {"error": "The transactions file had no rows."}
    headers = rows[0].keys()
    h_when = _find(headers, "createdat", "created", "date")
    h_time = _find(headers, "time", exact=True)
    h_item = _find(headers, "itemvariationname", "itemname", "item")
    h_qty = _find(headers, "soldcount", "quantity", "qty")
    h_order = _find(headers, "orderid", "transactionid")
    h_cid = _find(headers, "customerid")
    h_cname = _find(headers, "customername")
    h_email = _find(headers, "customeremail", "email")
    h_cov = _find(headers, "covers", "partysize", "guests")
    h_amt = _find(headers, "grossmoneyamount", "grosssales", "gross", "amount", "total")
    h_cat = _find(headers, "categoryname", "category")
    h_mod = _find(headers, "modifier")
    h_one = _find(headers, "customer", exact=True)
    if h_one and not (h_cid or h_cname):
        # A single "Customer" column: names if they look like names, else IDs.
        sample = [(r.get(h_one) or "").strip() for r in rows[:500]]
        sample = [v for v in sample if v]
        if sample and 2 * sum(" " in v for v in sample) >= len(sample):
            h_cname = h_one
        else:
            h_cid = h_one
    if not h_when or not h_item:
        return [], [], {"error": "Could not find a date column and an item column in that "
                                 "file. If it isn't a Square transactions export, use the "
                                 "simple format instead."}
    order = _slash_order((r.get(h_when) for r in rows[:2000]), region)

    orders = {}
    for r in rows:
        when = _parse_when(r.get(h_when), r.get(h_time) if h_time else None, order)
        name = (r.get(h_item) or "").strip()
        if not when or not name:
            continue
        mods = _split_mods(r.get(h_mod)) if h_mod else []
        name, paren_mods = _peel_mods(name)
        mods = mods + [m for m in paren_mods if m not in mods]
        if not name:
            continue
        oid = (r.get(h_order) or f"{when:%Y%m%d%H%M}") if h_order else f"{when:%Y%m%d%H%M}"
        o = orders.setdefault(oid, {"when": when, "items": [], "cid": None, "cname": None,
                                    "email": None, "party": None, "total": 0.0})
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
        if h_cid:
            o["cid"] = (r.get(h_cid) or "").strip().lower() or o["cid"]
        if h_cname:
            o["cname"] = " ".join((r.get(h_cname) or "").split()) or o["cname"]
        if h_email:
            o["email"] = _email(r.get(h_email)) or o["email"]
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

    people, by_name, shared = _directory(cust_text)
    people_by_email = {}
    for p in people.values():
        if p["email"]:
            people_by_email.setdefault(p["email"], p)
    visits, ambiguous = [], 0
    for o in orders.values():
        key = person = None
        if o["cid"]:
            key = o["cid"]
            person = people.get(key)
        elif o["cname"]:
            nk = _name_key(o["cname"])
            if nk in shared:
                ambiguous += 1          # two customers share this name: don't guess
            elif nk in by_name:
                key = by_name[nk]
                person = people[key]
            else:
                key = "name:" + nk
        if person is None and o["email"]:
            person = people_by_email.get(o["email"])
            key = key or (person["key"] if person else o["email"])
        email = (person or {}).get("email") or o["email"] or _email(o["cid"])
        visits.append({"when": o["when"], "key": key, "email": email,
                       "name": (person or {}).get("name") or o["cname"],
                       "items": o["items"], "party": o["party"] or 1,
                       "channel": "DINE_IN"})
    visits.sort(key=lambda v: v["when"])
    timed = any(v["when"].hour or v["when"].minute for v in visits)
    found = {n: bool(h) for n, h in (("timestamp", h_when), ("time of day", timed),
                                     ("item", h_item), ("quantity", h_qty),
                                     ("order id", h_order),
                                     ("customer link", h_cid or h_cname or h_email),
                                     ("covers", h_cov), ("amount", h_amt),
                                     ("category", h_cat), ("modifiers", h_mod))}
    report = {"fields_found": [k for k, v in found.items() if v],
              "fields_missing": [k for k, v in found.items() if not v],
              "orders": len(visits), "directory_rows": len(people),
              "linked_orders": sum(1 for v in visits if v["email"])}
    if ambiguous:
        report["ambiguous_orders"] = ambiguous
    return visits, list(people.values()), report


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
    """Fold visits into the dataset shape the engine expects. Customers are
    grouped by their key (Square customer ID, or email); a customer without a
    usable email address is kept, so they count, but can't be messaged."""
    # The business identity comes from the owner's Setup details, never from
    # the file; this is only a neutral label for the dataset itself.
    venue = venue or {"name": "", "suburb": "", "abn": ""}
    groups = {}
    for v in visits:
        key = v.get("key") or v.get("email")
        if key:
            groups.setdefault(key, []).append(v)
    by_key, by_email = {}, {}
    for p in people:
        if p.get("key"):
            by_key.setdefault(p["key"], p)
        if p.get("email"):
            by_email.setdefault(p["email"], p)
    price = {m["name"]: m["price"] for m in menu}

    def email_of(vs):
        return next((e for e in (_email(v.get("email")) for v in vs) if e), None)

    ordered = sorted(groups.items(), key=lambda kv: (email_of(kv[1]) or kv[0], kv[0]))
    customers = []
    dropped = 0
    for i, (key, vs) in enumerate(ordered):
        email = email_of(vs)
        person = by_key.get(key) or (by_email.get(email) if email else None)
        name = ((person or {}).get("name") or vs[0].get("name")
                or (email or key).split("@")[0])
        orders = []
        for j, v in enumerate(sorted(vs, key=lambda x: x["when"])):
            items = [it for it in v["items"] if it["name"] in price]
            if not items:
                dropped += 1
                continue
            gross = sum(price[it["name"]] * it["qty"] for it in items)
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
            "unsubscribed": bool((person or {}).get("unsubscribed")),
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
