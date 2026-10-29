#!/usr/bin/env python3
"""The rules the measured vectors must obey.

    ./vector_rules.py --patterns patterns.csv

  INV-UNIT-COLUMNS-BOUNDED   every vector value lies in [0, 1], and nothing in
                             the code that computes one clamps it
  INV-FEATURE-TOTAL          every vector value is defined for every row;
                             nothing is empty and nothing is filled

The second and third replace INV-ABSENT-NOT-ZERO, which held that a
measurement not taken was written empty and filled with the column mean
downstream. The pattern vector has no absent values: a distance, a share and
a count are defined for every display, so there is nothing to fill.

Exit 0 when all hold, 1 when any does not.
"""
import argparse, ast, csv, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER = os.path.join(os.path.dirname(HERE), "cluster")
sys.path.insert(0, CLUSTER)
import vectors_build as VB            # noqa: E402



def vector_columns():
    """Every column of the pattern vector."""
    return [n for n, _ in VB.PATTERN_COLS]


def clamps():
    """min()/max()/clip() calls in the pass that compute a vector column.

    Searched in rs_scene.row, where every column is written. A clamp there
    would hide an area computed against the wrong denominator."""
    src = open(os.path.join(CLUSTER, "rs_scene.py"), encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "row")
    seg = ast.get_source_segment(src, fn)
    return [m.group(0) for m in re.finditer(r"\b(min|max|np\.clip|clip)\(", seg)]


def check_bounded(rows):
    cols, bad = vector_columns(), []
    for r in rows:
        for c in cols:
            v = (r.get(c) or "").strip()
            if v and not 0.0 <= float(v) <= 1.0:
                bad.append("%s/%s %s is %s" % (r.get("session"), r.get("stem"), c, v))
    cl = clamps()
    print("  INV-UNIT-COLUMNS-BOUNDED")
    print("    %d column(s) over %d row(s); values outside [0, 1]: %d; clamps "
          "in rs_scene.row: %d" % (len(cols), len(rows), len(bad), len(cl)))
    for b in bad[:5]:
        print("    " + b)
    for c in cl:
        print("    a clamp in the code that writes the columns: %s" % c)
    if bad or cl:
        print("    ERR-FEATURE-OUT-OF-RANGE")
    return not bad and not cl


def check_total(rows):
    cols, bad = vector_columns(), []
    for r in rows:
        for c in cols:
            if c not in r or (r.get(c) or "").strip() == "":
                bad.append("%s/%s %s is undefined" % (r.get("session"), r.get("stem"), c))
    src = open(os.path.join(CLUSTER, "vectors_build.py"), encoding="utf-8").read()
    # filling is writing a value into the matrix from other rows; a mean
    # taken of silhouettes to report a cluster's cohesion fills nothing
    filled = re.findall(r"\]\s*=\s*np\.nan\w+|fillna|SimpleImputer|impute\(", src)
    print("  INV-FEATURE-TOTAL")
    print("    %d column(s) over %d row(s); undefined values: %d; filling in "
          "vectors_build.py: %d" % (len(cols), len(rows), len(bad), len(filled)))
    for b in bad[:5]:
        print("    " + b)
    if filled:
        print("    vectors_build.py fills values: %s" % ", ".join(sorted(set(filled))))
    if bad or filled:
        print("    ERR-FEATURE-UNDEFINED")
    return not bad and not filled


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patterns", required=True)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    if not rows:
        raise SystemExit("%s holds no rows" % a.patterns)
    print("the vector rules, over %s" % os.path.basename(a.patterns))
    ok = [check_bounded(rows), check_total(rows)]
    if all(ok):
        print("  all hold")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
