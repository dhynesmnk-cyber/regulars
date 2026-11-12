"""
Operator commands that run inside a venue's container, against its /data.

    python3 admin.py verify-audit             exit 0 if the audit chain is intact, 1 if not
    python3 admin.py reset-owner              clear the owner's password and sessions
    python3 admin.py paid-until YYYY-MM-DD    record that the venue has paid up to that day

regularsctl calls these; you shouldn't need to by hand. reset-owner must run
while the app is stopped (regularsctl stops it first): the running app keeps
its settings in memory and would write the old password straight back.
"""

import sys


def verify_audit():
    from state import AUDIT
    ok, broken_at, lines = AUDIT.verify()
    if ok:
        print(f"audit chain intact: {lines} entries")
        return 0
    print(f"AUDIT CHAIN BROKEN at entry {broken_at} of {lines}: treat as a security incident")
    return 1


def reset_owner():
    from state import AUDIT, LOCK, STATE, persist
    with LOCK:
        had_password = bool(STATE.get("password_hash"))
        STATE["password_hash"] = None
        STATE["sessions"] = {}
        persist()
    AUDIT.write("config_change", actor="admin",
                detail={"what": "owner_reset", "via": "admin", "had_password": had_password})
    print("owner password and sessions cleared; issue a new setup link to let them back in")
    return 0


def paid_until(day):
    """Safe while the app runs: it only reads this file, and it writes the
    audit entry itself when it notices the new date."""
    from datetime import date

    import billing
    try:
        paid = date.fromisoformat(day)
    except ValueError:
        print(f"not a date: {day!r} (use YYYY-MM-DD)")
        return 2
    billing.write(paid, note="set by operator")
    print(f"paid up to {paid.isoformat()}")
    return 0


COMMANDS = {"verify-audit": (verify_audit, 0), "reset-owner": (reset_owner, 0),
            "paid-until": (paid_until, 1)}


def main(argv):
    if not argv or argv[0] not in COMMANDS or len(argv) - 1 != COMMANDS[argv[0]][1]:
        print(__doc__.strip())
        return 2
    fn, _nargs = COMMANDS[argv[0]]
    return fn(*argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
