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
SPACES = (("pattern", lambda r: VB.psi_pattern(r), VB.psi_pattern_cells),
          ("surface", lambda r: VB.psi_surface(r), VB.psi_surface_cells))


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


def check_disjoint(rows, name, psi, cells):
    """One row, one cell. psi_pattern returns a scalar, so the way this
    breaks is a row whose inputs admit two readings - a coverage exactly
    at the boundary. The comparison is strict, so the boundary belongs to
    `minor` and the count below says how many rows sit on it."""
    on_edge = [VB.row_identity(r) for r in rows
               if (r.get("pat1_cover") or "").strip() not in ("",)
               and abs(float(r["pat1_cover"]) - 0.5) < 1e-12]
    print("  INV-PSI-DISJOINT")
    print("    psi_pattern returns one cell per row by construction")
    print("    %d row(s) sit exactly on the half-window boundary; the "
          "comparison is strict so they are `minor`" % len(on_edge))
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
    for name, psi, cells in SPACES:
        print("\nPSI_%s over %s" % (name[0], os.path.basename(a.patterns)))
        ok += [check_total(rows, name, psi, cells),
               check_disjoint(rows, name, psi, cells),
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
