"""Read Square export files into plain dict rows.

Accepts a CSV, a ZIP of CSVs (Square bundles wide date ranges as monthly files)
or a directory of either. Columns are matched by header *name*. Problems in a
row never abort the load: bad rows are skipped or blanked and reported as
Issues so one odd cell doesn't cost you a two-year import.
"""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Iterator
from pathlib import Path

from . import schema, values

csv.field_size_limit(1 << 24)

MAX_ZIP_MEMBER_BYTES = 1 << 30  # refuse to inflate anything over 1 GiB


@dataclass
class Issue:
    source: str
    line: int
    column: str
    message: str
    severity: str = "warn"  # "warn": value blanked or row kept; "error": row skipped


@dataclass
class ParsedFile:
    source: str
    kind: str
    sha256: str
    headers: list[str] = field(default_factory=list)
    rows: Iterable[dict] = field(default_factory=list)  # a list, or a one-shot iterator when streamed
    issues: list[Issue] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)  # unknown columns (new in Square?)
    dropped_private: list[str] = field(default_factory=list)  # present but never stored
    fatal: str | None = None  # why the file was not parsed at all

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")


def decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), falling back to Windows-1252 from Excel."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_csv_bytes(name: str, data: bytes, *, stream: bool = False) -> ParsedFile:
    """Parse one export. With stream=True, ``rows`` is a one-shot iterator and
    ``issues`` fills in as it is consumed (so read it after loading)."""
    sha = hashlib.sha256(data).hexdigest()
    reader = csv.reader(io.StringIO(decode(data), newline=""))

    headers: list[str] = []
    for row in reader:
        if any(c.strip() for c in row):
            headers = [c.strip() for c in row]
            break
    if not headers:
        return ParsedFile(name, schema.KIND_UNKNOWN, sha, fatal="file is empty")

    normed = [schema.norm(h) for h in headers]
    kind = schema.classify(set(normed))
    pf = ParsedFile(name, kind, sha, headers=headers)

    if kind == schema.KIND_ITEMS_SUMMARY:
        pf.fatal = (
            "Items Summary CSV is aggregated (no timestamps or transaction IDs) "
            "and is not needed; skipped"
        )
        return pf
    if kind == schema.KIND_UNKNOWN:
        pf.fatal = "not recognised as a Square Transactions or Items Detail export"
        return pf

    fields = schema.FIELDS[kind]
    mapping: dict[int, tuple[str, str]] = {}  # column index -> (key, kind)
    seen_keys: set[str] = set()
    for idx, (h, n) in enumerate(zip(headers, normed)):
        if n in fields:
            key, vkind = fields[n]
            if key in seen_keys:
                pf.issues.append(Issue(name, 1, h, "duplicate column ignored"))
                continue
            seen_keys.add(key)
            mapping[idx] = (key, vkind)
        elif n in schema.PRIVATE:
            pf.dropped_private.append(h)
        elif n in schema.IGNORED:
            pass
        else:
            pf.unmapped.append(h)

    missing = schema.REQUIRED[kind] - set(normed)
    if missing:
        pf.fatal = "missing required column(s): " + ", ".join(sorted(missing))
        return pf

    rows = _iter_rows(pf, kind, mapping, reader)
    pf.rows = rows if stream else list(rows)
    return pf


def _iter_rows(pf, kind, mapping, reader):
    ordinals: Counter = Counter()
    ncols = len(pf.headers)
    for row in reader:
        if not any(c.strip() for c in row):
            continue
        line = reader.line_num
        if len(row) != ncols:
            pf.issues.append(
                Issue(pf.source, line, "", f"row has {len(row)} fields, header has {ncols}")
            )
            row = (row + [""] * ncols)[:ncols]
        rec = _build_record(pf, kind, mapping, row, line)
        if rec is None:
            continue
        _add_key(kind, rec, ordinals)
        yield rec


def _build_record(pf, kind, mapping, row, line):
    rec: dict = {}
    parse = {
        "money": values.parse_money,
        "pct": values.parse_pct,
        "qty": values.parse_qty,
    }
    d = t = None
    for idx, (key, vkind) in mapping.items():
        raw = row[idx]
        try:
            if vkind == "date":
                d = values.parse_date(raw)
            elif vkind == "time":
                t = values.parse_time(raw)
            elif vkind in parse:
                rec[key] = parse[vkind](raw)
            else:
                rec[key] = raw.strip() or None
        except ValueError as e:
            if vkind in ("date", "time"):
                pf.issues.append(Issue(pf.source, line, key, str(e), "error"))
                return None
            rec[key] = None
            pf.issues.append(Issue(pf.source, line, key, str(e)))
    if d is None or t is None:
        pf.issues.append(Issue(pf.source, line, "date/time", "missing", "error"))
        return None

    dt = datetime.combine(d, t)
    # Local wall-clock time, deliberately NOT converted to UTC: hour-of-day
    # analysis wants the venue's clock, and DST makes local->UTC ambiguous.
    rec["local_ts"] = dt.isoformat(sep=" ")
    rec["local_date"] = d.isoformat()
    rec["local_hour"] = dt.hour
    rec["local_dow"] = dt.weekday()  # Monday = 0
    rec["is_refund"] = int("refund" in (rec.get("event_type") or "").lower())
    if not rec.get("transaction_id"):
        pf.issues.append(Issue(pf.source, line, "transaction_id", "blank Transaction ID"))
    return rec


def _add_key(kind: str, rec: dict, ordinals: Counter) -> None:
    """Stable identity so re-loading overlapping exports is idempotent.

    Two genuinely identical rows in one file (the same item rung up twice in a
    sale) are kept apart by an ordinal that counts repeats within the file.
    """
    base = (
        rec.get("transaction_id") or "",
        rec.get("payment_id") or "" if kind == schema.KIND_TRANSACTIONS else "",
        rec.get("event_type") or "",
        rec["local_ts"],
    )
    # Counter keys are 16-byte digests, not tuples of strings, to keep memory flat.
    ident = hashlib.blake2b("\x1f".join((kind,) + base).encode(), digest_size=16).digest()
    n = ordinals[ident]
    ordinals[ident] += 1
    rec["row_key"] = hashlib.sha256(ident + str(n).encode()).hexdigest()[:32]


def _sources(path: Path):
    """Yield (name, bytes) for every CSV reachable from path."""
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.suffix.lower() in (".csv", ".zip") and not p.name.startswith("."):
                yield from _sources(p)
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in sorted(zf.infolist(), key=lambda i: i.filename):
                base = Path(info.filename).name
                if (
                    info.is_dir()
                    or not base.lower().endswith(".csv")
                    or info.filename.startswith("__MACOSX")
                    or base.startswith("._")
                ):
                    continue
                if info.file_size > MAX_ZIP_MEMBER_BYTES:
                    raise ValueError(f"{path.name}!{info.filename} is too large")
                yield f"{path.name}!{info.filename}", zf.read(info)
    else:
        yield path.name, path.read_bytes()


def parse_path(path: str | Path) -> Iterator[ParsedFile]:
    """Lazily yield one ParsedFile per CSV, so a ZIP of 24 monthly files only
    ever holds one month of rows in memory at a time."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    for name, data in _sources(path):
        yield parse_csv_bytes(name, data, stream=True)
