"""Setup screen: on a venue's own instance, a checklist from first sign-in to
first export; on a local run, the demo's connect-or-upload screen. Plus
business details, upload and reset."""

from datetime import date

import abn as ABN
import billing as BILL
import engine as E
import security as SEC
from state import (AUDIT, ESP, LOCK, STATE, VERSION, billing_state, business_complete,
                   dataset, demo_instance, flash, fresh_state, mark, on_example, persist,
                   result, venue_instance)
from ui import e, page, stat
from upload import do_import
from views import (_business_card, _flash_html, _plan_card, _security_banner,
                   _suppression_card)
from web import html, redirect

DEMO_NO_UPLOAD = ("This is a demo with made-up customers, so uploads are switched off. "
                  "Ask for a free preview to try your own data.")

CONSENT_HTML = (
    '<div class="row" style="align-items:flex-start;gap:12px">'
    '<input type="checkbox" name="consent" value="1" style="width:auto;margin-top:5px">'
    '<span class="sm muted" style="flex:1">These contacts opted in at my POS, on my '
    'website or through my loyalty program. I&rsquo;m confirming that as the venue '
    '&mdash; exports don&rsquo;t carry consent, and under the Spam Act a card payment '
    'is not consent.</span></div>')


def _upload_limit_html(own_line=False):
    style = ' style="display:block;margin-top:6px"' if own_line else ""
    return (f'<span class="sm soft"{style}>Up to {SEC.max_upload_bytes() // (1024 * 1024)} MB '
            'in total. A bigger export? Pick a shorter date range in Square: the '
            'last two years is plenty.</span>')


def _import_report_html():
    rep = STATE.get("import_report")
    if not rep:
        return ""
    out = ['<div class="note"><b>Import report.</b> '
           f'{rep.get("orders", 0)} visits read into {rep.get("customers", 0)} customers '
           f'and a {rep.get("menu_items", 0)}-item menu. '
           + (f'{rep["with_email"]} of them have an email address. '
              if rep.get("mode") == "square" and "with_email" in rep else "")
           + (f'Fields found: {", ".join(rep.get("fields_found", []))}. '
              if rep.get("fields_found") else "")
           + (f'Not present in the file: {", ".join(rep.get("fields_missing", []))} &mdash; '
              'triggers that need those fields are asleep for this dataset. '
              if rep.get("fields_missing") else "")
           + (f'{rep.get("skipped_rows", 0)} rows skipped (no date, email or recognisable '
              'items). ' if rep.get("skipped_rows") else "")
           + (f'{rep["ambiguous_orders"]} visits were left unlinked because two customers '
              'share that name. ' if rep.get("ambiguous_orders") else "")
           + (f'{rep.get("unmatched", 0)} line items didn&rsquo;t match any other line and '
              'were priced from the file where possible. ' if rep.get("unmatched") else "")
           + 'Imports live in memory only &mdash; restarting the app clears them, by design.']
    if rep.get("warning"):
        out.append(f'<div class="warn"><b>Heads up.</b> {e(rep["warning"])}</div>')
    out.append('</div>')
    return "".join(out)


def page_setup():
    if venue_instance():
        return page_venue_setup()
    r = result()
    s = r["summary"]
    g = STATE["goals"]
    conn = STATE["connected"]
    ds = dataset()
    pre = _flash_html() + _security_banner() + _business_card() + _plan_card()

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

        if demo_instance():
            # Uploads sit in memory, shared by everyone signed in: a real
            # customer file put here would show to the next visitor.
            body.append(f'<div class="card"><h3>Your own data</h3>'
                        f'<p class="sm muted">{e(DEMO_NO_UPLOAD)}</p></div>')
        else:
            body.append('<div class="card"><h3>Or upload exports</h3>'
                        '<p class="sm muted">Prefer not to connect anything? Upload what you '
                        'already have. Two shapes are accepted: your Square dashboard exports '
                        '(transactions plus, optionally, the customer directory), or our simple '
                        'one-row-per-visit format. Column names are matched tolerantly and '
                        'you&rsquo;ll get a report of what was found and what wasn&rsquo;t.</p>'
                        '<form method="post" action="/upload" enctype="multipart/form-data">'
                        '<div class="filerow"><div class="field"><label>Square transactions / '
                        'item sales CSV &mdash; or the simple format</label>'
                        '<input type="file" name="f_main" accept=".csv,text/csv">'
                        + _upload_limit_html() + '</div>'
                        '<div class="field"><label>Square customer directory CSV '
                        '(optional)</label>'
                        '<input type="file" name="f_cust" accept=".csv,text/csv"></div>'
                        + CONSENT_HTML + '</div>'
                        '<div class="row between"><span class="sm muted">No file handy? Try a '
                        'sample: <a href="/sample/simple.csv">simple format</a> &middot; '
                        '<a href="/sample/square-transactions.csv">Square transactions</a> '
                        '&middot; '
                        '<a href="/sample/square-customers.csv">Square customers</a></span>'
                        '<button class="btn" type="submit">Read the file</button></div></form>'
                        '</div>')

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

    body.append(_import_report_html())

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


# ---------------------------------------------------------------- venue checklist
# A venue's own instance: from the first sign-in to the first export, one
# step at a time. Each step knows from the state whether it's done; the first
# one that isn't is "Next", and its section comes first on the page.

PAID = ("active", "due_soon", "grace")


def _nice_ts(iso):
    try:
        return BILL.nice(date.fromisoformat(str(iso)[:10]))
    except (TypeError, ValueError):
        return ""


def _steps(r):
    g = STATE["goals"]
    biz = STATE["business"]
    up = not on_example()
    ds = dataset()
    tool = STATE["sender"]["tool"]
    miles = STATE["milestones"]
    steps = []

    if business_complete():
        legal = (f'ABN {biz["abn"]}' if STATE.get("region", "AU") != "US"
                 else STATE.get("venue_address", ""))
        st = f'{e(biz["name"])}, {e(biz["suburb"])} &middot; {e(legal)}'
    else:
        st = ("Your business name and ABN sign every message and fill its legal footer. "
              "Approving and exporting stay off until they&rsquo;re saved.")
    steps.append({"key": "business", "title": "Your business", "done": business_complete(),
                  "status": st, "href": "#business", "cta": "Edit" if business_complete()
                  else "Add them"})

    if up:
        st = (f'{ds["stats"]["customers"]:,} customers and {ds["stats"]["orders"]:,} orders '
              f'read from your export.')
    elif miles.get("first_upload"):
        st = ("Regulars doesn&rsquo;t keep your customer list, so upload your latest export "
              "for this batch.")
    else:
        st = "Two files from your Square Dashboard. The steps are below."
    steps.append({"key": "upload", "title": "Upload your Square export", "done": up,
                  "status": st, "href": "#upload", "cta": "Upload a new one" if up
                  else "Upload"})

    saved = bool(miles.get("goals_saved"))
    pushing = [x["label"] for x in g["push_times"]] + list(g["push_products"])
    if saved:
        st = ("Pushing " + e(", ".join(pushing)) + "." if pushing
              else "Saved: aiming for repeat visits.") + " Offers confirmed."
    else:
        st = ("What you want more of, and the offers you&rsquo;ll give. Every message promises "
              "one, and the starting list is only a suggestion, so approving and exporting "
              "wait until you&rsquo;ve saved this screen once.")
    steps.append({"key": "goals", "title": "Your goals and offers", "done": saved,
                  "status": st, "href": "/goals", "cta": "Change" if saved
                  else "Check them"})

    read = up and bool(STATE.get("read_since_upload"))
    if read:
        st = "Read. Approve the ones you&rsquo;re happy with, or edit them first."
    elif up:
        st = (f'{r["summary"]["ready"]} are ready. Open a few to see each email and why that '
              f'person got it.')
    else:
        st = "Once your export is in."
    steps.append({"key": "read", "title": "Read your messages", "done": read, "status": st,
                  "href": "/inbox", "cta": "Read them"})

    chosen = tool != "none"
    st = (f'Sending from {e(ESP[tool][0])}.' if chosen else
          "Pick the email tool you already use, so the export carries its unsubscribe link.")
    steps.append({"key": "sender", "title": "Where they&rsquo;ll go out from", "done": chosen,
                  "status": st, "href": "/sending", "cta": "Change" if chosen else "Choose"})

    b = billing_state()
    if b["status"] != "off":
        paid = b["paid_until"]
        st = {"preview": "Free preview: read everything. Approving and exporting start once "
                         "your plan is paid.",
              "active": f"Paid up to {BILL.nice(paid)}." if paid else "",
              "due_soon": f"Paid up to {BILL.nice(paid)}. Renew to keep sending."
                          if paid else "",
              "grace": f"Ended on {BILL.nice(paid)}. Renew to keep sending." if paid else "",
              "lapsed": f"Ended on {BILL.nice(paid)}, so sending is paused." if paid else "",
              }[b["status"]]
        steps.append({"key": "plan", "title": "Your plan", "done": b["status"] in PAID,
                      "status": e(st), "href": "#plan",
                      "cta": "Renew" if b["status"] in PAID else "Pay"})

    exported = bool(miles.get("first_export"))
    st = (f'First batch exported on {_nice_ts(miles["first_export"])}.' if exported else
          "Download the CSV and import it into your email tool. Leave out the rows marked "
          "Holdout: they&rsquo;re how you&rsquo;ll see whether this worked.")
    steps.append({"key": "export", "title": "Export your first batch", "done": exported,
                  "status": st, "href": "/export.csv", "cta": "Download the CSV"})
    return steps


def _next_step(steps):
    return next((s["key"] for s in steps if not s["done"]), None)


def _checklist_html(steps, nxt):
    rows = []
    for n, s in enumerate(steps, 1):
        icon = ('<div class="ic">&#10003;</div>' if s["done"]
                else f'<div class="ic {"next" if s["key"] == nxt else "todo"}">{n}</div>')
        tag = (' <span class="pill p-acc" style="margin-left:10px">Next</span>'
               if s["key"] == nxt else "")
        rows.append(f'<div class="chk">{icon}<div class="t"><b>{s["title"]}{tag}</b>'
                    f'<span>{s["status"]} <a href="{s["href"]}">{e(s["cta"])} &rarr;</a>'
                    f'</span></div></div>')
    return '<h2>Your checklist</h2><div class="card tight">' + "".join(rows) + '</div>'


def _venue_upload_html():
    miles = STATE["milestones"]
    restart = ('<div class="warn" style="margin-top:0"><b>Upload again.</b> Regulars '
               'doesn&rsquo;t keep your customer list: it lives in memory and is gone after a '
               'restart. Your settings, approvals and sent history are all kept.</div>'
               if on_example() and miles.get("first_upload") else "")
    how = "".join(f'<li><b>{t}</b><span>{h}</span></li>' for t, h in E.PACK.EXPORT_STEPS)
    return (
        '<h2 id="upload">Upload your Square export</h2><div class="card">'
        f'{restart}'
        '<p class="sm muted" style="margin:0">In your Square Dashboard:</p>'
        f'<ol class="steps" style="margin-top:6px">{how}</ol>'
        '<form method="post" action="/upload" enctype="multipart/form-data">'
        '<div class="filerow"><div class="field"><label>1. Items Detail CSV (your sales)'
        '</label><input type="file" name="f_main" accept=".csv,text/csv">'
        + _upload_limit_html(own_line=True) + '</div>'
        '<div class="field"><label>2. Customer export (where the email addresses are)</label>'
        '<input type="file" name="f_cust" accept=".csv,text/csv">'
        '<span class="sm soft" style="display:block;margin-top:6px">Without it, Regulars can '
        'read what people order but has nobody to write to.</span></div>'
        + CONSENT_HTML + '</div>'
        '<div class="row between"><span class="sm muted" style="flex:1;min-width:240px">'
        'Regulars reads the files in memory and never writes your customer list to disk. '
        'Upload a fresh export for each batch.</span>'
        '<button class="btn" type="submit">Read the files</button></div></form>'
        '<p class="sm soft" style="margin-top:22px">Square moves its menus around from time to '
        'time. If the import report says something&rsquo;s missing, '
        + (f'email <a href="mailto:{e(billing_state()["support"])}">'
           f'{e(billing_state()["support"])}</a>.' if billing_state()["support"]
           else 'ask whoever set up Regulars for you.')
        + ' To see the layout first: <a href="/sample/square-transactions.csv">sample Items '
        'Detail</a> &middot; <a href="/sample/square-customers.csv">sample customer export</a>'
        '.</p></div>')


def _next_cta(nxt, r):
    tool = STATE["sender"]["tool"]
    if nxt == "goals":
        return ('<p style="margin-top:34px"><a class="btn big" href="/goals">Check your goals '
                'and offers &rarr;</a></p>')
    if nxt == "read":
        return (f'<p style="margin-top:34px"><a class="btn big" href="/inbox">Read your '
                f'{r["summary"]["ready"]} messages &rarr;</a></p>')
    if nxt == "sender":
        return ('<p style="margin-top:34px"><a class="btn big" href="/sending">Choose where '
                'they go out from &rarr;</a></p>')
    if nxt == "export":
        return ('<p style="margin-top:34px"><a class="btn big" href="/export.csv">Download the '
                f'CSV for {e(ESP[tool][0])}</a> <a class="btn ghost" href="/export-copy.txt">'
                'Copy as plain text</a></p>')
    return ""


def page_venue_setup():
    r = result()
    s = r["summary"]
    steps = _steps(r)
    nxt = _next_step(steps)
    up = not on_example()
    miles = STATE["milestones"]

    if up and nxt is None:
        head, lead = ("You&rsquo;re set.",
                      f'<b>{s["ready"]} messages</b> are ready, each written for one person. '
                      f'Each month: export from Square, upload, read, export.')
    elif up:
        ds = dataset()
        head, lead = ("Your messages are ready.",
                      f'{ds["stats"]["customers"]:,} customers and '
                      f'{ds["stats"]["orders"]:,} orders read. <b>{s["ready"]} messages</b> '
                      f'written, each for exactly one person.')
    elif miles.get("first_upload"):
        head, lead = ("Upload your latest export.",
                      "Regulars doesn&rsquo;t keep your customer list, so it&rsquo;s gone after "
                      "a restart. Upload a fresh Square export for this batch: your settings, "
                      "approvals and sent history are all kept.")
    else:
        head, lead = ("Welcome. Let&rsquo;s write your first messages.",
                      "Regulars reads your Square sales, works out what each customer actually "
                      "does, and writes each one the message that fits. Work down the "
                      "checklist: about ten minutes, and nothing goes anywhere until you export "
                      "it.")
    body = [f'<h1>{head}</h1><p class="lead">{lead}</p>', _flash_html(), _import_report_html(),
            _checklist_html(steps, nxt)]
    if not up and not miles.get("first_upload"):
        body.append('<p class="sm muted" style="margin-top:18px">Want to look around first? '
                    f'<a href="/inbox">Browse the messages Regulars wrote for '
                    f'{e(E.PACK.WORDS["example"])}</a>.</p>')

    sections = {"business": '<div id="business">' + _business_card() + '</div>',
                "upload": _venue_upload_html()}
    plan = _plan_card()
    if plan:
        sections["plan"] = '<div id="plan">' + plan + '</div>'
    if nxt in sections:
        body.append(sections.pop(nxt))
    else:
        body.append(_next_cta(nxt, r))

    if up:
        body.append('<h2>This batch</h2><div class="grid g3" style="margin-top:34px">')
        body.append(stat("Ready to send", f'{s["ready"]}', "each written for that person"))
        body.append(stat("Not contacted", f'{s["suppressed"] + s["no_match"]}', "on purpose"))
        body.append(stat("Distinct subjects", f'{s["unique_subjects"]}',
                         f'of {s["ready"]} messages'))
        body.append('</div>')
    for key in ("business", "upload", "plan"):
        if key in sections:
            body.append(sections[key])
    if up:
        body.append('<h2>Who we deliberately did not contact</h2>')
        body.append(_suppression_card(r))
    body.append('<form method="post" action="/reset" style="margin-top:70px">'
                '<button class="btn ghost sm" type="submit">Start over</button> '
                '<span class="sm soft">Clears goals, triggers, sender settings and the sent '
                'history. Your password, business details and plan are kept.</span></form>')
    return page("Setup", "".join(body), "/")


def get_setup(req):
    return html(page_setup())


def post_connect(req):
    if venue_instance():
        # On a venue's own instance this demo button would swap their upload
        # for the example restaurant. Square OAuth lives on Sending.
        flash("Connecting Square directly isn't switched on yet. Upload your Square export "
              "instead: the steps are on this page.")
        return redirect("/")
    STATE["connected"] = True
    STATE["source"] = "demo"
    STATE["dataset"] = None
    STATE["import_report"] = None
    persist()
    AUDIT.write("config_change", ip=req.ip,
                detail={"what": "connect_demo_square"})
    return redirect("/goals")


def post_upload(req):
    """Parsing a big CSV can take a while, so it runs before taking the state
    lock; only the swap-in of the parsed dataset is serialised."""
    if demo_instance():
        flash(DEMO_NO_UPLOAD)
        return redirect("/")
    ds, rep = do_import(req.fields, req.files, STATE.get("region", "AU"))
    with LOCK:
        if ds is None:
            STATE["import_report"] = rep
            STATE["connected"] = False
            AUDIT.write("upload", ip=req.ip,
                        detail={"ok": False, "error": str(rep.get("error", ""))[:160]})
            return redirect("/")
        STATE["dataset"] = ds
        STATE["import_report"] = rep
        STATE["connected"] = True
        STATE["source"] = "csv"
        STATE["read_since_upload"] = False
        mark("first_upload")
        AUDIT.write("upload", ip=req.ip,
                    detail={"ok": True, "mode": rep.get("mode"),
                            "customers": rep.get("customers"),
                            "orders": rep.get("orders"),
                            "attested": bool(rep.get("attested"))})
        return redirect("/")


def post_reset(req):
    keep = {k: STATE.get(k) for k in ("password_hash", "setup_burned", "billing_seen",
                                      "milestones", "sessions", "region", "venue_address",
                                      "business", "oauth_tokens_enc", "oauth_pending",
                                      "schema")}
    STATE.update(fresh_state())
    STATE.update({k: v for k, v in keep.items() if v is not None})
    STATE["milestones"]["goals_saved"] = None    # the offers are back to the suggestions
    AUDIT.write("reset", ip=req.ip,
                detail={"kept": "auth, market, business, tokens", "version": VERSION})
    persist()
    return redirect("/")


def post_region(req):
    reg = req.one("region", "")
    if reg not in ("AU", "US"):
        reg = STATE.get("region", "AU")
    addr = (req.one("venue_address", "") or "").strip()[:200]
    if reg == "US" and not addr and not STATE.get("venue_address"):
        flash("The US market requires a physical postal address in every "
              "commercial email (CAN-SPAM 15 U.S.C. 7704). Add the address and "
              "save again - nothing was changed.")
        return redirect("/")
    STATE["region"] = reg
    if addr:
        STATE["venue_address"] = addr
    AUDIT.write("config_change", ip=req.ip,
                detail={"what": "region", "region": reg})
    persist()
    flash(f"Market set to {'United States' if reg == 'US' else 'Australia'}. "
          f"Footers, season references and spelling now follow that market's rules.")
    return redirect("/")


def _clean(text, limit):
    """One line, single spaces, bounded - it goes into every email."""
    return " ".join(str(text or "").split())[:limit]


def post_business(req):
    """Save who the messages come from. All-or-nothing: if anything required
    is missing or wrong, nothing changes and the owner is told what."""
    reg = req.one("region", "")
    if reg not in ("AU", "US"):
        reg = STATE.get("region", "AU")
    name = _clean(req.one("biz_name"), 80)
    suburb = _clean(req.one("biz_suburb"), 60)
    abn_raw = req.one("biz_abn", "") or ""
    addr = _clean(req.one("venue_address"), 200)
    abn_fmt = ABN.normalise(abn_raw) if abn_raw.strip() else None

    problems = []
    if len(name) < 2:
        problems.append("your business name")
    if len(suburb) < 2:
        problems.append("your suburb or city")
    if reg == "AU" and not abn_fmt:
        problems.append("a valid ABN" + (" (that number fails the ABN check digit - look "
                                         "for a typo, or check it on abr.business.gov.au)"
                                         if abn_raw.strip() else ""))
    if reg == "US" and not addr and not STATE.get("venue_address"):
        problems.append("a physical postal address (CAN-SPAM requires one in every "
                        "commercial email)")
    if problems:
        flash("Business details not saved. Still needed: " + "; ".join(problems) + ".")
        return redirect("/")

    STATE["business"] = {"name": name, "suburb": suburb,
                         "abn": abn_fmt or (STATE["business"].get("abn", "")
                                            if reg == "US" else "")}
    STATE["region"] = reg
    if addr:
        STATE["venue_address"] = addr
    persist()
    AUDIT.write("config_change", ip=req.ip,
                detail={"what": "business", "region": reg, "name": name,
                        "abn_set": bool(STATE["business"]["abn"])})
    flash(f"Saved. Every message is now signed by {name}, and the footer follows "
          f"{'United States' if reg == 'US' else 'Australian'} rules.")
    return redirect("/")
