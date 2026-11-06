"""
Process-wide state for one Regulars instance: the venue's saved settings
(STATE), the demo dataset, the audit log, the request guard and the secret
box, plus the helpers every screen shares (sessions, persistence, the engine
run). One instance serves one venue.

LOCK serialises every read-modify-write of STATE. The HTTP server is
threaded, so two requests can arrive at once; the handler holds LOCK while a
route runs (long imports and network checks take it only around their STATE
changes).
"""

import copy
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone

import billing as BILL
import core
import engine as E
import security as SEC
import store
import ui


VERSION = "0.6.0"
SCHEMA = 3
BUILD_DATE = "2026-11-05"
HERE = os.path.dirname(os.path.abspath(__file__))

DS = E.PACK.demo_dataset()

# ---------------------------------------------------------------- security
# Single-instance threat model: one venue, one operator, one server process.
# Everything below fails safe - if a check can't run, access is refused.

AUDIT = SEC.AuditLog(os.environ.get("REGULARS_AUDIT_LOG")
                     or os.path.join(HERE, "regulars-audit.log"))
GUARD = SEC.Guard()
FLASH = []
CTX = threading.local()
LOCK = threading.RLock()
_BOX = None


def _guard_flag(ip, what):
    AUDIT.write("rate_limit", ip=ip, detail={"flagged": what})


_orig_flag = GUARD.flag


def _flag_and_audit(ip, what, now=None):
    _orig_flag(ip, what, now)
    AUDIT.write("rate_limit", ip=ip, detail={"flag": what})


GUARD.flag = _flag_and_audit


def flash(msg):
    FLASH.append(msg)


def take_flash():
    items, FLASH[:] = list(FLASH), []
    return items


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def box():
    """Lazy so a fresh install never creates a keyfile until a secret exists."""
    global _BOX
    if _BOX is None:
        _BOX = SEC.SecretBox(master=os.environ.get("REGULARS_MASTER_KEY"),
                             keyfile=os.environ.get("REGULARS_KEYFILE"))
    return _BOX


def _store_tokens(tokens):
    STATE["oauth_tokens_enc"] = box().encrypt(json.dumps(tokens)) if tokens else None
    persist()


def _load_tokens():
    enc = STATE.get("oauth_tokens_enc")
    if not enc:
        return None
    try:
        return json.loads(box().decrypt(enc))
    except Exception:
        AUDIT.write("secret_read", detail={"ok": False, "what": "oauth_tokens",
                                           "note": "decrypt/verify failed - treated as absent"})
        return None


# ---------------------------------------------------------------- setup link
# Provisioning puts a one-time token in REGULARS_SETUP_TOKEN and sends the
# owner https://<their host>/welcome?token=<it>. Until they use it, the
# instance is "unclaimed": every screen is shut except that link and the
# health check, so nobody who stumbles on the address gets an open app.

SETUP_TOKEN_MIN = 24


def setup_token():
    return (os.environ.get("REGULARS_SETUP_TOKEN") or "").strip() or None


def _token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def unclaimed():
    return setup_token() is not None and demo_mode()


def setup_token_matches(candidate):
    """Constant-time check of a presented token; burned tokens never match."""
    tok = setup_token()
    if not tok or not candidate:
        return False
    if _token_digest(tok) in (STATE.get("setup_burned") or []):
        return False
    return hmac.compare_digest(tok.encode("utf-8"), str(candidate).encode("utf-8"))


def burn_setup_token():
    STATE.setdefault("setup_burned", []).append(_token_digest(setup_token()))


def demo_mode():
    """No operator password configured yet - the prototype's open mode."""
    return not STATE.get("password_hash")


def demo_instance():
    """A hosted sales demo (regularsctl provision --demo): set up and closed
    behind a password like a venue, but showing the example restaurant and
    its made-up customers, so it keeps the demo screens."""
    return (os.environ.get("REGULARS_DEMO") or "").strip().lower() in ("1", "on", "yes", "true")


def venue_instance():
    """Provisioned for a real venue: regularsctl always gives one a setup
    token. Local runs and the demo have none, and keep the demo screens; so
    does a hosted demo, which has a token but is not a venue."""
    return setup_token() is not None and not demo_instance()


def on_example():
    """No upload in memory, so every screen is showing the example data."""
    return STATE.get("dataset") is None


def mark(milestone):
    """Record the first time a venue reached a milestone. Caller holds LOCK."""
    if not STATE["milestones"].get(milestone):
        STATE["milestones"][milestone] = now_utc()
        persist()


def session_from_token(token):
    if not token:
        return None
    return (STATE.get("sessions") or {}).get(token)


def create_session(ip):
    sess = SEC.new_session()
    sess["ip"] = ip
    STATE.setdefault("sessions", {})[sess["token"]] = sess
    if len(STATE["sessions"]) > 50:
        for t in sorted(STATE["sessions"],
                        key=lambda t: STATE["sessions"][t].get("created", 0))[:-50]:
            STATE["sessions"].pop(t, None)
    persist()
    return sess["token"]


def kill_sessions():
    STATE["sessions"] = {}
    persist()


def cookie_for(token):
    sec = "; Secure" if os.environ.get("REGULARS_TLS") else ""
    return (f"REGULARS_SID={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={SEC.SESSION_TTL_SECONDS}{sec}")


def expire_cookie():
    return "REGULARS_SID=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def local_request_allowed(ip):
    """First password may only be set from the box itself (or with the env
    override, for headless deploys where the operator accepts the risk)."""
    return (ip in ("127.0.0.1", "::1")
            or os.environ.get("REGULARS_OPEN_FIRST_RUN") == "1")

CAT_LABEL = {k: v for k, v, _ in E.OFFER_CATEGORIES}
CAT_HELP = {k: h for k, _, h in E.OFFER_CATEGORIES}

# Where the messages will actually go out from. The unsubscribe merge tag is
# different in every tool; writing the wrong one means a broken unsubscribe,
# which is a Spam Act problem, not a cosmetic one.
ESP = {
    "square":     ("Square Marketing", None, "https://squareup.com/au/en/marketing",
                   "Already in your Square Dashboard under Marketing. Square appends its "
                   "own unsubscribe footer to campaigns, so we leave a placeholder in "
                   "previews and strip it from exports."),
    "mailchimp":  ("Mailchimp", "*|UNSUB|*", "https://mailchimp.com",
                   "Free to 500 contacts. The most familiar choice; paste our CSV straight "
                   "into an audience."),
    "klaviyo":    ("Klaviyo", "{% unsubscribe %}", "https://www.klaviyo.com",
                   "Free to 250 contacts. Strongest flows and hospitality templates; "
                   "steeper learning curve."),
    "brevo":      ("Brevo", "{{{unsubscribe}}}", "https://www.brevo.com",
                   "300 emails a day free, then cheap per-email. Good once you're sending "
                   "weekly to a few thousand."),
    "mailerlite": ("MailerLite", "{$unsubscribe}", "https://www.mailerlite.com",
                   "Free to 1,000 subscribers. The simplest editor of the four."),
    "smtp":       ("Your own SMTP server", "{{unsubscribe_url}}", None,
                   "If the venue already sends mail through their own host or another "
                   "provider, we store the settings here so sending can be switched on "
                   "later. The prototype never transmits."),
    "none":       ("Nothing yet", "{{unsubscribe_url}}", None,
                   "No sender configured. The links above are the usual starting points; "
                   "all four have a free tier big enough for one venue's list."),
}


def _starting_goals():
    """The demo's goals, minus its own choices on a venue's instance: the
    example restaurant's quiet nights and products mean nothing there (a cafe
    could end up inviting its regulars to a Tuesday dinner it doesn't serve).
    The offer catalogue stays, as suggestions the owner confirms on Goals."""
    g = copy.deepcopy(E.PACK.DEFAULT_GOALS)
    if venue_instance():
        g["push_times"], g["push_products"] = [], []
    return g


def drop_example_goals(st):
    """A venue claimed before venues started without the example's goals,
    which has never saved its own: clear the example restaurant's choices.
    Returns True if it changed anything."""
    g = st["goals"]
    if (venue_instance() and not (st.get("milestones") or {}).get("goals_saved")
            and g.get("push_times") == E.PACK.DEFAULT_GOALS["push_times"]
            and g.get("push_products") == E.PACK.DEFAULT_GOALS["push_products"]):
        g["push_times"], g["push_products"] = [], []
        return True
    return False


def fresh_state():
    return {
        "connected": False,
        "source": "demo",
        "goals": _starting_goals(),
        "enabled": {t["id"] for t in E.TRIGGERS},
        "thresholds": {},
        "sender": {"tool": "none", "smtp_host": "", "smtp_port": 587, "smtp_user": "",
                   "smtp_pass": "", "from_addr": "", "reply_to": ""},
        "sent": [],
        "sent_history": {},
        "approved": set(),
        "dataset": None,
        "import_report": None,
        "region": "AU",
        "venue_address": "",
        # who the messages come from: signs every email and fills the legal footer
        "business": {"name": "", "suburb": "", "abn": ""},
        "password_hash": None,
        # sha256 of setup tokens already used, so a link never works twice
        "setup_burned": [],
        # the last paid-until date the audit log recorded (billing.py)
        "billing_seen": None,
        # when this venue first saved its goals and offers, uploaded and exported
        "milestones": {"goals_saved": None, "first_upload": None, "first_export": None},
        # a message has been opened since the last upload (memory only, like it)
        "read_since_upload": False,
        "sessions": {},
        "oauth_tokens_enc": None,
        "oauth_pending": {},
        "schema": SCHEMA,
    }


STATE = fresh_state()
_saved = store.load()
for k, v in _saved.items():
    if k in STATE:
        STATE[k] = v
STATE["approved"] = set(STATE.get("approved") or [])
STATE["business"] = {**fresh_state()["business"], **(STATE.get("business") or {})}
STATE["milestones"] = {**fresh_state()["milestones"], **(STATE.get("milestones") or {})}
drop_example_goals(STATE)
STATE["dataset"] = None          # imports are deliberately not persisted
STATE["import_report"] = None
STATE["source"] = "demo"         # every start begins on the demo; an import is
                                 # only real while this process holds it

_MIGRATED_FROM = int(_saved.get("schema", 1)) if _saved else None
STATE["schema"] = SCHEMA
for _t in list(STATE.get("sessions") or {}):
    if not SEC.session_valid(STATE["sessions"][_t]):
        STATE["sessions"].pop(_t, None)
STATE.setdefault("oauth_pending", {})
_now_ts = datetime.now(timezone.utc).timestamp()
for _st in list(STATE["oauth_pending"]):
    try:
        if _now_ts - float(STATE["oauth_pending"][_st]) > 900:
            STATE["oauth_pending"].pop(_st, None)
    except (TypeError, ValueError):
        STATE["oauth_pending"].pop(_st, None)
if _MIGRATED_FROM is not None and _MIGRATED_FROM < SCHEMA:
    AUDIT.write("config_change",
                detail={"what": "schema_migration", "from": _MIGRATED_FROM,
                        "to": SCHEMA, "version": VERSION})


def persist():
    store.save(STATE)


def dataset():
    return STATE["dataset"] or DS


def now():
    return E._dt(dataset()["now"])


# ---------------------------------------------------------------- identity
# Every message is signed with the business's name, and the legal footer
# carries its ABN (AU) or postal address (US). The demo data is signed by the
# demo venue; real data is never signed by anyone but the owner.

PLACEHOLDER_IDENTITY = {"name": "[Your business name]", "suburb": "[Suburb]", "abn": "[ABN]"}
OFFERS_UNCHECKED_MSG = ("Check your goals and offers on the Goals screen first, and save it. "
                        "Every message promises an offer, and the starting list is only a "
                        "suggestion (a free bottle of house wine, $15 off), so approving and "
                        "exporting wait until you've confirmed what you'll give.")
IDENTITY_MISSING_MSG = ("Add your business name, suburb and ABN under Your business on the "
                        "Setup screen first. Until then your messages are signed with "
                        "placeholders, so approving and exporting are switched off.")


def business_complete():
    b = STATE.get("business") or {}
    if not (b.get("name") and b.get("suburb")):
        return False
    if STATE.get("region") == "US":
        return bool(STATE.get("venue_address"))
    return bool(b.get("abn"))


def identity():
    """Who the messages come from: the owner's saved details; the demo venue
    while exploring the demo; placeholders on real data until details exist."""
    if business_complete():
        b = STATE["business"]
        return {"name": b["name"], "suburb": b["suburb"], "abn": b.get("abn", "")}
    if STATE.get("dataset") is None:
        return dict(E.PACK.VENUE)
    return dict(PLACEHOLDER_IDENTITY)


def header_label():
    """'Marlow &amp; Finch, Fitzroy' - the business and its suburb, for the page header.
    A venue's own instance never shows the example restaurant's name."""
    if venue_instance() and not business_complete():
        return ""
    v = identity()
    suburb = (v.get("suburb") or "").split(",")[0].strip()
    return ui.e(f"{v['name']}, {suburb}" if suburb else v["name"])


ui.venue_label = header_label
ui.lang = lambda: core.lang(STATE.get("region", "AU"))


def identity_missing():
    """Real customer data loaded, but no complete business details to sign with."""
    return STATE.get("dataset") is not None and not business_complete()


# ---------------------------------------------------------------- billing
# Free preview, pay to send (billing.py). The paid-until date comes from a
# file only the operator writes; the app records each new date in the audit
# log itself, so the log keeps a single writer.

def billing_state():
    on = BILL.enforced()
    paid = BILL.paid_until() if on else None
    if on:
        seen = paid.isoformat() if paid else None
        if seen != STATE.get("billing_seen"):
            AUDIT.write("config_change", actor="operator",
                        detail={"what": "billing", "paid_until": seen})
            STATE["billing_seen"] = seen
            persist()
    region = STATE.get("region", "AU")
    return {"status": BILL.status(paid, BILL.today(), on), "paid_until": paid,
            "pay_url": BILL.pay_url(region, os.environ.get("REGULARS_VENUE_ID")),
            "price": BILL.PRICE.get(region, BILL.PRICE["AU"]),
            "support": (os.environ.get("REGULARS_SUPPORT_EMAIL") or "").strip()}


def billing_message(b):
    if b["status"] == "preview":
        return ("Your messages are ready to read. Approving and exporting them starts once "
                "your plan is paid - see Your plan on the Setup screen.")
    if b["status"] == "lapsed":
        return (f"Your plan ended on {BILL.nice(b['paid_until'])}, so approving and exporting "
                f"are paused. Renew under Your plan on the Setup screen; everything else "
                f"keeps working.")
    return None


def offers_unchecked():
    """A venue's real data, but the owner hasn't yet confirmed the offers
    every message will promise at their till."""
    return (venue_instance() and STATE.get("dataset") is not None
            and not STATE["milestones"].get("goals_saved"))


def send_block():
    """Why approving or exporting is refused right now, or None. Only real
    customer data is ever blocked; the demo can always be rehearsed."""
    if identity_missing():
        return IDENTITY_MISSING_MSG
    if STATE.get("dataset") is None:
        return None
    if offers_unchecked():
        return OFFERS_UNCHECKED_MSG
    b = billing_state()
    return billing_message(b) if b["status"] in BILL.LOCKED else None


def result():
    g = dict(STATE["goals"])
    g["_region"] = STATE.get("region", "AU")
    g["_address"] = STATE.get("venue_address", "")
    g["_venue"] = identity()
    return E.run(dataset(), g, g, STATE["sent_history"],
                 now(), STATE["enabled"], STATE["thresholds"])


def th():
    return {**E.DEFAULT_THRESHOLDS, **STATE["thresholds"]}

