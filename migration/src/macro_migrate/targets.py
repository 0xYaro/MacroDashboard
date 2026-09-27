"""Turn a scenario's prose target string into individual scenario_target rows.

CONTRACT.md: "These are what `targets hit x of n` counts, and `n` is the row
count." So the parse is only correct when the row count equals the denominator
the grading journal recorded for that scenario, and every migrated week is
checked against that oracle in verify.py.

The grammar the journals actually use:

    **Eq:** S&P 7,450-7,700, breadth 48-58%, VIX 16-22. **Crypto:** BTC ...

- A bold `**Label:**` opens a group. A group holds one or more targets.
- Inside a group, targets are separated by a comma or a semicolon, except for a
  comma that sits inside a thousands separator or inside parentheses.
- A fragment that opens with a comparator, a currency, a digit or a connective
  word is a continuation of the fragment before it, not a new target. That is
  what makes "gold < $4,400 hot, >$4,750 cold" one two-sided target rather than
  two, and it is why W36's S3 counts 7.
- A fragment that states a variable is ambiguous is not a forecast and is not a
  target. "the 2Y is ambiguous because growth and inflation pull opposite ways"
  is why W37's S3 counts 8 and not 9.
- Two adjacent fragments naming the same series are one two-sided target:
  "hot tail 2Y >4.50%; cold tail 2Y <4.05%" is one row.

Not every target is numeric. "Leadership: Nasdaq beats the Dow again" is
kind=manual, carries no series and no band, and still counts toward n.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .config import TARGET_SUBJECTS
from .textutil import all_numbers, strip_md

# Opens a group: a bold run whose first colon ends the label.
_GROUP = re.compile(r"\*\*\s*([^*:]+?)\s*:([^*]*)\*\*")

# A comma that is a thousands separator, or a comma/semicolon inside parens,
# never separates two targets.
_THOUSANDS = re.compile(r",(?=\d{3}(?!\d))")

_AMBIGUOUS = re.compile(r"\bis ambiguous\b", re.I)

# A fragment opening with one of these continues the fragment before it.
_CONT_WORDS = {
    "and", "or", "with", "using", "per", "from", "toward", "towards", "rather",
    "which", "that", "so", "as", "though", "while", "but", "if", "only", "not",
    "no", "on", "in", "at", "for", "to", "by", "vs", "versus", "then", "plus",
    "including", "before", "after", "since", "until", "given", "taking",
    "depending", "up", "down", "either", "otherwise", "instead", "also",
}
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.I)
_CONT_OPEN = re.compile(r"^\s*(?:[<>≤≥=+\-−$–—]|\d)")

_GT = re.compile(r"(?:>|≥|\babove\b|\bover\b|\bmore than\b|\bat least\b)", re.I)
_LT = re.compile(r"(?:<|≤|\bunder\b|\bbelow\b|\bless than\b)", re.I)
_GT_VERB = re.compile(r"\b(reclaims?|takes?|clears?|extends?|repairs?|beats?|holds? >)\b", re.I)
_LT_VERB = re.compile(r"\b(loses?|breaks?|lags?)\b", re.I)
_RANGE = re.compile(
    r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*([KMBT])?\s*"
    r"[–—-]\s*"
    r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*([KMBT])?"
)
_MAG = {"K": 1000, "M": 10 ** 6, "B": 10 ** 9, "T": 10 ** 12}


@dataclass
class Target:
    ordinal: int
    group: str
    label: str
    kind: str                      # 'band' | 'manual'
    series_key: str | None = None
    comparator: str | None = None  # 'between' | 'gt' | 'lt'
    lo: Decimal | None = None
    hi: Decimal | None = None
    text: str | None = None
    inferred: bool = False         # comparator was read from a verb, not a symbol
    notes: list[str] = field(default_factory=list)


def _split_groups(s: str) -> list[tuple[str, str]]:
    """[(group label, body)] in source order."""
    out: list[tuple[str, str]] = []
    marks = list(_GROUP.finditer(s))
    if not marks:
        return [("", s)]
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(s)
        body = (m.group(2) or "") + s[m.end():end]
        out.append((m.group(1).strip(), body.strip().strip(".").strip()))
    return out


def _split_fragments(body: str) -> list[str]:
    """Split a group body on top-level commas and semicolons."""
    protected = _THOUSANDS.sub("\u0001", body)
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in protected:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch in ",;" and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.replace("\u0001", ",").strip() for p in parts if p.strip()]


def _is_continuation(frag: str, subject: str | None) -> bool:
    """A fragment that carries no subject of its own continues the one before.

    A leading digit only marks a continuation when no series is named: "30Y
    5.30-5.60%" opens with a digit and is a target of its own, while ">$4,750
    cold" opens with a comparator and completes the gold target before it.
    """
    if subject is not None:
        return False
    if _CONT_OPEN.match(frag):
        return True
    head = _LEADING_ARTICLE.sub("", frag.strip())
    first = re.split(r"[\s,]+", head, maxsplit=1)[0].lower().strip(".:;")
    return first in _CONT_WORDS


def _subject(frag: str) -> str | None:
    low = frag.lower()
    for pat, key in TARGET_SUBJECTS:
        if re.search(pat, low):
            return key
    return None


def _num(raw: str, mag: str | None) -> Decimal:
    v = Decimal(raw.replace(",", ""))
    if mag:
        v *= _MAG[mag]
    return v


def _classify(frag: str, series_key: str | None) -> dict:
    """Comparator, lo and hi for one fragment, or kind=manual."""
    t = strip_md(frag)
    m = _RANGE.search(t)
    if m:
        a = _num(m.group(1), m.group(2))
        b = _num(m.group(3), m.group(4))
        # "$4,150-$4,400" and "$70K-$79K": a bare second number inherits the
        # magnitude of the first, which is how the journals write them.
        if m.group(2) and not m.group(4):
            b = b * _MAG[m.group(2)] if b < a else b
        lo, hi = (a, b) if a <= b else (b, a)
        return {"kind": "band", "comparator": "between", "lo": lo, "hi": hi}

    nums = all_numbers(t)
    plus_gt = re.search(r"\d[\d,\.]*\s*(?:K|M|B|T)?\s*\+", t) is not None
    if _GT.search(t) or plus_gt:
        if nums:
            return {"kind": "band", "comparator": "gt", "lo": max(nums), "hi": None}
    if _LT.search(t):
        if nums:
            return {"kind": "band", "comparator": "lt", "lo": None, "hi": min(nums)}
    if _GT_VERB.search(t) and nums:
        return {"kind": "band", "comparator": "gt", "lo": max(nums), "hi": None,
                "inferred": True}
    if _LT_VERB.search(t) and nums:
        if len(nums) >= 2:
            return {"kind": "band", "comparator": "between",
                    "lo": min(nums), "hi": max(nums), "inferred": True}
        return {"kind": "band", "comparator": "lt", "lo": None, "hi": min(nums),
                "inferred": True}
    if len(nums) >= 2:
        return {"kind": "band", "comparator": "between",
                "lo": min(nums), "hi": max(nums), "inferred": True}
    return {"kind": "manual"}


def parse_targets(raw: str | None) -> tuple[list[Target], list[str]]:
    """Return (targets in source order, dropped fragments with a reason)."""
    dropped: list[str] = []
    if not raw:
        return [], ["targets string absent"]

    out: list[Target] = []
    for group, body in _split_groups(raw):
        frags = _split_fragments(body)
        kept: list[tuple[str, str | None]] = []
        for frag in frags:
            clean = strip_md(frag).strip(" .")
            if not clean:
                continue
            if _AMBIGUOUS.search(clean):
                dropped.append("%s: %s [states a variable is ambiguous, not a "
                               "forecast]" % (group, clean))
                continue
            subj = _subject(clean)
            if kept and (_is_continuation(frag, subj) or
                         (subj is not None and subj == kept[-1][1])):
                kept[-1] = (kept[-1][0] + ", " + clean, kept[-1][1] or subj)
                continue
            kept.append((clean, subj))

        for label, subj in kept:
            c = _classify(label, subj)
            out.append(Target(
                ordinal=len(out) + 1,
                group=group,
                label=label,
                kind=c["kind"],
                series_key=subj if c["kind"] == "band" else None,
                comparator=c.get("comparator"),
                lo=c.get("lo"),
                hi=c.get("hi"),
                text=label if c["kind"] == "manual" else None,
                inferred=bool(c.get("inferred")),
            ))
    for i, t in enumerate(out, 1):
        t.ordinal = i
    return out, dropped
