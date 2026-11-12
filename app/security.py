"""
Security layer for Regulars: operator auth, sessions, CSRF, rate limiting,
security headers, secrets encrypted at rest, and a tamper-evident audit log.

Threat model (stated plainly, because pretending otherwise helps nobody):
this is a single-instance app that one venue's operator signs into. It is not
a multi-tenant SaaS. What this module defends against is the realistic set -
someone finding the URL, someone brute-forcing the operator login, someone
posting forged forms, someone walking off with the config file, and someone
quietly editing the audit trail afterwards. Multi-tenant isolation, WAFs and
HSMs belong to the next architecture, and the teardown says so.

Stdlib only. The AEAD here is HMAC-SHA256 in counter mode with
encrypt-then-MAC and PBKDF2 key stretching: standard constructions, no
custom crypto primitives. Production should still prefer a managed secrets
manager over the keyfile; see DEPLOYMENT.md.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import struct
import time

PBKDF2_ITERATIONS = 240_000          # OWASP 2023 floor for PBKDF2-HMAC-SHA256
SESSION_TTL_SECONDS = 12 * 3600      # operator sessions expire
LOGIN_MAX_FAILS = 5                  # then the IP is locked out
LOGIN_WINDOW = 900                   # failures counted over 15 minutes
LOGIN_LOCKOUT = 900                  # and locked out for 15 minutes
RATE_POST = 120                      # per minute per IP
RATE_GET = 600
MAX_FORM_BYTES = 1024 * 1024        # every POST except a CSV upload
DEFAULT_UPLOAD_MB = 50              # CSV uploads; REGULARS_MAX_UPLOAD_MB overrides
UPLOAD_MB_RANGE = (1, 500)

AUDIT_EVENTS = (
    "login_ok", "login_fail", "lockout", "logout", "csrf_reject", "rate_limit",
    "oversize_upload", "config_change", "approve", "export", "upload",
    "oauth_start", "oauth_ok", "oauth_fail", "oauth_revoke", "reset",
    "audit_verify", "secret_read", "setup_ok", "setup_fail",
)

PASSWORD_MIN = 10
MAX_PROXY_HOPS = 5


# ---------------------------------------------------------------------------
# which address is the client?
# ---------------------------------------------------------------------------

def max_upload_bytes():
    """REGULARS_MAX_UPLOAD_MB (1-500, default 50). An upload is held in memory
    while it is read - several times its size - so raise it only with RAM to
    match (see Performance in the README)."""
    lo, hi = UPLOAD_MB_RANGE
    try:
        mb = int(os.environ.get("REGULARS_MAX_UPLOAD_MB") or DEFAULT_UPLOAD_MB)
    except ValueError:
        mb = DEFAULT_UPLOAD_MB
    return max(lo, min(hi, mb)) * 1024 * 1024


def trusted_proxy_hops():
    """REGULARS_TRUST_PROXY: how many reverse proxies sit in front of the app
    (1 for Caddy or nginx; 2 for a CDN in front of those). Unset or 0 means
    X-Forwarded-For is ignored entirely."""
    try:
        return max(0, min(MAX_PROXY_HOPS, int(os.environ.get("REGULARS_TRUST_PROXY") or 0)))
    except ValueError:
        return 0


def client_ip(peer, forwarded_for="", hops=0):
    """The address that rate limits, lockouts and the audit log key on.

    X-Forwarded-For is just a header: anyone can send one. Each proxy
    *appends* the address it saw, so with `hops` trusted proxies the honest
    entry is the hops-th from the right; everything to its left was written
    by the client. The header is believed only when hops > 0 and the
    connection itself comes from a loopback or private address - that is,
    from the proxy. Anything unparseable falls back to the connection.
    """
    peer = peer or "-"
    if hops <= 0 or not forwarded_for:
        return peer
    try:
        via = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not (via.is_loopback or via.is_private):
        return peer
    parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
    if not parts:
        return peer
    chosen = parts[-hops] if len(parts) >= hops else parts[0]
    try:
        return str(ipaddress.ip_address(chosen))
    except ValueError:
        return peer


def password_ok(password, again):
    """The operator-password rule: long enough, and typed the same twice."""
    return len(password or "") >= PASSWORD_MIN and password == again


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------

def hash_secret(password, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_secret(password, stored):
    try:
        algo, iters, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# secrets at rest: PBKDF2 -> (enc key, mac key), HMAC-CTR + encrypt-then-MAC
# ---------------------------------------------------------------------------

class SecretBox:
    PREFIX = "enc:v1:"

    def __init__(self, master=None, keyfile=None):
        self.source = None
        if master:
            key = master.encode("utf-8")
            self.source = "environment"
        else:
            keyfile = keyfile or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "regulars-master.key")
            if os.path.exists(keyfile):
                with open(keyfile, "rb") as f:
                    key = f.read()
                self.source = "keyfile"
            else:
                key = secrets.token_bytes(32)
                fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(key)
                self.source = "keyfile-created"
        self._enc = hashlib.pbkdf2_hmac("sha256", key, b"regulars-enc", 20_000)
        self._mac = hashlib.pbkdf2_hmac("sha256", key, b"regulars-mac", 20_000)

    def _keystream(self, nonce, length):
        out = b""
        counter = 0
        while len(out) < length:
            out += hmac.new(self._enc, nonce + struct.pack(">Q", counter),
                            hashlib.sha256).digest()
            counter += 1
        return out[:length]

    def encrypt(self, plaintext):
        if plaintext is None or plaintext == "":
            return ""
        raw = plaintext.encode("utf-8")
        nonce = secrets.token_bytes(16)
        ct = bytes(a ^ b for a, b in zip(raw, self._keystream(nonce, len(raw))))
        tag = hmac.new(self._mac, nonce + ct, hashlib.sha256).digest()
        return self.PREFIX + (nonce + ct + tag).hex()

    def decrypt(self, token):
        if not token or not token.startswith(self.PREFIX):
            return token                      # pre-encryption legacy value
        blob = bytes.fromhex(token[len(self.PREFIX):])
        nonce, ct, tag = blob[:16], blob[16:-32], blob[-32:]
        want = hmac.new(self._mac, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(want, tag):
            raise ValueError("secret failed authentication - tampered or wrong key")
        return bytes(a ^ b for a, b in zip(ct, self._keystream(nonce, len(ct)))) \
            .decode("utf-8")


# ---------------------------------------------------------------------------
# rate limiting / lockout
# ---------------------------------------------------------------------------

class Guard:
    """Per-IP sliding windows plus a login-failure lockout. Deliberately
    in-memory: losing it on restart fails safe (limits reset), and a shared
    store is the next architecture's problem."""

    def __init__(self):
        self.hits = {}          # ip -> {bucket: [timestamps]}
        self.fails = {}         # ip -> [timestamps]
        self.locked = {}        # ip -> until
        self.flags = []         # recent security notices for the /security screen

    def _prune(self, seq, window, now):
        return [t for t in seq if now - t < window]

    def locked_until(self, ip, now=None):
        return self.locked.get(ip, 0) > (now or time.time())

    def note_login_fail(self, ip, now=None):
        now = now or time.time()
        seq = self._prune(self.fails.get(ip, []), LOGIN_WINDOW, now)
        seq.append(now)
        self.fails[ip] = seq
        if len(seq) >= LOGIN_MAX_FAILS:
            self.locked[ip] = now + LOGIN_LOCKOUT
            self.fails[ip] = []
            self.flag(ip, f"login lockout after {len(seq)} failures in "
                          f"{LOGIN_WINDOW // 60} minutes")
            return True
        return False

    def clear_login_fails(self, ip):
        self.fails.pop(ip, None)

    def allow(self, ip, bucket, limit, window=60.0, now=None):
        now = now or time.time()
        if self.locked_until(ip, now):
            return False
        seq = self._prune(self.hits.get(ip, {}).get(bucket, []), window, now)
        if len(seq) >= limit:
            self.hits.setdefault(ip, {})[bucket] = seq
            self.flag(ip, f"rate limit exceeded on {bucket}")
            return False
        seq.append(now)
        self.hits.setdefault(ip, {})[bucket] = seq
        return True

    def flag(self, ip, what, now=None):
        self.flags.append({"ts": now or time.time(), "ip": ip, "what": what})
        self.flags = self.flags[-200:]


# ---------------------------------------------------------------------------
# tamper-evident audit log (hash-chained, append-only)
# ---------------------------------------------------------------------------

class AuditLog:
    def __init__(self, path):
        self.path = path
        self._last = self._tail_hash()

    def _tail_hash(self):
        last = "0" * 64
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        last = json.loads(line).get("hash", last)
        except OSError:
            pass
        return last

    def write(self, event, actor="operator", ip="-", detail=None):
        entry = {
            "ts": time.time(),
            "event": event,
            "actor": actor,
            "ip": ip,
            "detail": detail or {},
            "prev": self._last,
        }
        canon = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        entry["hash"] = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        self._last = entry["hash"]
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def read(self, limit=200):
        out = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        out.append(json.loads(line))
        except OSError:
            pass
        return out[-limit:]

    def verify(self):
        """Walk the chain. Returns (ok, broken_at_index_or_None, count)."""
        prev = "0" * 64
        n = 0
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if entry.get("prev") != prev:
                        return False, n, n
                    stored = entry.pop("hash", None)
                    canon = json.dumps(entry, sort_keys=True, separators=(",", ":"))
                    if hashlib.sha256(canon.encode()).hexdigest() != stored:
                        return False, n, n
                    prev = stored
                    n += 1
        except OSError:
            return True, None, 0
        return True, None, n


# ---------------------------------------------------------------------------
# sessions and CSRF
# ---------------------------------------------------------------------------

def new_session():
    return {
        "token": secrets.token_urlsafe(32),
        "csrf": secrets.token_urlsafe(24),
        "created": time.time(),
        "expires": time.time() + SESSION_TTL_SECONDS,
    }


def session_valid(sess, now=None):
    return bool(sess) and (now or time.time()) < sess.get("expires", 0)


def csrf_ok(sess, supplied):
    return bool(sess) and bool(supplied) and hmac.compare_digest(
        sess.get("csrf", ""), supplied or "")


# ---------------------------------------------------------------------------
# response headers
# ---------------------------------------------------------------------------

def security_headers(tls=False):
    h = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy":
            "default-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        "Cache-Control": "no-store",
    }
    if tls:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return h
