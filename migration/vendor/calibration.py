#!/usr/bin/env python3
"""Calibration arithmetic for the Scorecard Ledger.

Two jobs:

1. Score one week's scenario set (paste the result into the ledger row):
       python3 calibration.py brier "S1:30:Partial,S2:40:Partial,S3:30:Not"
   Prints the Brier score. Outcome weights: Materialized = 1, Partial = 0.5, Not = 0,
   normalised to sum to 1 across the set. Brier = sum((p_i - o_i)^2) over the three
   scenarios, range 0 (perfect) to 2. If nothing materialized at all, the truth lay
   outside the set: Brier = sum(p_i^2) + 1, which is the worst case by construction.

2. The rolling calibration read for section 0 (run after appending the row):
       python3 calibration.py read "<Scorecard Ledger.md>" [--n 8] [--json]
   Prints one line: base-case hit rate (Materialized = 1, Partial = 0.5) over the
   last N graded weeks, the full-materialization rate of the base case, mean Brier
   where available, and how often the base was S2 (the habit check).

Python 3 stdlib only.
"""
import json
import re
import sys

WEIGHT = {"materialized": 1.0, "partial": 0.5, "not": 0.0}


def brier(spec):
    """spec: 'S1:30:Partial,S2:40:Partial,S3:30:Not' -> float."""
    items = []
    for part in spec.split(","):
        sid, prob, grade = [x.strip() for x in part.split(":")]
        items.append((sid, float(prob) / 100.0, WEIGHT[grade.lower()]))
    total_w = sum(w for _, _, w in items)
    if total_w == 0:
        return sum(p * p for _, p, _ in items) + 1.0
    return sum((p - w / total_w) ** 2 for _, p, w in items)


def parse_ledger(path):
    rows = []
    header = None
    for ln in open(path, encoding="utf-8"):
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if len(cells) < 3 or not re.match(r"\d{4}-W\d{2}", cells[0]):
            continue
        rows.append(dict(zip(header, cells)))
    return header or [], rows


def read(path, n=8):
    header, rows = parse_ledger(path)
    last = rows[-n:]
    hits, full, briers, s2 = 0.0, 0, [], 0
    for r in last:
        g = (r.get("Base grade") or "").strip().lower()
        hits += WEIGHT.get(g, 0.0)
        full += 1 if g == "materialized" else 0
        base = r.get("Base case (prob)") or ""
        if re.match(r"\s*S2\b", base):
            s2 += 1
        b = (r.get("Brier") or "").strip()
        try:
            briers.append(float(b))
        except ValueError:
            pass
    k = len(last)
    out = {
        "graded_weeks": k,
        "base_hit_rate": round(hits / k, 3) if k else None,
        "base_full_rate": round(full / k, 3) if k else None,
        "mean_brier": round(sum(briers) / len(briers), 3) if briers else None,
        "brier_weeks": len(briers),
        "base_was_s2": s2,
        "first_week": last[0].get("Week") if last else None,
        "last_week": last[-1].get("Week") if last else None,
    }
    line = ("**Calibration:** base case scored %.1f of the last %d graded weeks (Partial = half credit), "
            "fully materialized in %d of %d (%.0f%%)" % (hits, k, full, k, 100.0 * full / k if k else 0))
    if briers:
        line += "; mean Brier %.2f over %d scored weeks (0 = perfect, 2 = worst)" % (out["mean_brier"], len(briers))
    line += "; base was S2 in %d of %d." % (s2, k)
    out["line"] = line
    return out


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "brier":
        print("%.3f" % brier(argv[2]))
        return 0
    if cmd == "read":
        n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 8
        out = read(argv[2], n)
        if "--json" in argv:
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(out["line"])
        return 0
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
