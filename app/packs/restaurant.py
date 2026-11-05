"""
Industry pack: independent cafes, restaurants and bars on Square.

Everything that knows about food lives here: the menu categories (sides,
desserts, mains, alcohol), mealtimes, table sizes, modifier habits ("oat
milk"), the ten triggers, their thresholds, the offer categories and the
email templates. The engine (engine.py) supplies everything that is the same
in every industry: suppression, trigger ordering, offer selection, tone,
regional spelling and the legal footer.

See packs/__init__.py for the interface a pack provides.
"""

from collections import Counter, defaultdict

import data as D
from core import DAY_NAMES, _dt, _median, _season_phrase, _vague, _vague_future

NAME = "restaurant"
LABEL = "Cafes, restaurants & bars"

# Demo venue, default goals and sample files for this industry.
VENUE = D.VENUE
DEFAULT_GOALS = D.DEFAULT_GOALS
demo_dataset = D.build
sample_simple = D.sample_simple
sample_square = D.sample_square

# Every number the triggers use, in one place, because the merchant gets to
# change all of them. These are defaults, not laws.
DEFAULT_THRESHOLDS = {
    # their usual, overdue
    "sig_min_visits": 3,        # orders of one thing before it counts as "their usual"
    "sig_overdue_mult": 1.5,    # x their own median gap before we call it overdue
    "sig_min_recency": 7,       # never nudge inside this many days of a visit
    # lapses and win-backs
    "lapsed_days": 110,         # absent longer than this = lapsed, not overdue
    "lapsed_spend_pct": 55,     # win-back credit is for real spenders only
    "lapsed_min_visits": 5,
    # birthday
    "birthday_window_days": 14,
    # the one who organises
    "social_avg_party": 3.0,
    "social_min_visits": 4,
    # big table potential
    "big_max_party": 6,
    "big_min_visits": 3,
    "big_avg_party_max": 3.0,   # habitual big tables belong to social_hub
    # new customers
    "new_window_days": 60,
    "new_max_visits": 2,
    # upsells
    "extras_min_visits": 4,
    "extras_max_attach": 0.20,  # adds a side/dessert on fewer than this share of visits
    "habit_min_visits": 6,
    "habit_max_variety": 0.45,
    "upsell_overdue_max": 1.5,  # upsells stop once someone is this overdue
    "wrong_time_min_visits": 3,
    # slowing down
    "drift_min_visits": 6,
    "drift_trend_min": 1.6,     # recent gaps this many x their early gaps
    "drift_max_recency": 90,
}

# UI metadata for the tuning panel: (key, label, help, step)
THRESHOLD_FIELDS = [
    ("sig_min_visits", "Orders before something is 'their usual'",
     "How many times must they order the same thing before a message can name it.", 1),
    ("sig_overdue_mult", "'Overdue' multiplier",
     "Their own median gap between visits, multiplied by this. A daily regular and a "
     "monthly regular get judged against themselves, not against each other.", 0.1),
    ("sig_min_recency", "Quiet period after a visit (days)",
     "Nobody gets a nudge inside this many days of being in. They don't need it.", 1),
    ("lapsed_days", "A lapse starts at (days absent)",
     "Past this, the nudge becomes a win-back with a real incentive behind it.", 5),
    ("lapsed_spend_pct", "Win-backs limited to spenders above (percentile)",
     "A $15 credit is an investment. Spend it on people whose absence costs you.", 5),
    ("lapsed_min_visits", "Minimum visits to count as a lost regular",
     "Someone who came once was never a regular. Don't pay to get them back.", 1),
    ("birthday_window_days", "Birthday window (days ahead)",
     "How far ahead a birthday is worth acting on. Beyond a fortnight nobody has "
     "decided where they're going, and birthday outranks everything else.", 1),
    ("social_avg_party", "'Organiser' = average table of at least",
     "The person who rounds everyone up. Your cheapest acquisition channel.", 0.5),
    ("social_min_visits", "Minimum visits for the organiser trigger",
     "One big birthday doesn't make someone your referral engine.", 1),
    ("big_max_party", "Biggest table that counts as 'big' (people)",
     "Has seated at least this many before, but usually comes as a pair.", 1),
    ("big_min_visits", "Minimum visits for the big-table trigger", "", 1),
    ("big_avg_party_max", "Usual table must be smaller than",
     "People who always bring a crowd get the organiser message instead.", 0.5),
    ("new_window_days", "A customer counts as new for (days)",
     "The habit-forming window. The second visit is the one that keeps them.", 5),
    ("new_max_visits", "Maximum visits to still count as new", "", 1),
    ("extras_min_visits", "Minimum visits for the side-dish upsell", "", 1),
    ("extras_max_attach", "Upsell if they add a side on under (share of visits)",
     "0.20 means one visit in five or less. Above that, they already know.", 0.05),
    ("habit_min_visits", "Minimum visits for the 'creature of habit' trigger", "", 1),
    ("habit_max_variety", "Narrow menu range means variety under",
     "Distinct items divided by total items. Low = orders the same few things.", 0.05),
    ("upsell_overdue_max", "Upsells stop once someone is this overdue",
     "Pitching a free side to someone who's lapsing is tone-deaf. Win them back first.", 0.1),
    ("wrong_time_min_visits", "Minimum visits for the quiet-slot invite", "", 1),
    ("drift_min_visits", "Minimum visits to detect slowing down", "", 1),
    ("drift_trend_min", "'Slowing down' = recent gaps this many x their early gaps",
     "Catches the slide while the habit is still recoverable.", 0.1),
    ("drift_max_recency", "Slowing-down window (days since last visit)",
     "Past this they've lapsed, and a lapse gets the win-back message.", 5),
]

DAYPART_LABEL = {"morning": "morning", "midday": "lunch", "evening": "dinner", "any": "any"}
# how a human actually says it
DAYPART_PHRASE = {"morning": "breakfast", "midday": "lunch", "evening": "dinner", "any": "any time"}


MOD_HABIT_MIN_SHARE = 0.55   # a modifier on 55%+ of their orders of the item is a habit


BY_NAME = {}


def set_menu(menu):
    """Point the engine at a menu. The demo venue and an imported CSV both
    flow through here, so triggers never hard-code one venue's dishes."""
    global BY_NAME
    BY_NAME = {}
    for m in menu:
        if isinstance(m, dict):
            BY_NAME[m["name"]] = (m["name"], m["category"], m["price"], m["cost"],
                                  m.get("tags", []))
        else:
            BY_NAME[m[0]] = tuple(m)
    return BY_NAME


set_menu(D.MENU)


def venue_stats(dataset):
    """Small venue-wide facts used to make suggestions concrete and true."""
    set_menu(dataset["menu"])
    side_dessert = Counter()
    mains = Counter()
    for c in dataset["customers"]:
        for o in c["orders"]:
            for it in o["items"]:
                cat = BY_NAME.get(it["name"], (0, "", 0, 0, []))[1]
                if cat == "Sides":
                    side_dessert[it["name"]] += it["qty"]
                elif cat == "Mains":
                    mains[it["name"]] += it["qty"]
    return {
        "top_side": side_dessert.most_common(1)[0][0] if side_dessert else "a side",
        "top_main": mains.most_common(1)[0][0] if mains else "a main",
        "side_counts": dict(side_dessert),
    }


def prepare(dataset):
    """Called by the engine before a run: point lookups at this dataset's menu."""
    set_menu(dataset["menu"])


# ---------------------------------------------------------------------------
# 1. PROFILE — what this customer actually does
# ---------------------------------------------------------------------------

def profile(c, now, spend_pct_fn=None):
    orders = c["orders"]
    if not orders:
        return {"id": c["id"], "visits": 0, "empty": True}

    dates = [_dt(o["at"]).date() for o in orders]
    dates.sort()
    last = dates[-1]
    first = dates[0]
    recency = (now.date() - last).days
    tenure = (now.date() - first).days

    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1) if (dates[i + 1] - dates[i]).days > 0]
    own_gap = _median(gaps)
    overdue = round(recency / own_gap, 2) if own_gap and own_gap > 0 else None

    # Cadence trend: compare the most recent third of gaps with the earliest
    # third. This catches someone slowing down *before* they lapse, which is a
    # different signal from "they're late for their usual" and needs a cheaper
    # response.
    gap_trend = early_gap = recent_gap = None
    if len(gaps) >= 6:
        k = len(gaps) // 3
        early_gap = round(sum(gaps[:k]) / k, 1)
        recent_gap = round(sum(gaps[-k:]) / k, 1)
        if early_gap > 0:
            gap_trend = round(recent_gap / early_gap, 2)

    totals = [o["net"] for o in orders]
    parties = [o["party_size"] for o in orders]

    item_counts = Counter()
    visits_with_item = defaultdict(set)
    for oi, o in enumerate(orders):
        for it in o["items"]:
            item_counts[it["name"]] += it["qty"]
            visits_with_item[it["name"]].add(oi)

    dp_counts = Counter(o["daypart"] for o in orders)
    wd_counts = Counter(o["weekday"] for o in orders)

    # a "visit with a side or dessert" — the merchant's attach rate for THIS person
    side_dessert = {n for n, t in BY_NAME.items() if t[1] in ("Sides", "Dessert")}
    attach_visits = sum(1 for o in orders if any(i["name"] in side_dessert for i in o["items"]))
    attach_rate = round(attach_visits / len(orders), 2)

    alcohol_visits = sum(1 for o in orders
                         if any("alcohol" in BY_NAME[i["name"]][4] for i in o["items"]
                                if i["name"] in BY_NAME))
    units = sum(item_counts.values()) or 1
    variety = round(len(item_counts) / units, 2)

    # modifier habits: how they customise what they order, per item. Only
    # dominant habits count (a two-visit trial of almond milk is not "their
    # milk") - the copy will speak with confidence or stay quiet.
    mod_visits = defaultdict(Counter)
    for oi, o in enumerate(orders):
        seen = set()
        for it in o["items"]:
            for md in it.get("mods") or []:
                key = (it["name"], md)
                if key not in seen:
                    mod_visits[it["name"]][md] += 1
                    seen.add(key)
    mod_habits = {}
    for item, ctr in mod_visits.items():
        n_vi = len(visits_with_item.get(item, ()))
        if not n_vi:
            continue
        top = [(md, n, round(n / n_vi, 2)) for md, n in ctr.most_common(2)
               if n >= 2 and n / n_vi >= MOD_HABIT_MIN_SHARE]
        if top:
            mod_habits[item] = top

    # signature item: ordered repeatedly AND on a meaningful share of visits
    sig_item, sig_visits, sig_share = None, 0, 0.0
    for name, oidx in visits_with_item.items():
        share = len(oidx) / len(orders)
        if len(oidx) >= 3 and share > sig_share and BY_NAME.get(name, (0, "", 0, 0, []))[1] not in ("Gift Card",):
            sig_item, sig_visits, sig_share = name, len(oidx), share

    birthday_days = None
    if c.get("birthday"):
        bd = _dt(c["birthday"] + "T00:00:00") if len(c["birthday"]) == 10 else None
        if bd:
            nxt = bd.replace(year=now.year)
            if nxt.date() < now.date():
                nxt = nxt.replace(year=now.year + 1)
            birthday_days = (nxt.date() - now.date()).days

    p = {
        "id": c["id"], "empty": False,
        "visits": len(orders),
        "first_days_ago": tenure,
        "recency_days": recency,
        "last_visit": last.isoformat(),
        "own_gap": round(own_gap, 1) if own_gap else None,
        "gap_trend": gap_trend,
        "early_gap": early_gap,
        "recent_gap": recent_gap,
        "overdue": overdue,
        "total_spend": round(sum(totals), 2),
        "avg_ticket": round(sum(totals) / len(totals), 2),
        "avg_party": round(sum(parties) / len(parties), 1),
        "max_party": max(parties),
        "solo_share": round(sum(1 for x in parties if x == 1) / len(parties), 2),
        "signature_item": sig_item,
        "signature_visits": sig_visits,
        "signature_share": round(sig_share, 2),
        "mod_habits": mod_habits,
        "top_items": [n for n, _ in item_counts.most_common(4)],
        "ever_bought": set(item_counts.keys()),
        "primary_daypart": dp_counts.most_common(1)[0][0],
        "daypart_counts": dict(dp_counts),
        "primary_weekday": wd_counts.most_common(1)[0][0],
        "weekday_counts": dict(wd_counts),
        "attach_rate": attach_rate,
        "variety": variety,
        "alcohol_share": round(alcohol_visits / len(orders), 2),
        "discount_share": round(sum(1 for o in orders if o["discount"] > 0) / len(orders), 2),
        "birthday_days": birthday_days,
        "is_new": tenure <= 60 and len(orders) <= 2,
        "spend_pct": spend_pct_fn(sum(totals)) if spend_pct_fn else 50,
        "biggest_table": max(parties),
    }
    return p


# ---------------------------------------------------------------------------
# 3. TRIGGERS — ordered, first match wins
#
# Each trigger returns (matched: bool, evidence: dict) so the UI can show
# exactly why a person got a particular message.
# ---------------------------------------------------------------------------

def t_signature_overdue(p, goals, th):
    if not p["signature_item"] or p["signature_visits"] < th["sig_min_visits"]:
        return False, {}
    if (not p["overdue"] or p["overdue"] < th["sig_overdue_mult"]
            or p["recency_days"] < th["sig_min_recency"]):
        return False, {}
    # Someone gone five months does not want a nudge about their flat white -
    # they need a reason to come back. Hand them to the win-back trigger.
    if p["recency_days"] >= th["lapsed_days"]:
        return False, {}
    return True, {
        "item": p["signature_item"],
        "times": p["signature_visits"],
        "usual_gap": p["own_gap"],
        "since": p["recency_days"],
        "multiple": p["overdue"],
        "mods": _ev_mods(p, p["signature_item"]),
    }


def t_lapsed_big_spender(p, goals, th):
    if (p["recency_days"] < th["lapsed_days"] or p["spend_pct"] < th["lapsed_spend_pct"]
            or p["visits"] < th["lapsed_min_visits"]):
        return False, {}
    return True, {
        "spend": p["total_spend"],
        "visits": p["visits"],
        "since": p["recency_days"],
        "last_visit": p["last_visit"],
        "pct": p["spend_pct"],
    }


def t_birthday(p, goals, th):
    if p["birthday_days"] is None or not (0 <= p["birthday_days"] <= th["birthday_window_days"]):
        return False, {}
    return True, {"in_days": p["birthday_days"], "visits": p["visits"]}


def t_social_hub(p, goals, th):
    if p["avg_party"] < th["social_avg_party"] or p["visits"] < th["social_min_visits"]:
        return False, {}
    if p["overdue"] and p["overdue"] > 2.5:
        return False, {}   # they're lapsing - the winback trigger should get them
    return True, {"avg_party": p["avg_party"], "visits": p["visits"],
                  "usual_day": DAY_NAMES[p["primary_weekday"]]}


def t_big_table(p, goals, th):
    if p["max_party"] < th["big_max_party"] or p["visits"] < th["big_min_visits"]:
        return False, {}
    # If they're USUALLY a big table, social_hub already covers them with a
    # bring-a-friend offer. This trigger is for people who are normally a pair
    # but have shown they can bring a crowd - a functions lead, not a referral.
    if p["avg_party"] >= th["big_avg_party_max"]:
        return False, {}
    if p["overdue"] and p["overdue"] >= th["upsell_overdue_max"]:
        return False, {}
    return True, {"max_party": p["max_party"], "visits": p["visits"],
                  "avg_party": p["avg_party"]}


def t_never_adds_extras(p, goals, th):
    if p["visits"] < th["extras_min_visits"] or p["attach_rate"] > th["extras_max_attach"]:
        return False, {}
    if not p["signature_item"]:
        return False, {}
    # "A free side with your main" is nonsense to someone who only ever buys a
    # flat white. The upsell has to fit what they actually order.
    sig_cat = BY_NAME.get(p["signature_item"], (0, "", 0, 0, []))[1]
    if sig_cat not in ("Brunch", "Mains"):
        return False, {}
    # Only worth upselling someone still coming in ON SCHEDULE. If they've
    # drifted, win them back first - an attach offer to a lapsed customer is
    # wasted, and it reads as tone-deaf.
    if p["overdue"] and p["overdue"] >= th["upsell_overdue_max"]:
        return False, {}
    return True, {"attach_pct": round(p["attach_rate"] * 100), "visits": p["visits"],
                  "usual": p["signature_item"]}


def t_habit_breaker(p, goals, th):
    """Loyal but predictable - and the merchant has something they've never tried."""
    if (p["visits"] < th["habit_min_visits"] or p["variety"] > th["habit_max_variety"]
            or not p["signature_item"]):
        return False, {}
    if p["overdue"] and p["overdue"] >= th["upsell_overdue_max"]:
        return False, {}   # drifting customers get a win-back, not a new-product pitch
    if goals["priority"] not in ("high_margin", "bigger_basket", "repeat"):
        return False, {}
    tried = p["ever_bought"]
    for prod in goals["push_products"]:
        if prod in tried or prod not in BY_NAME:
            continue
        cat = BY_NAME[prod][1]
        # Catering and gift cards are group/occasion products. Pitching one to a
        # solo regular reads as nonsense - those belong to the group triggers.
        if cat in ("Catering", "Gift Card"):
            continue
        # don't pitch a cocktail to someone who only ever comes for breakfast
        if cat == "Bar" and p["alcohol_share"] < 0.15 and p["primary_daypart"] == "morning":
            continue
        if cat == "Brunch" and p["primary_daypart"] == "evening":
            continue
        return True, {"usual": p["signature_item"], "times": p["signature_visits"],
                      "new_item": prod, "variety": p["variety"]}
    return False, {}


def t_wrong_time(p, goals, th):
    """The merchant wants a slot filled; this person comes at a different one."""
    if p["visits"] < th["wrong_time_min_visits"] or not goals["push_times"]:
        return False, {}
    if goals["priority"] not in ("fill_quiet", "repeat", "bigger_basket", "high_margin"):
        return False, {}
    for slot in goals["push_times"]:
        dp, wd = slot["daypart"], slot.get("weekday")
        already = p["weekday_counts"].get(wd, 0) if wd is not None else 0
        dp_visits = p["daypart_counts"].get(dp, 0)
        # they must NOT already be a target-slot regular - that's the whole point
        if wd is not None and already >= max(2, p["visits"] * 0.25):
            continue
        if dp_visits >= p["visits"] * 0.5:
            continue
        # Plausibility filter. A 7am-only coffee person who has never bought a
        # drink is not coming in for a Tuesday dinner offer. Sending them one
        # wastes the slot and risks an unsubscribe - which is the expensive
        # failure, because you lose them for everything else too.
        if dp == "evening" and dp_visits == 0 and p["alcohol_share"] == 0:
            continue
        if dp == "morning" and dp_visits == 0 and p["primary_daypart"] == "evening":
            continue
        return True, {"target_label": slot.get("label") or
                      f"{DAY_NAMES[wd] if wd is not None else ''} {DAYPART_LABEL[dp]}".strip(),
                      "target_daypart": dp,
                      "target_day": DAY_NAMES[wd] if wd is not None else None,
                      "their_daypart": DAYPART_PHRASE[p["primary_daypart"]],
                      "their_day": DAY_NAMES[p["primary_weekday"]],
                      "visits": p["visits"]}
    return False, {}


def t_new_second_visit(p, goals, th):
    if p["first_days_ago"] > th["new_window_days"] or p["visits"] > th["new_max_visits"]:
        return False, {}
    return True, {"first_days_ago": p["first_days_ago"], "visits": p["visits"],
                  "ordered": (p["top_items"][0] if p["top_items"] else None),
                  "their_day": DAY_NAMES[p["primary_weekday"]]}


def t_drifting(p, goals, th):
    """Still coming in, but the cadence is decaying.

    Distinct from signature_overdue (current gap vs their own median) and from
    the win-back (which needs a real lapse). Fires while the habit is still
    intact enough to save, and costs a free side rather than a $15 credit.
    """
    if p["visits"] < th["drift_min_visits"] or p.get("gap_trend") is None:
        return False, {}
    if p["gap_trend"] < th["drift_trend_min"]:
        return False, {}
    if p["recency_days"] > th["drift_max_recency"]:
        return False, {}                       # lapsed - win-back's job
    if p["overdue"] and p["overdue"] >= th["upsell_overdue_max"]:
        return False, {}                       # visibly missing - signature_overdue's job
    return True, {"trend": p["gap_trend"], "early_gap": p["early_gap"],
                  "recent_gap": p["recent_gap"], "visits": p["visits"],
                  "since": p["recency_days"]}


TRIGGERS = [
    {"id": "signature_overdue", "name": "Their usual, overdue",
     "rule": "Orders the same thing repeatedly, and it's past their own normal rhythm. "
             "The message names the exact thing they buy.",
     "offer_cat": "upgrade", "serves": ["repeat"], "weight": 100, "fn": t_signature_overdue},
    {"id": "birthday", "name": "Birthday coming up",
     "rule": "Birthday inside the window (Square captures this at loyalty signup). "
             "Time-critical, so it outranks the priority setting.",
     "offer_cat": "group", "serves": ["high_margin", "new_customers"], "weight": 98,
     "always_top": True, "fn": t_birthday},
    {"id": "lapsed_big_spender", "name": "Big spender gone quiet",
     "rule": "Absent past the lapse threshold and above the spend percentile floor. "
             "The only trigger allowed a real credit.",
     "offer_cat": "money_off", "serves": ["repeat"], "weight": 96, "fn": t_lapsed_big_spender},
    {"id": "social_hub", "name": "The one who organises",
     "rule": "Average table at or above the organiser threshold, still coming in. "
             "Give them a reason to bring more people.",
     "offer_cat": "free_item", "serves": ["new_customers", "fill_quiet"], "weight": 90,
     "fn": t_social_hub},
    {"id": "big_table", "name": "Big table potential",
     "rule": "Has seated a big table before but usually comes as a pair. A functions "
             "lead, not a referral.",
     "offer_cat": "group", "serves": ["high_margin"], "weight": 88, "fn": t_big_table},
    {"id": "wrong_time", "name": "Come at a different time",
     "rule": "You want a specific slot filled; they already like you, just not then. "
             "Plausibility-filtered so nobody gets an invite they'd find absurd.",
     "offer_cat": "money_off", "serves": ["fill_quiet"], "weight": 86, "fn": t_wrong_time},
    {"id": "never_adds_extras", "name": "Never orders extras",
     "rule": "Enough visits, and adds a side or dessert under the attach threshold. "
             "Suggests your venue's actual best-selling side.",
     "offer_cat": "free_item", "serves": ["bigger_basket"], "weight": 84,
     "fn": t_never_adds_extras},
    {"id": "habit_breaker", "name": "Creature of habit",
     "rule": "Loyal, narrow range, and you have something on your push list they've "
             "never tried.",
     "offer_cat": "free_item", "serves": ["high_margin", "bigger_basket"], "weight": 82,
     "fn": t_habit_breaker},
    {"id": "new_second_visit", "name": "New - needs a second visit",
     "rule": "First visit inside the new-customer window, only been once or twice. "
             "The habit-forming window.",
     "offer_cat": "money_off", "serves": ["repeat"], "weight": 80, "fn": t_new_second_visit},
    {"id": "drifting", "name": "Slowing down",
     "rule": "Still coming in, but recent gaps between visits are longer than their own "
             "early pattern by the drift multiplier. Catches the slide before it "
             "becomes a lapse.",
     "offer_cat": "free_item", "serves": ["repeat"], "weight": 70, "fn": t_drifting},
]


OFFER_CATEGORIES = [
    ("upgrade",   "Upgrade or perk",   "Their usual, done slightly better. Costs cents."),
    ("free_item", "Something free",    "A side, a dessert, a round. The workhorse."),
    ("money_off", "Money off",         "BOGO or a credit. Reserve for real lapses."),
    ("group",     "Group & booking",   "Tables of six, set menus, functions."),
]


def _mod_phrase(p, item):
    """'(oat milk, extra shot)' - the dominant way THIS person orders THIS
    item, or '' when we don't know it confidently. Customer-facing copy only
    ever states the choice itself; the counts behind it stay owner-side."""
    mods = (p.get("mod_habits") or {}).get(item) or []
    if not mods:
        return ""
    return " (" + ", ".join(m.lower() for m, _n, _s in mods) + ")"


def _ev_mods(p, item):
    mods = (p.get("mod_habits") or {}).get(item) or []
    if not mods:
        return None
    return "; ".join(f"{m} on {round(sh * 100)}% of their orders of it"
                     for m, _n, sh in mods)


def copy(trigger_id, ev, c, p, goals, stats=None, now=None, offer=None, offer_note=""):
    """Subject, body and owner-side `why` for one matched trigger.

    Customer-facing copy names their usual and uses vague timing only; every
    number stays in `why`, which is for the merchant's eyes and never leaves
    the app. The engine adds tone, regional spelling and the legal footer.
    """
    stats = stats or {}
    first = c["first_name"]
    oval = offer["label"]
    oval_cap = oval[0].upper() + oval[1:] if oval else oval
    v = VENUE

    if trigger_id == "signature_overdue":
        item = ev["item"].lower()
        mods = _mod_phrase(p, ev["item"])
        gap = int(round(ev["usual_gap"] or 7))
        subj = f"The {item} misses you, {first}"
        body = (f"Hi {first},\n\n"
                f"It's been {_vague(ev['since'])} since your last {item}{mods}, and we've "
                f"missed handing it over.\n\n"
                f"Whenever you're next in, {oval} - no code, no app, just mention it when "
                f"you order.\n\n"
                f"- {v['name']}")
        why = (f"Ordered a <b>{ev['item']}</b> on <b>{ev['times']}</b> separate visits. Their own "
               f"median gap is <b>{gap} days</b>; it has been <b>{ev['since']}</b> "
               f"({ev['multiple']}x their normal). Highest-response message in the system, "
               f"because it names the exact thing they buy. The email itself says only "
               f"&ldquo;{_vague(ev['since'])}&rdquo; - the numbers are yours, not theirs."
               + (f" They order it <b>{ev['mods']}</b>, and the email names the choice, "
                  f"never the count." if ev.get("mods") else "")
               + offer_note)

    elif trigger_id == "birthday":
        subj = f"Your birthday's coming up, {first}"
        body = (f"Hi {first},\n\n"
                f"Your birthday's {_vague_future(ev['in_days'])} - we only know because you "
                f"told us, and we only mention it because birthdays are our favourite "
                f"service of the week.\n\n"
                f"If you're the one who ends up organising it: tables of six or more get "
                f"{oval}, and we'll hold the good table by the window. Reply and we'll sort "
                f"the rest - set menu, deposits, the lot.\n\n"
                f"- {v['name']}")
        why = (f"Birthday on file is <b>{ev['in_days']} days</b> away, inside your "
               f"<b>{goals.get('_th', {}).get('birthday_window_days', 14)}-day</b> window. "
               f"They've visited <b>{ev['visits']}</b> times, so they know the venue. Group "
               f"bookings carry the best margin on the menu and are decided by exactly this "
               f"kind of person.")

    elif trigger_id == "lapsed_big_spender":
        when = (_season_phrase(ev["last_visit"], now, goals.get("_region", "AU"))
                if (now and ev["last_visit"]) else "a while back")
        subj = f"We miss you, {first}"
        body = (f"Hi {first},\n\n"
                f"You used to be one of our proper regulars, and then life happened - we "
                f"last saw you {when}.\n\n"
                f"Whatever happened, we'd like you back. {oval_cap} if you come in before the "
                f"end of the month, and your usual table is exactly where you left it.\n\n"
                f"- {v['name']}")
        why = (f"<b>{ev['since']} days</b> since their last visit, after <b>{ev['visits']}</b> "
               f"visits totalling <b>${ev['spend']:,.0f}</b> - top <b>{100 - ev['pct']}%</b> by "
               f"lifetime spend. Reactivating an existing regular costs far less than "
               f"acquiring a replacement. The email says &ldquo;{when}&rdquo;, not the day count.")

    elif trigger_id == "social_hub":
        subj = f"You're the one who rounds everyone up, {first}"
        body = (f"Hi {first},\n\n"
                f"Every good table has someone who rounds everyone up, and around here it's "
                f"usually you.\n\n"
                f"So here's an excuse to do it again: bring one more than last time and "
                f"{oval}. Book a table and we'll hold it.\n\n"
                f"- {v['name']}")
        why = (f"Average table <b>{ev['avg_party']}</b> across <b>{ev['visits']}</b> visits, most "
               f"often on a <b>{ev['usual_day']}</b>. This person is the organiser of a group, "
               f"which makes them your cheapest acquisition channel - one message reaches "
               f"<b>{int(round(ev['avg_party'])) + 1}</b> people.")

    elif trigger_id == "big_table":
        subj = f"Room for a few more next time, {first}?"
        body = (f"Hi {first},\n\n"
                f"You've sat one of our big tables before, and honestly they're our favourite "
                f"kind of night.\n\n"
                f"If you're planning something - a birthday, a work thing, twenty people who "
                f"can't agree on anything - we do set menus and private bookings, and tables "
                f"of six or more get {oval}. Reply and we'll send the menus.\n\n"
                f"- {v['name']}")
        why = (f"Has sat a table of <b>{ev['max_party']}</b> before (usual table "
               f"<b>{ev['avg_party']}</b>), across <b>{ev['visits']}</b> visits. Group set menus "
               f"and catering are the highest-margin lines on the menu and they are booked, "
               f"not impulse - so the blocker is awareness.")

    elif trigger_id == "never_adds_extras":
        side = str(stats.get("top_side", "a side")).lower()
        subj = f"Let us shout the {side} next time, {first}"
        body = (f"Hi {first},\n\n"
                f"If you've never had our {side}: it's the thing the kitchen makes first and "
                f"runs out of first, which tells you everything.\n\n"
                f"Next visit, {oval} - consider it an introduction.\n\n"
                f"- {v['name']}")
        why = (f"<b>{ev['visits']}</b> visits with a side or dessert on only "
               f"<b>{ev['attach_pct']}%</b> of them (your threshold is "
               f"<b>{round(goals.get('_th', {}).get('extras_max_attach', 0.2) * 100)}%</b>). "
               f"The suggested item is your venue's actual best-selling side. Attach revenue "
               f"is the cheapest on the board: no extra labour, no extra covers.")

    elif trigger_id == "habit_breaker":
        new = ev["new_item"].lower()
        subj = f"If you've never tried the {new}, {first}, this one's on us"
        body = (f"Hi {first},\n\n"
                f"You know exactly what you like here, and we respect that.\n\n"
                f"But if the {new} hasn't crossed your table yet, it's what the kitchen is "
                f"proudest of this month. {oval_cap} with your next visit.\n\n"
                f"- {v['name']}")
        why = (f"<b>{ev['times']}</b> orders of the <b>{ev['usual']}</b> across "
               f"<b>{p['visits']}</b> visits, variety score <b>{ev['variety']}</b> (low = narrow "
               f"range). They've never bought the <b>{ev['new_item']}</b>, which is on your push "
               f"list. Loyal customers are the only ones who'll try something new on your "
               f"say-so - and the email frames it as an invitation, not an observation.")

    elif trigger_id == "wrong_time":
        tl = ev["target_label"]
        tday = f"on a {ev['target_day']}" if ev.get("target_day") else f"for {ev['target_daypart']}"
        subj = f"An invitation to {tl.lower()}, {first}"
        body = (f"Hi {first},\n\n"
                f"We're trying to make {tl} as good as the rest of our week, and we'd love "
                f"your vote of confidence.\n\n"
                f"Come in {tday} this month and it's {oval} on any main. It's quieter, the "
                f"kitchen has time for you, and it might become your thing.\n\n"
                f"- {v['name']}")
        why = (f"You asked to fill <b>{tl}</b>. They visit <b>{ev['visits']}</b> times but almost "
               f"never then - they come for <b>{ev['their_daypart']}</b> on a "
               f"<b>{ev['their_day']}</b>. You're not discounting to strangers, you're moving "
               f"someone who already likes you into a slot that's already paid for. Note the "
               f"email never tells them you know when they come - it's framed as an invite.")

    elif trigger_id == "new_second_visit":
        subj = f"Glad you found us, {first}"
        body = (f"Hi {first},\n\n"
                f"Thanks for coming in recently - genuinely, new faces are the whole reason "
                f"we do this.\n\n"
                f"The second visit is the one that counts, so we'd like to make it easy: "
                f"{oval} when you come back this month.\n\n"
                f"- {v['name']}")
        why = (f"First visit <b>{ev['first_days_ago']} days</b> ago, <b>{ev['visits']}</b> visit(s) "
               f"total. Inside the habit-forming window, where one nudge is worth more than "
               f"any later win-back.")

    elif trigger_id == "drifting":
        early, late = int(round(ev["early_gap"])), int(round(ev["recent_gap"]))
        usual = (f"the {p['signature_item'].lower()}{_mod_phrase(p, p['signature_item'])}"
                 if p.get("signature_item") else "your usual")
        subj = f"We've missed seeing you around, {first}"
        body = (f"Hi {first},\n\n"
                f"We've missed seeing you as often lately, and the room is quieter for it.\n\n"
                f"No pressure and no hard feelings - life gets busy. But there's {oval} waiting "
                f"next time you're in, and {usual} is still on the menu.\n\n"
                f"- {v['name']}")
        why = (f"Their gap between visits was <b>{early} days</b> early on and is now "
               f"<b>{late} days</b> ({ev['trend']}x). Still a customer, so this is the cheap "
               f"moment to act: a small perk now costs less than a win-back credit in three "
               f"months. The email says only that they've been missed - never the trend.")

    else:
        subj, body, why = f"Hi {first}", "", ""

    return subj, body, why


def explain_no_match(p, goals):
    """Why did this person get nothing? Shown in the UI so it doesn't look like a bug."""
    if p.get("empty") or p["visits"] == 0:
        return "No order history to match against."
    reasons = []
    if not p["signature_item"]:
        reasons.append("no repeated order (nothing to call 'their usual')")
    if not p["overdue"] or p["overdue"] < 1.5:
        reasons.append(f"visiting on schedule (last in {p['recency_days']}d, usual gap {p['own_gap']}d)")
    if p["avg_party"] < 3:
        reasons.append(f"solo or pairs only (avg table {p['avg_party']})")
    if p["attach_rate"] > 0.20:
        reasons.append("already orders sides and desserts")
    if p["variety"] > 0.45:
        reasons.append("already orders from a wide range")
    if not p["is_new"] and p["recency_days"] < 110:
        reasons.append("not lapsed")
    return "No trigger fired: " + "; ".join(reasons[:3]) + "."
