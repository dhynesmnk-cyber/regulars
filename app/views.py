"""HTML fragments shared by several screens."""

import os
from datetime import timedelta

import billing as BILL
import core
import engine as E
import ui
from state import (CTX, IDENTITY_MISSING_MSG, OFFERS_UNCHECKED_MSG, STATE, VERSION,
                   billing_message, billing_state, demo_mode, identity_missing, offers_unchecked,
                   on_example, take_flash, venue_instance)
from ui import e


def money(v, dp=0):
    """A figure in the venue's market currency (A$ or US$)."""
    return core.money(v, dp, STATE.get("region", "AU"))


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


def _identity_banner():
    """Shown on screens where the owner might try to approve or export."""
    if not identity_missing():
        return ""
    return (f'<div class="warn"><b>Your business details are missing.</b> '
            f'{e(IDENTITY_MISSING_MSG)} <a href="/">Add them on Setup &rarr;</a></div>')


def _business_card():
    reg = STATE.get("region", "AU")
    addr = STATE.get("venue_address", "")
    b = STATE.get("business") or {}
    opts = "".join(f'<option value="{v}"{" selected" if reg == v else ""}>{l}</option>'
                   for v, l in (("AU", "Australia"), ("US", "United States")))
    missing = ('<div class="warn" style="margin-bottom:20px"><b>Not set yet.</b> Your messages '
               'are signed &ldquo;[Your business name]&rdquo; with &ldquo;[ABN]&rdquo; in the '
               'footer, and approving and exporting stay off until you save these details.'
               '</div>') if identity_missing() else ""
    return (
        '<h2>Your business</h2>'
        '<div class="card"><form method="post" action="/business">'
        f'{missing}'
        '<p class="sm muted" style="margin-bottom:20px">Every message is signed with your '
        'business name, and its legal footer carries who you are. '
        '<b>Australia</b>: Spam Act 2003 &mdash; sender identity including your ABN, and a '
        'working unsubscribe. '
        '<b>United States</b>: CAN-SPAM &mdash; a valid physical postal address in every '
        'commercial email, and opt-outs honoured within 10 business days. The market also '
        'sets season references and spelling.</p>'
        '<div class="grid g2">'
        '<div class="field"><label>Business name, as customers know it</label>'
        f'<input type="text" name="biz_name" maxlength="80" value="{e(b.get("name", ""))}" '
        'placeholder="Juniper &amp; Co"></div>'
        '<div class="field"><label>Suburb or city</label>'
        f'<input type="text" name="biz_suburb" maxlength="60" value="{e(b.get("suburb", ""))}" '
        'placeholder="Fitzroy, Melbourne"></div>'
        f'<div class="field"><label>Market</label><select name="region">{opts}</select></div>'
        '<div class="field"><label>ABN (required in Australia)</label>'
        f'<input type="text" name="biz_abn" maxlength="20" value="{e(b.get("abn", ""))}" '
        'placeholder="11 digits, e.g. 51 824 753 556" inputmode="numeric"></div>'
        '<div class="field"><label>Physical postal address (required for the US)</label>'
        f'<input type="text" name="venue_address" maxlength="200" value="{e(addr)}" '
        'placeholder="412 Smith St, Fitzroy VIC 3065"></div>'
        '</div>'
        '<div class="row between" style="margin-top:20px">'
        '<span class="sm soft">Applies to every message immediately. Nothing is sent '
        'anywhere by this app.</span>'
        '<button class="btn" type="submit">Save business details</button></div>'
        '</form></div>')


# ---------------------------------------------------------------- billing

def _send_banner():
    """Messages screens: why approving and exporting are off, if they are.
    A lapsed plan is shown by the page-wide notice instead."""
    if identity_missing():
        return _identity_banner()
    if STATE.get("dataset") is None:
        return ""
    if offers_unchecked():
        return (f'<div class="warn"><b>Check your offers first.</b> {e(OFFERS_UNCHECKED_MSG)} '
                f'<a href="/goals">Goals &rarr;</a></div>')
    b = billing_state()
    if b["status"] != "preview":
        return ""
    return (f'<div class="warn"><b>Preview.</b> {e(billing_message(b))} '
            f'<a href="/">Your plan &rarr;</a></div>')


def _billing_notice():
    b = billing_state()
    st, paid = b["status"], b["paid_until"]
    if st == "due_soon":
        days = (paid - BILL.today()).days
        when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        title, text = "Renewal due.", (f"Your plan is paid up to {BILL.nice(paid)} ({when}). "
                                       f"Renew to keep sending without a break.")
    elif st == "grace":
        stop = paid + timedelta(days=BILL.GRACE_DAYS)
        title, text = "Payment overdue.", (f"Your plan ended on {BILL.nice(paid)}. Approving and "
                                           f"exporting keep working until {BILL.nice(stop)}.")
    elif st == "lapsed":
        title, text = "Sending paused.", billing_message(b)
    else:
        return ""
    return f'<div class="warn"><b>{title}</b> {e(text)} <a href="/">Your plan &rarr;</a></div>'


# Screens that show customers and messages: on a venue's own instance, say so
# plainly while they're showing the example restaurant instead of its data.
EXAMPLE_SCREENS = ("/goals", "/triggers", "/inbox", "/sent", "/data")


def _example_notice(active):
    if not (venue_instance() and on_example()) or active not in EXAMPLE_SCREENS:
        return ""
    return (f'<div class="note"><b>Example data.</b> You&rsquo;re looking at '
            f'{e(E.PACK.WORDS["example"])} and its made-up {e(E.PACK.WORDS["customers"])}. '
            f'<a href="/#upload">Upload your Square export on Setup &rarr;</a></div>')


def _page_notice(active):
    return _example_notice(active) + _billing_notice()


def _footer():
    support = (os.environ.get("REGULARS_SUPPORT_EMAIL") or "").strip()
    out = f"Regulars {e(VERSION)}"
    if support:
        out += f' &middot; Questions? <a href="mailto:{e(support)}">{e(support)}</a>'
    if on_example():
        out += (f'<br>The example data is made up: {e(E.PACK.WORDS["example"])}, its '
                f'{e(E.PACK.WORDS["customers"])} and their {e(E.PACK.WORDS["orders"])}.')
    return (out + "<br>Every figure in every message traces to an order in the data; no "
            "figure appears in a customer&rsquo;s email unless a human put it there.")


ui.notice = _page_notice
ui.footer = _footer


def _plan_card():
    b = billing_state()
    st, paid = b["status"], b["paid_until"]
    if st == "off":
        return ""
    words = {
        "preview": "Preview: read every message free. Approving and exporting your customers' "
                   "messages starts once your plan is paid.",
        "active": f"Paid up to {BILL.nice(paid)}." if paid else "",
        "due_soon": f"Paid up to {BILL.nice(paid)}. Renew to keep sending without a break."
                    if paid else "",
        "grace": f"Your plan ended on {BILL.nice(paid)}. Sending keeps working until "
                 f"{BILL.nice(paid + timedelta(days=BILL.GRACE_DAYS))}." if paid else "",
        "lapsed": f"Your plan ended on {BILL.nice(paid)}, so approving and exporting are paused. "
                  f"Everything else still works." if paid else "",
    }[st]
    action = ""
    if st != "active":
        label = "Pay" if st == "preview" else "Renew"
        if b["pay_url"]:
            action = (f'<a class="btn" href="{e(b["pay_url"])}" target="_blank" '
                      f'rel="noopener">{label} &middot; {e(b["price"])}</a>')
        elif b["support"]:
            action = (f'<span>To pay, email <a href="mailto:{e(b["support"])}">'
                      f'{e(b["support"])}</a>.</span>')
        else:
            action = '<span>To pay, contact whoever set up Regulars for you.</span>'
    box = "warn" if st in ("preview", "grace", "lapsed") else ""
    return ('<h2>Your plan</h2><div class="card">'
            f'<div class="{box}" style="margin-bottom:20px">{e(words)}</div>'
            '<div class="row between">'
            f'<span class="sm soft">{e(b["price"])}, cancel anytime. Sending unlocks within one '
            f'business day of payment, usually sooner.</span>{action}</div></div>')
