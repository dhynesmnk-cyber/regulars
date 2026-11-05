"""Downloads: the audience CSV, the copy-paste text, and sample input files."""

import csv
import io
from datetime import datetime

import data as D
from state import AUDIT, CAT_LABEL, ESP, STATE, dataset, result
from web import download


def export_csv():
    r = result()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Regulars - ready-to-send audience"])
    w.writerow([f"# Venue: {dataset()['venue']['name']}, {dataset()['venue']['suburb']}"])
    w.writerow([f"# Sender: {ESP[STATE['sender']['tool']][0]}"])
    w.writerow([f"# Generated: {datetime.now().isoformat(timespec='seconds')}"])
    w.writerow([f"# {len(r['messages'])} messages. Every contact has consent on file or was "
                f"attested by the venue at import."])
    w.writerow(["# Import into your sender. Column names match common defaults."])
    w.writerow([])
    w.writerow(["Email", "First Name", "Last Name", "Subject", "Body", "Offer",
                "Offer Category", "Trigger", "Consent Source", "Consent Date", "Holdout"])
    for i, m in enumerate(r["messages"]):
        c = m["customer"]
        last = c["name"].split(" ", 1)
        w.writerow([c["email"], last[0], last[1] if len(last) > 1 else "", m["subject"],
                    m["body"], m["offer_label"], CAT_LABEL.get(m["offer_cat"], m["offer_cat"]),
                    m["trigger_name"], c.get("consent_source") or "", c.get("consent_at") or "",
                    "yes" if i % 10 == 9 else "no"])
    return buf.getvalue()


def export_copy():
    r = result()
    out = [f"REGULARS - {dataset()['venue']['name']}",
           f"Generated {datetime.now().isoformat(timespec='seconds')}",
           f"{len(r['messages'])} messages", "=" * 70, ""]
    by = {}
    for m in r["messages"]:
        by.setdefault(m["trigger_name"], []).append(m)
    for trig, ms in by.items():
        out += [f"### {trig.upper()} ({len(ms)} people)", "-" * 70]
        for m in ms[:3]:
            out += [f"TO: {m['customer']['name']} <{m['customer']['email']}>",
                    f"SUBJECT: {m['subject']}", "", m["body"], "",
                    f"WHY: {m['why'].replace('<b>', '').replace('</b>', '')}", "." * 70, ""]
        if len(ms) > 3:
            out += [f"... and {len(ms)-3} more in this group", "", ""]
    out += ["BEFORE YOU SEND", "=" * 70,
            "[ ] A named human has approved this batch",
            f"[ ] ABN in the footer is correct: {dataset()['venue']['abn']}",
            "[ ] Unsubscribe tag matches your sending tool (see the Sending screen)",
            "[ ] Reply-to is monitored by the venue",
            "[ ] Staff know about every offer in here - nothing worse than a coupon the till refuses",
            "[ ] The 10% holdout group has NOT been sent to",
            "[ ] No customer numbers (gaps, counts, ratios) were added to any email", ""]
    return "\n".join(out)


def get_export_csv(req):
    AUDIT.write("export", ip=req.ip,
                detail={"what": "audience_csv",
                        "region": STATE.get("region", "AU")})
    return download(export_csv(), "text/csv; charset=utf-8", "regulars-audience.csv")


def get_export_copy(req):
    AUDIT.write("export", ip=req.ip,
                detail={"what": "copy_txt",
                        "region": STATE.get("region", "AU")})
    return download(export_copy(), "text/plain; charset=utf-8", "regulars-copy.txt")


def get_sample_simple(req):
    return download(D.sample_simple(), "text/csv; charset=utf-8",
                    "regulars-sample-simple.csv")


def get_sample_square_transactions(req):
    tx, _ = D.sample_square()
    return download(tx, "text/csv; charset=utf-8", "sample-square-transactions.csv")


def get_sample_square_customers(req):
    _, cu = D.sample_square()
    return download(cu, "text/csv; charset=utf-8", "sample-square-customers.csv")
