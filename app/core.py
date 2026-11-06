"""
Industry-neutral wording helpers shared by the engine and every industry pack:
dates, coarse human timing, hemisphere-aware seasons and regional spelling.

These exist so customer-facing copy never states a number about the
customer: "a few weeks", "back in winter" - never "23 days".
"""

from datetime import datetime

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _dt(iso):
    return datetime.fromisoformat(iso)


def _median(xs):
    if not xs:
        return None
    xs = sorted(xs)
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def _vague(days):
    """Coarse, human recency. The customer never sees a number, a ratio or a
    count - that is the difference between a regular who feels known and a
    regular who feels watched."""
    if days < 21:
        return "a couple of weeks"
    if days < 45:
        return "a few weeks"
    if days < 80:
        return "a month or two"
    if days < 150:
        return "a season"
    return "far too long"


def _vague_future(days):
    if days <= 3:
        return "in the next few days"
    if days <= 10:
        return "in the next week or so"
    if days <= 21:
        return "in the next couple of weeks"
    return "in the next few weeks"


_SEASONS_SOUTH = {12: "summer", 1: "summer", 2: "summer", 3: "autumn", 4: "autumn",
                  5: "autumn", 6: "winter", 7: "winter", 8: "winter", 9: "spring",
                  10: "spring", 11: "spring"}
_SEASONS_NORTH = {m: s for m, s in _SEASONS_SOUTH.items()}
_SEASONS_NORTH.update({12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring",
                       5: "spring", 6: "summer", 7: "summer", 8: "summer", 9: "autumn",
                       10: "autumn", 11: "autumn"})

# Customer-copy spelling per region. Only words that actually appear in
# generated copy are mapped; this is not a general translator.
_US_SPELLING = [("organising", "organizing"), ("organiser", "organizer"),
                ("organised", "organized"), ("favourite", "favorite"),
                ("realise", "realize"), ("apologise", "apologize")]



# What the app shows the owner for each market: the currency symbol in front of
# its own figures, and the page language. Unknown regions read as Australian,
# like every STATE.get("region", "AU") in the app. Customer emails are not
# touched: an offer such as "$15 off" is the owner's own wording.
CURRENCY = {"AU": "A$", "US": "US$"}
LANG = {"AU": "en-AU", "US": "en-US"}


def money(v, dp=0, region="AU"):
    return f"{CURRENCY.get(region, CURRENCY['AU'])}{v:,.{dp}f}"


def lang(region="AU"):
    return LANG.get(region, LANG["AU"])


def _localise(text, region):
    if region != "US":
        return text
    for a, b in _US_SPELLING:
        text = text.replace(a, b).replace(a.capitalize(), b.capitalize())
    return text


def _season_phrase(iso_date, now, region="AU"):
    table = _SEASONS_NORTH if region == "US" else _SEASONS_SOUTH
    d = _dt(iso_date + "T00:00:00")
    season = table[d.month]
    if table[now.month] == season:
        return "earlier this season"
    return f"back in {season}"
