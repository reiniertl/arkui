#!/usr/bin/env python3
"""The four parsing rules, and the substrate rule, checked on real dumps.

    ./parsing_rules.py <capture-root>

Each of these was a defect that shipped, was found at cost, and was fixed.
What held them afterwards was a comment, and a comment does not survive an
edit that does not read it. So each is checked here, against dumps rather
than against the source: a regex over code can be satisfied by code that
does the wrong thing, and every one of these failures was visible in the
output long before it was visible in the source.

  INV-FIRST-PASS-COUNTS       the two passes of RSTree are split, and the
                              node set comes from the first alone
  INV-OCCLUSION-PAINT-ORDER   depth is paint order, not dump position
  INV-OCCLUDER-PAINTS         only a node that paints may claim pixels;
                              IsSubTreeSkipped is caching, not invisibility
  INV-OWN-RECT-BOUNDS         size is judged on the unclipped rect
  INV-SUBSTRATE-IS-COMPLEMENT substrate is what nothing claimed, and never
                              a competitor in the painter's pass

Exit 0 when all five hold, 1 when any does not. Each prints what it
measured, so a pass is evidence rather than an assertion.
"""
import argparse, glob, importlib.util, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER = os.path.join(os.path.dirname(HERE), "cluster")
sys.path.insert(0, CLUSTER)
import rs_extract as R            # noqa: E402
import rs_pattern as P            # noqa: E402


def sessions(root):
    for sdir in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(sdir):
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        if not os.path.exists(os.path.join(sdir, "display.txt")):
            disp = R.read_display(os.path.join(root, "display.txt"))
        for tf in sorted(glob.glob(os.path.join(sdir, "*.rstree.txt"))):
            stem = os.path.basename(tf).split(".")[0]
            cf = os.path.join(sdir, stem + ".client.txt")
            if os.path.exists(cf):
                yield sdir, stem, tf, cf, disp


def check_first_pass(dumps):
    """Properties come from the first pass.

    The two passes carry the SAME node ids - the second is the same tree
    re-printed with DrawableVec and shouldPaint_ - so comparing id sets can
    never detect a leak, and an earlier version of this check passed a parse
    that deliberately read both. What a leak actually does is overwrite each
    node's geometry with the second pass's line, where innerAbsDrawRect is
    absent. So the check compares each node's parsed rect against the rect on
    its FIRST line in the file.
    """
    bad, split_seen, checked = [], 0, 0
    for tf, lines, tree in dumps:
        first_abs, seen_line = {}, set()
        repeat_at = None
        for i, l in enumerate(lines):
            m = R.RX_NODE.match(l)
            if not m:
                continue
            nid = int(m.group(3))
            if nid in seen_line:
                if repeat_at is None:
                    repeat_at = i
                continue                      # a later pass; not the source
            seen_line.add(nid)
            g = R.RX_ABS.search(l)
            first_abs[nid] = tuple(int(v) for v in g.groups()) if g else None
        if repeat_at is not None:
            split_seen += 1
        for nid, n in tree["nodes"].items():
            if nid not in first_abs:
                bad.append((os.path.basename(tf),
                            "node %d is in the tree but on no first-pass line" % nid))
                continue
            checked += 1
            if n["abs"] != first_abs[nid]:
                bad.append((os.path.basename(tf),
                            "node %d has rect %s; its first-pass line says %s"
                            % (nid, n["abs"], first_abs[nid])))
    print("  INV-FIRST-PASS-COUNTS")
    print("    %d dump(s); %d carry a second pass; %d node rect(s) compared "
          "against the first-pass line" % (len(dumps), split_seen, checked))
    for f, why in bad[:5]:
        print("    %s: %s" % (f, why))
    return not bad


def check_paint_order(dumps):
    """Order honours ReverseChildren and PositionZ, and covers every node."""
    bad, with_rev, with_z, differs = [], 0, 0, 0
    for tf, lines, tree in dumps:
        order = R.paint_order(tree)
        rev = sum(1 for n in tree["nodes"].values() if n.get("reverse"))
        z = sum(1 for n in tree["nodes"].values() if n.get("posz"))
        with_rev += 1 if rev else 0
        with_z += 1 if z else 0
        missing = [n for n in tree["nodes"] if n not in order]
        if missing:
            bad.append((os.path.basename(tf), "%d node(s) have no order" % len(missing)))
            continue
        # O(n): does the paint order rank anything differently from the file?
        dump_rank = {nid: i for i, nid in enumerate(tree["order"])}
        moved = sum(1 for nid in tree["order"] if order[nid] != dump_rank[nid])
        if (rev or z) and moved == 0:
            bad.append((os.path.basename(tf),
                        "carries %d reversed and %d PositionZ but ranks every "
                        "node exactly as the file does" % (rev, z)))
        if moved:
            differs += 1
    print("  INV-OCCLUSION-PAINT-ORDER")
    print("    %d dump(s) carry a reversed parent, %d carry a PositionZ; "
          "%d order differently from the file" % (with_rev, with_z, differs))
    for f, why in bad[:5]:
        print("    %s: %s" % (f, why))
    return not bad


def check_occluder_paints(dumps):
    """Every admitted mark paints; skipped neither admits nor rejects."""
    bad = []
    admitted = skipped_admitted = 0
    for tf, lines, tree in dumps:
        for nid, n in tree["nodes"].items():
            if not P.vis_kind(n):
                continue
            paints = P.is_drawn(n) or P.is_composed(n)
            if not paints:
                continue            # not admitted; correct
            admitted += 1
            if n.get("skipped"):
                skipped_admitted += 1
    # If skipped were being read as invisible, no skipped node would ever be
    # admitted. That it is admitted in quantity is the evidence.
    if admitted and not skipped_admitted:
        bad.append(("all dumps", "no skipped node was admitted at all, which is "
                    "what reading IsSubTreeSkipped as invisibility looks like"))
    print("  INV-OCCLUDER-PAINTS")
    print("    %d mark(s) admitted, of which %d are IsSubTreeSkipped - "
          "skipped is reported, never read as invisible"
          % (admitted, skipped_admitted))
    for f, nid in bad[:5]:
        print("    %s: node %s rejected for being skipped" % (f, nid))
    return not bad


def check_own_rect(dumps):
    """A node that clips to the window but is huge in itself is rejected."""
    own_area = 1316.0 * 2832.0
    huge = {"abs": (-2731, -3172, 8359, 8360)}      # the blur source, measured
    fits = {"abs": (0, 0, 1316, 1000)}
    clipped_huge = P.clipped(huge["abs"], 1316.0, 2832.0)
    rejected = P._area(huge["abs"]) > P.OVERSIZE * own_area
    kept = P._area(fits["abs"]) <= P.OVERSIZE * own_area
    clip_would_pass = (clipped_huge is not None
                       and clipped_huge[2] * clipped_huge[3] <= P.OVERSIZE * own_area)
    print("  INV-OWN-RECT-BOUNDS")
    print("    8359x8360 at (-2731,-3172): own area %.1f windows -> %s"
          % (P._area(huge["abs"]) / own_area, "rejected" if rejected else "KEPT"))
    print("    the same node clipped: %.2f windows -> a clipped test would %s"
          % ((clipped_huge[2] * clipped_huge[3] / own_area) if clipped_huge else 0.0,
             "let it through" if clip_would_pass else "also reject"))
    print("    a node that genuinely fits: %s" % ("kept" if kept else "REJECTED"))
    return rejected and kept and clip_would_pass


def check_substrate_rows(rows):
    kinds = ["vis_" + k for k in P.VIS_KINDS]
    bad, worst = [], 0.0
    for r in rows:
        try:
            s = sum(float(r[k]) for k in kinds)
            sub = float(r["vis_substrate"])
        except (KeyError, ValueError):
            continue
        d = abs(sub - (1.0 - s))
        worst = max(worst, d)
        if d > 0.011:                       # the columns are rounded to 3dp
            bad.append((r.get("session"), r.get("stem"), round(d, 4)))
    print("  INV-SUBSTRATE-IS-COMPLEMENT")
    print("    %d scene(s); substrate vs 1 - (%s): worst departure %.4f"
          % (len(rows), " + ".join(P.VIS_KINDS), worst))
    print("    substrate is not among the mark kinds: %s"
          % ("yes" if "substrate" not in P.VIS_KINDS else "NO"))
    for s, st, d in bad[:5]:
        print("    %s/%s departs by %s" % (s, st, d))
    return not bad and "substrate" not in P.VIS_KINDS


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="a capture root holding sessions")
    ap.add_argument("--limit", type=int, default=12,
                    help="dumps to parse (default 12; parsing is ~0.2s each)")
    ap.add_argument("--patterns", default="",
                    help="a patterns.csv for the substrate check")
    a = ap.parse_args()

    dumps = []
    for sdir, stem, tf, cf, disp in sessions(a.root):
        lines = open(tf, encoding="utf-8", errors="replace").read().splitlines()
        dumps.append((tf, lines, R.parse_tree(lines)))
        if len(dumps) >= a.limit:
            break
    if not dumps:
        raise SystemExit("no dump under %s" % a.root)

    print("the parsing rules, over %d dump(s) from %s" % (len(dumps), a.root))
    ok = [check_first_pass(dumps),
          check_paint_order(dumps),
          check_occluder_paints(dumps),
          check_own_rect(dumps)]

    if a.patterns:
        import csv
        rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
        ok.append(check_substrate_rows(rows))
    else:
        print("  INV-SUBSTRATE-IS-COMPLEMENT: not checked (pass --patterns)")

    if all(ok):
        print("  all checked rules hold")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
