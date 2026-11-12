"""
Square OAuth 2.0 (authorization code) for Regulars.

The flow: /oauth/start builds the authorize URL with a CSRF state bound to the
operator session; /oauth/callback checks the state, exchanges the code for
tokens, and stores them encrypted at rest via security.SecretBox. Refresh and
revoke are implemented so a future sync job can keep the grant alive.

Credentials come from the environment, never from the state file:
    SQUARE_CLIENT_ID, SQUARE_CLIENT_SECRET
    REGULARS_REDIRECT_URI   (default http://localhost:8010/oauth/callback)

SQUARE_OAUTH_BASE exists so the tests can point the whole flow at a mock
token server; it must never be set in production.
"""

import json
import os
import time
import urllib.parse
import urllib.request

SCOPES = [
    "MERCHANT_PROFILE_READ",
    "ORDERS_READ",
    "CUSTOMERS_READ",
    "CATALOG_READ",
    "LOYALTY_READ",
]

AUTHORIZE_PATH = "/oauth2/authorize"
TOKEN_PATH = "/oauth2/token"
REVOKE_PATH = "/oauth2/token/revoke"


def configured():
    return bool(os.environ.get("SQUARE_CLIENT_ID") and os.environ.get("SQUARE_CLIENT_SECRET"))


def base_url():
    return os.environ.get("SQUARE_OAUTH_BASE", "https://connect.squareup.com")


def redirect_uri():
    return os.environ.get("REGULARS_REDIRECT_URI", "http://localhost:8010/oauth/callback")


def authorize_url(state):
    q = urllib.parse.urlencode({
        "client_id": os.environ.get("SQUARE_CLIENT_ID", ""),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "session": "false",
    })
    return f"{base_url()}{AUTHORIZE_PATH}?{q}"


def _post_json(path, payload):
    req = urllib.request.Request(
        base_url() + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def exchange_code(code):
    """Returns a token dict. Raises OSError/ValueError with a readable message."""
    try:
        data = _post_json(TOKEN_PATH, {
            "client_id": os.environ.get("SQUARE_CLIENT_ID", ""),
            "client_secret": os.environ.get("SQUARE_CLIENT_SECRET", ""),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
        })
    except Exception as ex:                      # network, HTTP error, bad JSON
        raise OSError(f"token exchange failed: {type(ex).__name__}: {ex}") from ex
    if "access_token" not in data:
        raise ValueError(f"token exchange returned no access token: "
                         f"{data.get('error_description') or data.get('error') or data}")
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "merchant_id": data.get("merchant_id", ""),
        "scopes": data.get("scopes", SCOPES),
        "expires_at": time.time() + int(data.get("expires_in", 0) or 0),
        "obtained_at": time.time(),
    }


def refresh(tokens):
    if not tokens.get("refresh_token"):
        raise ValueError("no refresh token on file - reconnect instead")
    data = _post_json(TOKEN_PATH, {
        "client_id": os.environ.get("SQUARE_CLIENT_ID", ""),
        "client_secret": os.environ.get("SQUARE_CLIENT_SECRET", ""),
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    })
    tokens = dict(tokens)
    tokens["access_token"] = data["access_token"]
    tokens["refresh_token"] = data.get("refresh_token", tokens["refresh_token"])
    tokens["expires_at"] = time.time() + int(data.get("expires_in", 0) or 0)
    return tokens


def revoke(tokens):
    try:
        _post_json(REVOKE_PATH, {
            "client_id": os.environ.get("SQUARE_CLIENT_ID", ""),
            "client_secret": os.environ.get("SQUARE_CLIENT_SECRET", ""),
            "merchant_id": tokens.get("merchant_id", ""),
            "refresh_token": tokens.get("refresh_token", ""),
        })
        return True
    except Exception:
        return False


def status(tokens):
    """Human-readable connection state for the Sending screen."""
    if not configured():
        return {"state": "unconfigured",
                "detail": "SQUARE_CLIENT_ID and SQUARE_CLIENT_SECRET are not set in this "
                          "app's environment, so the connect button stays simulated."}
    if not tokens or not tokens.get("access_token"):
        return {"state": "not_connected",
                "detail": "Credentials are configured; no venue has connected yet."}
    left = int(tokens.get("expires_at", 0) - time.time())
    if left <= 0:
        return {"state": "expired",
                "detail": "The access token expired; the next sync will refresh it "
                          "automatically, or reconnect now."}
    return {"state": "connected",
            "detail": f"Merchant {tokens.get('merchant_id', '?')} connected with scopes "
                      f"{', '.join(tokens.get('scopes', []))}. Access token valid for "
                      f"another {left // 3600}h {(left % 3600) // 60}m."}
