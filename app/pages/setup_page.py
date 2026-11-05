"""Setup screen: connect or upload, business details, onboarding, reset."""

import abn as ABN
import engine as E
from state import (AUDIT, ESP, LOCK, STATE, VERSION, dataset, flash, fresh_state, persist,
                   result)
from ui import e, page, stat
from upload import do_import
from views import _business_card, _flash_html, _security_banner, _suppression_card
from web import html, redirect


def page_setup():
    r = result()
    s = r["summary"]
    g = STATE["goals"]
    conn = STATE["connected"]
    ds = dataset()
    pre = _flash_html() + _security_banner() + _business_card()

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


def get_setup(req):
    return html(page_setup())


def post_connect(req):
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
    ds, rep = do_import(req.fields, req.files)
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
        AUDIT.write("upload", ip=req.ip,
                    detail={"ok": True, "mode": rep.get("mode"),
                            "customers": rep.get("customers"),
                            "orders": rep.get("orders"),
                            "attested": bool(rep.get("attested"))})
        return redirect("/")


def post_reset(req):
    keep = {k: STATE.get(k) for k in ("password_hash", "sessions", "region",
                                      "venue_address", "business", "oauth_tokens_enc",
                                      "oauth_pending", "schema")}
    STATE.update(fresh_state())
    STATE.update({k: v for k, v in keep.items() if v is not None})
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
