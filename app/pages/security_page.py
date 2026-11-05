"""Security screen: auth state, guards, credentials, audit-chain verification."""

import json
import os
from datetime import datetime, timezone

import oauth as OAUTH
import security as SEC
from state import (AUDIT, BUILD_DATE, GUARD, HERE, SCHEMA, STATE, VERSION, _load_tokens,
                   business_complete, demo_mode, expire_cookie, flash, kill_sessions,
                   setup_token)
from ui import e, page, stat
from views import _flash_html
from web import download, html, redirect


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
    checks = [
        ("Operator password set", not demo_mode()),
        ("Served over HTTPS (set REGULARS_TLS=1 behind your proxy to add HSTS)",
         bool(os.environ.get("REGULARS_TLS"))),
        ("Square OAuth application configured", OAUTH.configured()),
        ("Client addresses taken from your reverse proxy (REGULARS_TRUST_PROXY), so "
         "lockouts hit the attacker, not everyone", SEC.trusted_proxy_hops() > 0),
        ("Master key supplied via environment, not auto-keyfile",
         bool(os.environ.get("REGULARS_MASTER_KEY"))),
        ("Business name and " + ("postal address" if STATE.get("region") == "US" else "ABN")
         + " set - they sign every message", business_complete()),
        ("US market: postal address recorded" if STATE.get("region") == "US"
         else "AU market selected (no postal-address requirement)",
         STATE.get("region") != "US" or bool(STATE.get("venue_address"))),
    ]
    if setup_token():
        checks.insert(1, ("Claimed by its owner through the one-time setup link",
                          bool(STATE.get("setup_burned")) and not demo_mode()))
    for label, done in checks:
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


def get_security(req):
    return html(page_security())


def get_audit_log(req):
    AUDIT.write("export", ip=req.ip, detail={"what": "audit_log"})
    try:
        with open(AUDIT.path, "rb") as f:
            raw = f.read()
    except OSError:
        raw = b""
    return download(raw, "text/plain; charset=utf-8", "regulars-audit.log")


def post_verify(req):
    ok, broken_at, lines = AUDIT.verify()
    AUDIT.write("audit_verify", ip=req.ip,
                detail={"ok": ok, "broken_at": broken_at, "lines": lines})
    if ok:
        flash(f"Audit chain verified: all {lines} entries intact.")
    else:
        flash(f"CHAIN BROKEN at entry {broken_at}: the audit log was edited or "
              f"corrupted. Treat this as a security incident.")
    return redirect("/security")


def post_logout_all(req):
    kill_sessions()
    AUDIT.write("logout", ip=req.ip, detail={"what": "all_sessions_revoked"})
    return redirect("/login", cookie=expire_cookie())
