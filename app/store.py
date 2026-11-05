"""
Tiny persistence layer. Everything the merchant configures - goals, the offer
catalogue, trigger thresholds, which triggers are on, the sending setup and the
send ledger - is written to one JSON file beside the app, so a restart does not
wipe an afternoon of setup.

Imported CSV data is deliberately NOT persisted: it can be large, and the
prototype should never silently hold a venue's customer list on disk beyond the
running process. The upload screen says so.
"""

import json
import os

PATH = os.environ.get("REGULARS_STATE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "regulars-state.json")

KEYS = ("connected", "source", "goals", "enabled", "thresholds", "sender",
        "sent", "sent_history", "approved", "sessions", "password_hash", "setup_burned",
        "region", "venue_address", "business", "oauth_tokens_enc", "schema")


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: raw.get(k) for k in KEYS if k in raw}
    except (OSError, ValueError):
        return {}


def save(state):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: state.get(k) for k in KEYS}, f, indent=1, default=list)
    os.replace(tmp, PATH)


def clear():
    try:
        os.remove(PATH)
    except OSError:
        pass
