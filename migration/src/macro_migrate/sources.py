"""Readers for the five source shapes. Read-only: nothing here writes the vault.

1. Scorecard Ledger.md   markdown table, one row per graded week
2. Setup Ledger.md       markdown table, one row per proposed setup
3. scenario-rules.md     markdown table, R1..R12 and their teaching weeks
4. data/2026-W3*.json    sidecars for W36..W39
5. 2026-W3*.md           journals for W32, W34, W35, read through the vendored
                         legacy parser
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

from . import config
from .textutil import norm_week, strip_md

VENDOR = config.MIGRATION / "vendor"


def _load_vendor(name: str):
    spec = importlib.util.spec_from_file_location(
        "vendored_" + name, VENDOR / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


calibration = _load_vendor("calibration")
journal_parse = _load_vendor("journal_parse")


# --------------------------------------------------------------------------
# 1. Scorecard Ledger
# --------------------------------------------------------------------------

def read_scorecard_ledger(path: Path | None = None) -> list[dict]:
    """Rows in source order, exactly as calibration.py sees them.

    The same function the live calibration read uses, so a week_grade row and
    the sentence in section 0 can never drift apart.
    """
    path = path or config.SCORECARD_LEDGER
    _, rows = calibration.parse_ledger(str(path))
    return rows


# --------------------------------------------------------------------------
# 2. Setup Ledger
# --------------------------------------------------------------------------

def _md_table(text: str, header_starts: str) -> list[dict]:
    rows: list[dict] = []
    header: list[str] | None = None
    for ln in text.splitlines():
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if header is None:
            if cells and cells[0].startswith(header_starts):
                header = cells
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def read_setup_ledger(path: Path | None = None) -> list[dict]:
    path = path or config.SETUP_LEDGER
    return _md_table(path.read_text(encoding="utf-8"), "Week proposed")


# --------------------------------------------------------------------------
# 3. scenario-rules
# --------------------------------------------------------------------------

def read_scenario_rules(path: Path | None = None) -> list[dict]:
    path = path or config.SCENARIO_RULES
    out: list[dict] = []
    for r in _md_table(path.read_text(encoding="utf-8"), "#"):
        num = r.get("#", "")
        m = re.match(r"~*R(\d+)~*", num.strip())
        if not m:
            continue
        text = r.get("Rule", "").strip()
        # A retired rule is struck through, with the week that retired it.
        retired = None
        if text.startswith("~~") or num.strip().startswith("~~"):
            rm = re.search(r"retired\s+(?:in\s+)?(W\d{2}|\d{4}-W\d{2})", text, re.I)
            retired = norm_week(rm.group(1)) if rm else "unknown"
            text = text.strip("~").strip()
        weeks = [norm_week(t) for t in
                 re.findall(r"\b(?:\d{4}-)?W\d{2}\b",
                            r.get("Weeks that taught it", ""))]
        out.append({
            "number": int(m.group(1)),
            "text": strip_md(text),
            "retired_week": retired,
            "weeks": [w for w in weeks if w],
        })
    return out


# --------------------------------------------------------------------------
# 4 and 5. Sidecars, and the legacy bootstrap
# --------------------------------------------------------------------------

_PHASE_BOLD = re.compile(r"^(-\s+\*\*Phase:\*\*\s*)\*\*(.+?)\*\*(\s*\(.*)$")


def _repair_w32_phase(md: str) -> str:
    """W32 writes `- **Phase:** **3 - Late Cycle / Distribution** (...)`.

    journal_parse's phase matcher expects the number immediately after the
    label, so the extra bold run around the phase makes both phase calls parse
    as None. The journal is read-only and the vendored parser is not modified,
    so the repair is done on the text handed to the parser, and it is recorded
    for every W32 phase call in VERIFICATION.md.
    """
    out = []
    for ln in md.splitlines():
        m = _PHASE_BOLD.match(ln)
        out.append(m.group(1) + m.group(2) + m.group(3) if m else ln)
    return "\n".join(out)


def read_sidecar(week: str) -> dict:
    """The sidecar for a week: read from data/ where one exists, otherwise
    produced by running the vendored legacy parser over the journal."""
    p = config.DATA / (week + ".json")
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        d["_origin"] = "sidecar %s" % p.name
        d["_repairs"] = []
        return d

    md_path = config.MACRO / (week + ".md")
    md = md_path.read_text(encoding="utf-8")
    repairs: list[str] = []
    if week == "2026-W32":
        repaired = _repair_w32_phase(md)
        if repaired != md:
            md = repaired
            repairs.append(
                "phase lines un-bolded before parsing; journal_parse's matcher "
                "cannot read `**Phase:** **3 - ...**`")
    d = journal_parse.build(md, legacy=True)
    d["core_ok"] = journal_parse.core_ok(d)
    d["_origin"] = "journal_parse.py --legacy over %s" % md_path.name
    d["_repairs"] = repairs
    return d


def read_points(week: str) -> dict | None:
    p = config.DATA / (week + ".points.json")
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
