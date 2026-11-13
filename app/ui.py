"""Presentation: editorial and spare. Hairlines instead of cards, serif display
type, generous air, no rounded corners, no shadows, no gradients. One accent,
used rarely."""

import html as _h
import os

ACCENT = "#8a3413"   # rust - used for rules, active states, primary buttons only
INK = "#191714"
PAPER = "#fbfaf7"
HAIR = "#ddd6cb"

def e(s):
    return _h.escape(str(s if s is not None else ""))


def render(tpl, **ctx):
    for k, v in ctx.items():
        tpl = tpl.replace("{{" + k + "}}", "" if v is None else str(v))
    return tpl


CSS = f"""
:root{{--acc:{ACCENT};--ink:{INK};--paper:{PAPER};--hair:{HAIR};
--mid:#6b6459;--soft:#9c948a;--good:#3f6b3f;--bad:#8c2f2f;--warn:#8a6116;
--tint:#f6f2ea}}
*{{box-sizing:border-box}}
html{{-webkit-text-size-adjust:100%}}
body{{margin:0;background:var(--paper);color:var(--ink);
font:17px/1.75 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
-webkit-font-smoothing:antialiased}}
a{{color:var(--acc);text-decoration:none;border-bottom:1px solid transparent}}
a:hover{{border-bottom-color:var(--acc)}}
.wrap{{max-width:760px;margin:0 auto;padding:0 28px 140px}}
header{{border-bottom:1px solid var(--hair);background:var(--paper)}}
.hin{{max-width:760px;margin:0 auto;padding:30px 28px 0}}
.logo{{font-size:15px;letter-spacing:.22em;text-transform:uppercase}}
.logo b{{font-weight:600;color:var(--ink)}}
.logo small{{display:block;font-size:12px;letter-spacing:.06em;text-transform:none;
color:var(--soft);font-style:italic;margin-top:3px}}
nav{{display:flex;gap:26px;flex-wrap:wrap;margin:26px 0 0;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
nav a{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mid);
padding:0 0 14px;border-bottom:2px solid transparent}}
nav a:hover{{color:var(--ink);border-bottom-color:var(--hair)}}
nav a.on{{color:var(--ink);border-bottom-color:var(--acc);font-weight:600}}
h1{{font-size:40px;line-height:1.15;font-weight:600;letter-spacing:-.01em;
margin:72px 0 18px}}
h2{{font-size:13px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;
color:var(--soft);margin:76px 0 0;padding-bottom:12px;border-bottom:1px solid var(--hair)}}
h3{{font-size:19px;font-weight:600;margin:0 0 10px;letter-spacing:-.01em}}
p{{margin:0 0 18px}}
.lead{{font-size:20px;line-height:1.65;color:var(--mid);margin:0 0 44px;max-width:34em}}
.lead b{{color:var(--ink);font-weight:600}}
.card{{border:0;border-top:1px solid var(--hair);padding:34px 0;margin:0}}
.card.tight{{padding:26px 0}}
.row{{display:flex;gap:20px;flex-wrap:wrap;align-items:center}}
.between{{justify-content:space-between}}
.grid{{display:grid;gap:44px}}
.g2{{grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:44px 56px}}
.g3{{grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:40px 44px}}
.g4{{grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:36px}}
.stat{{border-top:1px solid var(--ink);padding-top:14px}}
.stat .l{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--soft);font-weight:600}}
.stat .v{{font-size:44px;font-weight:600;letter-spacing:-.02em;line-height:1.1;margin:6px 0 2px;
font-variant-numeric:tabular-nums}}
.stat .n{{color:var(--mid);font-size:14px;font-style:italic}}
.btn{{display:inline-block;background:var(--ink);color:var(--paper);border:1px solid var(--ink);
padding:13px 26px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
font-size:12px;letter-spacing:.14em;text-transform:uppercase;font-weight:600;cursor:pointer;
border-radius:0}}
.btn:hover{{background:var(--acc);border-color:var(--acc);color:#fff}}
.btn.ghost{{background:transparent;color:var(--ink);border-color:var(--hair)}}
.btn.ghost:hover{{border-color:var(--ink);background:transparent;color:var(--ink)}}
.btn.big{{padding:17px 34px;font-size:13px}}
.btn.sm{{padding:8px 16px;font-size:11px}}
.btn.green{{background:var(--good);border-color:var(--good);color:#fff}}
.btn.green:hover{{filter:brightness(1.1);background:var(--good)}}
.pill{{display:inline-block;padding:3px 0;margin-right:14px;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;font-weight:600;color:var(--mid)}}
.p-acc{{color:var(--acc)}} .p-good{{color:var(--good)}} .p-bad{{color:var(--bad)}}
.p-warn{{color:var(--warn)}} .p-mute{{color:var(--soft)}}
table{{width:100%;border-collapse:collapse;font-size:15.5px}}
th{{text-align:left;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--soft);font-weight:600;
padding:10px 12px 10px 0;border-bottom:1px solid var(--ink)}}
td{{padding:16px 12px 16px 0;border-bottom:1px solid var(--hair);vertical-align:top}}
td.r,th.r{{text-align:right;font-variant-numeric:tabular-nums}}
.note,.warn,.good,.bad{{border:0;border-left:2px solid var(--acc);background:transparent;
padding:4px 0 4px 22px;margin:34px 0;font-size:16px}}
.warn{{border-left-color:var(--warn)}} .bad{{border-left-color:var(--bad)}}
.good{{border-left-color:var(--good)}}
.mono,code{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
code{{font-size:.9em;background:var(--tint);padding:1px 5px}}
.steps{{counter-reset:s;list-style:none;padding:0;margin:34px 0}}
.steps li{{counter-increment:s;position:relative;padding:20px 0 20px 58px;
border-bottom:1px solid var(--hair)}}
.steps li:last-child{{border:0}}
.steps li:before{{content:counter(s,decimal-leading-zero);position:absolute;left:0;top:22px;
font-size:13px;color:var(--acc);letter-spacing:.08em;font-weight:600}}
.steps li>b{{display:block;font-size:17px;font-weight:600;margin-bottom:4px}}
.steps li span{{color:var(--mid);font-size:15.5px}}
details.msg{{border:0;border-bottom:1px solid var(--hair);margin:0}}
details.msg:first-of-type{{border-top:1px solid var(--hair)}}
details.msg summary{{cursor:pointer;padding:22px 0;list-style:none;display:flex;gap:18px;
align-items:baseline;flex-wrap:wrap}}
details.msg summary::-webkit-details-marker{{display:none}}
details.msg summary:hover .subj{{color:var(--ink)}}
details.msg[open] summary{{padding-bottom:14px}}
.who{{font-weight:600;font-size:17px;min-width:120px}}
.subj{{color:var(--mid);font-size:16px;flex:1;font-style:italic}}
.mbody{{padding:6px 0 34px}}
.email{{background:var(--tint);border:0;border-left:2px solid var(--hair);padding:26px 28px;
white-space:pre-wrap;font-size:15.5px;line-height:1.8;
font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif}}
.why{{background:transparent;border-left:2px solid var(--acc);padding:4px 0 4px 22px;
font-size:15px;color:var(--mid);margin:26px 0 0}}
.why b{{color:var(--ink)}}
.ev{{display:flex;gap:40px;flex-wrap:wrap;margin:26px 0;padding:18px 0;
border-top:1px solid var(--hair);border-bottom:1px solid var(--hair)}}
.ev div{{min-width:96px}}
.ev .l{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:10px;
letter-spacing:.16em;text-transform:uppercase;color:var(--soft);font-weight:600}}
.ev .v{{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}}
input[type=text],input[type=number],input[type=password],select,textarea{{
background:transparent;border:0;border-bottom:1px solid var(--hair);border-radius:0;
padding:9px 2px;font:16px/1.5 "Iowan Old Style",Palatino,Georgia,serif;color:var(--ink);width:100%}}
input:focus,select:focus,textarea:focus{{outline:0;border-bottom-color:var(--acc)}}
textarea{{border:1px solid var(--hair);padding:16px;line-height:1.7}}
label{{display:block;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--soft);
font-weight:600;margin-bottom:7px}}
.field{{margin-bottom:30px}}
.chk{{display:flex;gap:16px;align-items:flex-start;padding:18px 0;border-bottom:1px solid var(--hair)}}
.chk:last-child{{border:0}}
.chk .ic{{width:22px;height:22px;flex:0 0 22px;display:flex;align-items:center;
justify-content:center;font-size:12px;font-weight:700;padding:0;margin:3px 0 0;
border:1px solid var(--good);color:var(--good)}}
.ic.warn{{border-color:var(--warn);color:var(--warn)}} .ic.block{{border-color:var(--bad);color:var(--bad)}}
.ic.todo{{border-color:var(--hair);color:var(--soft)}} .ic.next{{border-color:var(--acc);color:var(--acc)}}
.chk .t b{{display:block;font-size:16.5px;font-weight:600}}
.chk .t span{{color:var(--mid);font-size:15px}}
.chk .t i{{color:var(--soft);font-size:13px;font-style:italic;display:block;margin-top:3px}}
.big{{font-size:56px;font-weight:600;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1}}
.muted{{color:var(--mid)}} .soft{{color:var(--soft)}} .sm{{font-size:14.5px}}
.foot{{color:var(--soft);font-size:13px;font-style:italic;border-top:1px solid var(--hair);
margin-top:90px;padding-top:22px}}
.bar{{height:2px;background:var(--hair);margin-top:8px}}
.bar i{{display:block;height:100%;background:var(--acc)}}
.trig{{display:flex;gap:26px;align-items:flex-start;padding:26px 0;border-bottom:1px solid var(--hair)}}
.trig:last-child{{border:0}}
.trig .n{{flex:0 0 54px;text-align:right}}
.trig .n b{{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}}
.trig .n span{{display:block;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
color:var(--soft);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
.trig.off{{opacity:.38}}
.dot{{width:7px;height:7;border-radius:50%;display:inline-block;margin-right:8px}}
input[type=checkbox],input[type=radio]{{width:15px;height:15px;accent-color:var(--acc)}}
.filerow{{border:1px dashed var(--hair);padding:26px;margin:26px 0}}
.filerow:hover{{border-color:var(--acc)}}
"""

NAV = [
    ("/", "Setup"),
    ("/goals", "Goals"),
    ("/triggers", "Triggers"),
    ("/inbox", "Messages"),
    ("/sending", "Sending"),
    ("/sent", "Sent"),
    ("/data", "Data"),
    ("/security", "Security"),
]

def hidden_nav():
    """Menu entries to leave out. The public demo hides the owner-only Security
    screen; state.py replaces this."""
    return set()


LAYOUT = """<!doctype html><html lang="{{lang}}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{title}} &middot; Regulars</title><style>{{css}}</style></head><body>
<header><div class="hin">
<div class="logo"><b>Regulars</b><small>{{venue}}</small></div>
<nav>{{nav}}</nav></div></header>
<div class="wrap">{{body}}
<div class="foot">{{foot}}</div></div></body></html>"""


# The business named in the header. state.py points this at the saved
# business identity, so this module never needs to know about settings.
def venue_label():
    return ""


# Banners for the top of every signed-in page (billing reminders, "this is
# the example data"), given the menu item the page sits under. views.py
# points this at the real state.
def notice(active):
    return ""


# The line at the foot of every signed-in page. views.py fills it in.
def footer():
    return "Regulars"


# The page language, from the venue's market. state.py points this at the
# saved region; it reads as Australian until one is chosen.
def lang():
    return "en-AU"


def _legal_url(var):
    """An https address from the environment, or "" if it isn't a plain one.
    A typo can't turn into a javascript: or odd-scheme link."""
    url = (os.environ.get(var) or "").strip()
    if not url.startswith("https://") or len(url) <= len("https://"):
        return ""
    if any(c.isspace() or c in '"\'<>`\\' or ord(c) < 32 for c in url):
        return ""
    return url


def legal_links():
    """'Terms - Privacy' for the foot of every page, from REGULARS_TERMS_URL
    and REGULARS_PRIVACY_URL (the same for every venue; set them in the
    shared settings). Each shows only when its address is usable."""
    links = [f'<a href="{e(url)}" target="_blank" rel="noopener">{label}</a>'
             for var, label in (("REGULARS_TERMS_URL", "Terms"),
                                ("REGULARS_PRIVACY_URL", "Privacy"))
             for url in [_legal_url(var)] if url]
    return " &middot; ".join(links)


def _foot(text):
    links = legal_links()
    return f"{text}<br>{links}" if links else text


def page(title, body, active="/", venue=None, bare=False):
    """bare: no business name and no menu - for screens shown before anyone
    has signed in, where the menu would only lead to closed doors."""
    if bare:
        return render(LAYOUT, title=e(title), css=CSS, nav="", body=body, venue="",
                      foot=_foot("Regulars"), lang=lang())
    body = notice(active) + body
    hide = hidden_nav()
    nav = "".join(f'<a href="{h}" class="{"on" if h == active else ""}">{e(l)}</a>'
                  for h, l in NAV if h not in hide)
    return render(LAYOUT, title=e(title), css=CSS, nav=nav, body=body, foot=_foot(footer()),
                  venue=venue if venue is not None else venue_label(), lang=lang())


def stat(label, value, note=""):
    return (f'<div class="stat"><div class="l">{e(label)}</div>'
            f'<div class="v">{value}</div><div class="n">{note}</div></div>')
