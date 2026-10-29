#!/usr/bin/env python3
"""INV-UNPLACED-SCENES-NAMED — what could not be placed is named.

    ./unplaced_rules.py --patterns patterns.csv --payload two_payload.json

Two populations, and they are different claims:

  unplaced     no dictionary pattern was found where a window could be read
  undescribed  none was found, and no window on the display was tagged
               enough for its structure to be read

They are checked apart because merging them loses the distinction that
matters: the first says the display holds no structure the dictionary
names, the second that its structure could not be read at all.

NAMED, not merely counted. A count cannot be traced back to a scene, and
this page published a cluster count and a silhouette for a long time with
no indication of how many scenes sat outside every cluster.
So the check requires the identities, verifies they are real scenes from
patterns.csv, and verifies the counts are the ones a fresh computation
gives rather than whatever the payload happens to say.

Exit 0 when it holds, 1 when it does not.
"""
import argparse, csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(os.path.dirname(HERE), "cluster"))
import vectors_build as VB            # noqa: E402

SPACES = ("pattern",)


def fresh(rows):
    """Recompute both populations, so the payload is checked and not read."""
    def f(r, c):
        v = (r.get(c) or "").strip()
        return float(v) if v else 0.0
    return {
        "pattern": {
            "undescribed": {VB.row_identity(r) for r in rows
                            if not VB.found(r) and not f(r, "read_windows")},
            "unplaced": {VB.row_identity(r) for r in rows
                         if not VB.found(r) and f(r, "read_windows")},
        },
    }


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
        print("  VIOLATED")
        return 1

    ok = True
    for sp in SPACES:
        got = up.get(sp)
        print("  %s" % sp)
        if got is None:
            print("    the payload names no populations for this space")
            ok = False
            continue
        if not (got.get("note") or "").strip():
            print("    no note: the space's asymmetry is unexplained")
            ok = False
        for pop in ("undescribed", "unplaced"):
            ids = got.get(pop)
            if ids is None:
                print("    %s is absent, which is not the same as empty" % pop)
                ok = False
                continue
            ids = set(ids)
            exp = want[sp][pop]
            stray = ids - measured
            print("    %-12s %3d of %d (%2.0f%%)"
                  % (pop, len(ids), len(rows),
                     100.0 * len(ids) / max(1, len(rows))))
            if ids != exp:
                print("      payload says %d, a fresh computation says %d"
                      % (len(ids), len(exp)))
                for sid in sorted(ids ^ exp)[:5]:
                    print("        differs: %s" % sid)
                ok = False
            if stray:
                print("      %d named scene(s) are not in patterns.csv: %s"
                      % (len(stray), ", ".join(sorted(stray)[:3])))
                ok = False
    if ok:
        print("  every scene that could not be placed is named")
        print("  holds")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
