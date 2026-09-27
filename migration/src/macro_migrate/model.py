"""Turn the source shapes into macro_core rows.

Every row carries a deterministic uuid5 over its natural key, so re-running the
migration is an upsert and the emitted SQL is byte-identical run to run.

What this module refuses to do is guess. A field the source does not carry is
None and is named in `findings`, which VERIFICATION.md prints per week. The
migration is required to be verifiable by hand, and a plausible invented value
is the one thing that makes that impossible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from . import config, sources
from .targets import parse_targets
from .textutil import (first_number, norm_week, oas_bps, strip_md,
                       uid)

ASSETS = ("equities", "crypto")


ORDINAL_OF = {"S1": 1, "S2": 2, "S3": 3}
BANDS = ("CRITICAL", "HOLD", "WATCH", "AVOID")


@dataclass
class Finding:
    week: str
    subject: str
    sentence: str


@dataclass
class Build:
    rows: dict[str, list[dict]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    series_keys: dict[str, dict] = field(default_factory=dict)
    read_log: dict[str, dict] = field(default_factory=dict)

    def add(self, table: str, row: dict) -> dict:
        self.rows.setdefault(table, []).append(row)
        return row

    def flag(self, week: str, subject: str, sentence: str) -> None:
        self.findings.append(Finding(week, subject, sentence))

    def count(self, table: str) -> int:
        return len(self.rows.get(table, []))


# ---------------------------------------------------------------------------
# small parsers
# ---------------------------------------------------------------------------

_PCT_RANGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*%")
_PCT_ONE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_OPERATED = re.compile(r"\b(operated(?:\s+(?:at|in))?|sit at|run at|run in)\b", re.I)


def parse_exposure(raw: str | None) -> tuple[Decimal | None, Decimal | None,
                                             Decimal | None, Decimal | None]:
    """(band lo, band hi, operated lo, operated hi) from the exposure line."""
    if not raw:
        return (None, None, None, None)
    t = strip_md(raw)
    m = _PCT_RANGE.search(t)
    lo, hi = (Decimal(m.group(1)), Decimal(m.group(2))) if m else (None, None)
    olo = ohi = None
    om = _OPERATED.search(t)
    if om:
        tail = t[om.end():]
        rm = _PCT_RANGE.search(tail)
        if rm:
            olo, ohi = Decimal(rm.group(1)), Decimal(rm.group(2))
        else:
            sm = _PCT_ONE.search(tail)
            if sm:
                olo = ohi = Decimal(sm.group(1))
    return (lo, hi, olo, ohi)


def split_clauses(trigger: str | None) -> list[str]:
    """Split a trigger on top-level semicolons.

    Joining the result with '; ' reproduces the trigger verbatim, so nothing the
    journal wrote is lost by the split. test_roundtrip asserts that.
    """
    if not trigger:
        return []
    out, depth, buf = [], 0, []
    for ch in trigger:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf).strip())
    return [c for c in out if c]


_DISJUNCT = re.compile(r"\bany of\b|\bhot tail\b|\bcold tail\b|\bor\b", re.I)


def clause_required(clause: str, trigger: str) -> bool | None:
    """Whether a clause must hold.

    The journals never state this in a field, so it is read from the shape of
    the trigger: a clause inside an 'Any of' / 'Hot tail / Cold tail'
    alternation is one of several, everything else in a semicolon-joined
    trigger must hold. Where the shape is not decidable the value is None.
    """
    if re.search(r"\bany of\b|\bhot tail\b|\bcold tail\b", trigger, re.I):
        return False
    if _DISJUNCT.search(clause):
        return None
    return True


_MONEY = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*([KMBT])?")
_MAG = {"K": 1000, "M": 10 ** 6, "B": 10 ** 9, "T": 10 ** 12}


_ENTRY_BAND = re.compile(
    r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*([KMBT])?\s*[–—-]\s*"
    r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*([KMBT])?")


def _level(raw: str | None) -> Decimal | None:
    """The first level in a cell, scaled by its own magnitude or its range's.

    `Target $65-66K box high` is a range whose magnitude is written once, at
    its end. Reading the first number alone gave W34's target as 65, which then
    made its long read as a short and its invalidation side flip to `above`.
    """
    if not raw:
        return None
    text = strip_md(raw)
    m = _MONEY.search(text)
    if not m:
        return None
    band = _ENTRY_BAND.search(text)
    if band is not None and band.start() == m.start():
        lo, _, _ = parse_entry(text)
        return lo
    v = Decimal(m.group(1).replace(",", ""))
    if m.group(2):
        v *= _MAG[m.group(2)]
    return v


def parse_entry(raw: str | None) -> tuple[Decimal | None, Decimal | None, str | None]:
    """(entry_lo, entry_hi, gate text) from a setup's Entry cell."""
    if not raw:
        return (None, None, None)
    t = strip_md(raw)
    m = _ENTRY_BAND.search(t)
    if not m:
        return (None, None, t)
    lo = Decimal(m.group(1).replace(",", ""))
    hi = Decimal(m.group(3).replace(",", ""))
    # A magnitude written once applies to both ends: `$62.0-62.5K` is 62,000 to
    # 62,500, not 62 to 62,500, and `$62K-62.5` is the same band written the
    # other way. W32 and W34 were migrated with a floor of 62.
    lo_mag = m.group(2) or m.group(4)
    hi_mag = m.group(4) or m.group(2)
    if lo_mag:
        lo *= _MAG[lo_mag]
    if hi_mag:
        hi *= _MAG[hi_mag]
    gate = (t[:m.start()] + " " + t[m.end():]).strip(" ,.;")
    gate = re.sub(r"\s+", " ", gate) or None
    return (lo, hi, gate)


def parse_invalidation(raw: str | None) -> tuple[Decimal | None, str | None]:
    if not raw:
        return (None, None)
    t = strip_md(raw)
    lvl = _level(t)
    low = t.lower()
    if "close-above" in low or "close above" in low or low.startswith("above"):
        return (lvl, "above")
    if "close-below" in low or "close below" in low or "below" in low:
        return (lvl, "below")
    # `daily close <$62K` states the side with a symbol rather than a word.
    if re.search(r"close\s*<", low):
        return (lvl, "below")
    if re.search(r"close\s*>", low):
        return (lvl, "above")
    return (lvl, None)


def infer_invalidation_dir(invalidation, target) -> str | None:
    """A setup's invalidation side, from its own target.

    `invalidation_dir` is NOT NULL in supabase/migrations/0003, and three Setup
    Ledger cells name a level with no side ("$1,990", "$81,500"). The side is not
    guessed: a setup whose target sits above its invalidation is a long and is
    invalidated below, and one whose target sits below it is a short and is
    invalidated above. The oracle in verify.py checks that rule against every
    row where the journal *did* write the side, and it agrees on all of them.
    """
    if invalidation is None or target is None:
        return None
    if target > invalidation:
        return "below"
    if target < invalidation:
        return "above"
    return None


_RR = re.compile(r"(\d+(?:\.\d+)?)\s*:\s*1")


def parse_rr(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    m = _RR.search(strip_md(raw))
    return Decimal(m.group(1)) if m else None


_STYLE = re.compile(r"\(([^)]*?(?:FTR|SFP|O/U|Cluster|HTF|LTF)[^)]*?)\)", re.I)


def parse_style(name: str | None) -> str | None:
    if not name:
        return None
    m = _STYLE.search(name)
    return m.group(1).strip() if m else None


#: Which config/series.yaml key an instrument named in a setup's title is a
#: level on, and which asset column that key is allowed to appear under.
#:
#: An equity setup is quoted against the cash index and never the future: the
#: cash index is what every phase condition, scenario band and setup level in
#: the journal is written against, and R1 exists because the two were once
#: conflated in the week-over-week arithmetic. A crypto setup is quoted against
#: the spot coin.
SETUP_SERIES: tuple[tuple[str, str, str], ...] = (
    (r"\bBTC\b|\bbitcoin\b", "btc", "crypto"),
    (r"\bETH\b|\bether(?:eum)?\b", "eth", "crypto"),
    (r"\bSOL\b|\bsolana\b", "sol", "crypto"),
    (r"\bnasdaq\b|\bNDX\b|\bQQQ\b|\bIXIC\b", "nasdaq_cash", "equities"),
    (r"S&P\s*500|S&P\b|\bSPX\b|\bSPY\b|\bES=F\b", "spx_cash", "equities"),
    (r"\bdow\b|\bDJIA\b|\bDJI\b", "dow_cash", "equities"),
    (r"\brussell\b|\bRUT\b|\bIWM\b", "rut_cash", "equities"),
)

_SETUP_SERIES = tuple(
    (re.compile(pattern, re.I), key, asset) for pattern, key, asset in SETUP_SERIES
)


def parse_setup_series(name: str | None, asset: str) -> tuple[str | None, str]:
    """Which series a setup's entry, invalidation and target are levels on.

    `macro_core.setup.series_id` is nullable and the worker refuses to score a
    setup that leaves it null, so the migration has to resolve it or say why it
    could not. The title is the only place the journal names the instrument in
    a machine-readable way, and every setup written since the Setup Ledger
    began leads with it.

    Nothing is guessed. A title naming no instrument, or naming two, resolves
    to null with a sentence: deriving the series from the entry level instead
    would put a Nasdaq setup on the S&P the day the two indices trade in the
    same thousands, and the resulting entry state, R-multiple and Setup Ledger
    row would all be wrong with none of them visibly so.

    Returns the key, or null, and the sentence that says which happened.
    """
    if not name:
        return None, "the setup has no name to read an instrument from"
    found = sorted({
        key for pattern, key, owner in _SETUP_SERIES
        if owner == asset and pattern.search(name)
    })
    if len(found) == 1:
        return found[0], "read from the setup title"
    if not found:
        return None, (
            "the setup title names no instrument this migration maps to a "
            "config/series.yaml key"
        )
    return None, (
        "the setup title names more than one instrument (%s), so no single "
        "series is the one its levels are quoted on" % ", ".join(found)
    )


_SECTOR_ROW = re.compile(
    r"(?:Sits under the|References)\s+\*{0,2}(CRITICAL|HOLD|WATCH|AVOID)\*{0,2}", re.I)


def parse_sector_row(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _SECTOR_ROW.search(raw)
    return m.group(1).upper() if m else None


_OUTCOME = {
    "not triggered": "not triggered",
    "triggered → stopped": "triggered → stopped",
    "triggered → target": "triggered → target",
    "open": "open",
}


def parse_outcome(cell: str) -> tuple[str | None, str | None]:
    """('not triggered (W36)') -> ('not triggered', '2026-W36')."""
    t = strip_md(cell)
    week = norm_week(t)
    body = re.sub(r"\((?:grade in\s+)?W\d{2}\)", "", t).strip()
    body = body.replace("->", "→").strip()
    for k, v in _OUTCOME.items():
        if body.lower().startswith(k):
            if k == "open":
                # "open (grade in W40)" names the week it will be graded in, not
                # a week in which it was graded.
                return (v, None)
            return (v, week)
    return (None, week)


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

ACTIVE_WEEK = config.JUDGMENT_WEEKS[-1]


def lifecycle(week: str, successor_id: str | None) -> dict:
    return {
        "status": "active" if week == ACTIVE_WEEK else "superseded",
        "authored_at": config.AUTHORED_ON.get(week),
        "activated_at": config.AUTHORED_ON.get(week),
        "superseded_by": successor_id,
    }


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def build_rules(b: Build) -> None:
    rules = sources.read_scenario_rules()
    for r in rules:
        b.add("scenario_rule", {
            "number": r["number"],
            "text": r["text"],
            "retired_week": r["retired_week"],
        })
        for w in r["weeks"]:
            b.add("scenario_rule_week", {"rule_number": r["number"], "week": w})
    b.read_log["scenario-rules.md"] = {
        "rules": len(rules),
        "teaching_weeks": sum(len(r["weeks"]) for r in rules),
    }


_GRADE = {"materialized": "Materialized", "partial": "Partial", "not": "Not"}
_OTHER = re.compile(
    r"\b(S[123])\b\s*(\d{1,3})\s*%\s*(?:→|->)\s*"
    r"(Materialized|Partial|Not)", re.I)


def build_week_grades(b: Build, sidecars: dict[str, dict]) -> None:
    rows = sources.read_scorecard_ledger()
    # Per-scenario detail from the grading week's sidecar, where one exists.
    detail: dict[str, list[dict]] = {}
    for graded, grader in config.GRADED_BY.items():
        sc = sidecars.get(grader)
        if sc and sc.get("scorecard", {}).get("scenario_grades"):
            detail[graded] = sc["scorecard"]["scenario_grades"]

    for r in rows:
        week = r["Week"].strip()
        base = strip_md(r.get("Base case (prob)", ""))
        grade = _GRADE.get(strip_md(r.get("Base grade", "")).lower())
        others = []
        for m in _OTHER.finditer(strip_md(r.get("Other scenarios (prob → grade)", ""))):
            others.append({"id": m.group(1).upper(),
                           "probability": int(m.group(2)),
                           "grade": _GRADE[m.group(3).lower()],
                           "source": "Scorecard Ledger"})
        base_id = None
        base_ordinal = None
        m = re.match(r"\s*(S[123])\b", base)
        if m:
            base_ordinal = ORDINAL_OF[m.group(1).upper()]
        if week in config.JUDGMENT_WEEKS and base_ordinal:
            sc = sidecars.get(week)
            titles = {ORDINAL_OF[s["id"]]: s["title"]
                      for s in (sc or {}).get("house_view", {}).get("scenarios", [])}
            title = titles.get(base_ordinal)
            ledger_title = None
            tm = re.search(r'"([^"]+)"', base)
            if tm:
                ledger_title = tm.group(1)
            if title and ledger_title and title.strip() == ledger_title.strip():
                base_id = uid("scenario", week, str(base_ordinal))
            elif title:
                b.flag(week, "week_grade.base_scenario_id",
                       "ledger base title %r does not match the migrated "
                       "scenario %r, so base_scenario_id is left null."
                       % (ledger_title, title))
        elif week in config.JUDGMENT_WEEKS:
            b.flag(week, "week_grade.base_scenario_id",
                   "the ledger's base case cell names no S1/S2/S3, so the row "
                   "cannot be linked to a migrated scenario.")

        # Merge the sidecar's per-scenario detail into other_grades.
        if week in detail:
            by_id = {d["id"]: d for d in detail[week]}
            for o in others:
                d = by_id.get(o["id"])
                if d:
                    o.update({"targets_hit": d.get("targets_hit"),
                              "targets_n": d.get("targets_n"),
                              "title": d.get("title"),
                              "note": d.get("note"),
                              "source": "sidecar %s" % config.GRADED_BY[week]})

        hit_cell = strip_md(r.get("Targets hit", ""))
        hits = re.match(r"(\d+)\s+of\s+(\d+)", hit_cell)
        brier_cell = strip_md(r.get("Brier", ""))
        try:
            brier = Decimal(brier_cell)
        except Exception:
            brier = None
        if hits is None:
            b.flag(week, "week_grade.targets_hit/targets_n",
                   "the ledger carries %r; rows before 2026-W36 predate the "
                   "column, so both are null." % (hit_cell or "-"))
        if brier is None:
            b.flag(week, "week_grade.brier",
                   "the ledger carries %r; rows before 2026-W36 predate the "
                   "column, so brier is null." % (brier_cell or "-"))

        grader = config.GRADED_BY.get(week)
        graded_at = config.AUTHORED_ON.get(grader) if grader else None
        if graded_at is None:
            # graded_at is nullable, so the honest value is null. The row for
            # week N was written by journal N+1, and for these weeks that journal
            # is in the 2026-W03 to 2026-W31 archive which docs/DECISIONS.md says
            # not to read. The Monday of week N+1 would be a manufactured fact.
            b.flag(week, "week_grade.graded_at",
                   "graded by an archive journal (2026-W03 to 2026-W31) that "
                   "this migration does not read, so no authoring date is "
                   "known and graded_at is null.")

        b.add("week_grade", {
            "week": week,
            "base_scenario_id": base_id,
            "base_scenario_label": base,
            "base_grade": grade,
            "other_grades": others,
            "eq_call": strip_md(r.get("Eq call", "")) or None,
            "crypto_call": strip_md(r.get("Crypto call", "")) or None,
            "key_miss": strip_md(r.get("Key miss", "")) or None,
            "targets_hit": int(hits.group(1)) if hits else None,
            "targets_n": int(hits.group(2)) if hits else None,
            "brier": brier,
            "premise_moved": False,
            "graded_at": graded_at,
            "_graded_by": grader,
        })
        b.flag(week, "week_grade.premise_moved",
               "no source carries this tag; the flag was introduced with this "
               "system, so every migrated row is false rather than null.")
    b.read_log["Scorecard Ledger.md"] = {"rows": len(rows)}


def build_setups(b: Build, sidecars: dict[str, dict]) -> None:
    ledger = sources.read_setup_ledger()

    # Sidecar setups, keyed by (week, asset, invalidation level).
    side: dict[tuple, dict] = {}
    for week, sc in sidecars.items():
        for asset in ASSETS:
            for s in (sc.get("strategy") or {}).get(asset, {}).get("setups", []):
                lvl, _ = parse_invalidation(s.get("invalidation"))
                tgt = _level(s.get("target"))
                if not s.get("name") and not lvl:
                    continue
                if lvl is None and tgt is None:
                    continue
                side[(week, asset, str(lvl))] = s

    seen_ledger: set[tuple] = set()
    for r in ledger:
        week = norm_week(r["Week proposed"])
        asset = strip_md(r["Asset"]).lower()
        name = strip_md(r["Setup"])
        entry_lo, entry_hi, gate = parse_entry(r["Entry"])
        inval, inval_dir = parse_invalidation(r["Invalidation"])
        target = _level(r["Target"])
        written_dir = inval_dir
        rr = parse_rr(r["R:R planned"])
        style = parse_style(name)
        sid = uid("setup", week, asset, name)
        key = (week, asset, str(inval))
        seen_ledger.add(key)
        sc = side.get(key)
        rationale = None
        sector_row = None
        if sc:
            rationale = strip_md(sc.get("raw")) or None
            sector_row = parse_sector_row(sc.get("raw"))
        else:
            b.flag(week, "setup %r" % name,
                   "no sidecar setup matched on invalidation %s, so rationale "
                   "and sector_focus_row are null." % inval)
        if entry_lo is None:
            # entry_lo and entry_hi are NOT NULL in supabase/migrations/0003.
            # The one cell that states a condition rather than a band names a
            # single level, which stands for both bounds so the row can load.
            level = _level(r["Entry"])
            entry_lo = entry_hi = level
            gate = strip_md(r["Entry"])
            b.flag(week, "setup %r" % name,
                   "the Entry cell states a condition rather than a band (%r). "
                   "entry_lo and entry_hi are NOT NULL in the schema, so the "
                   "single level the cell names (%s) stands for both bounds and "
                   "the whole cell is kept as gate_text."
                   % (strip_md(r["Entry"])[:90], level))
        if inval_dir is None:
            inval_dir = infer_invalidation_dir(inval, target)
            b.flag(week, "setup %r" % name,
                   "the Invalidation cell (%r) names a level but no side. "
                   "invalidation_dir is NOT NULL in the schema, so it is "
                   "derived from the setup's own target (%s against an "
                   "invalidation of %s) as %r, not guessed."
                   % (strip_md(r["Invalidation"]), target, inval, inval_dir))
        if style is None:
            b.flag(week, "setup %r" % name,
                   "the setup name carries no style parenthetical, so style "
                   "is null.")
        if sector_row is None:
            b.flag(week, "setup %r" % name,
                   "no 'Sits under the X row' line was found, so "
                   "sector_focus_id is null.")
        series_key, series_why = parse_setup_series(name, asset)
        if series_key is None:
            b.flag(week, "setup %r" % name,
                   "series_id is null: %s. The worker refuses to score a setup "
                   "that does not say which series its levels are on."
                   % series_why)

        b.add("setup", {
            "id": sid, "week": week, "asset": asset, "name": name,
            "series_key": series_key,
            "style": style, "entry_lo": entry_lo, "entry_hi": entry_hi,
            "invalidation": inval, "invalidation_dir": inval_dir,
            "target": target, "rr_planned": rr,
            "gate_expr": None, "gate_text": gate,
            "invalidation_dir_written": written_dir,
            "sector_focus_band": sector_row,
            "sector_focus_id": (uid("sector_focus", week, asset, sector_row)
                                if sector_row else None),
            "rationale": rationale,
            **lifecycle(week, None),
            "_source": "Setup Ledger.md",
        })

        outcome, graded_week = parse_outcome(r["Outcome (graded)"])
        r_cell = strip_md(r["R realised"])
        r_realised = first_number(r_cell) if r_cell not in ("-", "") else None
        if outcome is None:
            b.flag(week, "setup_grade %r" % name,
                   "outcome cell %r matches no vocabulary term." % r_cell)
        if outcome == "open":
            # setup_grade.graded_week is NOT NULL and no grading run has written
            # these rows: the ledger cell "open (grade in W40)" is a placeholder,
            # not a grading. Writing 2026-W40 would assert a grading that has not
            # happened, so the setup is migrated with no grade and the Monday run
            # of 2026-W40 writes the first one.
            b.flag(week, "setup_grade %r" % name,
                   "the ledger cell reads %r, which is a placeholder rather "
                   "than a grading, so no setup_grade row is written; the "
                   "2026-W40 Monday run writes the first one."
                   % strip_md(r["Outcome (graded)"]))
        else:
            b.add("setup_grade", {
                "setup_id": sid, "graded_week": graded_week, "outcome": outcome,
                "r_realised": r_realised, "note": strip_md(r["Note"]) or None,
            })

    # Setups proposed in a bootstrapped week that the Setup Ledger does not
    # cover, because the ledger only begins at 2026-W35.
    for (week, asset, lvl), s in sorted(side.items()):
        if (week, asset, lvl) in seen_ledger:
            continue
        name = strip_md(s.get("name")) or None
        if not name:
            continue
        entry_lo, entry_hi, gate = parse_entry(s.get("entry"))
        inval, inval_dir = parse_invalidation(s.get("invalidation"))
        target = _level(s.get("target"))
        written_dir = inval_dir
        if inval_dir is None:
            inval_dir = infer_invalidation_dir(inval, target)
        if entry_lo is None or inval is None or target is None:
            b.flag(week, "setup %r" % name,
                   "the bootstrapped journal writes this setup as prose rather "
                   "than as levels (entry %r, invalidation %r, target %r), and "
                   "entry_lo, invalidation and target are NOT NULL, so it is "
                   "not imported." % (strip_md(s.get("entry"))[:50],
                                      strip_md(s.get("invalidation"))[:40],
                                      strip_md(s.get("target"))[:40]))
            continue
        boot_series, boot_why = parse_setup_series(name, asset)
        if boot_series is None:
            b.flag(week, "setup %r" % name,
                   "series_id is null: %s." % boot_why)
        b.add("setup", {
            "id": uid("setup", week, asset, name), "week": week, "asset": asset,
            "name": name, "style": parse_style(name),
            "series_key": boot_series,
            "entry_lo": entry_lo, "entry_hi": entry_hi,
            "invalidation": inval, "invalidation_dir": inval_dir,
            "target": target, "rr_planned": parse_rr(s.get("rr")),
            "invalidation_dir_written": written_dir,
            "gate_expr": None, "gate_text": gate,
            "sector_focus_band": parse_sector_row(s.get("raw")),
            "sector_focus_id": (uid("sector_focus", week, asset,
                                    parse_sector_row(s.get("raw")))
                                if parse_sector_row(s.get("raw")) else None),
            "rationale": strip_md(s.get("raw")) or None,
            **lifecycle(week, None),
            "_source": "bootstrap",
        })
        b.flag(week, "setup %r" % name,
               "proposed before the Setup Ledger begins (2026-W35), so it "
               "carries no setup_grade row.")
    b.read_log["Setup Ledger.md"] = {"rows": len(ledger)}


def build_judgment(b: Build, sidecars: dict[str, dict]) -> None:
    weeks = list(config.JUDGMENT_WEEKS)
    nxt = {w: weeks[i + 1] for i, w in enumerate(weeks[:-1])}

    for week in weeks:
        sc = sidecars[week]
        successor = nxt.get(week)

        # ---- phase_call / phase_condition
        for asset in ASSETS:
            p = (sc.get("phase") or {}).get(asset) or {}
            pcid = uid("phase_call", week, asset)
            evidence = [strip_md(e) for e in (p.get("evidence") or [])
                        if not strip_md(e).startswith("What flips this")]
            falsifier = strip_md(p.get("flips")) or None
            direction = norm_direction(p.get("direction"))
            if direction is None:
                b.flag(week, "phase_call %s" % asset,
                       "the journal writes the move as %r, which is not one of "
                       "up, unchanged or down; direction is null rather than "
                       "coerced." % strip_md(p.get("direction")))
            elif direction != (p.get("direction") or "").strip():
                b.flag(week, "phase_call %s" % asset,
                       "the journal writes the move as %r; stored as %r, the "
                       "vocabulary the schema allows."
                       % (strip_md(p.get("direction")), direction))
            if p.get("phase") is None:
                b.flag(week, "phase_call %s" % asset,
                       "the journal's phase line did not parse, so phase is null.")
            if not falsifier:
                b.flag(week, "phase_call %s" % asset,
                       "no 'What flips this' line was found, so falsifier is null.")
            b.add("phase_call", {
                "id": pcid, "asset": asset, "week": week,
                "phase": p.get("phase"), "phase_name": strip_md(p.get("name")) or None,
                "prior_phase": p.get("prior"),
                "direction": direction,
                "reason": strip_md(p.get("reason")) or None,
                "evidence": evidence, "falsifier": falsifier,
                **lifecycle(week, uid("phase_call", successor, asset) if successor else None),
            })

            tracker = p.get("tracker") or []
            if len(tracker) != 4:
                b.flag(week, "phase_condition %s" % asset,
                       "the tracker carries %d rows; CONTRACT.md requires "
                       "exactly four." % len(tracker))
            for i, t in enumerate(tracker[:4], 1):
                b.add("phase_condition", {
                    "id": uid("phase_condition", week, asset, str(i)),
                    "phase_call_id": pcid, "ordinal": i,
                    "text": strip_md(t.get("condition")) or None,
                    "kind": "manual", "expr": None,
                })
                status = _norm_status(t.get("status"))
                if status is None:
                    b.flag(week, "condition_state_asserted %s #%d" % (asset, i),
                           "the tracker row's status reads %r, which is not one "
                           "of met, not yet, failed, unevaluable or void; the "
                           "row is asserted as unevaluable."
                           % strip_md(t.get("status"))[:60])
                    status = "unevaluable"
                b.add("condition_state_asserted", {
                    "condition_id": uid("phase_condition", week, asset, str(i)),
                    "owner_kind": "phase", "status": status,
                    "current_text": strip_md(t.get("current")) or None,
                    "asserted_at": config.AUTHORED_ON.get(week),
                    "asserted_by": "migration",
                    "_week": week, "_subject": "%s tracker %d" % (asset, i),
                })
            b.flag(week, "phase_condition %s" % asset,
                   "every condition is kind=manual with a null expr: the "
                   "journals write conditions as prose and this migration does "
                   "not invent typed expressions for them.")

        # ---- scenario_set / scenario / trigger_clause / scenario_target
        hv = sc.get("house_view") or {}
        setid = uid("scenario_set", week)
        swing = [strip_md(s) for s in (hv.get("swing_variables") or [])]
        prob_check = strip_md(hv.get("probability_check")) or None
        if not prob_check:
            b.flag(week, "scenario_set.probability_check",
                   "the journal carries no parseable probability-check line, "
                   "so the column is null.")
        b.add("scenario_set", {
            "id": setid, "week": week,
            "synthesis": strip_md(hv.get("synthesis")) or None,
            "probability_check": prob_check,
            "swing_variables": swing,
            "rules_waived": [],
            **lifecycle(week, uid("scenario_set", successor) if successor else None),
        })
        b.flag(week, "scenario_set.rules_waived",
               "no journal writes the 'rule consciously waived' line that "
               "scenario-rules.md asks for, so rules_waived is an empty array.")

        probs = 0
        for s in hv.get("scenarios") or []:
            ordinal = ORDINAL_OF.get(s.get("id", ""))
            if ordinal is None:
                b.flag(week, "scenario", "scenario id %r is not S1/S2/S3; "
                                         "skipped." % s.get("id"))
                continue
            scid = uid("scenario", week, str(ordinal))
            probs += int(s.get("prob") or 0)
            b.add("scenario", {
                "id": scid, "set_id": setid, "ordinal": ordinal,
                "title": strip_md(s.get("title")) or None,
                "probability": s.get("prob"),
                "positioning": strip_md(s.get("positioning")) or None,
            })
            # The verdict the grading journal wrote for this scenario, if that
            # journal is one this migration reads. It is the whole-trigger
            # judgment, carried so a reader can see what was concluded.
            grader = sidecars.get(config.GRADED_BY.get(week, ""))
            grade_note = None
            for g in ((grader or {}).get("scorecard") or {}).get("scenario_grades", []):
                if g.get("id") == s.get("id"):
                    grade_note = strip_md(g.get("note")) or None
            clauses = split_clauses(s.get("trigger"))
            if not clauses:
                b.flag(week, "trigger_clause %s" % s["id"],
                       "the scenario carries no trigger text.")
            for i, c in enumerate(clauses, 1):
                b.add("condition_state_asserted", {
                    "condition_id": uid("trigger_clause", week, str(ordinal), str(i)),
                    "owner_kind": "trigger",
                    # No journal records whether an individual clause fired. The
                    # grading prose speaks of the trigger as a whole, and
                    # inventing a per-clause verdict from it would be exactly the
                    # fabrication `unevaluable` exists to prevent.
                    "status": "unevaluable",
                    "current_text": grade_note,
                    "asserted_at": config.AUTHORED_ON.get(week),
                    "asserted_by": "migration",
                    "_week": week, "_subject": "%s clause %d" % (s["id"], i),
                })
                b.add("trigger_clause", {
                    "id": uid("trigger_clause", week, str(ordinal), str(i)),
                    "scenario_id": scid, "ordinal": i, "text": strip_md(c),
                    "kind": "manual", "expr": None, "catalyst_id": None,
                    "required": bool(clause_required(c, s.get("trigger") or "")),
                    "void_at": None, "void_reason": None,
                })
            b.flag(week, "trigger_clause %s" % s["id"],
                   "clauses are kind=manual with a null expr and a null "
                   "catalyst_id: the journals name catalysts in prose only.")
            b.flag(week, "condition_state_asserted %s" % s["id"],
                   "no journal records whether an individual trigger clause "
                   "fired, only a verdict on the trigger as a whole, so every "
                   "migrated clause is asserted `unevaluable` and carries that "
                   "verdict as its current_text%s."
                   % ("" if grade_note else
                      " (none here: this week's grading journal is not read)"))

            targets, dropped = parse_targets(s.get("targets"))
            for t in targets:
                if t.kind == "band" and t.series_key is None:
                    b.flag(week, "scenario_target %s #%d" % (s["id"], t.ordinal),
                           "a band naming no series config/series.yaml carries "
                           "(%r). The schema's shape check requires a band to "
                           "name its series, so it is stored as kind=manual and "
                           "still counts toward n." % t.label[:80])
                    t.kind, t.comparator, t.lo, t.hi = "manual", None, None, None
                    t.text = t.label
                b.add("scenario_target", {
                    "id": uid("scenario_target", week, str(ordinal), str(t.ordinal)),
                    "scenario_id": scid, "series_key": t.series_key,
                    "label": t.label, "kind": t.kind,
                    "comparator": t.comparator, "lo": t.lo, "hi": t.hi,
                    "text": t.text,
                })
                if t.inferred:
                    b.flag(week, "scenario_target %s #%d" % (s["id"], t.ordinal),
                           "comparator inferred from the verb rather than a "
                           "written symbol: %r" % t.label[:80])
            for d in dropped:
                b.flag(week, "scenario_target %s" % s["id"],
                       "fragment not counted as a target: %s" % d)
            if all(t.kind == "manual" for t in targets) and targets:
                b.flag(week, "scenario_target %s" % s["id"],
                       "every target is manual; CONTRACT.md calls that a "
                       "macro_quality finding.")
        if probs != 100:
            b.flag(week, "scenario_set",
                   "the three probabilities sum to %d, not 100." % probs)

        # ---- strategy / sector_focus / holdings_check
        for asset in ASSETS:
            st = (sc.get("strategy") or {}).get(asset) or {}
            stid = uid("strategy", week, asset)
            lo, hi, olo, ohi = parse_exposure(st.get("exposure"))
            if lo is None:
                b.flag(week, "strategy %s" % asset,
                       "no exposure band parsed from %r; exposure_lo and "
                       "exposure_hi are null." % strip_md(st.get("exposure"))[:80])
            if olo is None:
                b.flag(week, "strategy %s" % asset,
                       "the exposure line names no operated range, so "
                       "exposure_operated_lo and _hi are null.")
            b.add("strategy", {
                "id": stid, "week": week, "asset": asset,
                "stance": strip_md(st.get("stance")) or None,
                "posture": strip_md(st.get("posture")) or None,
                "exposure_lo": lo, "exposure_hi": hi,
                "exposure_operated_lo": olo, "exposure_operated_hi": ohi,
                **lifecycle(week, uid("strategy", successor, asset) if successor else None),
            })
            sf = st.get("sector_focus") or {}
            for band in BANDS:
                text = strip_md(sf.get(band)) or None
                if text is None:
                    b.flag(week, "sector_focus %s" % asset,
                           "the %s row is absent from the journal." % band)
                    continue
                b.add("sector_focus", {
                    "id": uid("sector_focus", week, asset, band),
                    "strategy_id": stid, "band": band, "text": text,
                })
            holdings = st.get("holdings") or []
            if not holdings:
                b.flag(week, "holdings_check %s" % asset,
                       "the journal carries no holdings-check list, so no "
                       "holdings_check rows exist for this strategy.")
            for h in holdings:
                note = strip_md(h.get("note")) or None
                band = None
                m = re.search(r"\b(CRITICAL|HOLD|WATCH|AVOID)\b row", note or "")
                if m:
                    band = m.group(1)
                b.add("holdings_check", {
                    "id": uid("holdings_check", week, asset, h.get("ticker", "")),
                    "strategy_id": stid, "ticker": strip_md(h.get("ticker")),
                    "action": (h.get("action") or None), "text": note,
                    "sector_focus_id": (uid("sector_focus", week, asset, band)
                                        if band else None),
                })
                if band is None:
                    b.flag(week, "holdings_check %s" % h.get("ticker"),
                           "the line names no sector-focus row, so "
                           "sector_focus_id is null.")


_SCORE_RAW = re.compile(r"(\d+)\s+of\s+(\d+)\s+met", re.I)


def score_met(score: dict | None) -> int | None:
    """The met count the journal's Score line states.

    journal_parse leaves the whole line in `raw` when it cannot split it, which
    is what W32 does, so the count is recovered from the text rather than left
    unchecked. The tracker is then checked against it like every other week.
    """
    if not score:
        return None
    if score.get("met") is not None:
        return int(score["met"])
    m = _SCORE_RAW.search(strip_md(score.get("raw", "")))
    return int(m.group(1)) if m else None


_DIRECTION = {
    "up": "up", "moved up": "up", "upgraded": "up", "raised": "up",
    "down": "down", "moved down": "down", "downgraded": "down", "cut": "down",
    "unchanged": "unchanged", "held": "unchanged", "no change": "unchanged",
}


def norm_direction(raw: str | None) -> str | None:
    """The phase move, in the three words the schema's CHECK allows.

    The journals write the move as prose inside the phase line, and
    journal_parse hands it back verbatim ("moved up"), which the CHECK on
    macro_core.phase_call rejects. Only the phrases actually in use are mapped;
    anything else is null and is named in VERIFICATION.md rather than coerced.
    """
    t = strip_md(raw or "").lower().strip(" .")
    return _DIRECTION.get(t)


def _norm_status(raw: str | None) -> str | None:
    t = strip_md(raw or "").lower()
    for k in ("not yet", "met", "failed", "unevaluable", "void"):
        if t.startswith(k):
            return k
    return None


# ---------------------------------------------------------------------------
# observations
# ---------------------------------------------------------------------------

_WORD_N = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
           "twelve": 12}
_STREAK = re.compile(
    r"\b(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"[-\s]+(?:consecutive\s+)?(?P<dir>inflow|outflow)\s+sessions?\b", re.I)


def parse_streak(display: str | None) -> int | None:
    """An ETF inflow streak as a signed session count.

    config/series.yaml fixes btc_etf_inflow_streak and eth_etf_inflow_streak as a
    signed integer: +2 is two consecutive inflow sessions, -4 is four consecutive
    outflow sessions. The journal writes a sentence, so the count and its
    direction are read from the leading clause, which is always the current
    streak; anything after the first semicolon describes the streak that broke.
    """
    if not display:
        return None
    m = _STREAK.search(strip_md(display))
    if not m:
        return None
    raw = m.group("n").lower()
    n = int(raw) if raw.isdigit() else _WORD_N[raw]
    return n if m.group("dir").lower() == "inflow" else -n


_NEG_PCT = re.compile(r"[-−]\s*(\d+(?:\.\d+)?)\s*%")


def parse_funding_extreme(display: str | None, extra: dict | None) -> Decimal | None:
    """The single most negative 8-hour funding rate, which config/series.yaml
    makes the value of `funding_extremes`; both tails stay in `extra`."""
    if extra and extra.get("most_negative"):
        try:
            return Decimal(str(extra["most_negative"][0][1]))
        except (IndexError, KeyError, TypeError, ValueError):
            pass
    if display:
        m = _NEG_PCT.search(strip_md(display))
        if m:
            return -Decimal(m.group(1))
    return None


_RISK_APPETITE = re.compile(
    r"Risk Appetite(?:\s+Index)?\s*[:]?\s*\*{0,2}([+\-−]?\d+(?:\.\d+)?)", re.I)


def parse_risk_appetite(display: str | None) -> Decimal | None:
    """State Street's Risk Appetite Index, printed inside the allocation cell.

    config/series.yaml makes it a series of its own, because one is a percentage
    of assets and the other a standardised index, so the pair could never share
    a unit.
    """
    if not display:
        return None
    m = _RISK_APPETITE.search(strip_md(display))
    if not m:
        return None
    return Decimal(m.group(1).replace("−", "-"))


#: The lead-in the journal writes in front of the index's own figure. Stripping
#: it is what turns "Risk Appetite Index +0.36 (Jul +0.18)" into the display a
#: series whose label already says "Risk Appetite Index" should carry.
_RA_LEAD_IN = re.compile(r"^Risk Appetite(?:\s+Index)?\s*[:]?\s*", re.I)


def _clauses(display: str) -> list[str]:
    """`display` split on its top-level semicolons, parentheses respected.

    "58.0% (Jul) (no new release; +1.42 pp MoM)" is one clause, not two: the
    semicolon inside the bracket belongs to the allocation's own aside.
    """
    out: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(display):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            out.append(display[start:i].strip())
            start = i + 1
    out.append(display[start:].strip())
    return [clause for clause in out if clause]


def split_risk_appetite(display: str) -> tuple[str, str | None]:
    """One journal cell as the two displays the two series should each carry.

    The journal prints State Street's equity allocation and its Risk Appetite
    Index in a single snapshot cell, and the migration used to hand that whole
    cell to both series. Two rows then read "57.4% (Aug) (-0.13 pp MoM); Risk
    Appetite Index +0.36 (Jul +0.18)", so a reader looking at the index saw a
    percentage first and a reader looking at the allocation saw an index
    reading it had no business stating.

    They are separate series in config/series.yaml because one is a percentage
    of assets and the other a standardised index, and a display that states
    both is the same conflation the split was made to prevent. So the cell is
    cut at its top-level semicolons: the clause that names the index becomes
    the index's display with its lead-in removed, and everything else stays
    with the allocation.
    """
    clauses = _clauses(display)
    own = [c for c in clauses if _RA_LEAD_IN.match(c)]
    if not own:
        return display, None
    rest = [c for c in clauses if not _RA_LEAD_IN.match(c)]
    # A cell that is nothing but the index clause keeps its text on the
    # allocation as well, because dropping it would leave that row with no
    # display at all and no value to read from one.
    allocation = "; ".join(rest) if rest else display
    return allocation, "; ".join(_RA_LEAD_IN.sub("", c) for c in own)


def _value_for(key: str, display: str, extra: dict | None) -> Decimal | int | None:
    if key in ("hy_oas_bps", "ig_oas_bps"):
        return oas_bps(display)
    if key in ("btc_etf_inflow_streak", "eth_etf_inflow_streak"):
        return parse_streak(display)
    if key == "funding_extremes":
        return parse_funding_extreme(display, extra)
    return first_number(display)


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _emit_observation(b: Build, week: str, key: str, asof: str | None,
                      value, display: str | None, tier: int, source: str | None,
                      released: str | None, note: str | None, extra: dict | None,
                      label: str) -> None:
    spec = config.SERIES_YAML_ROWS.get(key, {})
    if spec.get("value_kind") == "text":
        value = None
    if released and not _ISO_DATE.match(str(released)):
        # `released` is a date column. The sidecar occasionally carries a data
        # month ("2026-08") there, which is a release *period*, not a date.
        b.flag(week, "observation %s" % key,
               "the sidecar's `released` field reads %r, which is not a date; "
               "released is null and the value is kept in the note." % released)
        note = ((note + " ") if note else "") + "released as written: %s" % released
        released = None
    if asof and not _ISO_DATE.match(str(asof)):
        b.flag(week, "observation %s" % key,
               "asof reads %r, which is not a date; the row is not imported."
               % asof)
        return
    b.series_keys.setdefault(key, spec or {"key": key})
    b.add("observation", {
        "series_key": key, "asof": asof, "value": value,
        "display": display or None, "tier": tier,
        # observation.source is NOT NULL in supabase/migrations/0002. Where the
        # journal names no source the series' own source_name stands in, tagged
        # with the journal the reading was migrated from.
        "source": source or "%s (migrated from Weekly Market Journal %s)"
                  % (spec.get("source_name", "asserted"), week),
        "released": released or None, "note": note or None,
        "extra": extra or {},
        # observed_at is the moment the reading described. No journal records it,
        # and midnight on the asof date would be a fabricated moment, so it is
        # null for every migrated row and received_at carries the authoring date.
        "observed_at": None,
        "received_at": config.AUTHORED_ON.get(week),
        "_week": week, "_label": label,
    })


def build_observations(b: Build, sidecars: dict[str, dict]) -> None:
    for week in config.JUDGMENT_WEEKS:
        sc = sidecars[week]
        legacy_asof = config.LEGACY_SNAPSHOT_ASOF.get(week)
        seen: set[tuple[str, str | None]] = set()
        n = 0

        def emit(key, asof, value, display, tier, source, released, note, extra,
                 label):
            nonlocal n
            if (key, asof) in seen:
                b.flag(week, "observation %s" % key,
                       "a second reading for %s was already migrated; the later "
                       "row is skipped." % asof)
                return
            seen.add((key, asof))
            _emit_observation(b, week, key, asof, value, display, tier, source,
                              released, note, extra, label)
            n += 1

        for row in sc.get("snapshot") or []:
            label = (row.get("row") or "").strip()
            if not label:
                continue
            if label in config.UNMAPPED_LABELS:
                b.flag(week, "observation %r" % label,
                       "not imported: %s" % config.UNMAPPED_LABELS[label])
                continue
            key = config.SERIES_LABEL_TO_KEY.get(label)
            if not key:
                b.flag(week, "observation %r" % label,
                       "config/series.yaml carries no key for this snapshot row; "
                       "the row is not imported.")
                continue
            display = strip_md(row.get("fetched_value") or row.get("current") or "")
            whole_cell = display
            risk_appetite_display: str | None = None
            if key == "state_street_equity_allocation":
                # Cut before anything reads the cell, so the allocation's own
                # value, display and delta are all read from the allocation's
                # half and the index's from the index's.
                display, risk_appetite_display = split_risk_appetite(display)
            asof = row.get("asof") or legacy_asof
            if not row.get("asof"):
                b.flag(week, "observation %s" % key,
                       "the sidecar carries no per-row asof, so the whole "
                       "snapshot is dated %s from the journal's own 'Authored "
                       "as-of' header." % legacy_asof)
            tier = row.get("tier")
            if tier is None:
                b.flag(week, "observation %s" % key,
                       "the sidecar carries no tier; imported as tier 2.")
                tier = 2
            extra = row.get("extra") or {}
            spec = config.SERIES_YAML_ROWS.get(key, {})
            value = (None if spec.get("value_kind") == "text"
                     else _value_for(key, display, extra))
            if spec.get("value_kind") != "text" and value is None:
                b.flag(week, "observation %s" % key,
                       "no value could be read from %r; value is null with the "
                       "display kept beside it." % display[:70])
            emit(key, asof, value, display, tier,
                 strip_md(row.get("source")) or None, row.get("released"),
                 strip_md(row.get("note")) or None, extra, label)

            cash_key = config.CASH_OF.get(key)
            if cash_key:
                cash = extra.get("cash")
                if cash:
                    emit(cash_key, extra.get("cash_asof") or asof,
                         first_number(cash), str(cash), tier,
                         strip_md(row.get("source")) or None, None, None, {},
                         label + " (cash)")
                else:
                    b.flag(week, "observation %s" % cash_key,
                           "the futures row carries no cash value in `extra`, so "
                           "the cash index has no observation for this week.")
                # The prior week's cash close is a dated observation of its own.
                if extra.get("cash_prev") and extra.get("cash_prev_asof"):
                    emit(cash_key, extra["cash_prev_asof"],
                         first_number(extra["cash_prev"]),
                         str(extra["cash_prev"]), tier,
                         strip_md(row.get("source")) or None, None,
                         "prior-week close carried in the %s snapshot row's "
                         "`extra`" % label, {}, label + " (cash, prior week)")

            if key == "state_street_equity_allocation":
                # Read from the whole cell, because the index's own clause has
                # had the "Risk Appetite Index" lead-in cut off it and that
                # lead-in is what the parser anchors on.
                ra = parse_risk_appetite(whole_cell)
                if ra is None:
                    b.flag(week, "observation state_street_risk_appetite",
                           "the allocation cell names no Risk Appetite Index, so "
                           "the series has no observation for this week.")
                else:
                    # Its own clause, not the whole cell. The index's display
                    # states the index and nothing else.
                    emit("state_street_risk_appetite", asof, ra,
                         risk_appetite_display, tier,
                         strip_md(row.get("source")) or None, row.get("released"),
                         None, {}, label + " (Risk Appetite Index)")

        for m in sc.get("macro_state") or []:
            label = (m.get("label") or "").strip()
            if not label:
                b.flag(week, "observation (macro state)",
                       "a Macro & Fiscal State row has an empty label; skipped.")
                continue
            key = config.SERIES_LABEL_TO_KEY.get(label)
            if not key:
                b.flag(week, "observation %r" % label,
                       "config/series.yaml carries no key for this macro-state "
                       "row; the row is not imported.")
                continue
            if key == "fedwatch_next_fomc":
                b.flag(week, "observation fedwatch_next_fomc",
                       "CONTRACT.md names the defect that %s.points.json holds "
                       "two rows both labelled 'Next FOMC'. The sidecar's "
                       "macro_state labels them apart (%r), so the FedWatch "
                       "reading lands on its own series here." % (week, label))
            asof = config.LEGACY_SNAPSHOT_ASOF.get(week) or _week_asof(sc)
            emit(key, asof, None, strip_md(m.get("text")), 2, None, None, None,
                 {}, label)

        # Rows the points file carries that the merged snapshot dropped. This is
        # where ig_oas_bps comes from, and where CONTRACT.md's named defect is
        # fixed: 2026-W39.points.json holds two rows labelled "Next FOMC" and
        # only `extra.search_metric` tells them apart.
        pts = sources.read_points(week)
        if pts:
            for pt in pts.get("points", []):
                label = (pt.get("extra") or {}).get("search_metric") or pt.get("row")
                label = (label or "").strip()
                if label in config.UNMAPPED_LABELS or not label:
                    continue
                key = config.SERIES_LABEL_TO_KEY.get(label)
                if not key or (key, pt.get("asof")) in seen:
                    continue
                if any(k == key for k, _ in seen):
                    continue
                display = strip_md(pt.get("value") or "")
                spec = config.SERIES_YAML_ROWS.get(key, {})
                value = (None if spec.get("value_kind") == "text"
                         else _value_for(key, display, pt.get("extra")))
                b.flag(week, "observation %s" % key,
                       "read from %s.points.json, which carries a row the merged "
                       "sidecar snapshot dropped." % week)
                emit(key, pt.get("asof"), value, display, pt.get("tier") or 2,
                     strip_md(pt.get("source")) or None, pt.get("released"),
                     strip_md(pt.get("note")) or None, pt.get("extra") or {},
                     label)

        b.read_log.setdefault("snapshots", {})[week] = n


def _week_asof(sc: dict) -> str | None:
    for r in sc.get("snapshot") or []:
        if r.get("asof"):
            return r["asof"]
    return None


def build_all() -> Build:
    b = Build()
    sidecars = {w: sources.read_sidecar(w) for w in config.JUDGMENT_WEEKS}
    for w, sc in sidecars.items():
        b.read_log.setdefault("origins", {})[w] = {
            "origin": sc["_origin"], "repairs": sc["_repairs"],
            "parser_issues": sc.get("issues") or [],
        }
    build_rules(b)
    build_judgment(b, sidecars)
    build_week_grades(b, sidecars)
    build_setups(b, sidecars)
    build_observations(b, sidecars)
    b.sidecars = sidecars
    return b
