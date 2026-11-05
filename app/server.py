"""
Regulars - a Square wrapper that turns customer behaviour into ready-to-send
messages, one per person, derived from their own orders.

Run:  python3 server.py 8010

This module is the HTTP layer only: rate limits, sign-in and CSRF checks,
then a lookup in GET_ROUTES / POST_ROUTES. Each screen lives in pages/ with
its page renderer and form actions; shared state lives in state.py.
Python standard library only.
"""

import json
import os
import re
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import exports
import security as SEC
import store
from pages import (data_page, goals_page, login_page, messages_page, security_page,
                   sending_page, sent_page, setup_page, triggers_page)
from state import (AUDIT, CTX, DS, GUARD, LOCK, SCHEMA, STATE, VERSION, demo_mode,
                   local_request_allowed, result, session_from_token)
from ui import e, page
from upload import parse_multipart
from web import Req, send

# Older code and the tests reach these through `server`; they now live in
# state.py and exports.py.
from exports import export_copy, export_csv  # noqa: F401,E402
from state import (FLASH, _load_tokens, _store_tokens, dataset, fresh_state,  # noqa: F401
                   now, persist)


def get_healthz(req):
    return send(json.dumps({"status": "ok", "version": VERSION,
                            "schema": SCHEMA,
                            "region": STATE.get("region", "AU"),
                            "auth_enabled": not demo_mode()}),
                ctype="application/json")


GET_ROUTES = {
    "/login": login_page.get_login,
    "/": setup_page.get_setup,
    "/goals": goals_page.get_goals,
    "/triggers": triggers_page.get_triggers,
    "/inbox": messages_page.get_inbox,
    "/m": messages_page.get_message,
    "/sending": sending_page.get_sending,
    "/sent": sent_page.get_sent,
    "/data": data_page.get_data,
    "/security": security_page.get_security,
    "/security/audit.log": security_page.get_audit_log,
    "/oauth/start": sending_page.get_oauth_start,
    "/oauth/callback": sending_page.get_oauth_callback,
    "/export.csv": exports.get_export_csv,
    "/export-copy.txt": exports.get_export_copy,
    "/sample/simple.csv": exports.get_sample_simple,
    "/sample/square-transactions.csv": exports.get_sample_square_transactions,
    "/sample/square-customers.csv": exports.get_sample_square_customers,
    "/healthz": get_healthz,
}

# Reachable without signing in.
PUBLIC_GET = {"/login", "/healthz", "/oauth/callback"}

POST_ROUTES = {
    "/connect": setup_page.post_connect,
    "/upload": setup_page.post_upload,
    "/reset": setup_page.post_reset,
    "/region": setup_page.post_region,
    "/goals": goals_page.post_goals,
    "/triggers": triggers_page.post_triggers,
    "/tuning/reset": triggers_page.post_tuning_reset,
    "/sending": sending_page.post_sending,
    "/sending/test": sending_page.post_sending_test,
    "/approve": messages_page.post_approve,
    "/approve-edit": messages_page.post_approve,
    "/logout": login_page.post_logout,
    "/security/verify": security_page.post_verify,
    "/security/logout-all": security_page.post_logout_all,
    "/oauth/revoke": sending_page.post_oauth_revoke,
}

# These take LOCK themselves, around their STATE changes only, so a long CSV
# parse or a slow SMTP server doesn't hold up every other request.
SELF_LOCKING = {"/upload", "/sending/test"}


def _not_found(req):
    return send(page("Not found", '<h1>Not found.</h1>'
                     '<p style="margin-top:30px"><a href="/">Home</a></p>'), 404)


class Handler(BaseHTTPRequestHandler):
    server_version = "Regulars/" + VERSION

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}\n")

    def _send(self, body, status=200, ctype="text/html; charset=utf-8", extra=None,
              cookie=None):
        raw = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in SEC.security_headers(tls=bool(os.environ.get("REGULARS_TLS"))).items():
            self.send_header(k, v)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _redirect(self, loc, cookie=None):
        self.send_response(303)
        self.send_header("Location", loc)
        for k, v in SEC.security_headers(tls=bool(os.environ.get("REGULARS_TLS"))).items():
            self.send_header(k, v)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _html(self, body_html, status=200, cookie=None):
        """Render a page; when a session exists, every form in it gets its CSRF
        field injected, so no page function has to remember one."""
        tok = getattr(CTX, "csrf", "")
        if tok:
            body_html = re.sub(
                r"(<form[^>]*>)",
                lambda m: m.group(1) + f'<input type="hidden" name="csrf" value="{tok}">',
                body_html)
        self._send(body_html, status, cookie=cookie)

    def _respond(self, resp):
        if resp.location is not None:
            self._redirect(resp.location, cookie=resp.cookie)
        elif resp.inject_csrf:
            self._html(resp.body, resp.status, cookie=resp.cookie)
        else:
            self._send(resp.body, resp.status, ctype=resp.ctype, extra=resp.extra,
                       cookie=resp.cookie)

    def _ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd else (self.client_address[0] or "-")

    def _cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            part = part.strip()
            if part.startswith("REGULARS_SID="):
                return part[len("REGULARS_SID="):]
        return ""

    def _session(self):
        sess = session_from_token(self._cookie_token())
        return sess if SEC.session_valid(sess) else None

    def _authorized(self):
        return demo_mode() or self._session() is not None

    def do_GET(self):
        ip = self._ip()
        if not GUARD.allow(ip, "GET", SEC.RATE_GET):
            AUDIT.write("rate_limit", ip=ip,
                        detail={"bucket": "GET", "path": self.path[:120]})
            if GUARD.locked_until(ip):
                self._send(login_page.lockout_page(ip), 429)
            else:
                self._send(page("Too many requests", '<h1>Slow down.</h1>'
                                '<p>Too many requests from this address. Wait a minute '
                                'and try again.</p>'), 429)
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path not in PUBLIC_GET and not self._authorized():
            self._redirect("/login" if u.path == "/" else "/login?next=" + u.path)
            return
        sess = self._session()
        CTX.csrf = (sess or {}).get("csrf", "")
        CTX.local_ok = local_request_allowed(ip)
        req = Req(ip, u.path, q, cookie_token=self._cookie_token())
        try:
            with LOCK:
                resp = GET_ROUTES.get(u.path, _not_found)(req)
            self._respond(resp)
        except Exception:
            import traceback
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            detail = (f'<div class="email">{e(tb)}</div>' if demo_mode() else
                      '<p class="sm muted">The error was written to the server log. '
                      'The audit trail on /security shows what happened just before '
                      'it.</p>')
            self._send(page("Error", f'<h1>Something broke</h1>{detail}'), 500)

    def do_POST(self):
        ip = self._ip()
        if not GUARD.allow(ip, "POST", SEC.RATE_POST):
            AUDIT.write("rate_limit", ip=ip,
                        detail={"bucket": "POST", "path": self.path[:120]})
            self._send(page("Too many requests", '<h1>Slow down.</h1><p>Too many '
                            'requests from this address. Wait a minute and try again.</p>'),
                       429)
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        if n > SEC.MAX_BODY_BYTES:
            AUDIT.write("oversize_upload", ip=ip,
                        detail={"bytes": n, "path": u.path[:120]})
            self._send(page("Too large", '<h1>That file is too large.</h1><p>Uploads are '
                            f'capped at {SEC.MAX_BODY_BYTES // (1024 * 1024)} MB. Export a '
                            'shorter date range and upload that instead.</p>'), 413)
            return
        raw = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" in ctype:
            fields, files = parse_multipart(raw, ctype)
            form = {k: [v] for k, v in fields.items()}
        else:
            form = parse_qs(raw.decode(), keep_blank_values=True)
            fields, files = {}, {}
        req = Req(ip, u.path, q, form=form, fields=fields, files=files,
                  cookie_token=self._cookie_token())

        if u.path == "/login":
            with LOCK:
                resp = login_page.post_login(req)
            self._respond(resp)
            return
        sess = self._session()
        if not demo_mode():
            if sess is None:
                self._send(page("Sign in", '<h1>Session required.</h1><p>Your session '
                                'expired or you are not signed in.</p>'
                                '<p><a class="btn" href="/login">Sign in</a></p>'), 403)
                return
            if not SEC.csrf_ok(sess, req.one("csrf")):
                AUDIT.write("csrf_reject", ip=ip, detail={"path": u.path[:120]})
                self._send(page("Form expired", '<h1>That form had expired.</h1>'
                                '<p>The security token did not match your session. Reload '
                                'the page and try again.</p>'), 403)
                return
        CTX.csrf = (sess or {}).get("csrf", "")

        action = POST_ROUTES.get(u.path)
        if action is None:
            self._redirect("/")
            return
        if u.path in SELF_LOCKING:
            resp = action(req)
        else:
            with LOCK:
                resp = action(req)
        self._respond(resp)


class Server(ThreadingHTTPServer):
    # The stdlib default queues only 5 waiting connections; a browser opening
    # a few tabs plus a health check can exceed that and get resets.
    request_queue_size = 64
    daemon_threads = True


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    mode = "OPEN DEMO MODE - no operator password set" if demo_mode() else "password gated"
    print(f"Regulars {VERSION} -> http://0.0.0.0:{port}")
    print(f"  market: {STATE.get('region', 'AU')} | auth: {mode}")
    print(f"  audit log: {AUDIT.path}")
    r = result()
    print(f"  {DS['stats']['customers']} demo customers, {DS['stats']['orders']:,} orders -> "
          f"{r['summary']['ready']} messages ready")
    print(f"  state file: {store.PATH}")
    Server(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
