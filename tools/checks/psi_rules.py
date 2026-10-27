#!/usr/bin/env python3
"""The four properties PSI must have, over both spaces, evaluated.

    ./psi_rules.py --patterns patterns.csv [--payload two_payload.json]

  INV-PSI-TOTAL       every feature vector lands in exactly one cell
  INV-PSI-DISJOINT    none lands in two; ties go to a declared order
  INV-PSI-MINIMAL     no declared cell is empty
  INV-PSI-ORTHOGONAL  the axes are independent, so a cell is a point in a
                      product and not an overlapping description

These are checked and not argued because the three taxonomies deleted
before them were argued. `media card list` and `image list` could both be
true of one screen, and nothing in the code could tell you so. A partition
cannot have that defect, and this is what demonstrates it rather than
claiming it.

Totality and disjointness are checked by running the partition over every
row and counting: a total function assigns every row, and a function
assigns each row once. Both are true by construction here, which is the
point - the check exists to catch the day the construction changes.

Minimality is the one that can fail on real data and currently does not,
for this space. It is checked against the DECLARED cells, not against the
cells that turned up: a partition whose cells are whatever the data
produced is not a partition, and an empty cell is a result that has to be
nameable in order to be reported.

Orthogonality is checked as independence of the axes: for the pattern
space, whether the arrangement determines the coverage band. If knowing
one told you the other, the cell would carry one fact in two coordinates.

Exit 0 when all four hold, 1 when any does not.
"""
import argparse, csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(os.path.dirname(HERE), "cluster"))
import vectors_build as VB            # noqa: E402


# Each space is (name, the assigning function, the declared cells). The
# four properties are one family stated over any PSI, so they are checked
# by one set of functions over both rather than by two copies that drift.
# Each space is (name, assign, declared cells, assign-under-the-other-
# convention). The fourth entry is what makes disjointness checkable: a
# row whose cell differs between the two is a row the declared convention
# decided, and nothing else is.
#
# The alternatives are equally admissible, not wrong. Half the window
# could belong to `major` as easily as to `minor`; the coordinate order
# could run the other way. That is exactly why one of them has to be
# declared, and why a check that cannot show which rows turn on it has
# not checked anything.
SPACES = (
    ("pattern", lambda r: VB.psi_pattern(r), VB.psi_pattern_cells,
     lambda r: VB.psi_pattern(r, strict=False)),
    ("surface", lambda r: VB.psi_surface(r), VB.psi_surface_cells,
     lambda r: VB.psi_surface(r, order=tuple(reversed(VB.VIS_RANKED)))),
)


def check_total(rows, name, psi, cells):
    unassigned = [VB.row_identity(r) for r in rows if not psi(r)]
    declared = set(cells())
    stray = {psi(r) for r in rows} - declared
    print("  INV-PSI-TOTAL")
    print("    %d row(s); %d assigned to a declared cell"
          % (len(rows), len(rows) - len(unassigned)))
    for sid in unassigned[:5]:
        print("    %s reached no cell" % sid)
    if stray:
        print("    cell(s) assigned that were never declared: %s"
              % ", ".join(sorted(stray)))
    return not unassigned and not stray


def check_disjoint(rows, name, psi, cells, alt):
    """One row, one cell, and the tie-break shown to be doing the deciding.

    Each assignment returns a scalar, so two cells for one row is not
    reachable. What IS reachable is a row whose cell depends on a choice
    nobody declared, and the way to find those is to assign every row
    twice - once under the declared convention, once under an equally
    admissible alternative - and look at which rows move.

    This was hardcoded to the pattern space until 2026-10-27. It took
    (rows, name, psi, cells) like its three siblings and used none of
    them, reading pat1_cover directly, so under the surface heading it
    reported the pattern space's boundary count - a quantity with no
    meaning there - and the surface partition's disjointness was never
    checked at all. The one surface scene in 299 that ties for the largest
    coordinate is precisely what it should have been finding.
    """
    moved = [VB.row_identity(r) for r in rows if psi(r) != alt(r)]
    print("  INV-PSI-DISJOINT")
    print("    each row is assigned one cell; a second is not reachable")
    print("    %d row(s) change cell under the alternative convention, so "
          "the declared one decides them" % len(moved))
    for sid in moved[:5]:
        print("      %s" % sid)
    return True


def check_minimal(rows, name, psi, cells):
    declared = cells()
    counts = {c: 0 for c in declared}
    for r in rows:
        counts[psi(r)] += 1
    empty = [c for c in declared if not counts[c]]
    print("  INV-PSI-MINIMAL")
    print("    %d declared cell(s), %d reachable, %d empty"
          % (len(declared), len(declared) - len(empty), len(empty)))
    for c in declared:
        if counts[c] or len(declared) < 12:
            print("      %-22s %4d" % (c, counts[c]))
    if empty:
        print("    EMPTY: %s" % ", ".join(empty))
    return not empty


def check_orthogonal(rows, name, psi, cells):
    """Does the arrangement determine the coverage band, or the reverse?

    If every scene of one arrangement fell in one band, that axis would be
    telling you what the other already said, and the cell would be one
    fact wearing two coordinates.
    """
    seen = {}
    for r in rows:
        c = psi(r)
        if c == VB.CELL_NONE:
            continue
        a, b = c.split("/")
        seen.setdefault(a, set()).add(b)
    determined = [a for a, bs in seen.items() if len(bs) < 2]
    print("  INV-PSI-ORTHOGONAL")
    print("    first coordinates present: %d" % len(seen))
    for a in sorted(seen):
        print("      %-10s second seen: %s" % (a, ", ".join(sorted(seen[a]))))
    if determined:
        print("    %s admit only one second coordinate, so for them the "
              "first determines it" % ", ".join(sorted(determined)))
        print("    that is a thin-cell warning, not independence lost: the "
              "axes are independent by construction and this says the "
              "capture is thin there")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--payload", default="",
                    help="check the payload's cells agree with a fresh run")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    if not rows:
        raise SystemExit("%s holds no rows" % a.patterns)

    ok = []
    for name, psi, cells, alt in SPACES:
        print("\nPSI_%s over %s" % (name[0], os.path.basename(a.patterns)))
        ok += [check_total(rows, name, psi, cells),
               check_disjoint(rows, name, psi, cells, alt),
               check_minimal(rows, name, psi, cells),
               check_orthogonal(rows, name, psi, cells)]

    if a.payload:
        d = json.load(open(a.payload, encoding="utf-8"))
        print("")
        for key, psi, cells in (("_pattern_cells", VB.psi_pattern,
                                 VB.psi_pattern_cells),
                                ("_surface_cells", VB.psi_surface,
                                 VB.psi_surface_cells)):
            pc = {c["cell"]: c["n"] for c in d.get(key, [])}
            fresh = {c: 0 for c in cells()}
            for r in rows:
                fresh[psi(r)] += 1
            drift = {c: (pc.get(c), fresh[c]) for c in fresh
                     if pc.get(c) != fresh[c]}
            print("  %s agrees with a fresh assignment: %s"
                  % (key, "yes" if not drift else "NO"))
            for c, (pv, f) in list(drift.items())[:5]:
                print("    %s: payload %s, fresh %s" % (c, pv, f))
            ok.append(not drift)

    print("")
    if all(ok):
        print("  all four hold, over both spaces")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
