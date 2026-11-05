"""HTML fragments shared by several screens."""

from state import CTX, IDENTITY_MISSING_MSG, STATE, demo_mode, identity_missing, take_flash
from ui import e


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
