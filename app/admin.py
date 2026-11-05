"""
Operator commands that run inside a venue's container, against its /data.

    python3 admin.py verify-audit    exit 0 if the audit chain is intact, 1 if not
    python3 admin.py reset-owner     clear the owner's password and sessions

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


COMMANDS = {"verify-audit": verify_audit, "reset-owner": reset_owner}


def main(argv):
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(__doc__.strip())
        return 2
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
