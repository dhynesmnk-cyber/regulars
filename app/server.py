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
import socket
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import demo as DEMO
import exports
import security as SEC
import store
from pages import (data_page, goals_page, login_page, messages_page, security_page,
                   sending_page, sent_page, setup_page, triggers_page, welcome_page)
from state import (AUDIT, CTX, DS, GUARD, LOCK, SCHEMA, SETUP_TOKEN_MIN, STATE, VERSION, VISITORS,
                   demo_instance, demo_mode, local_request_allowed, public_demo, result,
                   session_from_token, set_visitor, setup_token, unclaimed)
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
    "/welcome": welcome_page.get_welcome,
}

# Reachable without signing in.
PUBLIC_GET = {"/login", "/healthz", "/oauth/callback", "/welcome"}

# All an unclaimed instance answers (see state.unclaimed).
UNCLAIMED_OPEN = {"/welcome", "/healthz"}

POST_ROUTES = {
    "/connect": setup_page.post_connect,
    "/upload": setup_page.post_upload,
    "/reset": setup_page.post_reset,
    "/region": setup_page.post_region,
    "/business": setup_page.post_business,
    "/goals": goals_page.post_goals,
    "/triggers": triggers_page.post_triggers,
    "/tuning/reset": triggers_page.post_tuning_reset,
    "/sending": sending_page.post_sending,
    "/sending/test": sending_page.post_sending_test,
    "/approve": messages_page.post_approve,
    "/approve-edit": messages_page.post_approve,
    "/approve-all": messages_page.post_approve_all,
    "/unapprove": messages_page.post_unapprove,
    "/export": exports.post_export,
    "/logout": login_page.post_logout,
    "/security/verify": security_page.post_verify,
    "/security/logout-all": security_page.post_logout_all,
    "/oauth/revoke": sending_page.post_oauth_revoke,
}

# These take LOCK themselves, around their STATE changes only, so a long CSV
# parse or a slow SMTP server doesn't hold up every other request.
SELF_LOCKING = {"/upload", "/sending/test"}


def _redact(path):
    """Setup tokens are secrets: keep them out of logs and the audit trail."""
    return re.sub(r"(token=)[^&\s]*", r"\1[redacted]", path)


def _not_found(req):
    return send(page("Not found", '<h1>Not found.</h1>'
                     '<p style="margin-top:30px"><a href="/">Home</a></p>'), 404)


class Handler(BaseHTTPRequestHandler):
    server_version = "Regulars/" + VERSION
    _demo_cookie = None      # public demo: set while answering a visitor's first request

    def log_message(self, fmt, *args):
        line = _redact(fmt % args)
        if public_demo():
            # '/m?id=<customer id from a visitor's file>' must not reach the log
            line = re.sub(r"\?\S*", "?...", line)
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {line}\n")

    def _send(self, body, status=200, ctype="text/html; charset=utf-8", extra=None,
              cookie=None):
        raw = body.encode() if isinstance(body, str) else body
        cookie = cookie or self._demo_cookie
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
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _redirect(self, loc, cookie=None):
        cookie = cookie or self._demo_cookie
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

    def _refuse_unread(self, body, status):
        """Answer without reading the request body (too big, or not allowed).
        Closing straight away would reset the connection while the browser is
        still sending, and it would show a network error instead of this
        page. So: answer, stop writing, discard whatever is still arriving for
        up to two seconds, then close - what web servers call a lingering
        close."""
        self.close_connection = True
        self._send(body, status, extra={"Connection": "close"})
        try:
            self.connection.shutdown(socket.SHUT_WR)
            self.connection.settimeout(0.5)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and self.connection.recv(65536):
                pass
        except OSError:
            pass

    def _ip(self):
        return SEC.client_ip(self.client_address[0], self.headers.get("X-Forwarded-For", ""),
                             SEC.trusted_proxy_hops())

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
        self._serve("GET", self._do_get)

    def do_POST(self):
        self._serve("POST", self._do_post)

    def _serve(self, method, handler):
        """Outside the public demo this is just handler(). Inside it, anything the
        demo doesn't offer is refused, and the rest is served from the visitor's
        own sandbox (demo.py), which is created on their first request."""
        if not public_demo():
            return handler()
        path = urlparse(self.path).path
        if path not in DEMO.ALLOWED[method]:
            title, text = DEMO.blocked_message(path)
            self._demo_answer(method, 403, title, text)
            return
        if path in DEMO.STATELESS:
            return handler()
        ip = self._ip()
        try:
            visitor, status = VISITORS.attach(
                DEMO.token_from_cookie(self.headers.get("Cookie")),
                may_create=lambda: GUARD.allow(ip, "DEMO_NEW", DEMO.NEW_SESSIONS_PER_MINUTE))
        except DEMO.Full:
            self._demo_answer(method, 503, "The demo is busy right now.",
                              "Everyone's sandbox is in use. Try again in a few minutes.")
            return
        except DEMO.Throttled:
            self._demo_answer(method, 429, "Slow down.",
                              "Too many new demo sessions from this address. Wait a minute.")
            return
        if status == "expired":
            visitor.flash.append("Your demo session timed out and was cleared, as promised. "
                                 "This is a fresh one.")
        self._demo_cookie = (DEMO.cookie_header(visitor.token, tls=bool(os.environ.get("REGULARS_TLS")))
                             if status != "existing" else None)
        set_visitor(visitor)
        try:
            handler()
        except Exception as exc:
            # A visitor's file may be in the message: log what failed, never its text.
            sys.stderr.write(f"error serving {method} {path}: {type(exc).__name__}\n")
            try:
                self._send(page("Error", "<h1>Something went wrong.</h1><p>Press Start over on "
                                "Setup and try again.</p>"), 500)
            except OSError:
                pass
        finally:
            set_visitor(None)
            self._demo_cookie = None

    def _demo_answer(self, method, status, title, text):
        """A plain page, without touching any visitor's sandbox."""
        body = page(title, f"<h1>{e(title)}</h1><p>{e(text)}</p>"
                    '<p style="margin-top:30px"><a class="btn" href="/inbox">Back to the demo</a></p>',
                    bare=True)
        if method == "POST":
            self._refuse_unread(body, status)     # its body is unread
        else:
            self._send(body, status, extra={"Retry-After": "60"} if status in (429, 503) else None)

    def do_HEAD(self):
        """Uptime monitors often check with HEAD. Only the health check answers
        it: every other page is a GET, so none can be triggered (an export
        logged, a message marked read) by a request that shows nothing."""
        if urlparse(self.path).path == "/healthz":
            return self.do_GET()
        self._send("", 405, extra={"Allow": "GET, POST"})

    def _do_get(self):
        ip = self._ip()
        if not GUARD.allow(ip, "GET", SEC.RATE_GET):
            AUDIT.write("rate_limit", ip=ip,
                        detail={"bucket": "GET", "path": _redact(self.path)[:120]})
            if GUARD.locked_until(ip):
                self._send(login_page.lockout_page(ip), 429)
            else:
                self._send(page("Too many requests", '<h1>Slow down.</h1>'
                                '<p>Too many requests from this address. Wait a minute '
                                'and try again.</p>'), 429)
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if unclaimed() and u.path not in UNCLAIMED_OPEN:
            self._respond(welcome_page.unclaimed_page())
            return
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
        except Exception as exc:
            import traceback
            if public_demo():
                sys.stderr.write(f"error serving GET {u.path}: {type(exc).__name__}\n")
                tb = ""
            else:
                tb = traceback.format_exc()
                sys.stderr.write(tb)
            detail = ('<p class="sm muted">Something went wrong on our side. Press Start over on '
                      'Setup and try again.</p>' if public_demo() else
                      f'<div class="email">{e(tb)}</div>' if demo_mode() else
                      '<p class="sm muted">The error was written to the server log. '
                      'The audit trail on /security shows what happened just before '
                      'it.</p>')
            self._send(page("Error", f'<h1>Something broke</h1>{detail}'), 500)

    def _do_post(self):
        ip = self._ip()
        if not GUARD.allow(ip, "POST", SEC.RATE_POST):
            AUDIT.write("rate_limit", ip=ip,
                        detail={"bucket": "POST", "path": _redact(self.path)[:120]})
            self._send(page("Too many requests", '<h1>Slow down.</h1><p>Too many '
                            'requests from this address. Wait a minute and try again.</p>'),
                       429)
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = -1
        if n < 0:                      # read(-1) would wait for the client to hang up
            self._refuse_unread(page("Bad request", "<h1>That request was malformed.</h1>"),
                                400)
            return
        upload = u.path == "/upload"
        limit = SEC.max_upload_bytes() if upload else SEC.MAX_FORM_BYTES
        if n > limit:
            AUDIT.write("oversize_upload", ip=ip,
                        detail={"bytes": n, "limit": limit, "path": u.path[:120]})
            mb = limit // (1024 * 1024)
            self._refuse_unread(page("Too large", (
                '<h1>That file is too large.</h1>'
                f'<p>Uploads are capped at {mb} MB. In Square, export a shorter date range '
                '- the last two years is plenty - and upload that instead.</p>'
                '<p style="margin-top:30px"><a class="btn" href="/">Back to Setup</a></p>')
                if upload else '<h1>That request is too large.</h1>'), 413)
            return
        if upload and n:
            # The only big body: refuse it unread unless the owner is signed in.
            # (Its CSRF token is inside the body, so that's checked after.)
            if unclaimed():
                self._refuse_unread(welcome_page.unclaimed_page().body, 403)
                return
            if not demo_mode() and self._session() is None:
                self._refuse_unread(page("Sign in", '<h1>Session required.</h1><p>Your '
                                         'session expired or you are not signed in.</p>'
                                         '<p><a class="btn" href="/login">Sign in</a></p>'), 403)
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

        if u.path == "/welcome":
            with LOCK:
                resp = welcome_page.post_welcome(req)
            self._respond(resp)
            return
        if unclaimed():
            self._respond(welcome_page.unclaimed_page())
            return
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


def bind_address():
    """REGULARS_BIND: the interface to listen on. Loopback by default, so a
    fresh install is never reachable from the network by accident; the
    Docker image sets 0.0.0.0 and publishes the port on 127.0.0.1 only."""
    return (os.environ.get("REGULARS_BIND") or "127.0.0.1").strip() or "127.0.0.1"


def network_warnings(bind, hops, tls, port):
    """Start-up advice about how the app is exposed. Pure, so it is tested."""
    loopback = bind in ("127.0.0.1", "::1", "localhost")
    out = []
    if not loopback and not hops:
        out.append(f"listening on {bind}:{port} and not told about a reverse proxy. If one "
                   f"is in front, set REGULARS_TRUST_PROXY=1, or every visitor shares its "
                   f"address and one person's lockout locks out everyone. If none is, the app "
                   f"is exposed without TLS: put it behind one.")
    elif tls and not hops:
        out.append("REGULARS_TLS is set but REGULARS_TRUST_PROXY isn't: behind a proxy, every "
                   "visitor shares its address for rate limits and lockouts. Set "
                   "REGULARS_TRUST_PROXY=1.")
    if not loopback and hops:
        out.append(f"listening on {bind}:{port}; trusting {hops} proxy hop(s) for client "
                   f"addresses. Only the proxy may reach this port (Docker: publish it as "
                   f"-p 127.0.0.1:{port}:{port}).")
    return out


class Server(ThreadingHTTPServer):
    # The stdlib default queues only 5 waiting connections; a browser opening
    # a few tabs plus a health check can exceed that and get resets.
    request_queue_size = 64
    daemon_threads = True


def main():
    # Under Docker or systemd stdout isn't a terminal and Python would hold
    # these start-up lines back; the operator needs them in the log at once.
    sys.stdout.reconfigure(line_buffering=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    token = setup_token()
    if token and len(token) < SETUP_TOKEN_MIN:
        sys.exit(f"REGULARS_SETUP_TOKEN is too short ({len(token)} characters; at least "
                 f"{SETUP_TOKEN_MIN}). Generate one with:\n  python3 -c \"import secrets; "
                 f"print(secrets.token_urlsafe(32))\"")
    if public_demo() and token:
        sys.exit("REGULARS_PUBLIC_DEMO and REGULARS_SETUP_TOKEN can't be combined: a public "
                 "demo has no owner to hand the app to. Provision a venue separately.")
    mode = ("CLOSED until the owner uses their setup link" if unclaimed() else
            "PUBLIC DEMO - a private in-memory sandbox per visitor; nothing can be sent"
            if public_demo() else
            "OPEN DEMO MODE - no operator password set" if demo_mode() else "password gated")
    if demo_instance():
        mode += " | hosted DEMO: example data only, uploads off"
    bind = bind_address()
    print(f"Regulars {VERSION} -> http://{bind}:{port}")
    print(f"  market: {STATE.get('region', 'AU')} | auth: {mode}")
    if public_demo():
        print(f"  sandboxes: up to {VISITORS.max_sessions} ({VISITORS.max_uploads} holding an "
              f"upload), deleted after {VISITORS.ttl // 60} min idle; upload cap "
              f"{SEC.max_upload_bytes() // (1024 * 1024)} MB; nothing written to disk")
    print(f"  audit log: {AUDIT.path}")
    r = result()
    print(f"  {DS['stats']['customers']} demo customers, {DS['stats']['orders']:,} orders -> "
          f"{r['summary']['ready']} messages ready")
    print(f"  state file: {store.PATH}")
    if unclaimed():
        print("  setup: waiting for the owner - send them https://<this host>/welcome?token="
              "<the value of REGULARS_SETUP_TOKEN>")
    for note in network_warnings(bind, SEC.trusted_proxy_hops(),
                                 bool(os.environ.get("REGULARS_TLS")), port):
        print(f"  network: {note}")
    Server((bind, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
