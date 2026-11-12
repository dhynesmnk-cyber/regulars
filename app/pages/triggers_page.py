"""Triggers screen: rule switches and threshold tuning."""

import engine as E
from state import AUDIT, CAT_LABEL, STATE, persist, result, th
from ui import e, page, stat
from web import html, redirect


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


def get_triggers(req):
    return html(page_triggers())


def post_triggers(req):
    STATE["enabled"] = set(req.form.get("t", []))
    for key, label, help_txt, step in E.THRESHOLD_FIELDS:
        v = req.one(f"th_{key}")
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
    AUDIT.write("config_change", ip=req.ip,
                detail={"what": "triggers",
                        "enabled": len(STATE["enabled"]),
                        "tuned": len(STATE["thresholds"])})
    return redirect("/triggers")


def post_tuning_reset(req):
    STATE["thresholds"] = {}
    persist()
    AUDIT.write("config_change", ip=req.ip, detail={"what": "tuning_reset"})
    return redirect("/triggers")
