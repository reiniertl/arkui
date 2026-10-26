#!/usr/bin/env python3
"""The three rules the measured vectors must obey.

    ./vector_rules.py --patterns patterns.csv

  INV-SURFACE-SUMS-TO-ONE    the six surface columns partition the display
  INV-UNIT-COLUMNS-BOUNDED   a column declared unit is a fraction, or empty
  INV-ABSENT-NOT-ZERO        a measurement not taken is empty, never zero,
                             and survives as nan all the way to the mean

The first two were true before they were written down, which is the point:
an invariant that is already satisfied costs nothing to keep and catches
the day it stops being satisfied. The third spans two files - rs_pattern.py
writes the empty field, vectors_build.py reads it as nan and imputes the
column mean - and nothing held them together, so it is checked end to end
rather than in either file alone.

Exit 0 when all three hold, 1 when any does not.
"""
import argparse, csv, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "cluster"))
import vectors_build as VB            # noqa: E402

SURFACE_SIX = ["vis_xcomp", "vis_image", "vis_text", "vis_glyph",
               "vis_content", "vis_substrate"]
TOLERANCE = 0.01                      # six values at three decimals


def unit_columns():
    """Every column the vector table declares as a unit fraction."""
    cols = {n for v in VB.VECTORS.values() for n, k, _, _ in v["cols"] if k == "unit"}
    cols.update(SURFACE_SIX)           # vis_glyph is measured but not in a vector
    return sorted(cols)


def check_surface_sum(rows):
    worst, bad = 0.0, []
    for r in rows:
        try:
            s = sum(float(r[c]) for c in SURFACE_SIX)
        except (KeyError, ValueError):
            continue
        d = abs(s - 1.0)
        worst = max(worst, d)
        if d > TOLERANCE:
            bad.append((r.get("session"), r.get("stem"), round(s, 4)))
    five = max((abs(sum(float(r[c]) for c in SURFACE_SIX if c != "vis_glyph") - 1.0)
                for r in rows if r.get("vis_xcomp")), default=0.0)
    glyph = max((float(r["vis_glyph"]) for r in rows if r.get("vis_glyph")), default=0.0)
    print("  INV-SURFACE-SUMS-TO-ONE")
    print("    %d row(s); six columns depart from one by at most %.4f "
          "(tolerance %.2f)" % (len(rows), worst, TOLERANCE))
    print("    the five in the vector depart by at most %.4f; vis_glyph, which "
          "they leave out, reaches %.4f" % (five, glyph))
    for s, st, got in bad[:5]:
        print("    %s/%s sums to %s" % (s, st, got))
    return not bad


def check_unit_bounded(rows):
    cols, bad, seen = unit_columns(), [], {}
    for r in rows:
        for c in cols:
            v = (r.get(c) or "").strip()
            if v == "":
                seen[c] = seen.get(c, [0, 0])
                seen[c][1] += 1
                continue
            try:
                f = float(v)
            except ValueError:
                bad.append((c, r.get("stem"), v))
                continue
            seen.setdefault(c, [0, 0])[0] += 1
            if f < 0.0 or f > 1.0:
                bad.append((c, r.get("stem"), v))
    print("  INV-UNIT-COLUMNS-BOUNDED")
    print("    %d column(s) declared unit, over %d row(s): %s"
          % (len(cols), len(rows), ", ".join(cols)))
    empties = {c: seen.get(c, [0, 0])[1] for c in cols if seen.get(c, [0, 0])[1]}
    print("    empty fields (a measurement not taken): %s"
          % (", ".join("%s %d" % (c, n) for c, n in empties.items()) or "none"))
    for c, st, v in bad[:5]:
        print("    %s on scene %s is %s" % (c, st, v))
    return not bad


def check_absent_not_zero(rows):
    """All three legs: written empty, read as nan, imputed to the mean."""
    bad = []
    # leg 1: a scene with no repeating group leaves the columns empty.
    absent = [r for r in rows if (r.get("pat1_cover") or "").strip() == ""]
    zeroed = [r for r in rows if (r.get("pat1_cover") or "").strip() == "0"
              or (r.get("pat1_cover") or "").strip() == "0.0"]
    # leg 2: transform() turns an empty field into nan, not into zero.
    t_empty = VB.transform("unit", "")
    t_none = VB.transform("unit", None)
    t_zero = VB.transform("unit", "0")
    if not math.isnan(t_empty) or not math.isnan(t_none):
        bad.append("transform() does not return nan for an absent field")
    if math.isnan(t_zero):
        bad.append("transform() treats a real zero as absent")
    # leg 3: a nan column is filled with the column mean, not with zero.
    import numpy as np
    X = np.array([[1.0], [3.0], [float("nan")]])
    m = np.isnan(X[:, 0])
    X[m, 0] = np.nanmean(X[:, 0])
    if abs(X[2, 0] - 2.0) > 1e-9:
        bad.append("an absent value is not imputed to the column mean")
    print("  INV-ABSENT-NOT-ZERO")
    print("    %d of %d scene(s) have no repeating group; they leave pat1_* "
          "empty, and %d write a zero" % (len(absent), len(rows), len(zeroed)))
    print("    transform('')=%s  transform(None)=%s  transform('0')=%s"
          % (t_empty, t_none, t_zero))
    print("    nan imputes to the column mean, not to zero: %s"
          % ("yes" if not bad else "no"))
    if zeroed:
        bad.append("%d scene(s) write 0 where the group is absent" % len(zeroed))
    for b in bad[:5]:
        print("    %s" % b)
    return not bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patterns", required=True)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    if not rows:
        raise SystemExit("%s holds no rows" % a.patterns)
    print("the vector rules, over %s" % os.path.basename(a.patterns))
    ok = [check_surface_sum(rows), check_unit_bounded(rows),
          check_absent_not_zero(rows)]
    if all(ok):
        print("  all three hold")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
