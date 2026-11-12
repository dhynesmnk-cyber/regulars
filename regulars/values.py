"""Pure value parsers. Each raises ValueError on input it cannot understand."""
from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal

_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d*)?|\d+(?:\.\d*)?|\.\d+)"
_MONEY_RE = re.compile(
    rf"""^\s*(?P<open>\()?\s*
         (?P<s1>[+-])?\s*
         (?:AUD|AU\$|A\$|\$)?\s*
         (?P<s2>[+-])?\s*
         (?P<num>{_NUM})\s*
         (?P<close>\))?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def parse_money(raw: str | None) -> int | None:
    """'$1,234.56' -> 123456, '-$3.20' -> -320, '($3.20)' -> -320, '' -> None.

    Returns integer cents so sums are exact.
    """
    if raw is None:
        return None
    s = raw.strip().replace(" ", " ").replace("−", "-")
    if not s:
        return None
    m = _MONEY_RE.match(s)
    if not m or bool(m["open"]) != bool(m["close"]) or (m["s1"] and m["s2"]):
        raise ValueError(f"not a money amount: {raw!r}")
    negative = bool(m["open"]) or "-" in (m["s1"] or "") + (m["s2"] or "")
    cents = (Decimal(m["num"].replace(",", "")) * 100).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )
    return int(-cents if negative else cents)


def parse_pct(raw: str | None) -> float | None:
    """'1.75%' -> 1.75, '1.75' -> 1.75, '' -> None."""
    if raw is None or not raw.strip():
        return None
    s = raw.strip().rstrip("%").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"not a percentage: {raw!r}") from None


def parse_qty(raw: str | None) -> float | None:
    """'2' -> 2.0, '0.500' -> 0.5, '-1' -> -1.0, '' -> None."""
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw.strip().replace(",", "").replace("−", "-"))
    except ValueError:
        raise ValueError(f"not a quantity: {raw!r}") from None


# Slash dates are read DAY-first (Australian). Month-first slash dates are not
# supported: they are ambiguous and silently swapping day and month would
# corrupt every hour-of-week chart.
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y")
_TIME_FORMATS = (
    "%H:%M:%S",
    "%H:%M",
    "%I:%M:%S %p",
    "%I:%M %p",
    "%I:%M:%S%p",
    "%I:%M%p",
)


def parse_date(raw: str | None) -> date:
    s = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognised date: {raw!r}")


def parse_time(raw: str | None) -> time:
    s = (raw or "").strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"unrecognised time: {raw!r}")
