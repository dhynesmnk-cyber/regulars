"""Sending screen: sender choice, SMTP settings and test, Square OAuth."""

import secrets
import smtplib
from datetime import datetime, timezone

import oauth as OAUTH
from state import (AUDIT, ESP, LOCK, STATE, _load_tokens, _store_tokens, flash, identity,
                   persist, public_demo)
from ui import e, page
from views import _flash_html
from web import html, redirect


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
    if public_demo():
        body.append('<div class="card"><p class="sm muted">Connecting Square directly is switched '
                    'off in the demo. Upload a Square export on Setup instead: it needs no '
                    'credentials at all.</p></div>')
    elif STATE.get("oauth_tokens_enc"):
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

    if not public_demo():     # no server details, no connection tests in the demo
        body.append('<h2>If you send through your own SMTP</h2>')
        body.append('<p class="sm muted" style="margin-top:18px">Stored locally in this app&rsquo;s '
                    'config file so real sending can be switched on later. The prototype never '
                    'transmits anything &mdash; the test button below only checks that the details '
                    'work.</p><div class="card"><div class="grid g2">')
        for key, lab, ph, typ in [("smtp_host", "Host", "smtp.example.com", "text"),
                                  ("smtp_port", "Port", "587", "number"),
                                  ("smtp_user", "Username", "campaigns@yourvenue.com.au", "text"),
                                  ("smtp_pass", "Password / app password", "", "password"),
                                  ("from_addr", "From address", f"{identity()['name']} <hello@...>", "text"),
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
                f'<div class="row"><a class="btn" href="/inbox">Approve and export on Messages'
                f'</a><a class="btn ghost" href="/sent">Earlier batches</a></div></div>')
    body.append(f'<div class="row between" style="margin-top:44px"><span></span>'
                f'<button class="btn big" type="submit">Save sending setup</button></div>')
    body.append('</form>')
    return page("Sending", "".join(body), "/sending")


def get_sending(req):
    return html(page_sending())


def _read_smtp_fields(req, sd):
    for key in ("smtp_host", "smtp_user", "smtp_pass", "from_addr", "reply_to"):
        sd[key] = req.one(key, sd.get(key, ""))
    try:
        sd["smtp_port"] = int(req.one("smtp_port", 587))
    except (TypeError, ValueError):
        pass


def post_sending(req):
    tool = req.one("tool", "none")
    if tool not in ESP:
        tool = "none"
    sd = STATE["sender"]
    sd["tool"] = tool
    if not public_demo():
        _read_smtp_fields(req, sd)     # the demo never stores server details
    tag = ESP[tool][1]
    STATE["goals"]["unsub_tag"] = tag or "{{unsubscribe_url}}"
    sd.pop("test_result", None)
    persist()
    AUDIT.write("config_change", ip=req.ip,
                detail={"what": "sending", "tool": tool})
    return redirect("/sending")


def post_sending_test(req):
    """The SMTP handshake can take up to the 8-second timeout, so it runs
    outside the state lock; only reading and writing the settings is locked."""
    with LOCK:
        sd = STATE["sender"]
        _read_smtp_fields(req, sd)
        host, port = sd.get("smtp_host"), sd.get("smtp_port") or 587
        user, password = sd.get("smtp_user"), sd.get("smtp_pass", "")
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
            if user:
                sm.login(user, password)
        outcome = {"ok": True, "msg": f"Connected and authenticated to {host}:{port}."}
    except Exception as ex:
        outcome = {"ok": False, "msg": f"{type(ex).__name__}: {ex}"}
    with LOCK:
        STATE["sender"]["test_result"] = outcome
    return redirect("/sending")


def get_oauth_start(req):
    if not OAUTH.configured():
        flash("Square OAuth is not configured on this server (SQUARE_CLIENT_ID / "
              "SQUARE_CLIENT_SECRET). The Sending screen explains the one-time setup.")
        return redirect("/sending")
    state = secrets.token_urlsafe(24)
    STATE.setdefault("oauth_pending", {})[state] = datetime.now(timezone.utc).timestamp()
    persist()
    AUDIT.write("oauth_start", ip=req.ip, detail={"redirect_uri": OAUTH.redirect_uri(),
                                                  "scopes": len(OAUTH.SCOPES)})
    return redirect(OAUTH.authorize_url(state))


def get_oauth_callback(req):
    q, ip = req.q, req.ip
    code = q.get("code", "")
    state = q.get("state", "")
    started = (STATE.get("oauth_pending") or {}).pop(state, None)
    persist()
    if q.get("error"):
        AUDIT.write("oauth_fail", ip=ip, detail={"error": q.get("error", "")[:120]})
        flash(f"Square declined the connection: {q.get('error')}.")
        return redirect("/sending")
    age = (datetime.now(timezone.utc).timestamp() - float(started)) if started else None
    if not code or started is None or age > 900:
        AUDIT.write("oauth_fail", ip=ip,
                    detail={"error": "state mismatch" if started is None
                            else "no code or authorization older than 15 minutes"})
        flash("That Square callback did not match a connection we started, or it "
              "arrived too late. Nothing was stored.")
        return redirect("/sending")
    try:
        tokens = OAUTH.exchange_code(code)
    except (OSError, ValueError) as ex:
        AUDIT.write("oauth_fail", ip=ip, detail={"error": str(ex)[:200]})
        flash(f"Square token exchange failed: {ex}")
        return redirect("/sending")
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
    return redirect("/")


def post_oauth_revoke(req):
    tokens = _load_tokens()
    revoked = bool(tokens) and OAUTH.revoke(tokens)
    _store_tokens(None)
    AUDIT.write("oauth_revoke", ip=req.ip,
                detail={"revoked_at_square": revoked,
                        "had_tokens": bool(tokens)})
    flash("Stored Square tokens deleted from this server."
          + (f" Revocation at Square: {'ok' if revoked else 'FAILED - revoke it in the Square Dashboard too'}."
             if tokens else ""))
    return redirect("/sending")
