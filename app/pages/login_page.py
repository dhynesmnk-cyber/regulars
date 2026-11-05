"""Sign-in screen: first password, login, lockout, logout."""

from datetime import datetime, timezone

import security as SEC
from state import (AUDIT, GUARD, STATE, VERSION, cookie_for, create_session, demo_mode,
                   expire_cookie, flash, local_request_allowed, persist)
from ui import page
from views import _flash_html
from web import html, redirect, send


def page_login(ip):
    fl = _flash_html()
    if demo_mode():
        if not local_request_allowed(ip):
            body = ['<h1>Sign in</h1>',
                    '<div class="card" style="max-width:640px">'
                    '<p>No operator password is set, so this instance runs in <b>open demo '
                    'mode</b> &mdash; every screen is reachable without signing in. That is '
                    'fine for evaluating Regulars; it is not fine for a live venue.</p>'
                    '<p class="sm muted">To lock it down: open this page from the machine the '
                    'app runs on (localhost), or start the app with '
                    '<span class="mono">REGULARS_OPEN_FIRST_RUN=1</span> if you accept setting '
                    'the first password over the network. Behind TLS + a reverse proxy, do it '
                    'on day one.</p>'
                    '<p style="margin-top:20px"><a class="btn" href="/">Continue in demo '
                    'mode</a></p></div>']
            return page("Sign in", fl + "".join(body), "/login")
        body = ['<h1>Create the operator password</h1>',
                '<p class="lead">First run on this machine. This password gates every screen. '
                'There is no recovery path except deleting the state file on the server, so '
                'write it down somewhere safe.</p>',
                '<form method="post" action="/login"><div class="card" style="max-width:520px">'
                '<div class="field"><label>Password (10 characters or more)</label>'
                '<input type="password" name="password" minlength="10" required></div>'
                '<div class="field"><label>Repeat it</label>'
                '<input type="password" name="password2" minlength="10" required></div>'
                '<button class="btn big" type="submit" style="margin-top:10px">Set password '
                'and sign in</button>'
                '<p class="sm soft" style="margin-top:14px">Stored as a PBKDF2-SHA256 hash '
                '(210k iterations). The plain password never touches the disk or the log.</p>'
                '</div></form>']
        return page("Sign in", fl + "".join(body), "/login")
    extra = ""
    if GUARD.locked_until(ip):
        until = GUARD.locked.get(ip, 0)
        mins = max(1, int((until - datetime.now(timezone.utc).timestamp()) // 60) + 1)
        extra = (f'<div class="warn"><b>Locked out.</b> Too many failed attempts from this '
                 f'address. Try again in about {mins} minutes.</div>')
    body = ['<h1>Sign in</h1>',
            '<form method="post" action="/login"><div class="card" style="max-width:460px">'
            '<div class="field"><label>Operator password</label>'
            '<input type="password" name="password" autofocus required></div>'
            '<button class="btn big" type="submit" style="margin-top:10px">Sign in</button>'
            '<p class="sm soft" style="margin-top:14px">Sessions last 12 hours. Failed '
            'attempts are rate-limited, locked out after five, and written to the audit '
            'log.</p></div></form>']
    return page("Sign in", fl + extra + "".join(body), "/login")


def lockout_page(ip):
    until = GUARD.locked.get(ip, 0)
    mins = max(1, int((until - datetime.now(timezone.utc).timestamp()) // 60) + 1)
    return page("Locked out", f"<h1>Too many failed sign-ins.</h1>"
                f"<p>This address is locked out for about {mins} more minutes. "
                f"Every attempt is written to the audit log.</p>")


def get_login(req):
    return html(page_login(req.ip))


def post_login(req):
    ip = req.ip
    pw = req.one("password", "") or ""
    if demo_mode():
        if not local_request_allowed(ip):
            return send(page("Sign in", '<h1>No operator password is set.</h1>'
                             '<p>This instance is in open demo mode and the first '
                             'password can only be created from the machine it runs on '
                             '(or with REGULARS_OPEN_FIRST_RUN=1).</p>'), 403)
        if len(pw) < 10 or pw != (req.one("password2", "") or ""):
            flash("Password not set: use at least 10 characters, typed the same twice.")
            return redirect("/login")
        STATE["password_hash"] = SEC.hash_secret(pw)
        AUDIT.write("config_change", ip=ip,
                    detail={"what": "operator_password_created", "version": VERSION})
        tok = create_session(ip)
        return redirect("/", cookie=cookie_for(tok))
    if GUARD.locked_until(ip):
        return send(lockout_page(ip), 429)
    if SEC.verify_secret(pw, STATE.get("password_hash") or ""):
        GUARD.clear_login_fails(ip)
        tok = create_session(ip)
        AUDIT.write("login_ok", ip=ip)
        return redirect("/", cookie=cookie_for(tok))
    locked = GUARD.note_login_fail(ip)
    AUDIT.write("login_fail", ip=ip)
    if locked:
        AUDIT.write("lockout", ip=ip,
                    detail={"minutes": SEC.LOGIN_LOCKOUT // 60})
    flash("Wrong password. Failed attempts are limited and logged.")
    return redirect("/login")


def post_logout(req):
    tok = req.cookie_token
    if tok:
        (STATE.get("sessions") or {}).pop(tok, None)
        persist()
    AUDIT.write("logout", ip=req.ip)
    return redirect("/login", cookie=expire_cookie())
