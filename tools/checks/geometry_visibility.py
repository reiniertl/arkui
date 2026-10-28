#!/usr/bin/env python3
"""Geometry and visibility, checked on real dumps.

    ./geometry_visibility.py <capture-root> [--session NAME]

  INV-GEOMETRY-FROM-TREE  every node's rectangle is its Bounds composed down
                          the parent chain with Translate and Scale; no
                          position is read from oldDirty or innerAbsDrawRect
  INV-OPACITY             every node's state follows the three alpha rules,
                          and a visible node has some of its rectangle on
                          the display or a visible descendant
  INV-VISIBLE-ONLY        the feature code admits no node the walk did not
                          find visible

The composition is recomputed here independently, with full 3x3 matrices
from the dump's property lines, and compared with the parser's. The scenes
whose layout was confirmed by eye against their screenshots are checked
against those rectangles when they are in the capture.

Exit 0 when all three hold, 1 when any does not.
"""
import argparse, ast, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER = os.path.join(os.path.dirname(HERE), "cluster")
sys.path.insert(0, CLUSTER)
import rs_extract as R            # noqa: E402
import rs_pattern as P            # noqa: E402
import rs_visibility as V         # noqa: E402

# Confirmed against the screenshot taken with each dump: the video player's
# rectangle, which must come out whole and wholly visible.
VIDEO = {
    "20261022_123014/0102": (307, 0, 701, 1247),
    "20261022_123014/0049": (307, 0, 701, 1247),
    "20261022_123014/0179": (307, 0, 701, 1247),
    "20261022_123014/0027": (307, 0, 701, 1247),
    "20261022_110104/0002": (307, 0, 701, 1247),
    "20261022_123014/0192": (0, 253, 1316, 740),
    "20261027_102041/0010": (0, 134, 1316, 979),
}
# and the gallery under the player in 0192: its first row starts at y 2070
GRID_ROW = ("20261022_123014/0192", 2070)
TOL = 1.0         # px


def scenes(root, only=None):
    for sdir in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(sdir) or (only and os.path.basename(sdir) != only):
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        if not os.path.exists(os.path.join(sdir, "display.txt")):
            disp = R.read_display(os.path.join(root, "display.txt"))
        for tf in sorted(glob.glob(os.path.join(sdir, "*.rstree.txt"))):
            stem = os.path.basename(tf).split(".")[0]
            cf = os.path.join(sdir, stem + ".client.txt")
            if os.path.exists(cf):
                yield os.path.basename(sdir), stem, tf, cf, disp


def mat(n):
    """The node's local transform as a 3x3 matrix, built from its line."""
    b = n["bounds"] or (0.0, 0.0, 0.0, 0.0)
    px, py = n["pivot"][0] * b[2], n["pivot"][1] * b[3]

    def mul(A, B):
        return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]
    T = [[1, 0, b[0] + n["tx"]], [0, 1, b[1] + n["ty"]], [0, 0, 1]]
    P_ = [[1, 0, px], [0, 1, py], [0, 0, 1]]
    S = [[n["sx"], 0, 0], [0, n["sy"], 0], [0, 0, 1]]
    Pi = [[1, 0, -px], [0, 1, -py], [0, 0, 1]]
    return mul(mul(mul(T, P_), S), Pi), mul


def recompose(tree):
    """Every node's rectangle, by matrices rather than by the parser's code."""
    nodes = tree["nodes"]
    parent = {}
    for nid in tree["order"]:
        for k in nodes[nid]["kids"]:
            parent.setdefault(k, nid)
    M = {}

    def m_of(nid):
        if nid in M:
            return M[nid]
        local, mul = mat(nodes[nid])
        p = parent.get(nid)
        M[nid] = mul(m_of(p), local) if p is not None else local
        return M[nid]
    sys.setrecursionlimit(10000)
    out = {}
    for nid in tree["order"]:
        b = nodes[nid]["bounds"]
        if b is None:
            out[nid] = None
            continue
        A = m_of(nid)
        xs = [A[0][0] * x + A[0][2] for x in (0.0, b[2])]
        ys = [A[1][1] * y + A[1][2] for y in (0.0, b[3])]
        out[nid] = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    return out


def reads_cached_rects():
    """String constants in rs_extract.py's code, docstrings excluded, that
    would match the cached draw rectangles."""
    tree = ast.parse(open(R.__file__, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(node.body[0].value, ast.Constant):
                docs.add(id(node.body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs and re.search(r"oldDirty|innerAbsDrawRect", n.value)]


def same(a, b):
    return a is not None and b is not None and all(abs(x - y) <= TOL for x, y in zip(a, b))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="the capture tree")
    ap.add_argument("--session", default="")
    a = ap.parse_args()

    geo_bad, op_bad, only_bad = [], [], []
    n_scenes = n_nodes = n_vis = moved = 0
    states = {V.INVISIBLE: 0, V.OCCLUDING: 0, V.TRANSLUCENT: 0}
    found_video, found_grid = {}, None
    for c in reads_cached_rects():
        geo_bad.append("rs_extract.py reads a cached rectangle: %r" % c)

    for ses, stem, tf, cf, disp in scenes(a.root, a.session or None):
        sid = "%s/%s" % (ses, stem)
        n_scenes += 1
        tree = R.parse_tree(open(tf, encoding="utf-8", errors="replace").read().splitlines())
        tags = R.parse_client(open(cf, encoding="utf-8", errors="replace").read().splitlines())
        for nid, t in tags.items():
            if nid in tree["nodes"]:
                tree["nodes"][nid]["tag"] = t
        nodes = tree["nodes"]

        # INV-GEOMETRY-FROM-TREE: the parser against the matrices
        ref = recompose(tree)
        for nid, n in nodes.items():
            n_nodes += 1
            if (n["abs"] is None) != (ref[nid] is None) or \
                    (n["abs"] is not None and not same(n["abs"], ref[nid])):
                geo_bad.append("%s node %d: parsed %s, composed %s"
                               % (sid, nid, n["abs"], ref[nid]))

        R.set_ownership(tree)
        order = R.paint_order(tree)
        plain = R.compose_geometry(nodes, tree["order"], transforms=False)
        parent = {}
        for nid in tree["order"]:
            for k in nodes[nid]["kids"]:
                parent.setdefault(k, nid)
        for s in tree["surfaces"]:
            if s["nested"] or s["visible"] != 1 or not s["vis"]:
                continue
            vis = V.walk(nodes, s, disp["w"], disp["h"], order)
            # INV-OPACITY: each state from the rule, recomputed here
            for nid, v in vis.items():
                ea, q = 1.0, nid
                while q is not None and q in nodes:
                    ea *= nodes[q]["alpha"]
                    q = parent.get(q)
                want = (V.INVISIBLE if ea <= 0 else
                        V.OCCLUDING if ea >= 1 and nodes[nid]["bga"] == 0xFF else
                        V.TRANSLUCENT)
                states[v["state"]] += 1
                if v["state"] != want:
                    op_bad.append("%s node %d: state %s, the alpha rule says %s"
                                  % (sid, nid, v["state"], want))
                if v["visible"] and v["state"] == V.INVISIBLE:
                    op_bad.append("%s node %d: alpha 0 and visible" % (sid, nid))
                if v["visible"] and v["frac"] <= 0 and not any(
                        vis.get(k, {}).get("visible") for k in nodes[nid]["kids"]):
                    op_bad.append("%s node %d: visible with nothing on the display"
                                  % (sid, nid))
                if v["visible"]:
                    n_vis += 1
                    if not same(nodes[nid]["abs"], plain[nid]):
                        moved += 1
            # the confirmed rectangles
            for nid, v in vis.items():
                t = nodes[nid]["tag"]
                if sid in VIDEO and t == "XComponent" and same(nodes[nid]["abs"], VIDEO[sid]):
                    found_video[sid] = v["frac"]
                if sid == GRID_ROW[0] and t == "GridItem" \
                        and abs(nodes[nid]["abs"][1] - GRID_ROW[1]) <= TOL:
                    found_grid = (found_grid or []) + [v["visible"]]

        # INV-VISIBLE-ONLY: the feature code's admission is the walk's
        own = P.owner_of(tree, disp)
        if own is not None:
            vis = V.walk(nodes, own, disp["w"], disp["h"], order)
            sc = P.Scene(nodes, own, disp["density"], order, vis)
            for nid in nodes:
                if sc._mine(nid) and not vis.get(nid, {}).get("visible"):
                    only_bad.append("%s node %d is admitted but not visible" % (sid, nid))

    for sid, want in VIDEO.items():
        if a.session and not sid.startswith(a.session + "/"):
            continue
        if sid not in found_video:
            geo_bad.append("%s: no XComponent at %s" % (sid, want))
        elif found_video[sid] < 1.0:
            op_bad.append("%s: the video is %.3f visible, not whole"
                          % (sid, found_video[sid]))
    if not a.session or GRID_ROW[0].startswith(a.session + "/"):
        if not found_grid:
            geo_bad.append("%s: no GridItem at y %d" % GRID_ROW)
        elif not all(found_grid):
            op_bad.append("%s: a GridItem at y %d is not visible" % GRID_ROW)

    def report(inv, bad, line):
        print(inv)
        print("  " + line)
        for b in bad[:8]:
            print("    " + b)
        if len(bad) > 8:
            print("    ... and %d more" % (len(bad) - 8))
        print("  VIOLATED" if bad else "  holds")

    print("geometry and visibility, %d scene(s) from %s" % (n_scenes, a.root))
    report("INV-GEOMETRY-FROM-TREE", geo_bad,
           "%d node rectangles recomposed by matrix; %d confirmed video "
           "rectangle(s) found; %d visible node(s) move when Translate and "
           "Scale are applied" % (n_nodes, len(found_video), moved))
    report("INV-OPACITY", op_bad,
           "%d visible of %d walked: %d invisible, %d occluding, %d translucent; "
           "videos %s; GridItems at y %d visible: %s"
           % (n_vis, sum(states.values()), states[V.INVISIBLE], states[V.OCCLUDING],
              states[V.TRANSLUCENT],
              ", ".join("%.3f" % f for f in found_video.values()) or "-",
              GRID_ROW[1], found_grid))
    report("INV-VISIBLE-ONLY", only_bad,
           "every node the feature code admits was found visible by the walk")
    return 1 if (geo_bad or op_bad or only_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
