"""Messages: the inbox, one message in full, and approval."""

from collections import Counter
from datetime import datetime

import engine as E
from state import (AUDIT, CAT_HELP, CAT_LABEL, STATE, flash, now, persist, result,
                   send_block)
from ui import e, page, stat
from views import _send_banner, evidence_html, money
from web import html, redirect


def page_inbox(flt=None):
    r = result()
    msgs = r["messages"]
    s = r["summary"]
    if flt and flt != "all":
        msgs = [m for m in msgs if m["offer_cat"] == flt]

    body = [_send_banner(), '<h1>Ready to send.</h1>',
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
    body = [_send_banner(),
            f'<p style="margin-top:40px"><a href="/inbox">&larr; All messages</a></p>',
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


def get_inbox(req):
    return html(page_inbox(req.q.get("cat")))


def get_message(req):
    if STATE.get("dataset") is not None:
        STATE["read_since_upload"] = True      # the checklist's "read your messages"
    return html(page_message(req.q.get("id", "")))


def post_approve(req):
    blocked = send_block()
    if blocked:
        flash(blocked)
        return redirect("/")
    one = req.one
    r = result()
    mid = req.q.get("id") or one("id", "")
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
        AUDIT.write("approve", ip=req.ip,
                    detail={"message_id": mid,
                            "customer_id": m["customer_id"],
                            "trigger": m["trigger_name"],
                            "offer": m["offer_label"],
                            "approver": one("approver") or "venue manager"})
    return redirect("/m?id=" + mid if req.path == "/approve-edit" else "/inbox")
