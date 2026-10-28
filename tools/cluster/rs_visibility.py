#!/usr/bin/env python3
"""What of a window is on the display: ALG-VISIBILITY.

    walk(nodes, surf, dw, dh, order)   per node: state, visible, frac

Visibility is decided by alpha alone (INV-OPACITY). Whether the renderer
paints a node this frame - shouldPaint_, the drawable slots, IsSubTreeSkipped
- is not this project's question and none of it is read here
(INV-OCCLUDER-PAINTS). A node is in one of three states:

    invisible    effective alpha 0: the node and its subtree contribute
                 nothing
    occluding    effective alpha 1 and background colour alpha FF: it hides
                 what is painted beneath it within its rectangle
    translucent  anything else, however transparent: it is visible, and what
                 is beneath it stays visible

A node with no background colour paints nothing of its own and never
occludes. Effective alpha is the product of Alpha over the node and every
ancestor.

The walk runs over a raster of the display, topmost first in paint order,
starting from the window's VisibleRegion - everything outside it is already
hidden by other windows. A node is measured when the walk reaches its
subtree, before any of its descendants is drawn, so only occluders OUTSIDE
the subtree count against it: a node's own descendants are its content and
never hide it. A video's surface child carries an opaque black background
at exactly the player's rectangle; measured against its own children every
player on the capture read as 0 percent visible. Visibility then propagates
up, so a container is visible when anything inside it is.
"""
import numpy as np

STEP = 4          # px; at 4 px the area error is under a percent

INVISIBLE, OCCLUDING, TRANSLUCENT = "invisible", "occluding", "translucent"


def state_of(n, ea):
    if ea <= 0.0:
        return INVISIBLE
    if ea >= 1.0 and n.get("bga") == 0xFF:
        return OCCLUDING
    return TRANSLUCENT


def _cells(r, W, H, step):
    """The raster cells a rectangle holds. Edges are rounded, so two
    rectangles that share an edge share no cell."""
    if not r or r[2] <= 0 or r[3] <= 0:
        return None
    x0 = max(0, int(round(r[0] / step)))
    y0 = max(0, int(round(r[1] / step)))
    x1 = min(W, int(round((r[0] + r[2]) / step)))
    y1 = min(H, int(round((r[1] + r[3]) / step)))
    if x1 <= x0 or y1 <= y0:
        return None
    return y0, y1, x0, x1


def walk(nodes, surf, dw, dh, order, step=STEP, keep=None):
    """Every node of one window: {id: {"state", "visible", "frac"}}.

    frac is the share of the node's on-display rectangle that no occluder
    outside its subtree covers. For a node keep(id) accepts, the result also
    carries "box" and "free": its raster cells and which of them are
    uncovered, so a caller can claim pixels without walking again.
    """
    W, H = int(-(-dw // step)), int(-(-dh // step))
    cover = np.ones((H, W), dtype=bool)
    for r in surf["vis"]:
        c = _cells(r, W, H, step)
        if c:
            cover[c[0]:c[1], c[2]:c[3]] = False

    parent = {}
    for nid, n in nodes.items():
        for k in n["kids"]:
            parent.setdefault(k, nid)

    def eff(nid):
        e = 1.0
        while nid is not None and nid in nodes:
            e *= nodes[nid].get("alpha", 1.0)
            nid = parent.get(nid)
        return e

    mine = [nid for nid, n in nodes.items() if n["surf"] == surf["id"]]
    mine.sort(key=lambda nid: -order.get(nid, 0))          # topmost first
    ea = {nid: eff(nid) for nid in mine}

    # The last-painted node of each subtree. Paint order is depth first, so
    # a subtree is one contiguous run and its first node in this walk is
    # where the subtree is entered.
    last = {nid: order.get(nid, 0) for nid in mine}
    inwin = set(mine)
    for nid in mine:                                  # children before parents
        p = parent.get(nid)
        if p in inwin and last[nid] > last[p]:
            last[p] = last[nid]
    entering = {}
    for nid in mine:
        entering.setdefault(last[nid], []).append(nid)

    out = {}
    for nid in mine:
        for m in entering.get(order.get(nid, 0), ()):
            st = state_of(nodes[m], ea[m])
            res = {"state": st, "visible": False, "frac": 0.0}
            c = _cells(nodes[m]["abs"], W, H, step) if st != INVISIBLE else None
            if c:
                free = ~cover[c[0]:c[1], c[2]:c[3]]
                k = int(free.sum())
                res["frac"] = k / float(free.size)
                res["visible"] = k > 0
                if k and keep is not None and keep(m):
                    res["box"], res["free"] = c, free.copy()
            out[m] = res
        if out[nid]["state"] == OCCLUDING:
            c = _cells(nodes[nid]["abs"], W, H, step)
            if c:
                cover[c[0]:c[1], c[2]:c[3]] = True

    # A container is visible when anything inside it is.
    for nid in mine:                                  # children before parents
        if out[nid]["visible"]:
            p = parent.get(nid)
            if p in out and out[p]["state"] != INVISIBLE:
                out[p]["visible"] = True
    return out
