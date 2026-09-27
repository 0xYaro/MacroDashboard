#!/usr/bin/env python3
"""Parse a weekly market journal (.md) into the structured sidecar shape.

Two modes:
  strict  (default) - the template is the contract; labels and headings must match
                      the Weekly Market Journal Template. Used by lint_journal.py.
  legacy  (--legacy) - tolerant matchers for journals written before the template
                      was enforced (bold "Phase 3: ..." lines, unlabeled synthesis,
                      "### Key swing variables" headings). Used once, to bootstrap a
                      sidecar for the previous week when none exists.

Usage:
    python3 journal_parse.py "<journal.md>" [--legacy] [--out sidecar.json]

Prints the sidecar JSON to stdout (or writes it with --out). Exit 0 when the core
fields parsed (week, >=25 snapshot rows, both phase calls with a phase number,
3 scenarios with probabilities, a synthesis); exit 1 otherwise, with a diagnostic
on stderr. The parse result always carries an `issues` list naming what was not
found, so callers can report precisely.

Python 3 stdlib only.
"""
import json
import re
import sys

GRADES = ("Materialized", "Partial", "Not")
DASH = r"[—\-–:]"  # em dash, hyphen, en dash, colon

# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

def clean(s):
    return (s or "").replace("==", "").strip()


def unbold(s):
    s = clean(s)
    if s.startswith("**") and s.endswith("**") and len(s) >= 4 and "**" not in s[2:-2]:
        return s[2:-2].strip()
    return s


def strip_md(s):
    """Remove bold/italic markers and wikilink brackets for value comparison."""
    s = clean(s).replace("**", "").replace("*", "")
    s = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", r"\1", s)
    return s.strip()


def is_placeholder(s):
    t = strip_md(s)
    if not t:
        return True
    return bool(re.search(r"\[(one line|1[–-]2 lines|title|name|Date\]|the falsifier|The lesson|Paste|One line|Variable|conditions|per-asset|posture|sector|TICKER|Asset\]|x of n|Weekday)", t)) \
        or "Wxx" in t or t in ("...", "…") or t.startswith("[") and t.endswith("]")


def split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def is_separator(cells):
    ne = [c for c in cells if c]
    return bool(ne) and all(re.fullmatch(r":?-{2,}:?", c) for c in ne)


def parse_table(lines, start):
    raw = []
    i = start
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        raw.append(split_row(lines[i]))
        i += 1
    if not raw:
        return [], [], start
    return raw[0], [r for r in raw[1:] if not is_separator(r)], i


def next_table(lines, start, end):
    for i in range(start, min(end, len(lines))):
        if lines[i].lstrip().startswith("|"):
            return i
    return -1


def cell(cells, i):
    return cells[i] if i < len(cells) else ""


# ---------------------------------------------------------------------------
# structure helpers
# ---------------------------------------------------------------------------

def section_ranges(lines):
    """{num: (start, end)} for '## N. ...' headings."""
    heads = [(i, int(m.group(1))) for i, ln in enumerate(lines)
             if (m := re.match(r"^##\s+(\d+)\.", ln))]
    out = {}
    for k, (i, num) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        out.setdefault(num, (i, end))
    return out


def sub_range(lines, start, end, keyword, level=3):
    pat = re.compile(r"^" + "#" * level + r"\s+(.*)")
    s = -1
    for i in range(start, end):
        m = pat.match(lines[i])
        if m and keyword.lower() in m.group(1).lower():
            s = i
            break
    if s == -1:
        return None
    e = end
    for j in range(s + 1, end):
        if re.match(r"^#{1," + str(level) + r"}\s", lines[j]):
            e = j
            break
    return (s, e)


def find_label(lines, start, end, keyword, allow_heading=False):
    """Index of a '**Label...**' line (or '- **Label' bullet) containing keyword.
    With allow_heading, a '### Label' heading also matches."""
    kw = keyword.lower()
    for i in range(start, end):
        ln = lines[i].strip()
        if ln.startswith("- "):
            ln = ln[2:].lstrip()
        if ln.startswith("**") and kw in ln.lower():
            return i
        if allow_heading and re.match(r"^#{2,4}\s", ln) and kw in ln.lower():
            return i
    return -1


def label_text(line):
    """Text after the closing ** of a bold label."""
    m = re.match(r"^\s*(?:-\s+)?\*\*[^*]+\*\*:?\s*(.*)$", line)
    return clean(m.group(1)) if m else ""


def bullets_after(lines, start, end, top_only=True):
    """Consecutive bullets after `start` (skipping blanks). Returns list of
    (indent, text). Stops at the first non-bullet, non-blank line."""
    out = []
    i = start
    while i < end and lines[i].strip() == "":
        i += 1
    while i < end:
        m = re.match(r"^(\s*)-\s+(.*)$", lines[i])
        if m:
            indent = len(m.group(1).replace("\t", "  "))
            if top_only and indent > 0 and out:
                out.append((indent, clean(m.group(2))))
            else:
                out.append((indent, clean(m.group(2))))
            i += 1
        elif lines[i].strip() == "":
            # allow a single blank inside a list
            if i + 1 < end and re.match(r"^\s*-\s+", lines[i + 1]):
                i += 1
            else:
                break
        else:
            break
    return out


def nested(bullets):
    """Group (indent, text) bullets into [{'text', 'children': [...]}]."""
    out = []
    for indent, text in bullets:
        if indent == 0 or not out:
            out.append({"text": text, "children": []})
        else:
            out[-1]["children"].append(text)
    return out


# ---------------------------------------------------------------------------
# section parsers
# ---------------------------------------------------------------------------

def parse_frontmatter(lines):
    fm = {}
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                break
            m = re.match(r"^(\w+):\s*(.+)$", lines[i])
            if m:
                fm[m.group(1)] = m.group(2).strip().strip("'\"")
    return fm


def parse_week(lines, fm):
    if fm.get("week"):
        return fm["week"]
    for ln in lines:
        m = re.match(r"^#\s+(.*)", ln)
        if m:
            wm = re.search(r"(\d{4}-W\d{1,2})", m.group(1))
            return wm.group(1) if wm else ""
    return ""


def parse_asof(lines):
    for ln in lines[:40]:
        m = re.search(r"Authored as-of\**\s*([^.*\]]+)", ln)
        if m:
            return clean(m.group(1))
    return ""


def parse_scorecard(lines, rng, issues):
    s, e = rng
    sc = {"expected": "", "happened": "", "scenario_grades": [], "phase_check": {},
          "setup_check": [], "trajectories": [], "wrong": "", "calibration": ""}

    i = find_label(lines, s, e, "What we expected")
    sc["expected"] = label_text(lines[i]) if i != -1 else ""
    i = find_label(lines, s, e, "What actually happened")
    sc["happened"] = label_text(lines[i]) if i != -1 else ""

    i = find_label(lines, s, e, "Scenario grading")
    if i != -1:
        for _, b in bullets_after(lines, i + 1, e):
            t = strip_md(b)
            m = re.search(r"S(\d)\s*\((\d+)%[^)]*\)\s*[\"“](.*?)[\"”]\s*" + DASH + r"?\s*(Materialized|Partial|Not)\b\.?(.*)$", t)
            if not m:
                m2 = re.search(r"S(\d)\s*" + DASH + r"?\s*[\"“](.*?)[\"”]\s*\((\d+)%[^)]*\)\s*" + DASH + r"?\s*(Materialized|Partial|Not)\b\.?(.*)$", t)
                if m2:
                    sid, prob, title, grade, rest = m2.group(1), m2.group(3), m2.group(2), m2.group(4), m2.group(5)
                else:
                    sc["scenario_grades"].append({"raw": b})
                    continue
            else:
                sid, prob, title, grade, rest = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            th = re.search(r"targets hit\s*(\d+)\s*of\s*(\d+)", rest, re.I)
            note = re.sub(r"^\s*" + DASH + r"?\s*(targets hit\s*\d+\s*of\s*\d+\.?)?\s*" + DASH + r"?\s*", "", rest, flags=re.I)
            sc["scenario_grades"].append({
                "id": "S" + sid, "prob": int(prob), "title": title.strip(), "grade": grade,
                "targets_hit": int(th.group(1)) if th else None,
                "targets_n": int(th.group(2)) if th else None,
                "note": clean(note),
            })
    else:
        issues.append("§0: 'Scenario grading' label not found")

    i = find_label(lines, s, e, "Phase-call check")
    if i != -1:
        for _, b in bullets_after(lines, i + 1, e):
            m = re.match(r"(Equities|Crypto):\s*(.*)$", strip_md(b))
            if m:
                txt = m.group(2)
                v = re.match(r"\s*(valid|advanced|wrong)\b", txt, re.I)
                sc["phase_check"][m.group(1).lower()] = {
                    "verdict": v.group(1).lower() if v else "", "note": clean(txt)}
    else:
        issues.append("§0: 'Phase-call check' label not found")

    i = find_label(lines, s, e, "Setup check")
    if i != -1:
        for _, b in bullets_after(lines, i + 1, e):
            t = strip_md(b)
            if re.match(r"No setup was proposed", t, re.I):
                sc["setup_check"].append({"name": "", "outcome": "none", "note": t})
                continue
            m = re.match(r"\*\*([^*]+):\*\*\s*(.*)$", b)
            name = m.group(1).strip() if m else ""
            body = m.group(2) if m else b
            o = re.match(r"\s*(not triggered|triggered\s*→\s*stopped|triggered\s*→\s*target|open)\b", body, re.I)
            sc["setup_check"].append({"name": name, "outcome": o.group(1).lower().replace(" ", "") if o else "", "note": clean(body)})
    else:
        issues.append("§0: 'Setup check' label not found")

    i = find_label(lines, s, e, "Multi-week trajectories")
    sc["trajectories"] = [b for _, b in bullets_after(lines, i + 1, e)] if i != -1 else []
    i = find_label(lines, s, e, "What we got wrong")
    sc["wrong"] = label_text(lines[i]) if i != -1 else ""
    i = find_label(lines, s, e, "Calibration")
    sc["calibration"] = label_text(lines[i]) if i != -1 else ""
    return sc


def parse_snapshot(lines, rng, legacy, issues):
    s, e = rng
    rows = []
    for kw, table in (("Price", "Price & Levels"), ("Sentiment", "Sentiment & Positioning"), ("Flows", "Flows & Structure")):
        sub = sub_range(lines, s, e, kw)
        if not sub:
            issues.append("§1: subsection '%s' not found" % table)
            continue
        ts = next_table(lines, sub[0] + 1, sub[1])
        if ts == -1:
            issues.append("§1: no table under '%s'" % table)
            continue
        _, data, _ = parse_table(lines, ts)
        for c in data:
            read = clean(cell(c, 3))
            cur = clean(cell(c, 2))
            flag = ""
            fm = re.search(r"↳\s*(.*)$", read + " " + cur)
            if fm:
                flag = clean(fm.group(1).replace("<br>", " "))
            rows.append({"table": table, "row": unbold(cell(c, 0)), "previous": unbold(cell(c, 1)),
                         "current": cur, "read": read, "source": unbold(cell(c, 4)), "flag": flag})

    macro = []
    ms = sub_range(lines, s, e, "Macro & Fiscal")
    if ms:
        bl = bullets_after(lines, ms[0] + 1, ms[1])
    else:
        i = find_label(lines, s, e, "Macro & Fiscal")
        bl = bullets_after(lines, i + 1, e) if i != -1 else []
        if i == -1:
            issues.append("§1: 'Macro & Fiscal State' not found")
    for _, b in bl:
        m = re.match(r"\*\*([^*]+):\*\*\s*(.*)$", b)
        macro.append({"label": m.group(1).strip() if m else "", "text": clean(m.group(2) if m else b)})

    cats = []
    cs = sub_range(lines, s, e, "Catalyst")
    if cs:
        ts = next_table(lines, cs[0] + 1, cs[1])
        if ts != -1:
            _, data, _ = parse_table(lines, ts)
            for c in data:
                if not any(strip_md(x) for x in c):
                    continue
                cats.append({"date": strip_md(cell(c, 0)), "catalyst": strip_md(cell(c, 1)),
                             "affected": strip_md(cell(c, 2)), "reaction": strip_md(cell(c, 3))})
    else:
        issues.append("§1: 'Catalysts' subsection not found")
    return rows, macro, cats


def parse_changed(lines, rng):
    s, e = rng
    out = {}
    for kw, key in (("Macro & Geopolitical", "macro"), ("Market Structure", "structure")):
        sub = sub_range(lines, s, e, kw)
        out[key] = [b for _, b in bullets_after(lines, sub[0] + 1, sub[1])] if sub else []
        if sub:
            # bullets may follow an italic note line
            if not out[key]:
                for i in range(sub[0] + 1, sub[1]):
                    if re.match(r"^\s*-\s+", lines[i]):
                        out[key] = [b for _, b in bullets_after(lines, i, sub[1])]
                        break
    return out


def parse_phase_block(lines, rng, legacy, asset, issues):
    s, e = rng
    ph = {"phase": None, "name": "", "prior": None, "direction": "", "reason": "", "evidence": [],
          "flips": "", "tracker": [], "score": {}}
    # phase line
    for i in range(s, e):
        ln = lines[i].strip()
        m = re.match(r"^-\s+\*\*Phase:\*\*\s*(\d)\s*" + DASH + r"\s*([^(]+?)\s*\((.*)\)\s*\.?$", ln)
        if m:
            ph["phase"], ph["name"] = int(m.group(1)), m.group(2).strip()
            inside = m.group(3)
            pm = re.search(r"was Phase\s*(\d)", inside)
            ph["prior"] = int(pm.group(1)) if pm else None
            dm = re.search(r"(moved up|unchanged|moved down)", inside)
            ph["direction"] = dm.group(1) if dm else ""
            rm = re.search(r"because\s*(.*)$", inside)
            ph["reason"] = clean(rm.group(1)) if rm else clean(inside)
            break
        if legacy:
            m = re.match(r"^\*\*Phase\s*(\d)\s*" + DASH + r"\s*([^*]+?)\.?\*\*\s*(.*)$", ln)
            if m:
                ph["phase"], ph["name"], ph["reason"] = int(m.group(1)), m.group(2).strip(), clean(m.group(3))
                pm = re.search(r"was Phase\s*(\d)", m.group(3))
                ph["prior"] = int(pm.group(1)) if pm else None
                dm = re.search(r"(moved up|unchanged|moved down|remains unchanged)", m.group(3))
                ph["direction"] = ("unchanged" if dm and "unchanged" in dm.group(1) else dm.group(1)) if dm else ""
                break
    if ph["phase"] is None:
        issues.append("§3 %s: phase line not parsed (expected '- **Phase:** N — name (was Phase M last week; ...)')" % asset)

    # evidence
    i = find_label(lines, s, e, "Evidence")
    if i != -1:
        ph["evidence"] = [t for _, t in bullets_after(lines, i + 1, e) if t]
    elif legacy:
        # first run of plain bullets after the phase line
        for j in range(s, e):
            if re.match(r"^-\s+(?!\*\*)", lines[j]):
                ph["evidence"] = [t for _, t in bullets_after(lines, j, e) if t]
                break
    i = find_label(lines, s, e, "What flips this")
    ph["flips"] = label_text(lines[i]) if i != -1 else ""
    # tracker
    ts = -1
    for j in range(s, e):
        if re.match(r"^####\s", lines[j]):
            ts = next_table(lines, j + 1, e)
            break
    if ts == -1 and legacy:
        ts = next_table(lines, s, e)
    if ts != -1:
        _, data, _ = parse_table(lines, ts)
        for c in data:
            st = strip_md(cell(c, 3)).lower()
            ph["tracker"].append({"n": strip_md(cell(c, 0)), "condition": strip_md(cell(c, 1)),
                                  "current": clean(cell(c, 2)), "status": st})
    for j in range(s, e):
        m = re.match(r"^\*\*Score:\*\*\s*(.*)$", lines[j])
        if m:
            t = m.group(1)
            sm = re.match(r"\s*(\d+)\s*of\s*(\d+)\s*met\s*→?\s*upgrade eligible:\s*(yes|no)\.?\s*(.*)$", t, re.I)
            ph["score"] = {"met": int(sm.group(1)), "of": int(sm.group(2)), "eligible": sm.group(3).lower(),
                           "note": clean(sm.group(4))} if sm else {"raw": clean(t)}
            break
    return ph


def parse_phase(lines, rng, legacy, issues):
    s, e = rng
    out = {}
    for asset in ("Equities", "Crypto"):
        sub = sub_range(lines, s, e, asset)
        if not sub:
            issues.append("§3: '%s' subsection not found" % asset)
            out[asset.lower()] = None
            continue
        out[asset.lower()] = parse_phase_block(lines, sub, legacy, asset, issues)
    return out


def parse_house(lines, rng, legacy, issues):
    s, e = rng
    hv = {"synthesis": "", "swing_variables": [], "scenarios": [], "probability_check": ""}
    i = find_label(lines, s, e, "Synthesis")
    if i != -1:
        hv["synthesis"] = label_text(lines[i])
    elif legacy:
        for j in range(s + 1, e):
            ln = lines[j].strip()
            if ln and not ln.startswith(("#", "*", "-", "|", ">")):
                hv["synthesis"] = clean(ln)
                break
    else:
        issues.append("§4: '**Synthesis:**' label not found")

    i = find_label(lines, s, e, "Key swing variables", allow_heading=legacy)
    if i != -1:
        hv["swing_variables"] = [b for _, b in bullets_after(lines, i + 1, e)]
    else:
        issues.append("§4: 'Key swing variables' not found")

    i = find_label(lines, s, e, "Forward Scenarios", allow_heading=legacy)
    ts = next_table(lines, (i + 1) if i != -1 else s, e)
    if ts != -1:
        _, data, _ = parse_table(lines, ts)
        for c in data:
            head = strip_md(cell(c, 0))
            m = re.match(r"S(\d)\s*" + DASH + r"?\s*[\"“]?(.*?)[\"”]?\s*\((\d+)%\)\s*$", head)
            hv["scenarios"].append({
                "id": "S" + m.group(1) if m else "", "title": m.group(2).strip() if m else head,
                "prob": int(m.group(3)) if m else None,
                "trigger": clean(cell(c, 1)), "targets": clean(cell(c, 2)), "positioning": clean(cell(c, 3))})
    else:
        issues.append("§4: Forward Scenarios table not found")
    i = find_label(lines, s, e, "Probability check")
    hv["probability_check"] = label_text(lines[i]) if i != -1 else ""
    if i == -1:
        issues.append("§4: '**Probability check:**' label not found")
    return hv


def _field(body, name, seps=r"·;"):
    m = re.search(r"(?:^|[" + seps + r"])\s*" + name + r"[:\s]+(.*?)(?=\s*[" + seps + r"]|$)", body, re.I | re.S)
    return clean(m.group(1)) if m else ""


def parse_setup(text):
    t = strip_md(text)
    m = re.match(r"^([^:]{2,80}):\s*(.*)$", t)
    name = m.group(1).strip() if m else ""
    body = m.group(2) if m else t
    st = {"name": name, "raw": clean(text), "entry": "", "invalidation": "", "target": "", "rr": ""}
    if re.search(r"No qualifying setup", body, re.I):
        st["none"] = True
        return st
    st["entry"] = _field(body, "Entry") or _field(body, "Entry at") or clean(re.split(r"[·;]", body)[0])
    st["invalidation"] = _field(body, "Invalidation")
    st["target"] = _field(body, "Target")
    st["rr"] = _field(body, r"(?:approximate\s+)?R:R")
    return st


def parse_strategy_block(lines, rng, legacy, asset, issues):
    s, e = rng
    st = {"stance": "", "posture": "", "exposure": "", "setups": [], "no_setup": "",
          "sector_focus": {}, "holdings": []}
    items = nested(bullets_after(lines, s + 1, e, top_only=False))
    # bullets_after stops on the first non-bullet; the block may start with a blank line, handled
    if not items:
        for j in range(s + 1, e):
            if re.match(r"^\s*-\s+", lines[j]):
                items = nested(bullets_after(lines, j, e, top_only=False))
                break
    for it in items:
        t = it["text"]
        m = re.match(r"\*\*([^*]+?):?\*\*:?\s*(.*)$", t)
        if not m:
            continue
        label, body = m.group(1).strip().lower(), clean(m.group(2))
        if label.startswith("default stance"):
            st["stance"] = body
        elif label.startswith("posture"):
            st["posture"] = body
        elif label.startswith("exposure"):
            st["exposure"] = body
        elif label.startswith("setups"):
            kids = [k for k in it["children"] if strip_md(k) and not strip_md(k).startswith("(")]
            if kids:
                for k in kids:
                    if re.search(r"No qualifying setup", k, re.I):
                        st["no_setup"] = strip_md(k)
                    else:
                        st["setups"].append(parse_setup(k))
            elif body:
                if re.search(r"No qualifying", body, re.I):
                    st["no_setup"] = strip_md(body)
                else:
                    st["setups"].append(parse_setup(body))
        elif label.startswith("sector focus"):
            for k in it["children"]:
                km = re.match(r"\*\*(CRITICAL|HOLD|WATCH|AVOID):\*\*\s*(.*)$", k)
                if km:
                    st["sector_focus"][km.group(1)] = clean(km.group(2))
        elif label.startswith("holdings check"):
            for k in it["children"]:
                km = re.match(r"\*\*([^*]+):\*\*\s*(.*)$", k)
                if not km:
                    continue
                body2 = km.group(2)
                am = re.match(r"\s*(keep|trim|add|exit)\b", body2, re.I)
                st["holdings"].append({"ticker": km.group(1).strip(), "action": am.group(1).lower() if am else "",
                                       "note": clean(body2)})
    return st


def parse_strategy(lines, rng, legacy, issues):
    s, e = rng
    out = {}
    for asset in ("Equities", "Crypto"):
        sub = sub_range(lines, s, e, asset)
        if not sub:
            issues.append("§5: '%s' subsection not found" % asset)
            out[asset.lower()] = None
            continue
        out[asset.lower()] = parse_strategy_block(lines, sub, legacy, asset, issues)
    return out


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build(text, legacy=False):
    lines = text.split("\n")
    issues = []
    fm = parse_frontmatter(lines)
    secs = section_ranges(lines)
    res = {"week": parse_week(lines, fm), "authored_asof": parse_asof(lines), "legacy": legacy,
           "sections_found": sorted(secs.keys()), "issues": issues}
    for n in range(6):
        if n not in secs:
            issues.append("section '## %d.' not found" % n)

    res["scorecard"] = parse_scorecard(lines, secs[0], issues) if 0 in secs else None
    if 1 in secs:
        rows, macro, cats = parse_snapshot(lines, secs[1], legacy, issues)
    else:
        rows, macro, cats = [], [], []
    res["snapshot"] = rows
    res["macro_state"] = macro
    res["catalysts"] = cats
    res["changed"] = parse_changed(lines, secs[2]) if 2 in secs else {"macro": [], "structure": []}
    res["phase"] = parse_phase(lines, secs[3], legacy, issues) if 3 in secs else {"equities": None, "crypto": None}
    res["house_view"] = parse_house(lines, secs[4], legacy, issues) if 4 in secs else None
    res["strategy"] = parse_strategy(lines, secs[5], legacy, issues) if 5 in secs else None
    return res


def core_ok(r):
    ph = r.get("phase") or {}
    hv = r.get("house_view") or {}
    eq, cr = ph.get("equities") or {}, ph.get("crypto") or {}
    sc = hv.get("scenarios") or []
    return (bool(r.get("week")) and len(r.get("snapshot") or []) >= 25
            and eq.get("phase") is not None and cr.get("phase") is not None
            and len(sc) == 3 and all(s.get("prob") is not None for s in sc)
            and bool(strip_md(hv.get("synthesis", ""))))


def core_diag(r):
    ph = r.get("phase") or {}
    hv = r.get("house_view") or {}
    return "week=%r snapshot=%d phase_eq=%r phase_cr=%r scenarios=%d synthesis=%r" % (
        r.get("week"), len(r.get("snapshot") or []),
        (ph.get("equities") or {}).get("phase"), (ph.get("crypto") or {}).get("phase"),
        len(hv.get("scenarios") or []), bool(strip_md(hv.get("synthesis", ""))))


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    legacy = "--legacy" in argv
    out = None
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
        args = [a for a in args if a != out]
    if len(args) != 1:
        sys.stderr.write("usage: journal_parse.py <journal.md> [--legacy] [--out sidecar.json]\n")
        return 2
    try:
        text = open(args[0], encoding="utf-8").read()
    except OSError as e:
        sys.stderr.write("journal_parse: cannot read %s: %s\n" % (args[0], e))
        return 2
    r = build(text, legacy=legacy)
    r["source_file"] = args[0]
    payload = json.dumps(r, ensure_ascii=False, indent=None if not out else 1)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
    else:
        sys.stdout.write(payload + "\n")
    if not core_ok(r):
        sys.stderr.write("journal_parse: core fields incomplete (%s)\n" % core_diag(r))
        for i in r["issues"]:
            sys.stderr.write("  - %s\n" % i)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
