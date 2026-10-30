#!/usr/bin/env python3
"""The scene in one pass, held against oracles of its definitions.

    ./scene_pass.py <capture-root> [--session NAME] [--patterns patterns.csv]

The pass (rs_scene.Scene) builds the scene and its vectors in one descent on
a 4 px raster. This recomputes the same things independently and compares:

  DISPLAY ORACLE   a full parse, the paint order from sortedChildren (its own)
                   and exact arithmetic on a compressed grid of every
                   rectangle edge,
                   no raster: every node's
                   on-display region - its shape, every clipping ancestor,
                   the display and its window's VisibleRegion, minus every
                   occluding shape painted above it outside its branch - and
                   from that the scene, the kept tree.
  PATTERN ORACLE   a deliberately naive implementation of the definitions,
                   plain recursion per root over the oracle's scene:
                   labels, element sets, d, the outermost match, composition,
                   and per display d, share and count.

  INV-DISPLAY-ROOT         the pass starts at the one LOGICAL_DISPLAY_NODE and
                           parses nothing outside it but its ancestors
  INV-SINGLE-PASS          no node is visited twice
  INV-PRUNED-NOT-PARSED    no node is parsed below a dropped branch, and a
                           node is parsed only as the child of one visited
  INV-GEOMETRY-FROM-TREE   every shape the pass composed equals the matrix
                           recomposition
  INV-OCCLUSION-PAINT-ORDER, INV-OPACITY, INV-CLIP-TO-BOUNDS, INV-VISIBLE-ONLY,
  INV-SCENE-PRUNED         the pass's kept tree equals the oracle's node for
                           node; a region thinner than one raster cell per
                           edge may be missed by the raster and is counted
  INV-VISIBLE-AREA-ONLY    every pattern share is within one cell per edge of
                           the exact area of its matches' visible regions
  pattern invariants       d and count per display equal the oracle's;
                           shuffling the order children are summarised in
                           changes no pattern column; the dictionary is a
                           literal
  INV-FEATURE-FIXED-MAP    every row computed alone equals the row in the
                           capture's patterns.csv

The confirmed scenes are checked against their rectangles when present.
Exit 0 when all hold, 1 when any does not.
"""
import argparse, ast, csv, glob, math, os, random, re, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER = os.path.join(os.path.dirname(HERE), "cluster")
sys.path.insert(0, CLUSTER)
import rs_extract as R            # noqa: E402
import rs_sample as PAIR           # noqa: E402
import rs_scene as S              # noqa: E402

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
# and the gallery in 0192: the row at y 2070 is on the display, the rows
# at 3117 and 4163 are below it
GRID = ("20261022_123014/0192", {2070: True, 3117: False, 4163: False})
# Photographed samples, confirmed against their own picture. The keyboard is
# on top in both pictures: a panel painted after TikTok's content in its
# window container, at the same PositionZ (INV-OCCLUSION-PAINT-ORDER).
KEYBOARD = ("20261022_110104/s000308", "20261022_110104/s000328")
# Swipers showing one full-screen video page, so one item and no carousel at
# that Swiper (INV-ITEMS-ARE-CHILDREN): s000552's keeps one child, whose
# wrapper lifted 14 nodes; s000001's outer one keeps the page and an empty
# full-screen Stack, not an item. s000001 still matches carousel through an
# inner Swiper whose layers are its own children, as s003479 does.
ONE_PAGE = ("20261022_110104/s000552", "20261022_123014/s000001")
# The launcher: its five windows, topmost first.
WINDOWS = ("20261027_102041/s000342", ("SCBGestureBack", "SCBStatusBar",
                                      "SCBGestureNavBar", "SCBDesktop", "SCBWallpaper"))
TOL = 1.0         # px, for rectangles
STEP = S.STEP


def paint_index(tree):
    """Each node's place in the paint order: a node is painted before its
    children, and children in sortedChildren order, the last on top
    (INV-OCCLUSION-PAINT-ORDER). ReverseChildren is the prepare stage's
    traversal and is not read."""
    nodes = tree["nodes"]
    child = {k for n in nodes.values() for k in n["kids"]}
    idx, i = {}, 0
    stack = [r for r in reversed(tree["order"]) if r not in child]
    while stack:
        nid = stack.pop()
        if nid in idx or nid not in nodes:
            continue
        idx[nid] = i
        i += 1
        stack.extend(reversed([k for k in nodes[nid]["kids"] if k in nodes]))
    for nid in tree["order"]:
        idx.setdefault(nid, i)
    return idx


RX_POSZ = re.compile(r"PositionZ\[(-?[\d.]+)\]")


def premise(lines):
    """INV-OCCLUSION-PAINT-ORDER's premise: every node's sortedChildren is
    non-decreasing in PositionZ. Read here, from each node's own line,
    since the parser no longer reads PositionZ. Returns the nodes where it
    fails and the number of nodes with two or more children."""
    ix = R.index_dump(lines)
    z = {}
    for nid, at in ix["first"].items():
        m = RX_POSZ.search(lines[at])
        z[nid] = float(m.group(1)) if m else 0.0
    bad, n = [], 0
    for nid, at in ix["first"].items():
        k = R.RX_KIDS.search(lines[at])
        ks = [int(x) for x in k.group(1).split()] if k else []
        ks = [x for x in ks if x in z]
        if len(ks) < 2:
            continue
        n += 1
        if any(z[a] > z[b] for a, b in zip(ks, ks[1:])):
            bad.append(nid)
    return bad, n


def oracle_scene(tree, tags, dw, dh, density):
    """The scene, recomputed exactly over the whole display, by definition.

    Exact and raster-free, on a compressed grid: every edge of every
    axis-aligned rectangle that matters - node boxes, clips, VisibleRegions,
    the display - becomes a grid line, so each cell is wholly inside or
    wholly outside every such rectangle and an area is an exact sum of cell
    areas. Occlusion is the definition, not a sweep: each cell holds the
    highest paint index of any occluder covering it, and a node is hidden
    in a cell exactly when that index lies above its whole branch. Paint
    order is paint_index's, built here from sortedChildren and shared with
    neither the pass nor rs_extract.

    Returns ({nid: {"kept", "kids", "area", "per", "cells"}}, ldn, area_of)
    where area_of(ids) is the exact area of the union of those nodes'
    visible regions.
    """
    import numpy as np
    nodes = tree["nodes"]
    parent = {}
    for nid in tree["order"]:
        for k in nodes[nid]["kids"]:
            parent.setdefault(k, nid)
    ldn = [i for i, n in nodes.items() if n["kind"] == "LOGICAL_DISPLAY_NODE"][0]
    order = paint_index(tree)
    surf = {s["id"]: s for s in tree["surfaces"]}
    info = {}

    def walk(nid, ea, clip, win, under):
        n = nodes[nid]
        ea *= n["alpha"]
        if n["kind"] == "SURFACE_NODE":
            if not under:
                s = surf[nid]
                win = nid if (s["visible"] == 1 and s["alpha"] > 0 and s["vis"]) else -1
            under = True
        info[nid] = {"ea": ea, "clip": clip, "win": win}
        b = n["abs"]
        kc = clip
        if n["clip"]:
            box = (b[0], b[1], b[0] + b[2], b[1] + b[3]) if b and b[2] > 0 and b[3] > 0 else None
            kc = _meet(clip, box)
        for k in n["kids"]:
            if k in nodes:
                walk(k, ea, kc, win, under)

    sys.setrecursionlimit(20000)
    walk(ldn, 1.0, (0.0, 0.0, dw, dh), None, False)
    last = {nid: order.get(nid, 0) for nid in info}
    for nid in sorted(info, key=lambda i: -order.get(i, 0)):
        p = parent.get(nid)
        if p in last and last[nid] > last[p]:
            last[p] = last[nid]

    def onbox(nid):
        """The node's box inside its clip, or None."""
        i, b = info[nid], nodes[nid]["abs"]
        if i["ea"] <= 0 or i["win"] == -1 or i["clip"] is None or not b or b[2] <= 0 or b[3] <= 0:
            return None
        return _meet(i["clip"], (b[0], b[1], b[0] + b[2], b[1] + b[3]))

    xs, ys = {0.0, dw}, {0.0, dh}
    for nid in info:
        r = onbox(nid)
        if r:
            xs.update((r[0], r[2])); ys.update((r[1], r[3]))
    for s in surf.values():
        for r in s["vis"]:
            xs.update((min(max(r[0], 0), dw), min(max(r[0] + r[2], 0), dw)))
            ys.update((min(max(r[1], 0), dh), min(max(r[1] + r[3], 0), dh)))
    X, Y = np.array(sorted(xs)), np.array(sorted(ys))
    cell = np.outer(np.diff(Y), np.diff(X))

    def idx(r):
        return (int(np.searchsorted(Y, r[1])), int(np.searchsorted(Y, r[3])),
                int(np.searchsorted(X, r[0])), int(np.searchsorted(X, r[2])))
    region = {}
    for w, s in surf.items():
        g = np.zeros(cell.shape, dtype=bool)
        for r in s["vis"]:
            rr = (max(r[0], 0), max(r[1], 0), min(r[0] + r[2], dw), min(r[1] + r[3], dh))
            if rr[2] > rr[0] and rr[3] > rr[1]:
                y0, y1, x0, x1 = idx(rr)
                g[y0:y1, x0:x1] = True
        region[w] = g

    # each cell: the highest paint index of an axis-aligned occluder over it
    top = np.full(cell.shape, -1, dtype=np.int64)
    for nid in info:
        n = nodes[nid]
        if not (info[nid]["ea"] >= 1 and n["bga"] == 0xFF):
            continue
        r = onbox(nid)
        if r is None:
            continue
        y0, y1, x0, x1 = idx(r)
        m = region[info[nid]["win"]][y0:y1, x0:x1] if info[nid]["win"] is not None else True
        sub = top[y0:y1, x0:x1]
        sub[...] = np.where(m, np.maximum(sub, order.get(nid, 0)), sub)

    out = {}
    for nid in info:
        o = {"area": 0.0, "per": 0.0, "cells": None}
        r = onbox(nid)
        if r is not None:
            y0, y1, x0, x1 = idx(r)
            vis = top[y0:y1, x0:x1] <= last[nid]
            if info[nid]["win"] is not None:
                vis &= region[info[nid]["win"]][y0:y1, x0:x1]
            if vis.any():
                o["area"] = float(cell[y0:y1, x0:x1][vis].sum())
                rows, cols = np.nonzero(vis)
                o["per"] = 2 * ((X[x0 + cols.max() + 1] - X[x0 + cols.min()]) +
                                (Y[y0 + rows.max() + 1] - Y[y0 + rows.min()]))
                o["cells"] = (y0, y1, x0, x1, vis)
        out[nid] = o
    for nid in sorted(out, key=lambda i: -order.get(i, 0)):
        o = out[nid]
        o["kept"] = o["area"] > 0 or o.get("kept", False)
        p = parent.get(nid)
        if p in out and o["kept"]:
            out[p]["kept"] = True
    for nid in out:
        out[nid]["kids"] = [k for k in nodes[nid]["kids"] if k in out and out[k]["kept"]]

    def area_of(ids):
        g = np.zeros(cell.shape, dtype=bool)
        for i in ids:
            c = out.get(i, {}).get("cells")
            if c:
                y0, y1, x0, x1, m = c
                g[y0:y1, x0:x1] |= m
        return float(cell[g].sum())

    def owned(ids):
        """INV-CLIENT-TAG-COVERAGE, exactly: each point of the display
        belongs to the deepest of these nodes whose visible region covers
        it, at equal depth the one painted later. Returns the area owned by
        tagged nodes and the area owned at all."""
        depth = {}
        for nid in tree["order"]:
            p = parent.get(nid)
            depth[nid] = depth.get(p, -1) + 1 if p is not None else 0
        od = np.full(cell.shape, -1, dtype=np.int64)
        oo = np.full(cell.shape, -1, dtype=np.int64)
        ot = np.zeros(cell.shape, dtype=bool)
        for i in ids:
            c = out.get(i, {}).get("cells")
            if not c:
                continue
            y0, y1, x0, x1, m = c
            d, o = depth.get(i, 0), order.get(i, 0)
            sd, so = od[y0:y1, x0:x1], oo[y0:y1, x0:x1]
            take = m & ((sd < d) | ((sd == d) & (so < o)))
            sd[take] = d
            so[take] = o
            ot[y0:y1, x0:x1][take] = bool(tags.get(i, ""))
        return float(cell[ot].sum()), float(cell[od >= 0].sum())
    return out, ldn, area_of, owned


def _meet(a, b):
    if a is None or b is None:
        return None
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def pass_scene(sc, tree):
    """The pass's kept tree, as plain dicts: each kept node's kept children
    and the window it sits in. The pattern oracle reads this, so it tests
    the matching on the scene the display oracle has already verified."""
    nodes = tree["nodes"]
    ldn = [i for i, n in nodes.items() if n["kind"] == "LOGICAL_DISPLAY_NODE"][0]
    out = {}

    def walk(nid, win, under):
        n = nodes[nid]
        if n["kind"] == "SURFACE_NODE":
            if not under:
                win = nid
            under = True
        out[nid] = {"kids": [k for k in n["kids"] if k in sc.kept], "win": win}
        for k in out[nid]["kids"]:
            walk(k, win, under)
    walk(ldn, None, False)
    return out


def oracle_patterns(tree, tags, sc, dw, dh, density):
    """d, share and count per pattern, by plain recursion over the scene.

    Deliberately naive: every quantity is recomputed from its definition at
    the node that needs it, with no summaries passed up. Areas are the
    pass's own visible cells, so a size label sees what the pass saw."""
    import functools
    import numpy as np
    nodes = tree["nodes"]
    memo = functools.lru_cache(maxsize=None)
    O = pass_scene(sc, tree)
    up = {k: i for i in O for k in O[i]["kids"]}       # the kept tree's parent

    def branch(nid):
        acc = [nid]
        for k in O[nid]["kids"]:
            acc += branch(k)
        return acc

    def cells(ids):
        g = np.zeros((sc.H, sc.W), dtype=bool)
        for i in ids:
            k = sc.kept[i]
            if k["free"] is not None:
                y0, y1, x0, x1 = k["box"]
                g[y0:y1, x0:x1] |= k["free"]
        return float(g.sum()) * STEP * STEP

    @memo
    def label(nid):
        n = nodes[nid]
        t = tags.get(nid, "")
        if t in S.PASS_THROUGH:
            return None
        if t in ("XComponent", "Video"):
            a = cells(branch(nid)) / (dw * dh)
            return "video:" + ("small" if a < S.SMALL else "medium" if a <= S.MEDIUM else "large")
        if t in ("TextInput", "TextArea", "Search"):
            return "text_input"
        if t in ("Image", "SymbolGlyph"):
            w, h = n["abs"][2] / density, n["abs"][3] / density
            if max(w, h) <= S.ICON_DP and S.ICON_AR[0] <= w / max(h, 1e-9) <= S.ICON_AR[1]:
                return "icon"
            return "thumb" if cells([nid]) / (dw * dh) < S.THUMB else "image_large"
        return t

    @memo
    def heads(nid):
        """The nearest kept meaningful descendants, through pass-through."""
        out = []
        for k in O[nid]["kids"]:
            out += [k] if label(k) is not None else list(heads(k))
        return tuple(out)

    @memo
    def pairs(nid):
        L = label(nid)
        # an item container's items are its kept children, each one item,
        # never what is lifted out of them (INV-ITEMS-ARE-CHILDREN)
        if L in ("List", "ListItemGroup", "Grid", "WaterFlow", "Swiper",
                 "SideBarContainer", "Scroll", "Refresh"):
            # an empty pass-through child is not an item
            n_items = len([k for k in O[nid]["kids"] if label(k) is not None or O[k]["kids"]])
            return {"%s/item%s" % (L, "*" if n_items >= 2 else "")} if n_items else set()
        cnt = {}
        for h in heads(nid):
            p = present(h)
            cnt[p] = cnt.get(p, 0) + 1
        return {"%s/%s%s" % (L, h, "*" if c >= 2 else "") for h, c in cnt.items()}

    @memo
    def d_at(nid):
        L = label(nid)
        S1 = {L} | pairs(nid)
        S2 = set(S1)
        for h in heads(nid):
            S2 |= pairs(h)
        ds = {}
        under_tabs = tags.get(up.get(nid), "") == "Tabs"
        for name, root, shp, depth in S.SHAPES:
            if root == L and not (name == "carousel" and under_tabs):
                X = {L} if depth == 0 else S1 if depth == 1 else S2
                ds[name] = min(1.0 - len(X & shp) / len(X | shp), ds.get(name, 1.0))
        return ds

    @memo
    def present(nid):
        for name, d in d_at(nid).items():
            if d <= S.FOUND and name in S.TOKENS:
                return name
        return label(nid)

    kept = list(O)
    dmin, found = {}, []
    for i in kept:                    # every kept node, whatever its tags
        if label(i) is None:
            continue
        for name, d in d_at(i).items():
            dmin[name] = min(d, dmin.get(name, 1.0))
            if d <= S.FOUND:
                found.append((name, i))
    parent = {k: i for i in kept for k in O[i]["kids"]}

    def ancestors(i):
        q = parent.get(i)
        while q is not None:
            yield q
            q = parent.get(q)
    fs = set(found)
    # outermost: a match with an ancestor matching the same pattern goes
    fs = {(p, i) for p, i in fs if not any((p, a) in fs for a in ancestors(i))}
    # composition: a token head of a composed match is consumed
    consumed = set()
    for p, i in fs:
        for h in heads(i):
            if present(h) in S.COMPOSED[p]:
                consumed.add((present(h), h))
    fs -= consumed
    return {p: {"d": dmin.get(p, 1.0), "n": len([i for q, i in fs if q == p])}
            for p in S.MOTIFS}


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


def dictionary_is_literal():
    """INV-DICTIONARY-DESIGNED, statically: DICTIONARY is a literal, and
    nothing else in the pass assigns to it or to SHAPES."""
    src = open(S.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            names = [t.id for t in n.targets if isinstance(t, ast.Name)]
            if "DICTIONARY" in names:
                try:
                    ast.literal_eval(n.value)
                except ValueError:
                    bad.append("DICTIONARY is not a literal")
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Subscript) and getattr(t.value, "id", "") in ("DICTIONARY", "SHAPES"):
                    bad.append("the dictionary is written to at line %d" % n.lineno)
    return bad


def recompose(tree):
    nodes = tree["nodes"]
    parent = {}
    for nid in tree["order"]:
        for k in nodes[nid]["kids"]:
            parent.setdefault(k, nid)

    def mul(A, B):
        return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]
    M = {}

    def m_of(nid):
        chain = []
        while nid is not None and nid not in M:
            chain.append(nid)
            nid = parent.get(nid)
        for c in reversed(chain):
            n = nodes[c]
            b = n["bounds"] or (0.0, 0.0, 0.0, 0.0)
            px, py = n["pivot"][0] * b[2], n["pivot"][1] * b[3]
            L = mul(mul(
                [[1, 0, b[0] + n["tx"] + px], [0, 1, b[1] + n["ty"] + py], [0, 0, 1]],
                [[n["sx"], 0, 0], [0, n["sy"], 0], [0, 0, 1]]),
                [[1, 0, -px], [0, 1, -py], [0, 0, 1]])
            p = parent.get(c)
            M[c] = mul(M[p], L) if p in M else L
        return M
    out = {}
    for nid in tree["order"]:
        b = nodes[nid]["bounds"]
        if b is None:
            out[nid] = None
            continue
        A = m_of(nid)[nid]
        pts = [(A[0][0] * x + A[0][1] * y + A[0][2], A[1][0] * x + A[1][1] * y + A[1][2])
               for x, y in ((0, 0), (b[2], 0), (b[2], b[3]), (0, b[3]))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
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


class Shuffled(S.Scene):
    """The pass with each node's kept children summarised in a random order.
    Occlusion is decided before _up runs, so only the element sets see it."""
    def _up(self, n, nid, v, kids, free, ptag):
        kids = list(kids)
        random.Random(nid).shuffle(kids)
        return S.Scene._up(self, n, nid, v, kids, free, ptag)


def same(a, b):
    return a is not None and b is not None and all(abs(x - y) <= TOL for x, y in zip(a, b))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="the capture tree")
    ap.add_argument("--session", default="")
    ap.add_argument("--patterns", default="", help="the capture's patterns.csv")
    a = ap.parse_args()
    csvrows = {}
    if a.patterns:
        for r in csv.DictReader(open(a.patterns, encoding="utf-8-sig")):
            csvrows["%s/%s" % (r["session"], r["sample"])] = r

    keys = ("root", "single", "pruned", "geo", "scene", "area", "pattern", "fixed", "premise",
            "coverage")
    bad = {k: [] for k in keys}
    n = dict.fromkeys(("scenes", "nodes", "parsed", "kept", "slivers",
                       "moved", "matches", "nodisplay", "compared", "parents", "coverage"), 0)
    ms_pass, found_video, grid_seen = [], {}, {}
    pcols = [p + x for p in S.MOTIFS for x in ("_d", "_share", "_n")]
    for c in reads_cached_rects():
        bad["geo"].append("rs_extract.py reads a cached rectangle: %r" % c)
    bad["pattern"] += dictionary_is_literal()

    # the scenes rs_pattern.py measures (ALG-PICTURE-PAIR)
    for ses, sample, tf, cf, disp in PAIR.scenes(a.root, a.session or None):
        sid = "%s/%s" % (ses, sample)
        dw, dh, dens = float(disp["w"]), float(disp["h"]), disp.get("density", 3.5) or 3.5
        lines = open(tf, encoding="utf-8", errors="replace").read().splitlines()
        client = open(cf, encoding="utf-8", errors="replace").read().splitlines() if cf else []
        nb, np_ = premise(lines)
        n["parents"] += np_
        bad["premise"] += ["%s node %d" % (sid, x) for x in nb]
        t = time.perf_counter()
        sc = S.Scene(lines, client, disp)
        try:
            row = sc.run()
        except S.NoDisplay:
            n["nodisplay"] += 1
            print("  ERR-NO-DISPLAY-NODE: %s yields no row" % sid)
            continue
        ms_pass.append(1000 * (time.perf_counter() - t))
        n["scenes"] += 1
        n["nodes"] += len(sc.ix["first"])
        n["parsed"] += len(sc.N)
        n["kept"] += len(sc.kept)
        tree = R.parse_tree(lines)
        tags = R.parse_client(client)
        nodes = tree["nodes"]

        # INV-DISPLAY-ROOT, INV-SINGLE-PASS, INV-PRUNED-NOT-PARSED
        ldn = [i for i, nd in nodes.items() if nd["kind"] == "LOGICAL_DISPLAY_NODE"][0]
        anc, q = set(), sc.ix["parent"].get(ldn)
        while q is not None:
            anc.add(q)
            q = sc.ix["parent"].get(q)
        under, st = set(), [ldn]
        while st:
            x = st.pop()
            if x in under or x not in nodes:
                continue
            under.add(x)
            st.extend(nodes[x]["kids"])
        stray = set(sc.N) - under - anc
        if stray:
            bad["root"].append("%s: %d node(s) parsed outside the display" % (sid, len(stray)))
        twice = [x for x, k in sc.visits.items() if k > 1]
        if twice:
            bad["single"].append("%s: %d node(s) visited twice" % (sid, len(twice)))
        par = sc.ix["parent"]
        for x in sc.N:
            if x in anc or x == ldn:
                continue
            p = par.get(x)
            if p not in sc.visits or sc.state.get(p) == S.INVISIBLE:
                bad["pruned"].append("%s: node %d parsed below a dropped branch" % (sid, x))
                break

        # INV-GEOMETRY-FROM-TREE: every shape the pass composed, by matrix
        ref = recompose(tree)
        plain = R.compose_geometry({k: dict(v) for k, v in nodes.items()}, tree["order"],
                                   transforms=False)
        # A node dropped at alpha 0 or in a window that is not visible is
        # never composed (INV-PRUNED-NOT-PARSED); every kept node is.
        for x, nd in sc.N.items():
            if x in sc.kept and nd["abs"] is None and ref[x] is not None:
                bad["geo"].append("%s node %d is kept and was never composed" % (sid, x))
            elif nd["abs"] is not None and not same(nd["abs"], ref[x]):
                bad["geo"].append("%s node %d: composed %s, matrix %s" % (sid, x, nd["abs"], ref[x]))
            if x in sc.kept and not same(nd["abs"], plain.get(x)):
                n["moved"] += 1

        # the scene, against the display oracle
        O, _, area_of, owned = oracle_scene(tree, tags, dw, dh, dens)
        for x, o in O.items():
            if x in sc.kept and not o["kept"]:
                bad["scene"].append("%s node %d is kept and is not on the display" % (sid, x))
            elif o["kept"] and x not in sc.kept:
                own = o["area"]
                if own > STEP * o["per"] or any(O[k]["kept"] and k in sc.kept for k in o["kids"]):
                    bad["scene"].append("%s node %d is on the display (%.0f px) and not kept"
                                        % (sid, x, own))
                else:
                    n["slivers"] += 1
        for x in sc.kept:
            if x not in O:
                bad["scene"].append("%s node %d is kept outside the display tree" % (sid, x))

        # the pattern vector, against the pattern oracle
        P = oracle_patterns(tree, tags, sc, dw, dh, dens)
        tot = dw * dh
        PS = pass_scene(sc, tree)

        def branch(i):
            acc = [i]
            for k in PS[i]["kids"]:
                acc += branch(k)
            return acc
        for p in S.MOTIFS:
            o, got = P[p], row
            n["matches"] += o["n"]
            if abs(got[p + "_d"] - round(o["d"], 4)) > 1e-4:
                bad["pattern"].append("%s %s: d %.4f, oracle %.4f" % (sid, p, got[p + "_d"], o["d"]))
            if abs(got[p + "_n"] - round(o["n"] / (o["n"] + 1.0), 4)) > 1e-4:
                bad["pattern"].append("%s %s: %d match(es) by the pass, %d by the oracle"
                                      % (sid, p, len([m for m in sc.matches if m["pattern"] == p]), o["n"]))
            # the exact visible area of the branches the pass matched
            ids = [i for m in sc.matches if m["pattern"] == p for i in branch(m["node"])]
            exact = area_of(ids)
            tol = STEP * sum(O[i]["per"] for i in ids if i in O)
            if got[p + "_share"] * tot > exact + tol + 0.0001 * tot:
                bad["area"].append("%s %s: share %.4f, its matches show %.4f"
                                   % (sid, p, got[p + "_share"], exact / tot))

        # INV-CLIENT-TAG-COVERAGE: the share of the visible canvas area whose
        # deepest node is tagged, from the display oracle's exact regions
        canvas = [i for i in O if O[i]["kept"] and nodes[i]["kind"] == "CANVAS_NODE"]
        num, den = owned(canvas)
        slack = 2 * STEP * sum(O[i]["per"] for i in canvas)
        if abs(row["tag_coverage"] * den - num) > slack + 0.0001 * tot:
            bad["coverage"].append("%s: tag coverage %.4f, the oracle's %.4f"
                                   % (sid, row["tag_coverage"], num / den if den else 0.0))
        n["coverage"] += 1

        # the properties
        sh = Shuffled(lines, client, disp).run()
        for c in pcols:
            if sh[c] != row[c]:
                bad["pattern"].append("%s %s changes when children are summarised in "
                                      "another order: %s, %s" % (sid, c, row[c], sh[c]))
        if sid in csvrows:
            n["compared"] += 1
            for c in pcols:
                if abs(float(csvrows[sid][c]) - row[c]) > 1e-9:
                    bad["fixed"].append("%s %s: alone %s, in the capture %s"
                                        % (sid, c, row[c], csvrows[sid][c]))


    # The confirmed dumps. They are the stored dumps a human checked against
    # the screenshot taken with each, named by their stem; most are not
    # photographed samples, so the pass is run on each directly.
    for sid in sorted(set(VIDEO) | {GRID[0]}):
        if a.session and not sid.startswith(a.session + "/"):
            continue
        ses, stem = sid.split("/")
        sdir = os.path.join(a.root, ses)
        tf, cf = (os.path.join(sdir, stem + x) for x in (".rstree.txt", ".client.txt"))
        if not (os.path.exists(tf) and os.path.exists(cf)):
            bad["geo"].append("%s: the confirmed dump is not in the capture" % sid)
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        lines = open(tf, encoding="utf-8", errors="replace").read().splitlines()
        client = open(cf, encoding="utf-8", errors="replace").read().splitlines()
        sc = S.Scene(lines, client, disp)
        sc.run()
        for x, k in sc.kept.items():
            nd = sc.N[x]
            if sid in VIDEO and nd["tag"] in ("XComponent", "Video") and same(nd["abs"], VIDEO[sid]):
                found_video[sid] = k["cells"] / k["on"] if k["on"] else 0.0
        if sid == GRID[0]:
            tags = R.parse_client(client)
            for x, nd in R.parse_tree(lines)["nodes"].items():
                if tags.get(x) == "GridItem" and nd["abs"]:
                    for y in GRID[1]:
                        if abs(nd["abs"][1] - y) <= TOL:
                            grid_seen.setdefault(y, []).append(x in sc.kept)

    # The confirmed photographed samples, run on their own render tree with
    # the tags their scene borrows.
    def photographed(sid):
        ses, smp = sid.split("/")
        for _, sdir, disp, rows in PAIR.sessions(a.root, ses):
            at = {r["raw"].strip(): i for i, r in enumerate(rows)}
            tf = os.path.join(sdir, "raw", smp + ".rstree.txt")
            if smp not in at or not os.path.exists(tf):
                return None, None
            lines = open(tf, encoding="utf-8", errors="replace").read().splitlines()
            cf = PAIR.tag_source(sdir, rows, at[smp])
            client = open(cf, encoding="utf-8", errors="replace").read().splitlines() if cf else []
            sc = S.Scene(lines, client, disp)
            sc.run()
            return sc, lines
        return None, None
    import numpy as np
    kb_cells = {}
    for sid in KEYBOARD:
        if a.session and not sid.startswith(a.session + "/"):
            continue
        sc, lines = photographed(sid)
        if sc is None:
            bad["scene"].append("%s: the confirmed sample is not in the capture" % sid)
            continue
        kb = [x for x, k in sc.ix["kind"].items()
              if k == "SURFACE_NODE" and "Name [softKeyboard" in lines[sc.ix["first"][x]]]
        branch = lambda x: [x] + [d for c in sc.N[x]["kids"] if c in sc.kept for d in branch(c)]
        ids = {d for x in kb if x in sc.kept for d in branch(x)}
        seen = np.zeros((sc.H, sc.W), dtype=bool)          # each cell once
        for d in ids:
            k = sc.kept[d]
            if k["free"] is not None:
                y0, y1, x0, x1 = k["box"]
                seen[y0:y1, x0:x1] |= k["free"]
        kb_cells[sid] = (len(ids), int(seen.sum()))
        if not kb_cells[sid][1]:
            bad["scene"].append("%s: the keyboard is on top in its picture and the pass "
                                "keeps none of it" % sid)
    one_page = {}
    for sid in ONE_PAGE:
        if a.session and not sid.startswith(a.session + "/"):
            continue
        sc, _ = photographed(sid)
        if sc is None:
            bad["pattern"].append("%s: the confirmed sample is not in the capture" % sid)
            continue
        def empty(c):
            return sc.label(sc.N[c], 0) is None and not any(k in sc.kept for k in sc.N[c]["kids"])
        targets = []
        for x in sc.kept:
            if sc.N[x]["tag"] != "Swiper":
                continue
            kept = [c for c in sc.N[x]["kids"] if c in sc.kept]
            items = sum(1 for c in kept if not empty(c))
            if kept and items <= 1:                 # a Swiper showing one page
                targets.append((x, len(kept), items))
        one_page[sid] = ["%d kept, %d item(s)" % (k, i) for _, k, i in targets]
        if not targets:
            bad["pattern"].append("%s: no Swiper with one page found" % sid)
        for x, k, i in targets:
            if any(m["pattern"] == "carousel" and m["node"] == x for m in sc.matches):
                bad["pattern"].append("%s: the Swiper %d keeps %d child(ren), %d item(s), and "
                                      "matches carousel" % (sid, x, k, i))
    win_order = None
    if not a.session or WINDOWS[0].startswith(a.session + "/"):
        sc, _ = photographed(WINDOWS[0])
        if sc is None:
            bad["scene"].append("%s: the confirmed sample is not in the capture" % WINDOWS[0])
        else:
            win_order = [w["name"].rstrip("0123456789") for w in sc.windows
                         if w["id"] in sc.kept]
            if tuple(win_order) != WINDOWS[1]:
                bad["scene"].append("%s: windows kept topmost first %s, expected %s"
                                    % (WINDOWS[0], win_order, list(WINDOWS[1])))

    for sid, want in VIDEO.items():
        if a.session and not sid.startswith(a.session + "/"):
            continue
        if sid not in found_video:
            bad["geo"].append("%s: no kept XComponent at %s" % (sid, want))
        elif found_video[sid] < 1.0:
            bad["scene"].append("%s: the video is %.3f visible, not whole" % (sid, found_video[sid]))
    if not a.session or GRID[0].startswith(a.session + "/"):
        for y, want in GRID[1].items():
            got = grid_seen.get(y)
            if not got or any(g != want for g in got):
                bad["scene"].append("%s: GridItems at y %d kept %s, expected %s"
                                    % (GRID[0], y, got, want))

    def report(invs, b, line, err):
        print(invs)
        print("  " + line)
        for x in b[:8]:
            print("    " + x)
        if len(b) > 8:
            print("    ... and %d more" % (len(b) - 8))
        if b:
            print("  " + err)
        print("  VIOLATED" if b else "  holds")

    q = sorted(ms_pass) or [0.0]
    print("the scene in one pass, %d scene(s) from %s" % (n["scenes"], a.root))
    print("  the pass alone: %.0f ms median, %.0f ms p90, %.0f ms max per dump; nodes "
          "per dump %d in the dump, %d parsed, %d kept (totals %d / %d / %d)"
          % (statistics.median(q), q[int(0.9 * len(q))], q[-1],
             n["nodes"] // max(1, n["scenes"]), n["parsed"] // max(1, n["scenes"]),
             n["kept"] // max(1, n["scenes"]), n["nodes"], n["parsed"], n["kept"]))
    report("INV-DISPLAY-ROOT", bad["root"],
           "every pass starts at the one LOGICAL_DISPLAY_NODE; %d dump(s) have none"
           % n["nodisplay"], "ERR-NO-DISPLAY-NODE")
    report("INV-SINGLE-PASS", bad["single"], "no node visited twice", "ERR-PASS-REVISIT")
    report("INV-PRUNED-NOT-PARSED", bad["pruned"],
           "every parsed node is the display's ancestor or the child of a visited node",
           "ERR-PASS-REVISIT")
    report("INV-GEOMETRY-FROM-TREE", bad["geo"],
           "every composed shape equals the matrix recomposition; %d confirmed video(s); "
           "%d kept node(s) move under Translate and Scale"
           % (len(found_video), n["moved"]), "ERR-GEOMETRY-MISMATCH")
    report("INV-SCENE-PRUNED, INV-OPACITY, INV-CLIP-TO-BOUNDS, INV-VISIBLE-ONLY, "
           "INV-OCCLUSION-PAINT-ORDER", bad["scene"],
           "the kept tree equals the oracle's node for node, the oracle ordering from "
           "sortedChildren; videos %s; keyboard kept %s; launcher windows %s; %d "
           "node(s) on the display by a sliver under one cell per edge"
           % (", ".join("%.3f" % v for v in found_video.values()) or "-",
              ", ".join("%s %d nodes %d cells" % ((k.split("/")[1],) + v) for k, v in kb_cells.items()) or "-",
              " > ".join(win_order) if win_order else "-", n["slivers"]),
           "ERR-NOT-ON-DISPLAY")
    report("INV-OCCLUSION-PAINT-ORDER, its premise", bad["premise"],
           "sortedChildren non-decreasing in PositionZ at all %d node(s) with two or more "
           "children" % n["parents"], "ERR-PAINT-ORDER-PREMISE")
    report("INV-VISIBLE-AREA-ONLY", bad["area"],
           "every pattern share within one cell per edge of its matches' exact visible area",
           "ERR-AREA-NOT-VISIBLE")
    report("INV-DICTIONARY-DESIGNED, INV-DISTANCE-ORDER-FREE, "
           "INV-MATCHES-OUTERMOST, INV-COMPOSITION-CONSUMES, INV-ITEMS-ARE-CHILDREN", bad["pattern"],
           "d and count equal the pattern oracle's on every display; %d match(es); shuffling "
           "changes nothing; no carousel at a Swiper showing one page: %s"
           % (n["matches"], "; ".join("%s %s" % (k.split("/")[1], ", ".join(v))
                                      for k, v in one_page.items()) or "-"),
           "ERR-MOTIF-ORACLE-MISMATCH")
    report("INV-CLIENT-TAG-COVERAGE", bad["coverage"],
           "every scene's tag coverage equals the oracle's exact one within one cell per "
           "edge (%d scenes); no window and no match is set aside for it" % n["coverage"],
           "ERR-MOTIF-ROOT-UNTAGGED")
    report("INV-FEATURE-FIXED-MAP", bad["fixed"],
           "%d row(s) computed alone equal their row in the capture" % n["compared"]
           if a.patterns else "not checked (pass --patterns)", "ERR-MOTIF-ORACLE-MISMATCH")
    return 1 if any(bad.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
