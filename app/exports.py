"""Downloads: the audience CSV, the copy-paste text, and sample input files."""

import csv
import io
from datetime import datetime

import engine as E
from state import (AUDIT, CAT_LABEL, ESP, STATE, approved_messages, flash, identity, mark,
                   now, persist, send_block, unsub_tag)
from web import download, redirect


def batches():
    """Exported batches, oldest first: batch id -> its ledger entries."""
    out = {}
    for x in STATE["sent"]:
        if x.get("batch"):
            out.setdefault(x["batch"], []).append(x)
    return out


def export_csv(entries):
    """One batch as the file the owner imports into their email tool. Built
    from the ledger, so it is exactly what was approved, edits included."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Regulars - ready-to-send audience"])
    v = identity()
    w.writerow([f"# Venue: {v['name']}, {v['suburb']}"])
    w.writerow([f"# Sender: {ESP[STATE['sender']['tool']][0]}"])
    w.writerow([f"# Exported: {entries[0].get('exported_at', '') if entries else ''}"])
    w.writerow([f"# {len(entries)} messages, each approved by the venue. Every contact has "
                f"consent on file or was attested by the venue at import."])
    w.writerow(["# Import into your sender. Column names match common defaults."])
    w.writerow([])
    w.writerow(["Email", "First Name", "Last Name", "Subject", "Body", "Offer",
                "Offer Category", "Trigger", "Consent Source", "Consent Date", "Holdout"])
    for x in entries:
        name = x["name"].split(" ", 1)
        basis = x.get("consent_basis") or ""
        w.writerow([x["email"], name[0], name[1] if len(name) > 1 else "", x["subject"],
                    x["body"], x["offer_label"], CAT_LABEL.get(x["offer_cat"], x["offer_cat"]),
                    x["trigger_name"], "" if basis == "unrecorded" else basis,
                    x.get("consent_at") or "", "yes" if x.get("holdout") else "no"])
    return buf.getvalue()


def export_copy(entries):
    v = identity()
    out = [f"REGULARS - {v['name']}",
           f"Exported {entries[0].get('exported_at', '') if entries else ''}",
           f"{len(entries)} messages", "=" * 70, ""]
    by = {}
    for x in entries:
        by.setdefault(x["trigger_name"], []).append(x)
    for trig, xs in by.items():
        out += [f"### {trig.upper()} ({len(xs)} people)", "-" * 70]
        for x in xs[:3]:
            out += [f"TO: {x['name']} <{x['email']}>", f"SUBJECT: {x['subject']}", "",
                    x["body"], "", "." * 70, ""]
        if len(xs) > 3:
            out += [f"... and {len(xs)-3} more in this group", "", ""]
    out += ["BEFORE YOU SEND", "=" * 70,
            "[ ] A named human has approved this batch",
            f"[ ] ABN in the footer is correct: {v['abn']}",
            "[ ] Unsubscribe tag matches your sending tool (see the Sending screen)",
            "[ ] Reply-to is monitored by the venue",
            "[ ] Staff know about every offer in here - nothing worse than a coupon the till refuses",
            "[ ] The 10% holdout group has NOT been sent to",
            "[ ] No customer numbers (gaps, counts, ratios) were added to any email", ""]
    return "\n".join(out)


def ledger_entry(m, approval, batch, when, position):
    """The permanent record of one exported message: who, the exact words
    (the owner's edit if they made one), the consent basis, and whether it
    falls in the batch's holdout (every tenth)."""
    c = m["customer"]
    body = approval.get("body")
    if body and approval.get("unsub_tag") and approval["unsub_tag"] != unsub_tag():
        body = body.replace(approval["unsub_tag"], unsub_tag())
    return {
        "id": m["id"], "customer_id": m["customer_id"], "name": c["name"],
        "email": c["email"], "trigger_name": m["trigger_name"],
        "offer_label": m["offer_label"], "offer_cat": m["offer_cat"],
        "offer_cost": m["offer_cost"],
        "subject": approval.get("subject") or m["subject"],
        "body": body or m["body"],
        "approver": approval.get("approver") or "venue manager",
        "approved_at": approval.get("approved_at", ""),
        "consent_basis": c.get("consent_source") or "unrecorded",
        "consent_at": c.get("consent_at") or "",
        "batch": batch, "exported_at": when, "holdout": position % 10 == 9,
    }


def _refused():
    """Approve and export share one rule: state.send_block()."""
    msg = send_block()
    if not msg:
        return None
    flash(msg)
    return redirect("/")


def post_export(req):
    """Make a batch from the approved messages. Only now are they recorded as
    sent and their customers' frequency cap started."""
    refused = _refused()
    if refused:
        return refused
    ready = approved_messages()
    if not ready:
        flash("Approve at least one message first: the export carries only what you've "
              "approved.")
        return redirect("/inbox")
    stamp = datetime.now()
    batch = stamp.strftime("%Y%m%d-%H%M%S")
    while batch in batches():
        batch += "x"
    when = stamp.strftime("%Y-%m-%d %H:%M")
    for i, (m, a) in enumerate(ready):
        STATE["sent"].append(ledger_entry(m, a, batch, when, i))
        STATE["sent_history"][m["customer_id"]] = now().isoformat()
        STATE["approved"].pop(m["id"], None)
    persist()
    if STATE.get("dataset") is not None:
        mark("first_export")           # the venue's first real batch out the door
    AUDIT.write("export", ip=req.ip,
                detail={"what": "batch", "batch": batch, "messages": len(ready),
                        "region": STATE.get("region", "AU")})
    flash(f"Batch of {len(ready)} exported. Download the file below and import it into your "
          f"email tool.")
    return redirect("/sent")


def _batch_for(req):
    """The batch a download asks for (the latest by default), or a redirect."""
    refused = _refused()
    if refused:
        return None, refused
    all_ = batches()
    bid = req.q.get("batch") or (list(all_)[-1] if all_ else None)
    if bid not in all_:
        flash("Nothing has been exported yet. Approve the messages you want to send, then "
              "choose Export.")
        return None, redirect("/inbox")
    return bid, None


def get_export_csv(req):
    bid, refused = _batch_for(req)
    if refused:
        return refused
    AUDIT.write("export", ip=req.ip,
                detail={"what": "audience_csv", "batch": bid,
                        "region": STATE.get("region", "AU")})
    return download(export_csv(batches()[bid]), "text/csv; charset=utf-8",
                    f"regulars-batch-{bid}.csv")


def get_export_copy(req):
    bid, refused = _batch_for(req)
    if refused:
        return refused
    AUDIT.write("export", ip=req.ip,
                detail={"what": "copy_txt", "batch": bid,
                        "region": STATE.get("region", "AU")})
    return download(export_copy(batches()[bid]), "text/plain; charset=utf-8",
                    f"regulars-batch-{bid}.txt")


def get_sample_simple(req):
    return download(E.PACK.sample_simple(), "text/csv; charset=utf-8",
                    "regulars-sample-simple.csv")


def get_sample_square_transactions(req):
    tx, _ = E.PACK.sample_square()
    return download(tx, "text/csv; charset=utf-8", "sample-square-transactions.csv")


def get_sample_square_customers(req):
    _, cu = E.PACK.sample_square()
    return download(cu, "text/csv; charset=utf-8", "sample-square-customers.csv")
