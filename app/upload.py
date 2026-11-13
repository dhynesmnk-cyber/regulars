"""CSV upload: multipart parsing and turning a file into a dataset."""

import re
from datetime import datetime

import importer as IMP


def parse_multipart(raw, ctype):
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    if not m:
        return {}, {}
    boundary = ("--" + m.group(1)).encode()
    fields, files = {}, {}
    for part in raw.split(boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, bodypart = part.partition(b"\r\n\r\n")
        hdr = head.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]*)"', hdr)
        if not nm:
            continue
        fn = re.search(r'filename="([^"]*)"', hdr)
        if fn:
            files[nm.group(1)] = (fn.group(1), bodypart)
        else:
            fields[nm.group(1)] = bodypart.decode("utf-8", "replace")
    return fields, files


def _directory_only(headers):
    """The customer export on its own: names and emails, no items."""
    norm = {re.sub(r"[^a-z0-9]", "", h.lower()) for h in headers}
    return (not IMP.is_square(headers) and "items" not in norm
            and bool(norm & {"emailaddress", "squarecustomerid", "givenname", "firstname"}))


def do_import(fields, files, region="AU"):
    menu_names = []
    main = files.get("f_main", ("", b""))[1].decode("utf-8-sig", "replace")
    cust = files.get("f_cust", ("", b""))[1].decode("utf-8-sig", "replace")
    if not main.strip():
        return None, {"error": "No file received. Pick a CSV and try again."}
    head = IMP.header_row(main)
    if _directory_only(head):
        return None, {"error": "That looks like your customer export. Put the Items Detail "
                               "CSV (Transactions > Export) in the first box and the customer "
                               "export in the second."}
    square = IMP.is_square(head)
    attested = fields.get("consent") == "1"
    if square:
        visits, people, rep = IMP.parse_square(main, cust, menu_names, region)
    else:
        visits, people, rep = IMP.parse_simple(main, menu_names, region)
    if rep.get("error"):
        return None, rep
    if not visits:
        return None, {"error": "No usable rows found. Check the file has a header row with at "
                                "least date, email and items (simple format) or a Square "
                                "transactions export."}
    menu = IMP.build_menu(visits)
    ds = IMP.to_dataset(visits, people, menu, datetime.now().replace(second=0, microsecond=0),
                        attested_consent=attested)
    reachable = sum(1 for c in ds["customers"] if c["email"])
    rep.update(customers=ds["stats"]["customers"], menu_items=len(menu),
               unmatched=ds.get("unmatched_line_items", 0), mode="square" if square else "simple",
               attested=attested, with_email=reachable)
    rep.setdefault("orders", ds["stats"]["orders"])   # the simple format never set it: "0 visits read"
    if square and not reachable:
        rep["warning"] = ("None of these orders is linked to a customer with an email address, "
                          "so nobody can be messaged yet. Upload your customer export too "
                          "(Customers > Directory > Export) - that's where Square keeps the "
                          "email addresses.")
    elif not attested and not any(c["email_consent"] for c in ds["customers"]):
        rep["warning"] = ("No marketing consent found in the file and none attested, so every "
                          "contact is quarantined under the Spam Act. Tick the consent box if "
                          "these people opted in at your POS, website or loyalty program.")
    return ds, rep
