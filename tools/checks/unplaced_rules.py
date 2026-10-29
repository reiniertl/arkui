#!/usr/bin/env python3
"""INV-UNPLACED-SCENES-NAMED — what could not be placed is named.

    ./unplaced_rules.py --patterns patterns.csv --payload two_payload.json

One population: the scenes with no dictionary motif found on their display,
each named with its tag coverage. There is no second population - no window
is set aside as unreadable (INV-CLIENT-TAG-COVERAGE): a scene whose
components are mostly untagged is still measured, and its low coverage is
shown beside it.

NAMED, not merely counted. A count cannot be traced back to a scene. So the
check requires the identities, verifies they are real scenes from
patterns.csv, that each carries the coverage its row has, and that the set is
the one a fresh computation gives rather than whatever the payload says.

Exit 0 when it holds, 1 when it does not.
"""
import argparse, csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(os.path.dirname(HERE), "cluster"))
import vectors_build as VB            # noqa: E402

def fresh(rows):
    """Recompute the population, so the payload is checked and not read."""
    return {VB.row_identity(r): float(r["tag_coverage"]) for r in rows if not VB.found(r)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--payload", required=True)
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    d = json.load(open(a.payload, encoding="utf-8"))
    measured = {VB.row_identity(r) for r in rows}
    want = fresh(rows)

    print("INV-UNPLACED-SCENES-NAMED over %s" % os.path.basename(a.payload))
    up = d.get("_unplaced")
    if not up:
        print("    the payload carries no _unplaced block, so nothing the")
        print("    vectors could not place is named anywhere a reader looks")
        print("  ERR-SCENE-UNPLACED\n  VIOLATED")
        return 1

    ok = True
    got = up.get("pattern") or {}
    ids, cov = got.get("unplaced"), got.get("tag_coverage") or {}
    if ids is None:
        print("    the payload names no unplaced scenes, which is not the same as none")
        ok = False
        ids = []
    if "undescribed" in got:
        print("    a second population is still named: undescribed")
        ok = False
    ids = set(ids)
    print("  unplaced %d of %d (%.0f%%)" % (len(ids), len(rows), 100.0 * len(ids) / max(1, len(rows))))
    if ids != set(want):
        print("    payload says %d, a fresh computation says %d" % (len(ids), len(want)))
        for sid in sorted(ids ^ set(want))[:5]:
            print("      differs: %s" % sid)
        ok = False
    stray = ids - measured
    if stray:
        print("    %d named scene(s) are not in patterns.csv: %s" % (len(stray), ", ".join(sorted(stray)[:3])))
        ok = False
    off = [s_ for s_ in ids & set(want) if abs(float(cov.get(s_, -1)) - want[s_]) > 1e-9]
    if off:
        print("    %d named scene(s) carry no coverage or another than their row's: %s"
              % (len(off), ", ".join(sorted(off)[:3])))
        ok = False
    if ok:
        print("  every scene that could not be placed is named")
        print("  holds")
        return 0
    print("  ERR-SCENE-UNPLACED\n  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
