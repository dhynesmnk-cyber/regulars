"""
Billing v0: has this venue paid, and where does it pay?

"Free preview, pay to send": an owner can sign in, upload and read every
message without paying; approving or exporting real customers' messages
needs a paid-up period. Payment happens on your Stripe Payment Link; you
confirm it with `regularsctl paid <venue>`, which records a paid-until date
in a small file beside the state file. Only the operator writes that file -
the app just reads it, again whenever it changes - so a payment unlocks
sending without a restart, and the running app never overwrites it.

Billing is opt-in (REGULARS_BILLING=on, set by regularsctl provision), so
local runs, demos and tests behave as before. No state import here:
admin.py and regularsctl use this module too.
"""

import calendar
import json
import os
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import store

WARN_DAYS = 7      # banner from this many days before the paid-up period ends
GRACE_DAYS = 7     # sending keeps working this many days after it ends
LOCKED = ("preview", "lapsed")
PRICE = {"AU": "A$40 a month + GST", "US": "US$40 a month"}

_cache = {"key": None, "data": {}}


def enforced():
    return (os.environ.get("REGULARS_BILLING") or "").strip().lower() in ("on", "1", "yes", "true")


def path():
    return (os.environ.get("REGULARS_BILLING_FILE")
            or os.path.join(os.path.dirname(os.path.abspath(store.PATH)), "regulars-billing.json"))


def today():
    return datetime.now(timezone.utc).date()


def read():
    """The billing file, re-read only when it has changed (each write
    replaces it, so the inode moves even within one clock tick)."""
    p = path()
    try:
        st = os.stat(p)
    except OSError:
        return {}
    key = (p, st.st_ino, st.st_mtime_ns, st.st_size)
    if _cache["key"] != key:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        _cache.update(key=key, data=data if isinstance(data, dict) else {})
    return dict(_cache["data"])


def paid_until():
    raw = read().get("paid_until")
    try:
        return date.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None


def write(paid, note=""):
    """Operator only (admin.py paid-until). Atomic, so the app never reads half a file."""
    p = path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"paid_until": paid.isoformat(),
                   "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "note": note}, f)
    os.replace(tmp, p)


def status(paid, on_day, on=True):
    """off | preview | active | due_soon | grace | lapsed. `paid` is the last
    paid-up day, inclusive."""
    if not on:
        return "off"
    if paid is None:
        return "preview"
    left = (paid - on_day).days
    if left > WARN_DAYS:
        return "active"
    if left >= 0:
        return "due_soon"
    if -left <= GRACE_DAYS:
        return "grace"
    return "lapsed"


def add_months(d, n):
    """Same day n months on, clamped to the month's end (31 Jan + 1 = 28/29 Feb)."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def pay_url(region, venue_id=None):
    """The market's Stripe Payment Link, tagged with the venue so the payment
    shows up in Stripe against it (client_reference_id)."""
    base = (os.environ.get("REGULARS_PAY_URL_US" if region == "US" else "REGULARS_PAY_URL_AU")
            or "").strip()
    if not base:
        return None
    if not venue_id:
        return base
    parts = urlsplit(base)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "client_reference_id"]
    query.append(("client_reference_id", venue_id))
    return urlunsplit(parts._replace(query=urlencode(query)))


def nice(d):
    """'5 December 2026' - no leading zero, no locale surprises."""
    return f"{d.day} {d.strftime('%B %Y')}"
