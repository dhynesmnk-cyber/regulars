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


def do_import(fields, files):
    menu_names = []
    main = files.get("f_main", ("", b""))[1].decode("utf-8-sig", "replace")
    cust = files.get("f_cust", ("", b""))[1].decode("utf-8-sig", "replace")
    if not main.strip():
        return None, {"error": "No file received. Pick a CSV and try again."}
    head = main.lstrip("\ufeff").splitlines()[0] if main.strip() else ""
    norm = [re.sub(r"[^a-z0-9]", "", h.lower()) for h in head.split(",")]
    square = any(n in norm for n in ("itemvariationname", "createdat", "locationid",
                                     "orderid", "soldcount"))
    attested = fields.get("consent") == "1"
    if square:
        visits, people, rep = IMP.parse_square(main, cust, menu_names)
    else:
        visits, people, rep = IMP.parse_simple(main, menu_names)
    if rep.get("error"):
        return None, rep
    if not visits:
        return None, {"error": "No usable rows found. Check the file has a header row with at "
                                "least date, email and items (simple format) or a Square "
                                "transactions export."}
    menu = IMP.build_menu(visits)
    ds = IMP.to_dataset(visits, people, menu, datetime.now().replace(second=0, microsecond=0),
                        attested_consent=attested)
    rep.update(customers=ds["stats"]["customers"], menu_items=len(menu),
               unmatched=ds.get("unmatched_line_items", 0), mode="square" if square else "simple",
               attested=attested)
    if not attested and not any(c["email_consent"] for c in ds["customers"]):
        rep["warning"] = ("No marketing consent found in the file and none attested, so every "
                          "contact is quarantined under the Spam Act. Tick the consent box if "
                          "these people opted in at your POS, website or loyalty program.")
    return ds, rep
