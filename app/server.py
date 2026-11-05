"""
Regulars - a Square wrapper that turns customer behaviour into ready-to-send
messages, one per person, derived from their own orders.

Run:  python3 server.py 8010

Screens: Setup (connect or CSV upload), Goals, Triggers (+ tuning), Messages,
Sending, Sent, Data. Python standard library only.
"""

import copy
import csv
import io
import json
import os
import re
import secrets
import smtplib
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import data as D
import engine as E
import importer as IMP
import oauth as OAUTH
import security as SEC
import store
from ui import e, page, render, stat

VERSION = "0.6.0"
SCHEMA = 2
BUILD_DATE = "2026-11-05"
HERE = os.path.dirname(os.path.abspath(__file__))

DS = D.build()

# ---------------------------------------------------------------- security
# Single-instance threat model: one venue, one operator, one server process.
# Everything below fails safe - if a check can't run, access is refused.

AUDIT = SEC.AuditLog(os.environ.get("REGULARS_AUDIT_LOG")
                     or os.path.join(HERE, "regulars-audit.log"))
GUARD = SEC.Guard()
FLASH = []
CTX = threading.local()
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


def demo_mode():
    """No operator password configured yet - the prototype's open mode."""
    return not STATE.get("password_hash")


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


def fresh_state():
    return {
        "connected": False,
        "source": "demo",
        "goals": copy.deepcopy(D.DEFAULT_GOALS),
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
        "password_hash": None,
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


def result():
    g = dict(STATE["goals"])
    g["_region"] = STATE.get("region", "AU")
    g["_address"] = STATE.get("venue_address", "")
    return E.run(dataset(), g, g, STATE["sent_history"],
                 now(), STATE["enabled"], STATE["thresholds"])


def th():
    return {**E.DEFAULT_THRESHOLDS, **STATE["thresholds"]}


def money(v, dp=0):
    return f"${v:,.{dp}f}"


EV_LABEL = {
    "item": "Their usual", "times": "Times ordered", "usual_gap": "Usual gap (days)",
    "since": "Days since", "multiple": "x their normal", "spend": "Lifetime spend",
    "visits": "Visits", "last_visit": "Last visit", "pct": "Spend percentile",
    "in_days": "Birthday in (days)", "avg_party": "Avg table size",
    "usual_day": "Usual day", "max_party": "Biggest table", "attach_pct": "Adds a side %",
    "usual": "Their usual", "new_item": "Never tried", "variety": "Variety score",
    "mods": "How they order it",
    "target_label": "You want to fill", "their_daypart": "They come for",
    "their_day": "Their usual day", "first_days_ago": "Days since first visit",
    "ordered": "They ordered", "target_daypart": "Target daypart", "target_day": "Target day",
    "trend": "Gap trend x", "early_gap": "Early gap (days)", "recent_gap": "Recent gap (days)",
}


def evidence_html(ev):
    out = ['<div class="ev">']
    for k, v in ev.items():
        if v is None:
            continue
        label = EV_LABEL.get(k, k.replace("_", " ").title())
        if k == "spend":
            v = money(v)
        elif k == "multiple":
            v = f"{v}x"
        elif k == "attach_pct":
            v = f"{v}%"
        elif k == "pct":
            v = f"top {100 - v}%"
        out.append(f'<div><div class="l">{e(label)}</div><div class="v">{e(v)}</div></div>')
    out.append("</div>")
    return "".join(out)


def _suppression_card(r):
    s = r["summary"]
    labels = {
        "no_contact": ("No email address", "Square never exposes cardholder phone numbers or "
                       "emails from a card payment. These people only become reachable if staff "
                       "capture their email at the register."),
        "no_consent": ("Email but no consent record", "A card payment is not marketing consent "
                       "under the Spam Act. Quarantined until re-permissioned."),
        "unsubscribed": ("Previously unsubscribed", "Permanent. No trigger can override this."),
        "just_visited": ("Been in inside the quiet window", "They don't need an incentive. "
                         "Sending one trains people to wait for a coupon."),
        "too_soon": ("Sent something recently",
                     f'Frequency cap is {STATE["goals"]["min_days_between_sends"]} days.'),
        "no_history": ("No order history", "Nothing to match an offer against."),
    }
    rows = ""
    for code, n in sorted(s["suppression_breakdown"].items(), key=lambda x: -x[1]):
        lab, det = labels.get(code, (code, ""))
        rows += (f'<tr><td><b>{e(lab)}</b><br><span class="sm soft">{e(det)}</span></td>'
                 f'<td class="r"><b>{n}</b></td></tr>')
    rows += (f'<tr><td><b>No trigger matched</b><br><span class="sm soft">Contactable and '
             f'consented, but nothing about their behaviour warrants a message right now. '
             f'That is the correct answer, not a failure.</span></td>'
             f'<td class="r"><b>{s["no_match"]}</b></td></tr>')
    return (f'<table>{rows}</table>'
            f'<div class="note">The {s["suppression_breakdown"].get("no_contact", 0)} people with '
            f'no email address are your biggest single opportunity and they cost nothing to fix: '
            f'turn on the email prompt in your Square POS settings and get staff asking. '
            f'<a href="/data">More on this &rarr;</a></div>')


# ---------------------------------------------------------------------------
# 0. SECURITY / REGION SCREENS
# ---------------------------------------------------------------------------

def _flash_html():
    return "".join(f'<div class="warn"><b>Note.</b> {e(m)}</div>' for m in take_flash())


def _security_banner():
    if not demo_mode():
        return ""
    if getattr(CTX, "local_ok", False):
        txt = ("No operator password is set yet. Create one from the sign-in page "
               "before a real venue touches this instance.")
    else:
        txt = ("No operator password is set, so every screen is publicly reachable "
               "(open demo mode). Set the password from the machine this runs on "
               "(localhost), or start the app with REGULARS_OPEN_FIRST_RUN=1.")
    return (f'<div class="warn"><b>Security.</b> {e(txt)} '
            '<a href="/login">Sign-in &rarr;</a> &middot; '
            '<a href="/security">Security screen &rarr;</a></div>')


def _region_card():
    reg = STATE.get("region", "AU")
    addr = STATE.get("venue_address", "")
    opts = "".join(f'<option value="{v}"{" selected" if reg == v else ""}>{l}</option>'
                   for v, l in (("AU", "Australia"), ("US", "United States")))
    return (
        '<h2>Market &amp; footer law</h2>'
        '<div class="card"><form method="post" action="/region">'
        '<p class="sm muted" style="margin-bottom:20px">The market changes the legal footer '
        'inside every message, the season references, and the spelling. '
        '<b>Australia</b>: Spam Act 2003 &mdash; sender identity, a working unsubscribe, ABN. '
        '<b>United States</b>: CAN-SPAM &mdash; a valid physical postal address in every '
        'commercial email, and opt-outs honoured within 10 business days.</p>'
        '<div class="grid g2">'
        f'<div class="field"><label>Market</label><select name="region">{opts}</select></div>'
        '<div class="field"><label>Physical postal address (required for the US)</label>'
        f'<input type="text" name="venue_address" value="{e(addr)}" '
        'placeholder="412 Smith St, Fitzroy VIC 3065"></div>'
        '</div>'
        '<div class="row between" style="margin-top:20px">'
        '<span class="sm soft">Applies to message footers immediately. Nothing is sent '
        'anywhere by this app.</span>'
        '<button class="btn" type="submit">Save market</button></div>'
        '</form></div>')


def page_login(ip):
    fl = _flash_html()
    if demo_mode():
        if not local_request_allowed(ip):
            body = ['<h1>Sign in</h1>',
                    '<div class="card" style="max-width:640px">'
                    '<p>No operator password is set, so this instance runs in <b>open demo '
                    'mode</b> &mdash; every screen is reachable without signing in. That is '
                    'fine for evaluating Regulars; it is not fine for a live venue.</p>'
                    '<p class="sm muted">To lock it down: open this page from the machine the '
                    'app runs on (localhost), or start the app with '
                    '<span class="mono">REGULARS_OPEN_FIRST_RUN=1</span> if you accept setting '
                    'the first password over the network. Behind TLS + a reverse proxy, do it '
                    'on day one.</p>'
                    '<p style="margin-top:20px"><a class="btn" href="/">Continue in demo '
                    'mode</a></p></div>']
            return page("Sign in", fl + "".join(body), "/login")
        body = ['<h1>Create the operator password</h1>',
                '<p class="lead">First run on this machine. This password gates every screen. '
                'There is no recovery path except deleting the state file on the server, so '
                'write it down somewhere safe.</p>',
                '<form method="post" action="/login"><div class="card" style="max-width:520px">'
                '<div class="field"><label>Password (10 characters or more)</label>'
                '<input type="password" name="password" minlength="10" required></div>'
                '<div class="field"><label>Repeat it</label>'
                '<input type="password" name="password2" minlength="10" required></div>'
                '<button class="btn big" type="submit" style="margin-top:10px">Set password '
                'and sign in</button>'
                '<p class="sm soft" style="margin-top:14px">Stored as a PBKDF2-SHA256 hash '
                '(210k iterations). The plain password never touches the disk or the log.</p>'
                '</div></form>']
        return page("Sign in", fl + "".join(body), "/login")
    extra = ""
    if GUARD.locked_until(ip):
        until = GUARD.locked.get(ip, 0)
        mins = max(1, int((until - datetime.now(timezone.utc).timestamp()) // 60) + 1)
        extra = (f'<div class="warn"><b>Locked out.</b> Too many failed attempts from this '
                 f'address. Try again in about {mins} minutes.</div>')
    body = ['<h1>Sign in</h1>',
            '<form method="post" action="/login"><div class="card" style="max-width:460px">'
            '<div class="field"><label>Operator password</label>'
            '<input type="password" name="password" autofocus required></div>'
            '<button class="btn big" type="submit" style="margin-top:10px">Sign in</button>'
            '<p class="sm soft" style="margin-top:14px">Sessions last 12 hours. Failed '
            'attempts are rate-limited, locked out after five, and written to the audit '
            'log.</p></div></form>']
    return page("Sign in", fl + extra + "".join(body), "/login")


def page_security():
    ok, broken_at, lines = AUDIT.verify()
    tail = AUDIT.read(40)
    sess = STATE.get("sessions") or {}
    tokens = _load_tokens() if STATE.get("oauth_tokens_enc") else None
    ost = OAUTH.status(tokens)
    _kf = os.environ.get("REGULARS_KEYFILE") or os.path.join(HERE, "regulars-master.key")
    if os.environ.get("REGULARS_MASTER_KEY"):
        key_src = "environment (REGULARS_MASTER_KEY)"
    elif os.path.exists(_kf):
        key_src = f"keyfile {os.path.basename(_kf)} (0600)"
    else:
        key_src = "not created yet - auto-generated keyfile on first secret stored"

    body = ['<h1>Security oversight</h1>',
            '<p class="lead">What protects this instance, what it has noticed from the '
            'outside, and proof the noticeboard itself has not been edited.</p>',
            _flash_html()]

    body.append('<div class="grid g3">')
    body.append(stat("Version", VERSION, f'schema {SCHEMA} &middot; built {BUILD_DATE}'))
    body.append(stat("Sign-in", "Open demo" if demo_mode() else "Password gated",
                     f'{len(sess)} active session' + ("" if len(sess) == 1 else "s")))
    body.append(stat("Audit chain", "INTACT" if ok else "BROKEN",
                     f'{lines} entries, hash-chained'))
    body.append('</div>')

    if not ok:
        body.append(f'<div class="bad"><b>Tamper indicator.</b> The audit log stops verifying '
                    f'at entry {broken_at}. Someone edited '
                    f'<span class="mono">regulars-audit.log</span>, or it was corrupted. Treat '
                    f'every entry from that point on as untrustworthy and investigate before '
                    f'trusting anything this log says.</div>')
    if demo_mode():
        body.append('<div class="warn"><b>No operator password is set.</b> Every screen is '
                    'publicly reachable while this instance stays in demo mode. '
                    '<a href="/login">Set the password &rarr;</a></div>')
    else:
        body.append('<div class="good"><b>Password gating is on.</b> Sessions expire after 12 '
                    'hours, every state-changing form carries a CSRF token, and five bad '
                    'passwords lock an address out for 15 minutes. '
                    '<form method="post" action="/security/logout-all" style="display:inline">'
                    '<button class="btn ghost" type="submit">Sign out everywhere</button>'
                    '</form></div>')

    body.append('<h2>Outside-attack detection</h2>')
    body.append('<div class="card"><table>'
                f'<tr><td>POST rate limit</td><td class="r mono">{SEC.RATE_POST} / min / address</td></tr>'
                f'<tr><td>GET rate limit</td><td class="r mono">{SEC.RATE_GET} / min / address</td></tr>'
                f'<tr><td>Upload size cap</td><td class="r mono">{SEC.MAX_BODY_BYTES // (1024 * 1024)} MB</td></tr>'
                f'<tr><td>Password attempts</td><td class="r mono">{SEC.LOGIN_MAX_FAILS} fails '
                f'&rarr; {SEC.LOGIN_LOCKOUT // 60} min lockout</td></tr>'
                f'<tr><td>Addresses locked right now</td><td class="r mono">{len(GUARD.locked)}</td></tr>'
                '</table>')
    flags = GUARD.flags[-6:]
    if flags:
        body.append('<p class="sm muted" style="margin-top:14px">Recent flags: '
                    + "; ".join(f'<b>{e(f["what"])}</b> ({e(f["ip"])})' for f in flags)
                    + '.</p>')
    else:
        body.append('<p class="sm muted" style="margin-top:14px">No flags raised this '
                    'process lifetime. Flags and every event below persist in the audit '
                    'log across restarts.</p>')
    body.append('</div>')

    body.append('<h2>Credentials &amp; secrets</h2>')
    body.append('<div class="card"><table>'
                f'<tr><td>OAuth-token encryption key</td><td class="r mono">{e(key_src)}</td></tr>'
                f'<tr><td>Square OAuth application</td><td class="r mono">'
                f'{"configured" if OAUTH.configured() else "not configured"}</td></tr>'
                f'<tr><td>Stored Square tokens</td><td class="r mono">'
                f'{"yes - encrypted at rest" if tokens else "none"}</td></tr>'
                f'<tr><td>Connection state</td><td class="r mono">{e(ost["state"])}</td></tr>'
                '</table>'
                f'<p class="sm soft" style="margin-top:12px">{e(ost["detail"])}</p></div>')
    if not OAUTH.configured():
        body.append('<p class="sm soft" style="margin-top:10px">To enable the real Square '
                    'authorization-code flow, set <span class="mono">SQUARE_CLIENT_ID</span>, '
                    '<span class="mono">SQUARE_CLIENT_SECRET</span> and '
                    '<span class="mono">REGULARS_REDIRECT_URI</span> in the app&rsquo;s '
                    'environment (create the application free in the Square Developer '
                    'Dashboard). Credentials never live in the state file.</p>')

    body.append('<h2>Deployment checklist</h2><div class="card"><table>')
    for label, done in [
        ("Operator password set", not demo_mode()),
        ("Served over HTTPS (set REGULARS_TLS=1 behind your proxy to add HSTS)",
         bool(os.environ.get("REGULARS_TLS"))),
        ("Square OAuth application configured", OAUTH.configured()),
        ("Master key supplied via environment, not auto-keyfile",
         bool(os.environ.get("REGULARS_MASTER_KEY"))),
        ("US market: postal address recorded" if STATE.get("region") == "US"
         else "AU market selected (no postal-address requirement)",
         STATE.get("region") != "US" or bool(STATE.get("venue_address"))),
    ]:
        mark = "&#10003;" if done else "&#9888;"
        cls = "good" if done else "warn"
        body.append(f'<tr><td><span class="{cls}">{mark}</span> {e(label)}</td>'
                    f'<td class="r mono">{"ok" if done else "todo"}</td></tr>')
    body.append('</table></div>')

    body.append('<h2>Audit trail (last 40)</h2>')
    rows = ""
    for ev in reversed(tail):
        ts = datetime.fromtimestamp(ev.get("ts", 0), timezone.utc).strftime("%m-%d %H:%M:%S")
        det = json.dumps(ev.get("detail") or {})
        rows += (f'<tr><td class="sm mono">{e(ts)}</td><td><b>{e(ev.get("event", "?"))}</b></td>'
                 f'<td class="sm mono">{e(ev.get("ip") or "-")}</td>'
                 f'<td class="sm muted">{e(det[:150])}</td></tr>')
    body.append('<div class="card"><div class="row between" style="margin-bottom:16px">'
                '<form method="post" action="/security/verify">'
                '<button class="btn ghost" type="submit">Verify chain integrity</button></form>'
                '<a class="btn ghost" href="/security/audit.log">Download full log</a></div>'
                '<table><tr><th>When (UTC)</th><th>Event</th><th>Address</th><th>Detail</th></tr>'
                + (rows or '<tr><td colspan="4" class="muted">Nothing recorded yet.</td></tr>')
                + '</table></div>')
    body.append('<p class="sm muted" style="margin-top:26px">Every entry is hash-chained to '
                'the one before it, so deleting or editing a line breaks verification. The '
                'log lives in <span class="mono">regulars-audit.log</span> beside the app; '
                'point <span class="mono">REGULARS_AUDIT_LOG</span> at a mounted volume in '
                'production so it survives redeploys.</p>')
    return page("Security", "".join(body), "/security")


# ---------------------------------------------------------------------------
# 1. SETUP
# ---------------------------------------------------------------------------

def page_setup():
    r = result()
    s = r["summary"]
    g = STATE["goals"]
    conn = STATE["connected"]
    ds = dataset()
    pre = _flash_html() + _security_banner() + _region_card()

    if not conn:
        body = ['<h1>Connect your Square, or bring a CSV.</h1>',
                '<p class="lead">Regulars reads your sales history, works out what each customer '
                'actually does, and writes them the one message that fits. Not a blast. '
                '<b>One message per person</b>, based on their own orders.</p>']
        body.append(pre)
        body.append('<h2>Two ways in</h2>')
        body.append('<div class="card"><div class="row between">'
                    '<div style="flex:1;min-width:260px"><h3>Connect Square</h3>'
                    '<p class="sm muted">Read-only. Orders, Customers and Catalogue permissions, '
                    'nothing else. Revocable any time from your Square Dashboard. In production '
                    'this is Square&rsquo;s OAuth screen; here it is a button.</p></div>'
                    '<form method="post" action="/connect">'
                    '<button class="btn big">Connect Square</button></form></div></div>')

        body.append('<div class="card"><h3>Or upload exports</h3>'
                    '<p class="sm muted">Prefer not to connect anything? Upload what you already '
                    'have. Two shapes are accepted: your Square dashboard exports (transactions '
                    'plus, optionally, the customer directory), or our simple one-row-per-visit '
                    'format. Column names are matched tolerantly and you&rsquo;ll get a report of '
                    'what was found and what wasn&rsquo;t.</p>'
                    '<form method="post" action="/upload" enctype="multipart/form-data">'
                    '<div class="filerow"><div class="field"><label>Square transactions / item '
                    'sales CSV &mdash; or the simple format</label>'
                    '<input type="file" name="f_main" accept=".csv,text/csv"></div>'
                    '<div class="field"><label>Square customer directory CSV (optional)</label>'
                    '<input type="file" name="f_cust" accept=".csv,text/csv"></div>'
                    '<div class="row" style="align-items:flex-start;gap:12px">'
                    '<input type="checkbox" name="consent" value="1" style="width:auto;margin-top:5px">'
                    '<span class="sm muted" style="flex:1">These contacts opted in at my POS, on my '
                    'website or through my loyalty program. I&rsquo;m confirming that as the venue '
                    '&mdash; exports don&rsquo;t carry consent, and under the Spam Act a card payment '
                    'is not consent.</span></div></div>'
                    '<div class="row between"><span class="sm muted">No file handy? Try a sample: '
                    '<a href="/sample/simple.csv">simple format</a> &middot; '
                    '<a href="/sample/square-transactions.csv">Square transactions</a> &middot; '
                    '<a href="/sample/square-customers.csv">Square customers</a></span>'
                    '<button class="btn" type="submit">Read the file</button></div></form></div>')

        body.append('<h2>Then three questions</h2><ol class="steps">')
        for t, d in [("What do you want more of?",
                      "Specific times you want busier, and specific products you want to sell. "
                      "&ldquo;Tuesday and Wednesday dinner&rdquo; and &ldquo;espresso martinis&rdquo; "
                      "beat &ldquo;more revenue&rdquo; every time, because the message can name the thing."),
                     ("What will you give, and to whom?",
                      "Offers live in categories &mdash; an upgrade, something free, money off, a group "
                      "perk &mdash; and each rule picks one. You can change every pairing. There is no "
                      "blanket discount, because a blanket discount is wrong for most of your customers "
                      "and expensive for the rest."),
                     ("How often is too often?",
                      f'Default: at most one message per person every '
                      f'{g["min_days_between_sends"]} days, and nobody who&rsquo;s been in this week gets '
                      f'anything at all.')]:
            body.append(f'<li><b>{t}</b><span>{d}</span></li>')
        body.append('</ol>')
        body.append('<p style="margin-top:40px"><a class="btn ghost" href="/inbox">Skip ahead '
                    '&mdash; browse the demo messages</a></p>')
        return page("Setup", "".join(body), "/")

    src = ("Synthetic demo venue &mdash; Marlow &amp; Finch, Fitzroy"
           if STATE["source"] == "demo" else
           f'Imported from your CSV &mdash; {ds["stats"]["customers"]} customers, '
           f'{ds["stats"]["orders"]:,} orders')
    body = [f'<h1>Your messages are ready.</h1>',
            f'<p class="lead">{src}. {ds["stats"]["orders"]:,} orders read. '
            f'<b>{s["ready"]} messages</b> written, each for exactly one person.</p>']

    rep = STATE.get("import_report")
    if rep:
        body.append('<div class="note"><b>Import report.</b> '
                    f'{rep.get("orders", 0)} visits read into {rep.get("customers", 0)} customers '
                    f'and a {rep.get("menu_items", 0)}-item menu. '
                    + (f'Fields found: {", ".join(rep.get("fields_found", []))}. '
                       if rep.get("fields_found") else "")
                    + (f'Not present in the file: {", ".join(rep.get("fields_missing", []))} &mdash; '
                       'triggers that need those fields are asleep for this dataset. '
                       if rep.get("fields_missing") else "")
                    + (f'{rep.get("skipped_rows", 0)} rows skipped (no date, email or recognisable '
                       'items). ' if rep.get("skipped_rows") else "")
                    + (f'{rep.get("unmatched", 0)} line items didn&rsquo;t match any other line and '
                       'were priced from the file where possible. ' if rep.get("unmatched") else "")
                    + 'Imports live in memory only &mdash; restarting the app clears them, by design.')
        if rep.get("warning"):
            body.append(f'<div class="warn"><b>Heads up.</b> {e(rep["warning"])}</div>')
        body.append('</div>')

    body.append(pre)
    body.append('<div class="grid g3">')
    body.append(stat("Ready to send", f'{s["ready"]}', "each written for that person"))
    body.append(stat("Not contacted", f'{s["suppressed"] + s["no_match"]}', "on purpose"))
    body.append(stat("Distinct subjects", f'{s["unique_subjects"]}', f'of {s["ready"]} messages'))
    body.append('</div>')
    body.append('<div class="good"><b>Nobody gets the same message.</b> Every one names something '
                'that person actually orders, or an occasion they actually told you about. The '
                'specificity is the product &mdash; it is why these get opened and why people '
                'don&rsquo;t unsubscribe. And the customer-facing copy never quotes their '
                'patterns back at them: the numbers are yours, on the message page, not theirs.</div>')

    body.append('<h2>Where you are</h2><ol class="steps">')
    for done, t, d, href, cta in [
        (True, "Square connected or CSV read", src + ".", None, None),
        (bool(g["push_times"] or g["push_products"]), "Your goals",
         ("Pushing " + ", ".join(x["label"] for x in g["push_times"]) + "; products: " +
          ", ".join(g["push_products"])) if (g["push_times"] or g["push_products"])
         else "Not set &mdash; we&rsquo;ll fall back to generic retention.", "/goals", "Set goals"),
        (True, "Triggers and their numbers",
         f'{len([t for t in E.TRIGGERS if t["id"] in STATE["enabled"]])} of {len(E.TRIGGERS)} rules '
         f'on; every threshold editable.', "/triggers", "Review"),
        (STATE["sender"]["tool"] != "none", "Sending",
         f'Currently: {ESP[STATE["sender"]["tool"]][0]}.', "/sending", "Configure"),
    ]:
        mark = "&#10003;" if done else "&#9675;"
        link = f' <a href="{href}">{cta} &rarr;</a>' if href else ""
        body.append(f'<li><b>{mark} {t}</b><span>{d}{link}</span></li>')
    body.append('</ol>')
    body.append(f'<p style="margin-top:44px"><a class="btn big" href="/inbox">See the {s["ready"]} '
                f'messages &rarr;</a> <form method="post" action="/reset" style="display:inline">'
                f'<button class="btn ghost" type="submit" title="Clears venue data and config; '
                f'password, market and tokens are kept">Start over</button></form></p>')

    body.append('<h2>Who we deliberately did not contact</h2>')
    body.append(_suppression_card(r))
    return page("Setup", "".join(body), "/")


# ---------------------------------------------------------------------------
# 2. GOALS
# ---------------------------------------------------------------------------

DAYPARTS = [("morning", "Breakfast / morning"), ("midday", "Lunch"), ("evening", "Dinner")]
PRIORITIES = [("repeat", "Get existing customers back more often"),
              ("fill_quiet", "Fill the quiet periods I'm already open for"),
              ("bigger_basket", "Get people to spend more per visit"),
              ("high_margin", "Sell more of my high-margin items"),
              ("new_customers", "Get my regulars to bring new people")]


def page_goals():
    g = STATE["goals"]
    r = result()
    ds = dataset()
    body = ['<h1>What do you want more of?</h1>',
            '<p class="lead">Be specific. &ldquo;Tuesday and Wednesday dinner&rdquo; and '
            '&ldquo;espresso martinis&rdquo; produce better messages than &ldquo;more sales&rdquo;, '
            'because the message can name the thing.</p>']
    body.append('<form method="post" action="/goals">')

    body.append('<h2>Times you want busier</h2>')
    body.append('<p class="sm muted" style="margin-top:18px">Pick the slots you&rsquo;re already open '
                'and staffed for but not full. We only suggest these to people who could plausibly '
                'come then &mdash; nobody gets an invite to a dinner they&rsquo;ve never attended and '
                'showed no sign of wanting.</p><div class="card">')
    for wd in range(7):
        opts = "".join(
            f'<label class="row" style="gap:8px;margin:6px 0;font:inherit;text-transform:none;'
            f'letter-spacing:0;color:var(--ink)">'
            f'<input type="checkbox" style="width:auto" name="slot" value="{dp}|{wd}" '
            f'{"checked" if any(s["daypart"]==dp and s.get("weekday")==wd for s in g["push_times"]) else ""}> '
            f'{E.DAY_SHORT[wd]} {lab}</label>'
            for dp, lab in DAYPARTS)
        body.append(f'<div style="display:inline-block;vertical-align:top;margin-right:34px">'
                    f'<div class="sm" style="font-weight:600;margin-bottom:6px">{E.DAY_NAMES[wd]}</div>{opts}</div>')
    body.append('</div>')

    body.append('<h2>Products you want to sell more of</h2>')
    body.append('<p class="sm muted" style="margin-top:18px">Suggested only to people who have never '
                'bought them and who already come in for something similar.</p><div class="card">')
    cats = {}
    for m in ds["menu"]:
        cats.setdefault(m["category"], []).append(m["name"])
    for cat in sorted(cats):
        opts = "".join(
            f'<label class="row" style="gap:8px;margin:6px 26px 6px 0;display:inline-flex;'
            f'font:inherit;text-transform:none;letter-spacing:0;color:var(--ink)">'
            f'<input type="checkbox" style="width:auto" name="product" value="{e(n)}" '
            f'{"checked" if n in g["push_products"] else ""}> {e(n)}</label>' for n in cats[cat])
        body.append(f'<div style="margin-bottom:16px"><div class="sm" style="font-weight:600;'
                    f'margin-bottom:4px">{e(cat)}</div>{opts}</div>')
    body.append('</div>')

    body.append('<h2>What are you optimising for?</h2>')
    body.append('<p class="sm muted" style="margin-top:18px">This nudges the rules that serve it up '
                'the order. It never deletes the others &mdash; the most specific match for each '
                'person still wins.</p><div class="card">')
    for val, lab in PRIORITIES:
        body.append(f'<label class="row" style="gap:10px;margin:10px 0;font:inherit;text-transform:'
                    f'none;letter-spacing:0;color:var(--ink)">'
                    f'<input type="radio" style="width:auto" name="priority" value="{val}" '
                    f'{"checked" if g["priority"]==val else ""}> {e(lab)}</label>')
    body.append('</div>')

    body.append('<h2>How should the messages sound?</h2>')
    body.append('<p class="sm muted" style="margin-top:18px">Tone changes the voice &mdash; the '
                'greeting, the sign-off, the connective phrasing. It never changes the evidence '
                'rules, the offer, or the legal footer, and it can never make a message quote '
                'the customer&rsquo;s numbers back at them. Save, then read a few on the '
                'Messages screen; every message stays hand-editable before you approve '
                'it.</p><div class="card">')
    for tk, tl in E.TONES.items():
        body.append(f'<label class="row" style="gap:10px;margin:10px 0;font:inherit;'
                    f'text-transform:none;letter-spacing:0;color:var(--ink)">'
                    f'<input type="radio" style="width:auto" name="tone" value="{tk}" '
                    f'{"checked" if g.get("tone", "warm") == tk else ""}> {e(tl)}</label>')
    body.append('</div>')

    body.append('<h2>The offer catalogue</h2>')
    body.append('<p class="sm muted" style="margin-top:18px">Offers live in categories because the '
                'right nudge for a daily coffee regular is nonsense for a Friday-night group. Edit '
                'the list, add your own, then choose which offer each rule uses underneath.</p>')
    body.append('<div class="card" style="padding-top:26px"><table><thead><tr><th>Category</th>'
                '<th>What you&rsquo;ll say</th><th style="width:110px">Costs you</th>'
                '<th style="width:60px">Drop</th></tr></thead><tbody>')
    for i, o in enumerate(g["catalog"]):
        sel = "".join(f'<option value="{k}" {"selected" if o["cat"]==k else ""}>{e(v)}</option>'
                      for k, v in CAT_LABEL.items())
        body.append(f'<tr><td><select name="cat_{i}" style="width:150px">{sel}</select>'
                    f'<input type="hidden" name="id_{i}" value="{e(o["id"])}"></td>'
                    f'<td><input type="text" name="label_{i}" value="{e(o["label"])}"></td>'
                    f'<td><input type="number" step="0.1" min="0" name="cost_{i}" value="{o["cost"]}"></td>'
                    f'<td style="text-align:center"><input type="checkbox" style="width:auto" '
                    f'name="drop_{i}" value="1"></td></tr>')
    body.append('<tr><td><select name="new_cat" style="width:150px">'
                + "".join(f'<option value="{k}">{e(v)}</option>' for k, v in CAT_LABEL.items())
                + '</select></td><td><input type="text" name="new_label" '
                  'placeholder="add your own offer&hellip;"></td>'
                  '<td><input type="number" step="0.1" min="0" name="new_cost" value="0"></td>'
                  '<td></td></tr>')
    body.append('</tbody></table>')
    body.append(f'<p class="sm soft" style="margin-top:14px">Categories: '
                + " &middot; ".join(f'<b>{e(v)}</b> &mdash; {e(h)}' for k, v, h in E.OFFER_CATEGORIES)
                + '</p></div>')

    body.append('<h2>Which offer each rule uses</h2><div class="card" style="padding-top:26px">')
    body.append('<div class="grid g2">')
    for t in E.TRIGGERS:
        cur = g["trigger_offer"].get(t["id"])
        opts = ""
        for ck, cv in CAT_LABEL.items():
            members = [o for o in g["catalog"] if o["cat"] == ck]
            if not members:
                continue
            opts += f'<optgroup label="{e(cv)}">'
            for o in members:
                opts += (f'<option value="{e(o["id"])}" {"selected" if o["id"]==cur else ""}>'
                         f'{e(o["label"])} ({money(o["cost"], 2)})</option>')
            opts += '</optgroup>'
        body.append(f'<div class="field"><label>{e(t["name"])}</label>'
                    f'<select name="toffer_{t["id"]}">{opts}</select></div>')
    body.append('</div></div>')

    body.append('<h2>How often is too often?</h2><div class="card"><div class="grid g2">'
                f'<div class="field" style="margin:0"><label>Minimum days between messages to the '
                f'same person</label><input type="number" name="min_days" '
                f'value="{g["min_days_between_sends"]}" min="7" max="90"></div>'
                f'<div class="field" style="margin:0"><label>Don&rsquo;t contact anyone who visited '
                f'in the last</label><input type="number" name="skip_days" '
                f'value="{g["skip_if_visited_within_days"]}" min="0" max="14"></div>'
                '</div><p class="sm muted" style="margin:20px 0 0">Enforced in code, not left to '
                'judgement. The Spam Act sets no numeric cap, but ACMA expects restraint, and your '
                'unsubscribe rate is the real limit.</p></div>')

    body.append(f'<div class="row between" style="margin-top:50px"><div><b>{r["summary"]["ready"]} '
                f'messages</b> <span class="muted">will be generated with these settings</span></div>'
                f'<button class="btn big" type="submit">Save and regenerate</button></div>')
    body.append('</form>')
    return page("Goals", "".join(body), "/goals")


# ---------------------------------------------------------------------------
# 3. TRIGGERS + TUNING
# ---------------------------------------------------------------------------

def page_triggers():
    r = result()
    counts = r["trigger_counts"]
    s = r["summary"]
    t_h = th()
    body = ['<h1>Triggers, and their numbers.</h1>',
            '<p class="lead">Each customer is tested against these rules in order and the '
            '<b>first match wins</b>, so everyone gets exactly one message &mdash; the most specific '
            'thing that&rsquo;s true about them. If nothing matches, they get nothing, and that is '
            'counted rather than hidden. Every number below is yours to change.</p>']
    body.append('<div class="grid g3">')
    body.append(stat("Rules on", f'{len([t for t in E.TRIGGERS if t["id"] in STATE["enabled"]])}',
                     f'of {len(E.TRIGGERS)}'))
    body.append(stat("Messages", f'{s["ready"]}', "one per matched customer"))
    body.append(stat("No match", f'{s["no_match"]}', "correctly left alone"))
    body.append('</div>')

    body.append('<form method="post" action="/triggers">')
    body.append('<h2>The rules</h2><div class="card" style="padding-top:10px">')
    for i, t in enumerate(E._ordered_triggers(STATE["goals"]), 1):
        on = t["id"] in STATE["enabled"]
        n = counts.get(t["id"], 0)
        cat = CAT_LABEL.get(t["offer_cat"], t["offer_cat"])
        offer = E.offer_for(STATE["goals"], t["id"])
        body.append(
            f'<div class="trig {"off" if not on else ""}">'
            f'<div class="n"><b>{n}</b><span>people</span></div>'
            f'<div style="flex:1"><label class="row" style="gap:10px;margin:0;font:inherit;'
            f'text-transform:none;letter-spacing:0;color:var(--ink)">'
            f'<input type="checkbox" style="width:auto" name="t" value="{t["id"]}" '
            f'{"checked" if on else ""}> <b style="font-size:18px">{e(t["name"])}</b></label>'
            f'<div class="sm muted" style="margin-top:6px">{e(t["rule"])}</div>'
            f'<div style="margin-top:10px"><span class="pill p-acc">{e(cat)}: '
            f'{e(offer["label"])}</span><span class="pill p-mute">priority {i}</span></div></div></div>')
    body.append('</div>')

    body.append('<h2>The numbers</h2>')
    body.append('<p class="sm muted" style="margin-top:18px">These are the thresholds behind the '
                'rules above. Change one and apply &mdash; the people-counts re-run immediately, so '
                'you can see what a number is worth before you commit to it. Reset returns to the '
                'defaults we&rsquo;d ship with.</p>')
    body.append('<div class="card" style="padding-top:26px"><div class="grid g2">')
    for key, label, help_txt, step in E.THRESHOLD_FIELDS:
        val = t_h[key]
        body.append(f'<div class="field"><label>{e(label)}</label>'
                    f'<input type="number" step="{step}" name="th_{key}" value="{val}">'
                    + (f'<div class="sm soft" style="margin-top:6px">{e(help_txt)}</div>'
                       if help_txt else '') + '</div>')
    body.append('</div></div>')

    body.append('<div class="row between" style="margin-top:44px">'
                '<span class="sm muted">Turning a rule off removes those people from the list '
                'entirely &mdash; they are not reassigned to a weaker message.</span>'
                '<span><button class="btn ghost" type="submit" formaction="/tuning/reset" '
                'formnovalidate>Reset numbers</button> '
                '<button class="btn big" type="submit">Apply</button></span></div>')
    body.append('</form>')

    body.append('<h2>Why this order</h2><div class="card"><ol class="steps">')
    for t, d in [
        ("Birthday always comes first", "It&rsquo;s time-critical. A birthday inside the window "
         "beats a coffee nudge, whatever your priority setting says."),
        ("Then whatever you said you wanted most of", "Your priority is worth a nudge up the order "
         "for the rules that serve it. It doesn&rsquo;t delete the others."),
        ("Then most specific to least", "&ldquo;You order the flat white constantly&rdquo; is a "
         "stronger message than &ldquo;we miss you&rdquo;, so it gets used while it&rsquo;s true."),
        ("Upsells only go to people currently coming in", "Pitching a free side to someone who "
         "hasn&rsquo;t visited in months is tone-deaf and wasted. They get a win-back instead."),
        ("Win-backs are for real lapses", "A nudge about their usual is for someone running late. "
         "Past your lapse threshold it becomes a credit, because a nudge won&rsquo;t bring them back."),
    ]:
        body.append(f'<li><b>{t}</b><span>{d}</span></li>')
    body.append('</ol></div>')
    return page("Triggers", "".join(body), "/triggers")


# ---------------------------------------------------------------------------
# 4. MESSAGES
# ---------------------------------------------------------------------------

def page_inbox(flt=None):
    r = result()
    msgs = r["messages"]
    s = r["summary"]
    if flt and flt != "all":
        msgs = [m for m in msgs if m["offer_cat"] == flt]

    body = ['<h1>Ready to send.</h1>',
            f'<p class="lead">{s["ready"]} messages written from your sales data, '
            f'{s["unique_subjects"]} different subject lines. Open any one to see the email, the '
            f'numbers behind it, and why that person got that offer.</p>']
    body.append('<div class="grid g3">')
    body.append(stat("Messages ready", f'{s["ready"]}', "each written for one person"))
    body.append(stat("Cost if all redeem", money(s["total_offer_cost_if_all_redeem"]),
                     "expect roughly a fifth to"))
    body.append(stat("Left alone", f'{s["suppressed"] + s["no_match"]}', "deliberately"))
    body.append('</div>')

    body.append('<h2>By offer category</h2><div class="card" style="padding-top:20px">')
    body.append('<div class="row" style="gap:26px"><a href="/inbox" class="pill '
                + ("p-acc" if not flt else "p-mute") + f'">All ({s["ready"]})</a>')
    for k, v in CAT_LABEL.items():
        n = s["by_cat"].get(k, 0)
        on = "p-acc" if flt == k else "p-mute"
        body.append(f'<a href="/inbox?cat={k}" class="pill {on}">{e(v)} ({n})</a>')
    body.append('</div>')
    body.append('<div class="grid g2" style="margin-top:34px">')
    for k, v in CAT_LABEL.items():
        n = s["by_cat"].get(k, 0)
        offers = Counter(m["offer_label"] for m in r["messages"] if m["offer_cat"] == k)
        lines = "".join(f'<div class="row between sm" style="margin:4px 0"><span>{e(o)}</span>'
                        f'<span class="muted">{c}</span></div>' for o, c in offers.most_common())
        body.append(f'<div class="card tight"><div class="row between"><h3 style="margin:0">{e(v)}</h3>'
                    f'<span class="pill p-acc">{n}</span></div>'
                    f'<div class="sm soft" style="margin:6px 0 10px">{e(CAT_HELP[k])}</div>{lines}</div>')
    body.append('</div></div>')

    body.append('<h2>The messages</h2>')
    body.append('<div class="row between" style="margin:20px 0 30px">'
                f'<span class="sm muted">Showing {len(msgs)}</span>'
                '<span><a class="btn sm ghost" href="/export.csv">CSV for your sender</a> '
                '<a class="btn sm ghost" href="/export-copy.txt">All copy as text</a></span></div>')
    for m in msgs[:120]:
        c = m["customer"]
        ap = m["id"] in STATE["approved"]
        initial = c["name"].split(" ", 1)[1][:1] + "." if " " in c["name"] else ""
        ap_pill = '<span class="pill p-good">approved</span>' if ap else ""
        dim = ' style="opacity:.5"' if ap else ""
        body.append(
            f'<details class="msg"{dim}><summary>'
            f'<span class="who">{e(c["first_name"])} {e(initial)}</span>'
            f'<span class="subj">{e(m["subject"])}</span>{ap_pill}'
            f'<span class="pill p-acc">{e(CAT_LABEL.get(m["offer_cat"], m["offer_cat"]))}</span>'
            f'<span class="pill p-mute">{e(m["trigger_name"])}</span>'
            f'</summary><div class="mbody">{_message_body(m)}</div></details>')
    if len(msgs) > 120:
        body.append(f'<p class="sm muted" style="margin-top:24px">Showing the first 120 of '
                    f'{len(msgs)}. The CSV export carries all of them.</p>')
    return page("Messages", "".join(body), "/inbox")


def _message_body(m):
    c = m["customer"]
    return (
        f'<div class="row between sm"><span class="muted">To: <b>{e(c["email"])}</b></span>'
        f'<span class="muted">Consent: {e(c.get("consent_source") or "unrecorded")}'
        f'{", " + e(c.get("consent_at")) if c.get("consent_at") else ""}</span></div>'
        f'{evidence_html(m["evidence"])}'
        f'<div class="sm soft">These numbers are for you. None of them appear in the email below '
        f'&mdash; the customer gets their usual and a vague sense of time, never a ratio.</div>'
        f'<div class="row between" style="margin-top:22px"><div><div class="sm soft">Subject</div>'
        f'<b style="font-size:18px">{e(m["subject"])}</b></div>'
        f'<span class="pill p-mute">{e(m["trigger_name"])}</span></div>'
        f'<div class="email" style="margin-top:18px">{e(m["body"])}</div>'
        f'<div class="why"><b>Why this person, why this offer:</b> {m["why"]}</div>'
        f'<div class="row" style="margin-top:26px">'
        f'<form method="post" action="/approve?id={e(m["id"])}" style="display:inline">'
        f'<button class="btn green sm" type="submit">Approve this one</button></form>'
        f'<a class="btn ghost sm" href="/m?id={e(m["id"])}">Open &amp; edit</a>'
        f'<span class="sm soft">Offer: {e(m["offer_label"])} &mdash; costs '
        f'{money(m["offer_cost"], 2)} if redeemed</span></div>'
    )


def page_message(mid):
    r = result()
    m = next((x for x in r["messages"] if x["id"] == mid), None)
    if not m:
        return page("Not found", '<h1>Message not found.</h1>'
                    '<p style="margin-top:30px"><a href="/inbox">Back to the list</a></p>', "/inbox")
    c = m["customer"]
    p = m["profile"]
    body = [f'<p style="margin-top:40px"><a href="/inbox">&larr; All messages</a></p>',
            f'<h1>{e(c["name"])}</h1>',
            f'<p class="lead">{e(m["trigger_name"])} &rarr; {e(CAT_LABEL.get(m["offer_cat"], ""))}: '
            f'{e(m["offer_label"])}</p>']
    body.append('<div class="grid g4">')
    body.append(stat("Visits", f'{p["visits"]}', f'over {p["first_days_ago"]} days'))
    body.append(stat("Lifetime spend", money(p["total_spend"]), f'avg ticket {money(p["avg_ticket"], 2)}'))
    body.append(stat("Last in", f'{p["recency_days"]}d', f'own rhythm {p["own_gap"] or "-"} days'))
    body.append(stat("Usual table", f'{p["avg_party"]}', f'biggest {p["max_party"]}'))
    body.append('</div>')

    body.append('<h2>Their behaviour &mdash; yours to see, never theirs</h2>')
    body.append('<div class="card"><table>')
    rows = [
        ("Signature order", f'{e(p["signature_item"] or "none")}'
         + (f' &mdash; on {p["signature_visits"]} of {p["visits"]} visits' if p["signature_item"] else "")),
        ("Most ordered", ", ".join(e(x) for x in p["top_items"])),
        ("Never bought", ", ".join(e(x) for x in STATE["goals"]["push_products"]
                                   if x not in p["ever_bought"]) or "nothing from your push list"),
        ("Usual time", f'{e(E.DAYPART_PHRASE[p["primary_daypart"]])}, most often a '
                       f'{e(E.DAY_NAMES[p["primary_weekday"]])}'),
        ("Adds a side or dessert", f'{round(p["attach_rate"]*100)}% of visits'),
        ("Menu variety", f'{p["variety"]} (low = orders the same few things)'),
        ("Buys alcohol", f'{round(p["alcohol_share"]*100)}% of visits'),
        ("Cadence trend", (f'{p["gap_trend"]}x their early rhythm' if p.get("gap_trend")
                           else "steady")),
        ("Birthday", (f'in {p["birthday_days"]} days' if p.get("birthday_days") is not None
                      else "not recorded")),
        ("Spend percentile", f'top {100 - p["spend_pct"]}% of your customers'),
        ("Contactable", f'{"yes" if c.get("email") else "no"} &mdash; '
                        f'{e(c.get("email") or "no email on file")}'),
        ("Consent", e(c.get("consent_source") or "none recorded")),
    ]
    for k, v in rows:
        body.append(f'<tr><td class="sm" style="width:36%"><b>{e(k)}</b></td><td>{v}</td></tr>')
    body.append('</table>')
    body.append('<div class="note" style="margin:26px 0 0">This table is the reason the email can be '
                'short and warm. Everything here stays in the app; the customer sees a message that '
                'reads like it was written by someone who knows them &mdash; because it was derived '
                'by someone who does.</div></div>')

    body.append('<h2>Why this offer</h2>')
    body.append(f'<div class="why" style="font-size:16px">{m["why"]}</div>')
    body.append(f'<div class="note sm"><b>The rule that fired:</b> {e(m["trigger_rule"])}</div>')

    body.append('<h2>The message</h2>')
    body.append('<form method="post" action="/approve-edit" class="card">')
    body.append(f'<input type="hidden" name="id" value="{e(m["id"])}">')
    body.append('<div class="field"><label>Subject line</label>'
                f'<input type="text" name="subject" value="{e(m["subject"])}"></div>')
    body.append('<div class="field"><label>Email body</label>'
                f'<textarea name="body" rows="16">{e(m["body"])}</textarea></div>')
    body.append('<div class="warn sm">Edit freely &mdash; but keep the footer. It carries the venue '
                'name, the ABN, the reason they&rsquo;re receiving this and the unsubscribe tag. '
                'Those four things are what keep you on the right side of the Spam Act. And keep '
                'the copy free of their numbers: no gaps, no counts, no ratios.</div>')
    body.append('<div class="row"><button class="btn big green" type="submit">Approve and add to '
                'the send list</button><a class="btn ghost" href="/inbox">Back</a></div>')
    body.append('</form>')
    return page(c["name"], "".join(body), "/inbox")


# ---------------------------------------------------------------------------
# 5. SENDING
# ---------------------------------------------------------------------------

def page_sending():
    g = STATE["goals"]
    sd = STATE["sender"]
    tool = sd["tool"]
    body = ['<h1>Where will these go out from?</h1>',
            '<p class="lead">Regulars writes the messages; something else has to send them. Tell us '
            'what you already use and the export adapts to it &mdash; including the unsubscribe '
            'merge tag, which is different in every tool and is not a cosmetic detail.</p>']
    body.append(_flash_html())

    _ost = OAUTH.status(_load_tokens() if STATE.get("oauth_tokens_enc") else None)
    body.append('<h2>Square connection (OAuth)</h2>')
    if STATE.get("oauth_tokens_enc"):
        body.append('<div class="good"><b>Square tokens stored &mdash; encrypted at rest.</b> '
                    'The authorization-code flow completed against Square&rsquo;s OAuth server. '
                    'Pulling live data through these tokens is the first production step; until '
                    'then the app keeps using the demo or uploaded dataset. '
                    '<form method="post" action="/oauth/revoke" style="display:inline">'
                    '<button class="btn ghost" type="submit">Revoke &amp; delete tokens</button>'
                    '</form></div>')
    elif OAUTH.configured():
        body.append('<div class="card"><p class="sm muted">This server is configured with a '
                    'Square OAuth application. Connecting sends you to Square&rsquo;s own '
                    'authorization screen; Regulars receives an access token and stores it '
                    'encrypted. Read-only scopes only: merchant profile, orders, customers, '
                    'catalog, loyalty.</p>'
                    '<p style="margin-top:16px"><a class="btn big" href="/oauth/start">'
                    'Connect with Square &rarr;</a></p></div>')
    else:
        body.append('<div class="card"><p class="sm muted">Square OAuth is not configured on '
                    'this server yet. Set <span class="mono">SQUARE_CLIENT_ID</span>, '
                    '<span class="mono">SQUARE_CLIENT_SECRET</span> and '
                    '<span class="mono">REGULARS_REDIRECT_URI</span> in the environment (the '
                    'application is free to create in the Square Developer Dashboard), and the '
                    'connect button here will run the real authorization-code flow. '
                    'Until then, use the CSV upload on the Setup screen &mdash; it needs no '
                    'credentials at all.</p></div>')

    body.append('<form method="post" action="/sending">')
    body.append('<h2>Your sender</h2><div class="card" style="padding-top:20px">')
    for k, (name, tag, url, blurb) in ESP.items():
        body.append(f'<label class="row" style="gap:12px;margin:0 0 22px;font:inherit;'
                    f'text-transform:none;letter-spacing:0;color:var(--ink);align-items:flex-start">'
                    f'<input type="radio" style="width:auto;margin-top:6px" name="tool" value="{k}" '
                    f'{"checked" if tool==k else ""}>'
                    f'<span style="flex:1"><b style="font-size:17px">{e(name)}</b>'
                    + (f' &mdash; <a href="{url}">open</a>' if url else '')
                    + f'<span class="sm muted" style="display:block;margin-top:3px">{e(blurb)}</span>'
                    + (f'<span class="sm soft" style="display:block;margin-top:3px">Unsubscribe tag '
                       f'we write into your footer: <code>{e(tag)}</code></span>' if tag else
                       '<span class="sm soft" style="display:block;margin-top:3px">Unsubscribe '
                       'handled by Square itself.</span>')
                    + '</span></label>')
    body.append('</div>')

    body.append('<h2>If you send through your own SMTP</h2>')
    body.append('<p class="sm muted" style="margin-top:18px">Stored locally in this app&rsquo;s '
                'config file so real sending can be switched on later. The prototype never '
                'transmits anything &mdash; the test button below only checks that the details '
                'work.</p><div class="card"><div class="grid g2">')
    for key, lab, ph, typ in [("smtp_host", "Host", "smtp.example.com", "text"),
                              ("smtp_port", "Port", "587", "number"),
                              ("smtp_user", "Username", "campaigns@yourvenue.com.au", "text"),
                              ("smtp_pass", "Password / app password", "", "password"),
                              ("from_addr", "From address", "Marlow & Finch <hello@...>", "text"),
                              ("reply_to", "Reply-to (should be a monitored inbox)", "", "text")]:
        body.append(f'<div class="field"><label>{e(lab)}</label>'
                    f'<input type="{typ}" name="{key}" value="{e(sd.get(key) or "")}" '
                    f'placeholder="{e(ph)}"></div>')
    body.append('</div>')
    res = sd.get("test_result")
    if res:
        cls = "good" if res["ok"] else "bad"
        body.append(f'<div class="{cls} sm"><b>Connection test:</b> {e(res["msg"])}</div>')
    body.append('<div class="row"><button class="btn ghost sm" type="submit" '
                'formaction="/sending/test">Test the connection</button>'
                '<span class="sm soft">Runs from wherever this app is running. It will fail in a '
                'sandbox with no network, and that is fine.</span></div></div>')

    body.append('<h2>What changes in the export</h2><div class="card">')
    tag = g.get("unsub_tag") or "{{unsubscribe_url}}"
    body.append(f'<p>Footer unsubscribe tag: <code>{e(tag)}</code> &mdash; rewritten automatically '
                f'when you pick a sender above. Columns: Email, First Name, Last Name, Subject, '
                f'Body, Offer, Offer Category, Trigger, Consent Source, Consent Date, Holdout.</p>'
                f'<p class="sm muted">The holdout column marks every tenth person. Do not send to '
                f'them. They are the control group that lets you prove any of this worked, and '
                f'comparing the venue total before-and-after without them overstates everything.</p>'
                f'<div class="row"><a class="btn" href="/export.csv">Download the CSV</a>'
                f'<a class="btn ghost" href="/export-copy.txt">Copy as plain text</a></div></div>')
    body.append(f'<div class="row between" style="margin-top:44px"><span></span>'
                f'<button class="btn big" type="submit">Save sending setup</button></div>')
    body.append('</form>')
    return page("Sending", "".join(body), "/sending")


# ---------------------------------------------------------------------------
# 6. SENT
# ---------------------------------------------------------------------------

def page_sent():
    body = ['<h1>Sent.</h1>',
            '<p class="lead">Every message, who approved it, on what consent basis, when. Two jobs: '
            'an evidence trail if anyone ever asks, and the feedback that makes the next batch '
            'better.</p>']
    if not STATE["sent"]:
        body.append('<div class="card"><h3>Nothing sent yet</h3>'
                    '<p class="muted sm">Approve messages from the list and they&rsquo;ll appear '
                    'here. This prototype transmits nothing &mdash; in production this pushes to '
                    'the sender you configured, or you export a CSV and upload it yourself.</p></div>')
        body.append('<h2>How measurement will work</h2><ol class="steps">')
        for t, d in [
            ("Hold back 10% automatically", "A random control group that gets nothing. Without it "
             "you cannot tell whether the campaign worked or whether those people were coming "
             "anyway. It is the single most-missed feature in hospitality marketing."),
            ("Match on customer, not on venue total", "Comparing the whole venue before and after "
             "a send overstates the effect massively, because it counts people who were never in "
             "the audience. We compare audience against holdout."),
            ("Measure at 21 days", "Long enough for a monthly regular to have had the chance to "
             "come in, short enough to act on the result."),
            ("Feed the result back into the rules", "After three campaigns you stop using generic "
             "response rates and start using yours. That is when the projections become "
             "believable, and it is the reason not to churn."),
        ]:
            body.append(f'<li><b>{t}</b><span>{d}</span></li>')
        body.append('</ol>')
        return page("Sent", "".join(body), "/sent")

    body.append('<div class="grid g3">')
    body.append(stat("Approved", f'{len(STATE["sent"])}', "this session"))
    body.append(stat("Offer cost", money(sum(x["offer_cost"] for x in STATE["sent"]), 2),
                     "if all redeem"))
    body.append(stat("Contacts", f'{len({x["customer_id"] for x in STATE["sent"]})}', "unique"))
    body.append('</div>')
    body.append('<div class="card"><table><thead><tr><th>Customer</th><th>Trigger</th><th>Offer</th>'
                '<th>Subject</th><th>Approved</th><th class="r">Cost</th></tr></thead><tbody>')
    for x in STATE["sent"]:
        body.append(f'<tr><td><b>{e(x["name"])}</b><br><span class="sm soft">{e(x["email"])}</span></td>'
                    f'<td class="sm">{e(x["trigger_name"])}</td><td class="sm">{e(x["offer_label"])}</td>'
                    f'<td class="sm">{e(x["subject"])}</td><td class="sm">{e(x["approved_at"])}<br>'
                    f'<span class="soft">{e(x["approver"])}</span></td>'
                    f'<td class="r">{money(x["offer_cost"], 2)}</td></tr>')
    body.append('</tbody></table></div>')
    body.append('<div class="good sm">Each approval records the customer ID, the exact message '
                'content, the trigger that produced it, the consent basis and a timestamp. That '
                'record is append-only. It is what you produce if ACMA ever asks how you got '
                'consent, and it is why nothing in this product sends automatically.</div>')
    return page("Sent", "".join(body), "/sent")


# ---------------------------------------------------------------------------
# 7. DATA
# ---------------------------------------------------------------------------

def page_data():
    r = result()
    s = r["summary"]
    ds = dataset()
    body = ['<h1>Your data.</h1>',
            '<p class="lead">What comes out of Square, what doesn&rsquo;t, and the one thing worth '
            'fixing before spending money on any marketing tool &mdash; including this one.</p>']

    body.append('<h2>The problem worth solving first</h2>')
    body.append('<div class="grid g3">')
    body.append(stat("Customer records", f'{ds["stats"]["customers"]}', "in the directory"))
    body.append(stat("Email-sendable",
                     f'{sum(1 for c in ds["customers"] if c["email"] and c["email_consent"] and not c["unsubscribed"])}',
                     "consent on file"))
    body.append(stat("No contact details", f'{s["suppression_breakdown"].get("no_contact", 0)}',
                     "cannot be reached at all"))
    body.append('</div>')
    body.append('<div class="warn"><b>This is the highest-return change available to you and it '
                'costs nothing.</b> Square does not give you a customer&rsquo;s email or mobile '
                'number from a card payment &mdash; it gives a masked card number and sometimes a '
                'name. A customer only becomes contactable if they give you their email at the '
                'register, through loyalty signup, on your website, or when they book.</p>'
                '<p style="margin:14px 0 0">In your Square POS settings, turn on the email receipt '
                'prompt and the marketing opt-in. Then get staff to ask. Two sentences at the till '
                'move this number by ten percent a month, which is worth more than any campaign '
                'this product will ever generate.</div>')

    body.append('<h2>What we read from Square</h2>')
    body.append('<div class="card"><table><thead><tr><th>Square API</th><th>What it gives us</th>'
                '<th>What we do with it</th></tr></thead><tbody>')
    for api, gives, does in [
        ("Orders", "Every ticket: items, quantities, time, day, covers if recorded, discounts, "
                   "dine-in or takeaway.",
         "The whole product. Visit rhythm, their usual, their gap between visits, whether they add "
         "sides, whether they come with a group, which dayparts they use."),
        ("Customers", "Name, email, mobile if given, notes, birthday if captured, "
                      "<code>email_unsubscribed</code>.",
         "Who to contact, who must never be contacted, and first names for personalisation."),
        ("Catalog", "Your menu: items, prices, categories, modifiers.",
         "So a message can name a real dish, and so upsells suggest things that actually exist."),
        ("Loyalty (if subscribed)", "Points, tiers, rewards, birthdays.",
         "Birthday triggers and tier-aware offers. Only present if you pay for Square Loyalty."),
        ("CSV exports", "The same facts, as files, if you'd rather not connect anything.",
         "Everything above, via the upload on the setup screen. Column names matched tolerantly; "
         "you get a report of what was and wasn't in the file."),
    ]:
        body.append(f'<tr><td><b>{e(api)}</b></td><td class="sm">{gives}</td>'
                    f'<td class="sm">{does}</td></tr>')
    body.append('</tbody></table></div>')

    body.append('<h2>What we keep, and what we never do</h2>')
    body.append('<div class="card">')
    for status, t, d in [
        ("pass", "Nothing is sent automatically", "No scheduler, no auto-responder, no "
         "API-triggered send. A named human approves each message or batch."),
        ("pass", "Customer-facing copy carries no surveillance", "Emails name their usual and use "
         "vague time (&ldquo;a few weeks&rdquo;). Gaps, ratios, counts and percentiles exist only "
         "on the message page, for you."),
        ("pass", "Unsubscribes are permanent and portable", "Written to the suppression list in the "
         "same request. If you leave, you get the list exported to you. No trigger overrides it."),
        ("pass", "SMS is off", "Since 1 July 2026, sending under a venue&rsquo;s branded sender ID "
         "requires ACMA Register approval as an entity associate, a registered sender ID, and the "
         "venue&rsquo;s recorded consent. Email carries Spam Act obligations but no registration "
         "regime, so email is the channel until that machinery is worth building."),
        ("warn", "Automated decision-making disclosure", "From 10 December 2026 businesses must "
         "disclose automated decisions that could affect people&rsquo;s rights or interests. "
         "Segmenting customers for targeting counts. We hand you the privacy-policy clause at "
         "onboarding."),
        ("pass", "Exports are minimised", "The CSV carries email, first name, subject, body, offer, "
         "category and consent source. No spend history, no mobile number, no order details &mdash; "
         "your sender doesn&rsquo;t need them and holding less is safer."),
        ("pass", "Imports live in memory only", "An uploaded CSV is held for the running process "
         "and dropped on restart. Your customer list is not written to disk by this app."),
    ]:
        body.append(f'<div class="chk"><div class="ic {status}">{"&#10003;" if status=="pass" else "!"}</div>'
                    f'<div class="t"><b>{e(t)}</b><span>{d}</span></div></div>')
    body.append('</div>')
    body.append('<div class="warn sm"><b>Not legal advice.</b> This maps to published ACMA, OAIC and '
                'Square requirements as at November 2026. Before you send for a paying customer, '
                'have an Australian commercial lawyer review your merchant agreement, privacy '
                'policy template and consent-capture wording. Budget $3,000&ndash;$5,000 once. It '
                'is the cheapest insurance in this business.</div>')
    return page("Data", "".join(body), "/data")


# ---------------------------------------------------------------------------
# exports and samples
# ---------------------------------------------------------------------------

def export_csv():
    r = result()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Regulars - ready-to-send audience"])
    w.writerow([f"# Venue: {dataset()['venue']['name']}, {dataset()['venue']['suburb']}"])
    w.writerow([f"# Sender: {ESP[STATE['sender']['tool']][0]}"])
    w.writerow([f"# Generated: {datetime.now().isoformat(timespec='seconds')}"])
    w.writerow([f"# {len(r['messages'])} messages. Every contact has consent on file or was "
                f"attested by the venue at import."])
    w.writerow(["# Import into your sender. Column names match common defaults."])
    w.writerow([])
    w.writerow(["Email", "First Name", "Last Name", "Subject", "Body", "Offer",
                "Offer Category", "Trigger", "Consent Source", "Consent Date", "Holdout"])
    for i, m in enumerate(r["messages"]):
        c = m["customer"]
        last = c["name"].split(" ", 1)
        w.writerow([c["email"], last[0], last[1] if len(last) > 1 else "", m["subject"],
                    m["body"], m["offer_label"], CAT_LABEL.get(m["offer_cat"], m["offer_cat"]),
                    m["trigger_name"], c.get("consent_source") or "", c.get("consent_at") or "",
                    "yes" if i % 10 == 9 else "no"])
    return buf.getvalue()


def export_copy():
    r = result()
    out = [f"REGULARS - {dataset()['venue']['name']}",
           f"Generated {datetime.now().isoformat(timespec='seconds')}",
           f"{len(r['messages'])} messages", "=" * 70, ""]
    by = {}
    for m in r["messages"]:
        by.setdefault(m["trigger_name"], []).append(m)
    for trig, ms in by.items():
        out += [f"### {trig.upper()} ({len(ms)} people)", "-" * 70]
        for m in ms[:3]:
            out += [f"TO: {m['customer']['name']} <{m['customer']['email']}>",
                    f"SUBJECT: {m['subject']}", "", m["body"], "",
                    f"WHY: {m['why'].replace('<b>', '').replace('</b>', '')}", "." * 70, ""]
        if len(ms) > 3:
            out += [f"... and {len(ms)-3} more in this group", "", ""]
    out += ["BEFORE YOU SEND", "=" * 70,
            "[ ] A named human has approved this batch",
            f"[ ] ABN in the footer is correct: {dataset()['venue']['abn']}",
            "[ ] Unsubscribe tag matches your sending tool (see the Sending screen)",
            "[ ] Reply-to is monitored by the venue",
            "[ ] Staff know about every offer in here - nothing worse than a coupon the till refuses",
            "[ ] The 10% holdout group has NOT been sent to",
            "[ ] No customer numbers (gaps, counts, ratios) were added to any email", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

def parse_multipart(raw, ctype):
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    if not m:
        return {}, {}
    boundary = ("--" + m.group(1)).encode()
    fields, files = {}, {}
    for part in raw.split(boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, bodypart = part.partition(b"\r\n\r\n")
        hdr = head.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]*)"', hdr)
        if not nm:
            continue
        fn = re.search(r'filename="([^"]*)"', hdr)
        if fn:
            files[nm.group(1)] = (fn.group(1), bodypart)
        else:
            fields[nm.group(1)] = bodypart.decode("utf-8", "replace")
    return fields, files


def do_import(fields, files):
    menu_names = []
    main = files.get("f_main", ("", b""))[1].decode("utf-8-sig", "replace")
    cust = files.get("f_cust", ("", b""))[1].decode("utf-8-sig", "replace")
    if not main.strip():
        return None, {"error": "No file received. Pick a CSV and try again."}
    head = main.lstrip("\ufeff").splitlines()[0] if main.strip() else ""
    norm = [re.sub(r"[^a-z0-9]", "", h.lower()) for h in head.split(",")]
    square = any(n in norm for n in ("itemvariationname", "createdat", "locationid",
                                     "orderid", "soldcount"))
    attested = fields.get("consent") == "1"
    if square:
        visits, people, rep = IMP.parse_square(main, cust, menu_names)
    else:
        visits, people, rep = IMP.parse_simple(main, menu_names)
    if rep.get("error"):
        return None, rep
    if not visits:
        return None, {"error": "No usable rows found. Check the file has a header row with at "
                                "least date, email and items (simple format) or a Square "
                                "transactions export."}
    menu = IMP.build_menu(visits)
    ds = IMP.to_dataset(visits, people, menu, datetime.now().replace(second=0, microsecond=0),
                        attested_consent=attested)
    rep.update(customers=ds["stats"]["customers"], menu_items=len(menu),
               unmatched=ds.get("unmatched_line_items", 0), mode="square" if square else "simple",
               attested=attested)
    if not attested and not any(c["email_consent"] for c in ds["customers"]):
        rep["warning"] = ("No marketing consent found in the file and none attested, so every "
                          "contact is quarantined under the Spam Act. Tick the consent box if "
                          "these people opted in at your POS, website or loyalty program.")
    return ds, rep


class Handler(BaseHTTPRequestHandler):
    server_version = "Regulars/" + VERSION

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}\n")

    def _send(self, body, status=200, ctype="text/html; charset=utf-8", extra=None,
              cookie=None):
        raw = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in SEC.security_headers(tls=bool(os.environ.get("REGULARS_TLS"))).items():
            self.send_header(k, v)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _redirect(self, loc, cookie=None):
        self.send_response(303)
        self.send_header("Location", loc)
        for k, v in SEC.security_headers(tls=bool(os.environ.get("REGULARS_TLS"))).items():
            self.send_header(k, v)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _html(self, body_html, status=200, cookie=None):
        """Render a page; when a session exists, every form in it gets its CSRF
        field injected, so no page function has to remember one."""
        tok = getattr(CTX, "csrf", "")
        if tok:
            body_html = re.sub(
                r"(<form[^>]*>)",
                lambda m: m.group(1) + f'<input type="hidden" name="csrf" value="{tok}">',
                body_html)
        self._send(body_html, status, cookie=cookie)

    def _ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd else (self.client_address[0] or "-")

    def _cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            part = part.strip()
            if part.startswith("REGULARS_SID="):
                return part[len("REGULARS_SID="):]
        return ""

    def _session(self):
        sess = session_from_token(self._cookie_token())
        return sess if SEC.session_valid(sess) else None

    def _authorized(self):
        return demo_mode() or self._session() is not None

    def _lockout_page(self):
        until = GUARD.locked.get(self._ip(), 0)
        mins = max(1, int((until - datetime.now(timezone.utc).timestamp()) // 60) + 1)
        return page("Locked out", f"<h1>Too many failed sign-ins.</h1>"
                    f"<p>This address is locked out for about {mins} more minutes. "
                    f"Every attempt is written to the audit log.</p>")

    def _do_reset(self, ip):
        keep = {k: STATE.get(k) for k in ("password_hash", "sessions", "region",
                                          "venue_address", "oauth_tokens_enc",
                                          "oauth_pending", "schema")}
        STATE.update(fresh_state())
        STATE.update({k: v for k, v in keep.items() if v is not None})
        AUDIT.write("reset", ip=ip,
                    detail={"kept": "auth, market, tokens", "version": VERSION})
        persist()
        self._redirect("/")

    def _oauth_start(self, ip):
        if not OAUTH.configured():
            flash("Square OAuth is not configured on this server (SQUARE_CLIENT_ID / "
                  "SQUARE_CLIENT_SECRET). The Sending screen explains the one-time setup.")
            self._redirect("/sending")
            return
        state = secrets.token_urlsafe(24)
        STATE.setdefault("oauth_pending", {})[state] = datetime.now(timezone.utc).timestamp()
        persist()
        AUDIT.write("oauth_start", ip=ip, detail={"redirect_uri": OAUTH.redirect_uri(),
                                                  "scopes": len(OAUTH.SCOPES)})
        self._redirect(OAUTH.authorize_url(state))

    def _oauth_callback(self, q, ip):
        code = q.get("code", "")
        state = q.get("state", "")
        started = (STATE.get("oauth_pending") or {}).pop(state, None)
        persist()
        if q.get("error"):
            AUDIT.write("oauth_fail", ip=ip, detail={"error": q.get("error", "")[:120]})
            flash(f"Square declined the connection: {q.get('error')}.")
            self._redirect("/sending")
            return
        age = (datetime.now(timezone.utc).timestamp() - float(started)) if started else None
        if not code or started is None or age > 900:
            AUDIT.write("oauth_fail", ip=ip,
                        detail={"error": "state mismatch" if started is None
                                else "no code or authorization older than 15 minutes"})
            flash("That Square callback did not match a connection we started, or it "
                  "arrived too late. Nothing was stored.")
            self._redirect("/sending")
            return
        try:
            tokens = OAUTH.exchange_code(code)
        except (OSError, ValueError) as ex:
            AUDIT.write("oauth_fail", ip=ip, detail={"error": str(ex)[:200]})
            flash(f"Square token exchange failed: {ex}")
            self._redirect("/sending")
            return
        _store_tokens(tokens)
        STATE["connected"] = True
        STATE["source"] = "square"
        persist()
        AUDIT.write("oauth_ok", ip=ip,
                    detail={"merchant_id": tokens.get("merchant_id", ""),
                            "scopes": tokens.get("scopes", [])})
        flash("Square connected. The access token is stored encrypted at rest; pulling "
              "live data through it is the first production step - the app keeps using "
              "the demo or uploaded dataset until then.")
        self._redirect("/")

    def _do_login(self, one, ip):
        pw = one("password", "") or ""
        if demo_mode():
            if not local_request_allowed(ip):
                self._send(page("Sign in", '<h1>No operator password is set.</h1>'
                                '<p>This instance is in open demo mode and the first '
                                'password can only be created from the machine it runs on '
                                '(or with REGULARS_OPEN_FIRST_RUN=1).</p>'), 403)
                return
            if len(pw) < 10 or pw != (one("password2", "") or ""):
                flash("Password not set: use at least 10 characters, typed the same twice.")
                self._redirect("/login")
                return
            STATE["password_hash"] = SEC.hash_secret(pw)
            AUDIT.write("config_change", ip=ip,
                        detail={"what": "operator_password_created", "version": VERSION})
            tok = create_session(ip)
            self._redirect("/", cookie=cookie_for(tok))
            return
        if GUARD.locked_until(ip):
            self._send(self._lockout_page(), 429)
            return
        if SEC.verify_secret(pw, STATE.get("password_hash") or ""):
            GUARD.clear_login_fails(ip)
            tok = create_session(ip)
            AUDIT.write("login_ok", ip=ip)
            self._redirect("/", cookie=cookie_for(tok))
        else:
            locked = GUARD.note_login_fail(ip)
            AUDIT.write("login_fail", ip=ip)
            if locked:
                AUDIT.write("lockout", ip=ip,
                            detail={"minutes": SEC.LOGIN_LOCKOUT // 60})
            flash("Wrong password. Failed attempts are limited and logged.")
            self._redirect("/login")

    def do_GET(self):
        ip = self._ip()
        if not GUARD.allow(ip, "GET", SEC.RATE_GET):
            AUDIT.write("rate_limit", ip=ip,
                        detail={"bucket": "GET", "path": self.path[:120]})
            if GUARD.locked_until(ip):
                self._send(self._lockout_page(), 429)
            else:
                self._send(page("Too many requests", '<h1>Slow down.</h1>'
                                '<p>Too many requests from this address. Wait a minute '
                                'and try again.</p>'), 429)
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path not in ("/login", "/healthz", "/oauth/callback") \
                and not self._authorized():
            self._redirect("/login" if u.path == "/" else "/login?next=" + u.path)
            return
        sess = self._session()
        CTX.csrf = (sess or {}).get("csrf", "")
        CTX.local_ok = local_request_allowed(ip)
        try:
            if u.path == "/login":
                self._html(page_login(ip))
            elif u.path == "/":
                self._html(page_setup())
            elif u.path == "/goals":
                self._html(page_goals())
            elif u.path == "/triggers":
                self._html(page_triggers())
            elif u.path == "/inbox":
                self._html(page_inbox(q.get("cat")))
            elif u.path == "/m":
                self._html(page_message(q.get("id", "")))
            elif u.path == "/sending":
                self._html(page_sending())
            elif u.path == "/sent":
                self._html(page_sent())
            elif u.path == "/data":
                self._html(page_data())
            elif u.path == "/security":
                self._html(page_security())
            elif u.path == "/security/audit.log":
                AUDIT.write("export", ip=ip, detail={"what": "audit_log"})
                try:
                    with open(AUDIT.path, "rb") as f:
                        raw = f.read()
                except OSError:
                    raw = b""
                self._send(raw, ctype="text/plain; charset=utf-8",
                           extra={"Content-Disposition":
                                  'attachment; filename="regulars-audit.log"'})
            elif u.path == "/oauth/start":
                self._oauth_start(ip)
            elif u.path == "/oauth/callback":
                self._oauth_callback(q, ip)
            elif u.path == "/export.csv":
                AUDIT.write("export", ip=ip,
                            detail={"what": "audience_csv",
                                    "region": STATE.get("region", "AU")})
                self._send(export_csv(), ctype="text/csv; charset=utf-8",
                           extra={"Content-Disposition":
                                  'attachment; filename="regulars-audience.csv"'})
            elif u.path == "/export-copy.txt":
                AUDIT.write("export", ip=ip,
                            detail={"what": "copy_txt",
                                    "region": STATE.get("region", "AU")})
                self._send(export_copy(), ctype="text/plain; charset=utf-8",
                           extra={"Content-Disposition":
                                  'attachment; filename="regulars-copy.txt"'})
            elif u.path == "/sample/simple.csv":
                self._send(D.sample_simple(), ctype="text/csv; charset=utf-8",
                           extra={"Content-Disposition":
                                  'attachment; filename="regulars-sample-simple.csv"'})
            elif u.path == "/sample/square-transactions.csv":
                tx, _ = D.sample_square()
                self._send(tx, ctype="text/csv; charset=utf-8",
                           extra={"Content-Disposition":
                                  'attachment; filename="sample-square-transactions.csv"'})
            elif u.path == "/sample/square-customers.csv":
                _, cu = D.sample_square()
                self._send(cu, ctype="text/csv; charset=utf-8",
                           extra={"Content-Disposition":
                                  'attachment; filename="sample-square-customers.csv"'})
            elif u.path == "/healthz":
                self._send(json.dumps({"status": "ok", "version": VERSION,
                                       "schema": SCHEMA,
                                       "region": STATE.get("region", "AU"),
                                       "auth_enabled": not demo_mode()}),
                           ctype="application/json")
            else:
                self._send(page("Not found", '<h1>Not found.</h1>'
                                '<p style="margin-top:30px"><a href="/">Home</a></p>'), 404)
        except Exception:
            import traceback
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            detail = (f'<div class="email">{e(tb)}</div>' if demo_mode() else
                      '<p class="sm muted">The error was written to the server log. '
                      'The audit trail on /security shows what happened just before '
                      'it.</p>')
            self._send(page("Error", f'<h1>Something broke</h1>{detail}'), 500)

    def do_POST(self):
        ip = self._ip()
        if not GUARD.allow(ip, "POST", SEC.RATE_POST):
            AUDIT.write("rate_limit", ip=ip,
                        detail={"bucket": "POST", "path": self.path[:120]})
            self._send(page("Too many requests", '<h1>Slow down.</h1><p>Too many '
                            'requests from this address. Wait a minute and try again.</p>'),
                       429)
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        if n > SEC.MAX_BODY_BYTES:
            AUDIT.write("oversize_upload", ip=ip,
                        detail={"bytes": n, "path": u.path[:120]})
            self._send(page("Too large", '<h1>That file is too large.</h1><p>Uploads are '
                            f'capped at {SEC.MAX_BODY_BYTES // (1024 * 1024)} MB. Export a '
                            'shorter date range and upload that instead.</p>'), 413)
            return
        raw = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" in ctype:
            fields, files = parse_multipart(raw, ctype)
            form = {k: [v] for k, v in fields.items()}
        else:
            form = parse_qs(raw.decode(), keep_blank_values=True)
            files = {}
        one = lambda k, d=None: (form.get(k, [d])[0])

        if u.path == "/login":
            self._do_login(one, ip)
            return
        sess = self._session()
        if not demo_mode():
            if sess is None:
                self._send(page("Sign in", '<h1>Session required.</h1><p>Your session '
                                'expired or you are not signed in.</p>'
                                '<p><a class="btn" href="/login">Sign in</a></p>'), 403)
                return
            if not SEC.csrf_ok(sess, one("csrf")):
                AUDIT.write("csrf_reject", ip=ip, detail={"path": u.path[:120]})
                self._send(page("Form expired", '<h1>That form had expired.</h1>'
                                '<p>The security token did not match your session. Reload '
                                'the page and try again.</p>'), 403)
                return
        CTX.csrf = (sess or {}).get("csrf", "")

        if u.path == "/connect":
            STATE["connected"] = True
            STATE["source"] = "demo"
            STATE["dataset"] = None
            STATE["import_report"] = None
            persist()
            AUDIT.write("config_change", ip=ip,
                        detail={"what": "connect_demo_square"})
            self._redirect("/goals")

        elif u.path == "/upload":
            ds, rep = do_import(fields, files)
            if ds is None:
                STATE["import_report"] = rep
                STATE["connected"] = False
                AUDIT.write("upload", ip=ip,
                            detail={"ok": False, "error": str(rep.get("error", ""))[:160]})
                self._redirect("/")
                return
            STATE["dataset"] = ds
            STATE["import_report"] = rep
            STATE["connected"] = True
            STATE["source"] = "csv"
            AUDIT.write("upload", ip=ip,
                        detail={"ok": True, "mode": rep.get("mode"),
                                "customers": rep.get("customers"),
                                "orders": rep.get("orders"),
                                "attested": bool(rep.get("attested"))})
            self._redirect("/")

        elif u.path == "/reset":
            self._do_reset(ip)

        elif u.path == "/goals":
            g = STATE["goals"]
            g["push_times"] = []
            for sv in form.get("slot", []):
                dp, wd = sv.split("|")
                g["push_times"].append({"daypart": dp, "weekday": int(wd),
                                        "label": f"{E.DAY_NAMES[int(wd)]} "
                                                 f"{E.DAYPART_LABEL[dp]}"})
            g["push_products"] = form.get("product", [])
            g["priority"] = one("priority", "repeat")
            want_tone = one("tone", "")
            g["tone"] = want_tone if want_tone in E.TONES else g.get("tone", "warm")
            keep_ids = set()
            newcat = []
            for i, o in enumerate(g["catalog"]):
                if form.get(f"drop_{i}", [""])[0] == "1":
                    continue
                lab = one(f"label_{i}", o["label"]).strip()
                if not lab:
                    continue
                try:
                    cost = max(0.0, float(one(f"cost_{i}", o["cost"])))
                except (TypeError, ValueError):
                    cost = o["cost"]
                newcat.append({"id": one(f"id_{i}", o["id"]),
                               "cat": one(f"cat_{i}", o["cat"]),
                               "label": lab, "cost": cost})
                keep_ids.add(newcat[-1]["id"])
            nl = one("new_label", "").strip()
            if nl:
                nid = "own_" + re.sub(r"[^a-z0-9]", "", nl.lower())[:18]
                try:
                    ncost = max(0.0, float(one("new_cost", 0)))
                except (TypeError, ValueError):
                    ncost = 0.0
                newcat.append({"id": nid, "cat": one("new_cat", "free_item"),
                               "label": nl, "cost": ncost})
                keep_ids.add(nid)
            if newcat:
                g["catalog"] = newcat
            for t in E.TRIGGERS:
                want = one(f"toffer_{t['id']}")
                if want and any(o["id"] == want for o in g["catalog"]):
                    g["trigger_offer"][t["id"]] = want
                elif g["trigger_offer"].get(t["id"]) not in keep_ids:
                    g["trigger_offer"].pop(t["id"], None)
            try:
                g["min_days_between_sends"] = max(7, min(90, int(one("min_days", 14))))
                g["skip_if_visited_within_days"] = max(0, min(14, int(one("skip_days", 4))))
            except (TypeError, ValueError):
                pass
            persist()
            AUDIT.write("config_change", ip=ip,
                        detail={"what": "goals",
                                "priority": g.get("priority"),
                                "tone": g.get("tone", "warm"),
                                "catalog": len(g.get("catalog") or [])})
            self._redirect("/triggers")

        elif u.path == "/triggers":
            STATE["enabled"] = set(form.get("t", []))
            for key, label, help_txt, step in E.THRESHOLD_FIELDS:
                v = one(f"th_{key}")
                if v in (None, ""):
                    continue
                try:
                    fv = float(v)
                except ValueError:
                    continue
                dflt = E.DEFAULT_THRESHOLDS[key]
                fv = max(0.0, min(fv, max(1000.0, dflt * 20)))
                if step >= 1:
                    fv = int(round(fv))
                if abs(fv - dflt) < 1e-9:
                    STATE["thresholds"].pop(key, None)
                else:
                    STATE["thresholds"][key] = fv
            persist()
            AUDIT.write("config_change", ip=ip,
                        detail={"what": "triggers",
                                "enabled": len(STATE["enabled"]),
                                "tuned": len(STATE["thresholds"])})
            self._redirect("/triggers")

        elif u.path == "/tuning/reset":
            STATE["thresholds"] = {}
            persist()
            AUDIT.write("config_change", ip=ip, detail={"what": "tuning_reset"})
            self._redirect("/triggers")

        elif u.path == "/sending":
            tool = one("tool", "none")
            if tool not in ESP:
                tool = "none"
            sd = STATE["sender"]
            sd["tool"] = tool
            for key in ("smtp_host", "smtp_user", "smtp_pass", "from_addr", "reply_to"):
                sd[key] = one(key, sd.get(key, ""))
            try:
                sd["smtp_port"] = int(one("smtp_port", 587))
            except (TypeError, ValueError):
                pass
            tag = ESP[tool][1]
            STATE["goals"]["unsub_tag"] = tag or "{{unsubscribe_url}}"
            sd.pop("test_result", None)
            persist()
            AUDIT.write("config_change", ip=ip,
                        detail={"what": "sending", "tool": tool})
            self._redirect("/sending")

        elif u.path == "/sending/test":
            sd = STATE["sender"]
            for key in ("smtp_host", "smtp_user", "smtp_pass", "from_addr", "reply_to"):
                sd[key] = one(key, sd.get(key, ""))
            try:
                sd["smtp_port"] = int(one("smtp_port", 587))
            except (TypeError, ValueError):
                pass
            host, port = sd.get("smtp_host"), sd.get("smtp_port") or 587
            try:
                if not host:
                    raise OSError("no host configured")
                with smtplib.SMTP(host, port, timeout=8) as sm:
                    sm.ehlo()
                    try:
                        sm.starttls()
                        sm.ehlo()
                    except smtplib.SMTPNotSupportedError:
                        pass
                    if sd.get("smtp_user"):
                        sm.login(sd["smtp_user"], sd.get("smtp_pass", ""))
                sd["test_result"] = {"ok": True,
                                     "msg": f"Connected and authenticated to {host}:{port}."}
            except Exception as ex:
                sd["test_result"] = {"ok": False, "msg": f"{type(ex).__name__}: {ex}"}
            self._redirect("/sending")

        elif u.path in ("/approve", "/approve-edit"):
            r = result()
            mid = q.get("id") or one("id", "")
            m = next((x for x in r["messages"] if x["id"] == mid), None)
            if m:
                STATE["approved"].add(mid)
                STATE["sent"].append({
                    "id": mid, "customer_id": m["customer_id"], "name": m["customer"]["name"],
                    "email": m["customer"]["email"], "trigger_name": m["trigger_name"],
                    "offer_label": m["offer_label"], "offer_cat": m["offer_cat"],
                    "offer_cost": m["offer_cost"],
                    "subject": one("subject") or m["subject"],
                    "body": one("body") or m["body"],
                    "approver": one("approver") or "venue manager",
                    "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "consent_basis": m["customer"].get("consent_source") or "unrecorded",
                })
                STATE["sent_history"][m["customer_id"]] = now().isoformat()
                persist()
                AUDIT.write("approve", ip=ip,
                            detail={"message_id": mid,
                                    "customer_id": m["customer_id"],
                                    "trigger": m["trigger_name"],
                                    "offer": m["offer_label"],
                                    "approver": one("approver") or "venue manager"})
            self._redirect("/m?id=" + mid if u.path == "/approve-edit" else "/inbox")

        elif u.path == "/logout":
            tok = self._cookie_token()
            if tok:
                (STATE.get("sessions") or {}).pop(tok, None)
                persist()
            AUDIT.write("logout", ip=ip)
            self._redirect("/login", cookie=expire_cookie())

        elif u.path == "/region":
            reg = one("region", "")
            if reg not in ("AU", "US"):
                reg = STATE.get("region", "AU")
            addr = (one("venue_address", "") or "").strip()[:200]
            if reg == "US" and not addr and not STATE.get("venue_address"):
                flash("The US market requires a physical postal address in every "
                      "commercial email (CAN-SPAM 15 U.S.C. 7704). Add the address and "
                      "save again - nothing was changed.")
                self._redirect("/")
                return
            STATE["region"] = reg
            if addr:
                STATE["venue_address"] = addr
            AUDIT.write("config_change", ip=ip,
                        detail={"what": "region", "region": reg})
            persist()
            flash(f"Market set to {'United States' if reg == 'US' else 'Australia'}. "
                  f"Footers, season references and spelling now follow that market's rules.")
            self._redirect("/")

        elif u.path == "/security/verify":
            ok, broken_at, lines = AUDIT.verify()
            AUDIT.write("audit_verify", ip=ip,
                        detail={"ok": ok, "broken_at": broken_at, "lines": lines})
            if ok:
                flash(f"Audit chain verified: all {lines} entries intact.")
            else:
                flash(f"CHAIN BROKEN at entry {broken_at}: the audit log was edited or "
                      f"corrupted. Treat this as a security incident.")
            self._redirect("/security")

        elif u.path == "/security/logout-all":
            kill_sessions()
            AUDIT.write("logout", ip=ip, detail={"what": "all_sessions_revoked"})
            self._redirect("/login", cookie=expire_cookie())

        elif u.path == "/oauth/revoke":
            tokens = _load_tokens()
            revoked = bool(tokens) and OAUTH.revoke(tokens)
            _store_tokens(None)
            AUDIT.write("oauth_revoke", ip=ip,
                        detail={"revoked_at_square": revoked,
                                "had_tokens": bool(tokens)})
            flash("Stored Square tokens deleted from this server."
                  + (f" Revocation at Square: {'ok' if revoked else 'FAILED - revoke it in the Square Dashboard too'}."
                     if tokens else ""))
            self._redirect("/sending")
        else:
            self._redirect("/")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    mode = "OPEN DEMO MODE - no operator password set" if demo_mode() else "password gated"
    print(f"Regulars {VERSION} -> http://0.0.0.0:{port}")
    print(f"  market: {STATE.get('region', 'AU')} | auth: {mode}")
    print(f"  audit log: {AUDIT.path}")
    r = result()
    print(f"  {DS['stats']['customers']} demo customers, {DS['stats']['orders']:,} orders -> "
          f"{r['summary']['ready']} messages ready")
    print(f"  state file: {store.PATH}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
