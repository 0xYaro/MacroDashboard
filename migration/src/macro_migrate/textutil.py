"""Text, number and identity helpers shared by every reader.

Two rules the whole migration obeys:

- A value that cannot be read out of the source is `None`, never a zero and
  never a guess. Every `None` is reported by name in VERIFICATION.md.
- Every identifier is a uuid5 over a natural key, so a second run of this tool
  produces byte-identical output and a re-run is an upsert, not a duplicate.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

# Fixed namespace. Never change it: the ids in out/ are derived from it.
NS = uuid.UUID("4f6c0f6e-7a1d-5c3b-9d2e-6a1f0b8c4d25")

MINUS = "−"       # true minus sign, used throughout the journals
ENDASH = "–"
EMDASH = "—"


def uid(*parts: str) -> str:
    """Deterministic uuid5 over a natural key."""
    return str(uuid.uuid5(NS, "|".join(parts)))


def strip_md(s: str | None) -> str:
    """Drop bold/italic markers, wikilink brackets and HTML line breaks."""
    if not s:
        return ""
    t = s.replace("==", "")
    t = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", r"\1", t)
    t = t.replace("**", "").replace("<br>", " ")
    t = re.sub(r"(?<!\w)\*(?!\s)([^*]+?)(?<!\s)\*(?!\w)", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_dashes(s: str) -> str:
    return s.replace(MINUS, "-").replace(ENDASH, "-").replace(EMDASH, "-")


_MAG = {"k": Decimal(10) ** 3, "m": Decimal(10) ** 6,
        "b": Decimal(10) ** 9, "t": Decimal(10) ** 12}

# A signed number, optionally $-prefixed, optionally with a K/M/B/T magnitude.
_NUM = re.compile(
    r"(?P<sign>[+\-−])?\s*\$?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<mag>[KMBT])?\b",
)


def first_number(s: str | None) -> Decimal | None:
    """The first signed, magnitude-aware number in a display string."""
    if not s:
        return None
    m = _NUM.search(strip_md(s))
    if not m:
        return None
    v = Decimal(m.group("num").replace(",", ""))
    mag = (m.group("mag") or "").lower()
    if mag:
        v *= _MAG[mag]
    if m.group("sign") in ("-", MINUS):
        v = -v
    return v


def all_numbers(s: str | None) -> list[Decimal]:
    out: list[Decimal] = []
    if not s:
        return out
    for m in _NUM.finditer(strip_md(s)):
        v = Decimal(m.group("num").replace(",", ""))
        mag = (m.group("mag") or "").lower()
        if mag:
            v *= _MAG[mag]
        if m.group("sign") in ("-", MINUS):
            v = -v
        out.append(v)
    return out


_BPS = re.compile(r"(\d+(?:\.\d+)?)\s*bps", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def oas_bps(display: str | None) -> Decimal | None:
    """Option-adjusted spread in basis points.

    CONTRACT.md pins HY and IG OAS to basis points because every condition is
    written in basis points. The journal prints both forms ("2.70% (270 bps)")
    and occasionally only the percent, so the explicit bps figure wins and a
    bare percent is converted rather than stored as 2.70.
    """
    if not display:
        return None
    t = strip_md(display)
    m = _BPS.search(t)
    if m:
        return Decimal(m.group(1))
    m = _PCT.search(t)
    if m:
        return Decimal(m.group(1)) * 100
    return None


def dec(v) -> str:
    """Render a Decimal for SQL without exponent notation."""
    d = Decimal(str(v))
    s = format(d, "f")
    return s


def sql_str(s: str | None) -> str:
    if s is None:
        return "NULL"
    return "'" + s.replace("'", "''") + "'"


def sql_num(v) -> str:
    if v is None:
        return "NULL"
    return dec(v)


def sql_int(v) -> str:
    if v is None:
        return "NULL"
    return str(int(v))


def sql_bool(v) -> str:
    if v is None:
        return "NULL"
    return "true" if v else "false"


def sql_date(s: str | None) -> str:
    if not s:
        return "NULL"
    return "DATE " + sql_str(s)


def sql_ts(s: str | None) -> str:
    """A date in the vault becomes midnight UTC. Doctrine: timestamptz in UTC."""
    if not s:
        return "NULL"
    if len(s) == 10:
        s = s + "T00:00:00Z"
    return "TIMESTAMPTZ " + sql_str(s)


def sql_text_array(items) -> str:
    if items is None:
        return "NULL"
    if not items:
        return "ARRAY[]::text[]"
    return "ARRAY[" + ", ".join(sql_str(i) for i in items) + "]::text[]"


def sql_int_array(items) -> str:
    if items is None:
        return "NULL"
    if not items:
        return "ARRAY[]::int[]"
    return "ARRAY[" + ", ".join(str(int(i)) for i in items) + "]::int[]"


def sql_jsonb(obj) -> str:
    import json
    if obj is None:
        return "NULL"
    return sql_str(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))) + "::jsonb"


def series_ref(key: str) -> str:
    """A series reference by key, resolved at load time.

    config/series.yaml is written by a parallel agent. Until it lands these keys
    are proposals; the preflight block at the head of every emitted file fails
    loudly on any key the database does not hold, so an unreconciled key can
    never load as a silent null.
    """
    return "(SELECT id FROM macro_core.series WHERE key = %s)" % sql_str(key)


WEEK_RE = re.compile(r"\b(?:(\d{4})-)?W(\d{2})\b")


def norm_week(token: str, default_year: str = "2026") -> str | None:
    """'W27' or '2026-W27' -> '2026-W27'."""
    m = WEEK_RE.search(token or "")
    if not m:
        return None
    return "%s-W%s" % (m.group(1) or default_year, m.group(2))
