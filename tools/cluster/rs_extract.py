#!/usr/bin/env python3
"""Read a RenderService dump: only what selection and matching need.

    index_dump(lines)        each node's id, kind, line and parent, and nothing
                             else - a node nobody reaches costs one match
    parse_node(lines, ix, n) one node's selection fields
    parse_surface(...)       one window's name, Visible, alpha, VisibleRegion
    parse_tree(lines)        the whole tree parsed and placed, for the checks
    parse_client(lines)      the client dump -> node id -> component tag
    read_display(path)       the display's size and density

INV-PARSE-ONLY-SELECTION-FIELDS: per node only id, kind, parent, children,
Bounds, Translate, Scale, Pivot, Alpha, the background colour's alpha,
and ClipToBounds are read; per window its name,
Visible, alpha and VisibleRegion. Nothing from the uni-render pass is read:
how a node is rendered is not this project's question.
"""
import math, os, re

RX_NODE = re.compile(r'^(\s*\|?\s*)([A-Z_]+NODE)\[(\d+)\]')
RX_KIDS = re.compile(r'sortedChildren:\s*\d+\(([^)]*)\)')
RX_NAME = re.compile(r'Name \[([^\]]+)\]')
RX_ALPHA = re.compile(r'Alpha:\s*([\d.]+)')
RX_VIS = re.compile(r'Visible:\s*(\d)')
RX_CLIENT = re.compile(r'\w+Node\[(\d+)\].*?instanceId\[(\d+)\].*?frameNodeId\[(\d+)\], frameNodeTag\[([^\]]*)\]')
RX_LOCAL = re.compile(r'Properties:\s*Bounds\[\s*(-?[\d.]+|-?inf)\s+(-?[\d.]+|-?inf)\s+(-?[\d.]+|-?inf)\s+(-?[\d.]+|-?inf)\s*\]')
RX_TX = re.compile(r'\bTranslateX\[(-?[\d.]+)\]')
RX_TY = re.compile(r'\bTranslateY\[(-?[\d.]+)\]')
RX_SX = re.compile(r'\bScaleX\[(-?[\d.]+)\]')
RX_SY = re.compile(r'\bScaleY\[(-?[\d.]+)\]')
RX_PIVOT = re.compile(r'\bPivot\[(-?[\d.]+),\s*(-?[\d.]+)\]')
RX_PALPHA = re.compile(r'\bAlpha\[(-?[\d.]+)\]')
RX_BG = re.compile(r'\bBackgroundColor\[#([0-9A-Fa-f]{2})[0-9A-Fa-f]{6}\]')



def _bounds(line):
    """The node's own Bounds, or None when absent or not finite."""
    m = RX_LOCAL.search(line)
    if not m:
        return None
    v = [float(x) for x in m.groups()]
    return tuple(v) if all(math.isfinite(x) for x in v) else None


def _num(rx, line, dflt):
    m = rx.search(line)
    return float(m.group(1)) if m else dflt


def region_rects(line, key):
    """A region is '[x, y, w, h]' repeated, or '[Empty]'."""
    m = re.search(re.escape(key) + r'(?:\s+\d+)?\s*:?\s*(.*)$', line)
    if not m:
        return []
    rest, out = m.group(1), []
    rx = re.compile(r'^\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\s*,?')
    while True:
        r = rx.match(rest)
        if not r:
            break
        out.append(tuple(int(v) for v in r.groups()))
        rest = rest[r.end():]
    return out


def region_area(rects):
    """Union area by sweeping x slabs and merging y intervals."""
    rs = [(x, y, x + w, y + h) for (x, y, w, h) in rects if w > 0 and h > 0]
    if not rs:
        return 0.0
    xs = sorted({r[0] for r in rs} | {r[2] for r in rs})
    total = 0.0
    for i in range(len(xs) - 1):
        x1, x2 = xs[i], xs[i + 1]
        w = x2 - x1
        if w <= 0:
            continue
        mid = (x1 + x2) / 2.0
        iv = sorted((r[1], r[3]) for r in rs if r[0] <= mid < r[2])
        if not iv:
            continue
        h, cs, ce = 0.0, iv[0][0], iv[0][1]
        for a, b in iv[1:]:
            if a > ce:
                h += ce - cs
                cs, ce = a, b
            elif b > ce:
                ce = b
        h += ce - cs
        total += w * h
    return total


def index_dump(lines):
    """Where everything is, without parsing it.

    RSTree prints the tree twice: the main thread's pass, which carries the
    properties, and the uni-render thread's. Only the first is read. The
    split is the first repeated node id - a node cannot appear twice in one
    tree - so a build that renames the marker still splits in the right
    place. Each node's id, kind, line and parent are read from the start of
    its line and its indentation; nothing else is, so a node nobody reaches
    costs one match (INV-PRUNED-NOT-PARSED).
    """
    ix = {"first": {}, "kind": {}, "parent": {}, "order": []}
    stack, seen = [], set()
    for i, l in enumerate(lines):
        if "RenderServiceUniThread" in l:
            break
        m = RX_NODE.match(l)
        if not m:
            continue
        nid, ind = int(m.group(3)), len(m.group(1))
        if nid in seen:
            break
        seen.add(nid)
        while stack and stack[-1][0] >= ind:
            stack.pop()
        if stack:
            ix["parent"][nid] = stack[-1][1]
        stack.append((ind, nid))
        ix["first"][nid] = i
        ix["kind"][nid] = m.group(2)
        ix["order"].append(nid)
    return ix


def parse_node(lines, ix, nid):
    """One node's selection fields, from its first-pass line."""
    l = lines[ix["first"][nid]]
    # Every layout property sits in one short segment, Properties: up to
    # RSUIContextToken - a median of 72 characters in lines of 659 - so it
    # is searched there; sortedChildren follows it.
    i = l.find("Properties:")
    j = l.find("RSUIContextToken", i) if i != -1 else -1
    pr = "" if i == -1 else (l[i:] if j == -1 else l[i:j])
    k = RX_KIDS.search(l, j if j != -1 else 0)
    pv = RX_PIVOT.search(pr)
    bg = RX_BG.search(pr)
    return {
        "id": nid, "kind": ix["kind"][nid], "pid": nid >> 32, "tag": "",
        "abs": None,                    # placed by whoever composes it
        "bounds": _bounds(pr),
        "tx": _num(RX_TX, pr, 0.0), "ty": _num(RX_TY, pr, 0.0),
        "sx": _num(RX_SX, pr, 1.0), "sy": _num(RX_SY, pr, 1.0),
        "pivot": (float(pv.group(1)), float(pv.group(2))) if pv else (0.5, 0.5),
        "alpha": _num(RX_PALPHA, pr, 1.0),
        # the alpha byte of the background colour; None when the node has
        # no background colour and so never occludes
        "bga": int(bg.group(1), 16) if bg else None,
        "clip": "ClipToBounds[True]" in pr,
        # sortedChildren: the paint order, stable-sorted by PositionZ in
        # RenderService itself (INV-OCCLUSION-PAINT-ORDER)
        "kids": [int(x) for x in k.group(1).split()] if k else [],
    }


def parse_surface(lines, ix, n):
    """A window's name, Visible, alpha and VisibleRegion, from its line
    alone: enough to decide whether the window is on the display at all.
    The name is read only to report the window."""
    l = lines[ix["first"][n["id"]]]
    nm = RX_NAME.search(l)
    return {
        "id": n["id"], "name": nm.group(1) if nm else "", "pid": n["pid"],
        "alpha": float(RX_ALPHA.search(l).group(1)) if RX_ALPHA.search(l) else 1.0,
        "visible": int(RX_VIS.search(l).group(1)) if RX_VIS.search(l) else 1,
        "vis": region_rects(l, "VisibleRegion"),
    }


def parse_tree(lines):
    """The whole tree, every node parsed and placed. For the checks: the
    pipeline parses through the pass instead."""
    ix = index_dump(lines)
    nodes, surfaces = {}, []
    for nid in ix["order"]:
        nodes[nid] = n = parse_node(lines, ix, nid)
        if n["kind"] == "SURFACE_NODE":
            surfaces.append(parse_surface(lines, ix, n))
    compose_geometry(nodes, ix["order"])
    return {"nodes": nodes, "order": ix["order"], "surfaces": surfaces}


IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def affine(P, Q):
    """P after Q, for affines (a, b, c, d, e, f): x maps to a*x + c*y + e and
    y to b*x + d*y + f."""
    a, b, c, d, e, f = P
    A, B, C, D, E, F = Q
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f)


def local_transform(n, transforms=True):
    """A node's own transform in its parent's space: the Bounds offset and
    Translate, then Scale about the Pivot. Rotation is not composed."""
    b = n["bounds"] or (0.0, 0.0, 0.0, 0.0)
    if not transforms:
        return (1.0, 0.0, 0.0, 1.0, b[0], b[1])
    px, py = n["pivot"][0] * b[2], n["pivot"][1] * b[3]
    L = (1.0, 0.0, 0.0, 1.0, b[0] + n["tx"] + px, b[1] + n["ty"] + py)
    L = affine(L, (n["sx"], 0.0, 0.0, n["sy"], 0.0, 0.0))
    return affine(L, (1.0, 0.0, 0.0, 1.0, -px, -py))


def place(n, M):
    """A node's rectangle under transform M, or None without Bounds."""
    b = n["bounds"]
    if b is None:
        return None
    a_, b_, c_, d_, e_, f_ = M
    xs = (e_, a_ * b[2] + e_)
    ys = (f_, d_ * b[3] + f_)
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def compose_geometry(nodes, order, transforms=True):
    """Every node's rectangle on the display (INV-GEOMETRY-FROM-TREE): its
    Bounds placed by the transform composed down the parent chain. Scroll
    is already in Bounds. The dump's cached draw rectangles, oldDirty and
    innerAbsDrawRect, are left from earlier frames and are not read. With
    transforms=False only the Bounds offsets are composed, which is how the
    check measures what Translate and Scale change. Returns {id: rect} when
    transforms is False, and sets each node's "abs" otherwise.
    """
    parent = {}
    for nid in order:
        for k in nodes[nid]["kids"]:
            parent.setdefault(k, nid)
    M, out = {}, {}
    for nid in order:
        chain, q = [], nid
        while q is not None and q not in M:
            chain.append(q)
            q = parent.get(q)
        for c in reversed(chain):
            p = parent.get(c)
            M[c] = affine(M[p] if p in M else IDENT, local_transform(nodes[c], transforms))
        r = place(nodes[nid], M[nid])
        if transforms:
            nodes[nid]["abs"] = r
        else:
            out[nid] = r
    return out


def parse_client(lines):
    out = {}
    for l in lines:
        m = RX_CLIENT.search(l)
        if m:
            out[int(m.group(1))] = m.group(4)
    return out


DISPLAY_DEFAULT = {"w": 1316.0, "h": 2832.0, "density": 3.5}


def read_display(path):
    """The display's size and density, or the fallback - which the caller is
    told about, because every fraction is computed against it."""
    d = dict(DISPLAY_DEFAULT)
    if not path or not os.path.exists(path):
        d["fallback"] = True
        return d
    sect = ""
    for l in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r'^\s*\[([A-Z][A-Z ]+)\]\s*$', l)
        if m:
            sect = m.group(1); continue
        m = re.search(r'^\s*Bounds<L,T,W,H>:\s*(-?\d+),\s*(-?\d+),\s*(\d+),\s*(\d+)', l)
        if m and d["w"] == DISPLAY_DEFAULT["w"]:
            d["w"], d["h"] = float(m.group(3)), float(m.group(4))
        if sect == "SCREEN PROPERTY":
            mm = re.match(r'^\s*Density:\s*([\d.]+)', l)
            if mm:
                d["density"] = float(mm.group(1))
    return d
