"""
Owner setup link: how the owner of a provisioned instance sets their password.

Provisioning puts a one-time token in REGULARS_SETUP_TOKEN and sends the owner
https://<their host>/welcome?token=<it>. The link works once: setting the
password burns it. Until then every other screen is shut (see
server.Handler), so the instance is never open to whoever finds the address
first. Wrong tokens count towards the same lockout as wrong passwords.
"""

import security as SEC
from pages.login_page import lockout_page
from state import (AUDIT, GUARD, STATE, VERSION, burn_setup_token, cookie_for,
                   create_session, demo_mode, flash, setup_token, setup_token_matches)
from ui import e, page
from web import html, redirect, send


def unclaimed_page():
    return send(page("Waiting for its owner",
                     '<h1>This Regulars instance hasn&rsquo;t been set up yet.</h1>'
                     '<p class="lead">If it&rsquo;s yours, open the setup link from your '
                     'welcome email. It works once, and it&rsquo;s where you choose your '
                     'password.</p>'
                     '<p class="sm muted">Lost the link? Ask whoever set up Regulars for you '
                     'to issue a new one.</p>', "/welcome", bare=True), 403)


def _form(token, problem=""):
    warn = f'<div class="warn">{e(problem)}</div>' if problem else ""
    return page("Welcome", (
        '<h1>Welcome to Regulars.</h1>'
        '<p class="lead">Choose the password that protects your customer data. You&rsquo;ll '
        'use it to sign in from now on, and this setup link stops working once it&rsquo;s '
        'set.</p>'
        f'{warn}'
        '<form method="post" action="/welcome"><div class="card" style="max-width:520px">'
        f'<input type="hidden" name="token" value="{e(token)}">'
        f'<div class="field"><label>Password ({SEC.PASSWORD_MIN} characters or more)</label>'
        f'<input type="password" name="password" minlength="{SEC.PASSWORD_MIN}" required '
        'autofocus autocomplete="new-password"></div>'
        '<div class="field"><label>Repeat it</label>'
        f'<input type="password" name="password2" minlength="{SEC.PASSWORD_MIN}" required '
        'autocomplete="new-password"></div>'
        '<button class="btn big" type="submit" style="margin-top:10px">Set password and '
        'start</button>'
        '<p class="sm soft" style="margin-top:14px">Stored as a PBKDF2-SHA256 hash. There is '
        'no email reset: if you forget it, whoever runs your instance can issue a new setup '
        'link. Write it down somewhere safe.</p>'
        '</div></form>'), "/welcome", bare=True)


def _already_set_up():
    return html(page("Already set up",
                     '<h1>This instance is already set up.</h1>'
                     '<p class="lead">The setup link has been used. Sign in with your '
                     'password.</p>'
                     '<p style="margin-top:20px"><a class="btn" href="/login">Sign in</a></p>',
                     "/welcome", bare=True))


def _refuse(req):
    locked = GUARD.note_login_fail(req.ip)
    AUDIT.write("setup_fail", ip=req.ip)
    if locked:
        AUDIT.write("lockout", ip=req.ip, detail={"minutes": SEC.LOGIN_LOCKOUT // 60})
    return send(page("Setup link",
                     '<h1>That setup link isn&rsquo;t valid.</h1>'
                     '<p class="lead">It may have been used already, or copied incompletely. '
                     'Setup links work once. If you&rsquo;ve already chosen a password, '
                     '<a href="/login">sign in</a>.</p>', "/welcome", bare=True), 403)


def get_welcome(req):
    if not demo_mode():
        return _already_set_up()
    if not setup_token():
        return redirect("/login")
    if GUARD.locked_until(req.ip):
        return send(lockout_page(req.ip), 429)
    token = req.q.get("token", "")
    if not setup_token_matches(token):
        return _refuse(req)
    return html(_form(token))


def post_welcome(req):
    if not demo_mode():
        return _already_set_up()
    if not setup_token():
        return redirect("/login")
    if GUARD.locked_until(req.ip):
        return send(lockout_page(req.ip), 429)
    token = req.one("token", "") or ""
    if not setup_token_matches(token):
        return _refuse(req)
    pw = req.one("password", "") or ""
    if not SEC.password_ok(pw, req.one("password2", "") or ""):
        return html(_form(token, f"Password not set: use at least {SEC.PASSWORD_MIN} "
                                 f"characters, typed the same twice."))
    STATE["password_hash"] = SEC.hash_secret(pw)
    burn_setup_token()
    GUARD.clear_login_fails(req.ip)
    AUDIT.write("setup_ok", ip=req.ip,
                detail={"what": "operator_password_created", "via": "setup_link",
                        "version": VERSION})
    session = create_session(req.ip)          # persists STATE, burned token included
    flash("You're in. Your setup checklist is below: start with your business details.")
    return redirect("/", cookie=cookie_for(session))
