"""Goals screen: times, products, priority, tone, offer catalogue."""

import re

import engine as E
from state import AUDIT, CAT_LABEL, STATE, dataset, mark, persist, result
from ui import e, page
from views import money
from web import html, redirect


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


def get_goals(req):
    return html(page_goals())


def post_goals(req):
    form, one = req.form, req.one
    g = STATE["goals"]
    mark("goals_saved")            # the owner has seen and confirmed their offers
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
    AUDIT.write("config_change", ip=req.ip,
                detail={"what": "goals",
                        "priority": g.get("priority"),
                        "tone": g.get("tone", "warm"),
                        "catalog": len(g.get("catalog") or [])})
    return redirect("/triggers")
