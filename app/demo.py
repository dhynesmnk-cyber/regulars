"""
Public demo: a hosted copy of the app that anyone can try, from which nothing
can be sent.

Every visitor gets a private sandbox: their own goals, settings and uploaded
file, held in memory only and forgotten after a period of inactivity. Nobody
sees anyone else's, and nothing is written to disk. Everything that could send
a message, store a secret or reach outside the server is switched off (see
ALLOWED_GET / ALLOWED_POST below: anything not listed is refused).

This module is only the bookkeeping: the visitor store, the route allow-list
and the limits. state.py makes STATE resolve to the current visitor's dict, and
server.py attaches a visitor to each request.

Turn it on with REGULARS_PUBLIC_DEMO=1. Limits (all optional):
  REGULARS_DEMO_TTL_MIN        minutes idle before a sandbox is deleted (30)
  REGULARS_DEMO_MAX_SESSIONS   sandboxes at once; more get a "busy" page (40)
  REGULARS_DEMO_MAX_UPLOADS    sandboxes holding an uploaded file at once (3)
  REGULARS_DEMO_IMPORTS_AT_ONCE  CSV files parsed simultaneously (2)
  REGULARS_MAX_UPLOAD_MB       largest upload (5 in the public demo)
"""

import os
import re
import secrets
import threading
import time

COOKIE = "REGULARS_DEMO"
COOKIE_MAX_AGE = 24 * 3600          # the server-side idle limit is the real one
NEW_SESSIONS_PER_MINUTE = 10        # per address, so one client can't fill the store
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")

# What a visitor may do. Everything else gets the "not part of the demo" page.
ALLOWED_GET = frozenset({
    "/", "/goals", "/triggers", "/inbox", "/m", "/sending", "/sent", "/data",
    "/healthz",
    "/sample/simple.csv", "/sample/square-transactions.csv", "/sample/square-customers.csv",
})
ALLOWED_POST = frozenset({
    "/connect",          # in the demo this just loads the example restaurant
    "/upload", "/reset", "/region", "/business",
    "/goals", "/triggers", "/tuning/reset",
    "/sending",          # choosing an email tool only; SMTP details are ignored
    "/approve", "/approve-edit", "/approve-all", "/unapprove",
})
ALLOWED = {"GET": ALLOWED_GET, "POST": ALLOWED_POST}

# Answered without a visitor sandbox: nothing here reads or writes visitor state.
STATELESS = frozenset({
    "/healthz",
    "/sample/simple.csv", "/sample/square-transactions.csv", "/sample/square-customers.csv",
})

# (path prefix, title, explanation) - the first match explains a refused request.
_WHY = (
    ("/export", "Exports are switched off in the demo.",
     "On a live instance this downloads the messages you approved as a file for your email "
     "tool. The demo can't send or hand over anything."),
    ("/sending/test", "Connection tests are switched off in the demo.",
     "On a live instance this checks your email server's details. The demo makes no "
     "outside connections."),
    ("/oauth", "Connecting Square is switched off in the demo.",
     "Upload a Square export on Setup instead: it needs no credentials."),
    ("/security", "Security is for the owner of a live instance.",
     "It shows the sign-in state and the tamper-evident audit trail of a real venue."),
    ("/login", "Sign-in isn't part of the demo.", "There is no password to set here."),
    ("/logout", "Sign-in isn't part of the demo.", "There is no password to set here."),
    ("/welcome", "Sign-in isn't part of the demo.", "There is no password to set here."),
)


def blocked_message(path):
    """(title, explanation) for a request the public demo refuses."""
    for prefix, title, text in _WHY:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "."):
            return title, text
    return "That isn't part of the public demo.", "Try the screens in the menu."


def token_from_cookie(header):
    for part in (header or "").split(";"):
        part = part.strip()
        if part.startswith(COOKIE + "="):
            return part[len(COOKIE) + 1:]
    return ""


def cookie_header(token, tls=False):
    return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={COOKIE_MAX_AGE}{'; Secure' if tls else ''}")


class Full(Exception):
    """Every sandbox is taken."""


class Throttled(Exception):
    """This address is creating sandboxes too fast."""


class Visitor:
    """One visitor's sandbox: their settings dict and their pending notes."""
    __slots__ = ("token", "state", "flash", "last_seen")

    def __init__(self, token, state, now):
        self.token = token
        self.state = state
        self.flash = []
        self.last_seen = now


class Visitors:
    """The store of sandboxes. Idle ones are deleted; the number is capped."""

    def __init__(self, make_state, ttl_seconds=1800, max_sessions=40, max_uploads=3,
                 clock=time.monotonic):
        self.make_state = make_state
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self.max_uploads = max_uploads
        self._clock = clock
        self._lock = threading.Lock()
        self._by_token = {}

    def _sweep(self, now):
        for token in [t for t, v in self._by_token.items() if now - v.last_seen > self.ttl]:
            del self._by_token[token]          # dropping it frees any uploaded dataset

    def attach(self, token, may_create=None):
        """(visitor, status). status is 'existing', 'new', or 'expired' (they
        had a cookie for a sandbox that has since been deleted). Raises Full or
        Throttled instead of creating one when it mustn't."""
        now = self._clock()
        with self._lock:
            self._sweep(now)
            if token and _TOKEN_RE.match(token):
                v = self._by_token.get(token)
                if v is not None:
                    v.last_seen = now
                    return v, "existing"
            status = "expired" if token and _TOKEN_RE.match(token) else "new"
            if may_create is not None and not may_create():
                raise Throttled()
            if len(self._by_token) >= self.max_sessions:
                raise Full()
            v = Visitor(secrets.token_urlsafe(24), self.make_state(), now)
            self._by_token[v.token] = v
            return v, status

    def uploads(self):
        with self._lock:
            return sum(1 for v in self._by_token.values() if v.state.get("dataset") is not None)

    def may_upload(self, visitor):
        """A sandbox already holding a file may replace it; a new one needs a free slot."""
        return visitor.state.get("dataset") is not None or self.uploads() < self.max_uploads

    def __len__(self):
        with self._lock:
            return len(self._by_token)

    def clear(self):
        with self._lock:
            self._by_token.clear()


def _int_env(name, default, lo, hi):
    try:
        n = int(os.environ.get(name) or default)
    except ValueError:
        n = default
    return max(lo, min(hi, n))


def limits_from_env():
    return {"ttl_seconds": _int_env("REGULARS_DEMO_TTL_MIN", 30, 1, 240) * 60,
            "max_sessions": _int_env("REGULARS_DEMO_MAX_SESSIONS", 40, 1, 500),
            "max_uploads": _int_env("REGULARS_DEMO_MAX_UPLOADS", 3, 0, 50)}


# CSV parsing holds several times the file's size in memory; only a couple at once.
IMPORT_SLOTS = threading.BoundedSemaphore(_int_env("REGULARS_DEMO_IMPORTS_AT_ONCE", 2, 1, 8))
