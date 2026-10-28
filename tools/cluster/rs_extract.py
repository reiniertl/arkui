#!/usr/bin/env python3
"""Read a RenderService dump into a tree, and say who owns what.

Parsing only. This file used to be a second feature extractor - a port of the
PowerShell one, 26 columns wide, most of them later pruned or superseded. The
extractor is gone; rs_pattern.py is the only one, and this is the parsing it
stands on.

    parse_tree(lines)        the RSTree dump -> nodes, surfaces, order, and
                             every node's absolute rectangle (INV-GEOMETRY-
                             FROM-TREE)
    parse_client(lines)      the client dump -> node id -> component tag
    paint_order(tree)        real paint order: ReverseChildren and PositionZ
    set_ownership(tree)      which window-level surface each node belongs to
    window_weight(tree, s)   how many nodes that window admits, and how many
                             pixels its leaves actually paint
    read_display(path)       display geometry, density, rotation, cutout
"""
import re, os, csv, sys, math, hashlib, argparse, glob
from collections import defaultdict

SIG_GRID = 16.0        # rects are quantised to this before comparison
SIG_FLOOR = 20.0       # permille of the display: below this a rect is noise
SIG_CEIL = 600.0       # and above it a rect is a container every app has

RX_NODE = re.compile(r'^(\s*\|?\s*)([A-Z_]+NODE)\[(\d+)\]')
RX_KIDS = re.compile(r'sortedChildren:\s*\d+\(([^)]*)\)')
RX_NAME = re.compile(r'Name \[([^\]]+)\]')
RX_NODENAME = re.compile(r'nodeName:\s*\[([^\]]*)\]')
RX_Z = re.compile(r'zOrder:\s*(\d+)')
RX_ALPHA = re.compile(r'Alpha:\s*([\d.]+)')
RX_VIS = re.compile(r'Visible:\s*(\d)')
RX_STATE = re.compile(r'abilityState:\s*(\w+)')
RX_HEAD = re.compile(r'headroom:\s*([\d.]+)')
RX_HWC = re.compile(r'allHwcAndFilterNode:\s*\{([^}]*)\}')
RX_HWCID = re.compile(r'<(\d+)>')
RX_DV = re.compile(r'DrawableVec:\[([^\]]*)\]')
RX_UNINODE = re.compile(r'^\s*([A-Z_]+NODE)\[(\d+)\]')
RX_CLIENT = re.compile(r'\w+Node\[(\d+)\].*?instanceId\[(\d+)\].*?frameNodeId\[(\d+)\], frameNodeTag\[([^\]]*)\]')
RX_VSYNC = re.compile(r'^\s*--\s*vsyncId:\s*(\d+)')
RX_ANIM = re.compile(r'^\s*Animating Node:\s*\[([^\]]*)\]')

SCROLLERS = {"List", "Grid", "Swiper", "Scroll", "Tabs", "WaterFlow", "Navigation"}
RX_SURFTAG = re.compile(r'XComponent|Web|UIExtension|Embedded|Plugin')


RX_POSZ = re.compile(r'PositionZ\[(-?[\d.]+)\]')
RX_SHOULD = re.compile(r'shouldPaint_\[([01])\]')

RX_LOCAL = re.compile(r'Properties:\s*Bounds\[\s*(-?[\d.]+|-?inf)\s+(-?[\d.]+|-?inf)\s+(-?[\d.]+|-?inf)\s+(-?[\d.]+|-?inf)\s*\]')
# The layout properties a node's position is composed from. Each is printed
# only when it differs from its default.
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


def _local_w(line):
    m = RX_LOCAL.search(line)
    if not m or "inf" in m.group(1):
        return 0.0
    try:
        return float(m.group(3))
    except ValueError:
        return 0.0


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


def overlap(rect, region):
    """How much of rect lies inside the union-free list of region rects."""
    x, y, w, h = rect
    s = 0.0
    for (rx_, ry, rw, rh) in region:
        l = max(x, rx_); t = max(y, ry)
        r = min(x + w, rx_ + rw); b = min(y + h, ry + rh)
        if r > l and b > t:
            s += (r - l) * (b - t)
    return s


def parse_tree(lines):
    """Both passes: the main thread's, which carries the properties, and the
    uni-render thread's, which carries DrawableVec.  The split is the first
    repeated node id - a node cannot appear twice in one tree - so a build
    that renames the marker still splits in the right place."""
    cut, seen = len(lines), set()
    for i, l in enumerate(lines):
        if "RenderServiceUniThread" in l:
            cut = i
            break
        m = RX_NODE.match(l)
        if m:
            if m.group(3) in seen:
                cut = i
                break
            seen.add(m.group(3))

    nodes, order, surfaces, hwc_ids = {}, [], [], set()
    vsync, animating = 0, []
    for l in lines[:cut]:
        mv = RX_VSYNC.match(l)
        if mv:
            vsync = int(mv.group(1)); continue
        ma = RX_ANIM.match(l)
        if ma:
            animating = [x.strip() for x in ma.group(1).split(",") if x.strip()]; continue
        m = RX_NODE.match(l)
        if not m:
            continue
        nid = int(m.group(3))
        k = RX_KIDS.search(l)
        pv = RX_PIVOT.search(l)
        bg = RX_BG.search(l)
        hw = RX_HWC.search(l)
        if hw and hw.group(1).strip():
            hwc_ids.update(int(x) for x in RX_HWCID.findall(hw.group(1)))
        n = {
            "id": nid, "kind": m.group(2), "pid": nid >> 32,
            # round, not floor: PowerShell computes this as [int]($indent/2)
            # and [int] rounds half to even, so an odd indent lands a level
            # higher there. Flooring here put depth_mean out by half a level
            # on every scene.
            "depth": int(round(len(m.group(1).replace("|", "")) / 2)),
            # composed after the pass, once every parent is known
            "abs": None,
            "bounds": _bounds(l),
            "tx": _num(RX_TX, l, 0.0), "ty": _num(RX_TY, l, 0.0),
            "sx": _num(RX_SX, l, 1.0), "sy": _num(RX_SY, l, 1.0),
            "pivot": (float(pv.group(1)), float(pv.group(2))) if pv else (0.5, 0.5),
            "alpha": _num(RX_PALPHA, l, 1.0),
            # the alpha byte of the background colour; None when the node
            # has no background colour and so paints nothing of its own
            "bga": int(bg.group(1), 16) if bg else None,
            "localw": _local_w(l),
            "kids": [int(x) for x in k.group(1).split()] if k else [],
            "blur": bool(re.search(r'BlurRadiusX|BackgroundFilter|HasFrostedGlassEffect|NeedFilter: true', l)),
            "shadow": "ShadowRadius" in l or "ShadowElevation" in l,
            "corner": "CornerRadius" in l,
            "border": "BorderWidth" in l,
            "blend": "blendType" in l or "skblendmode" in l,
            "skipped": "IsSubTreeSkipped: 1" in l,
            # Paint order is NOT dump order. A parent may be marked
            # ReverseChildren, which inverts the order of its sortedChildren,
            # and PositionZ re-sorts siblings on top of that - one dump
            # carries 34 reversed parents and PositionZ values from 1 to
            # 2200. Approximating "painted after" by position in the file put
            # a video's blurred backdrop on TOP of the video, and reported a
            # plainly visible player as entirely covered.
            "reverse": "ReverseChildren: 1" in l,
            "posz": float(RX_POSZ.search(l).group(1)) if RX_POSZ.search(l) else 0.0,
            "should": True,
            "redrawn": "hasDrawContent: 1" in l,
            "headroom": float(RX_HEAD.search(l).group(1)) if RX_HEAD.search(l) else 1.0,
            "tag": "", "surf": 0, "win": 0, "slots": 0, "paints": False,
            "hwc": False, "skipctx": False,
            "anim": None,
        }
        if "RSAnimationManager" in l:
            def g(rx, cast=int, dflt=0):
                mm = re.search(rx, l)
                return cast(mm.group(1)) if mm else dflt
            n["anim"] = {
                "repeat": g(r'RepeatCount:(-?\d+)'),
                "fps": g(r'FrameRateRange_prefered:(\d+)'),
                "scene": g(r'FrameRateRange_componentScene:(\w+)', str, ""),
            }
        nodes[nid] = n
        order.append(nid)
        if n["kind"] == "SURFACE_NODE":
            nm = RX_NAME.search(l)
            name = nm.group(1) if nm else ""
            wid = 0
            mw = re.search(r'(\d+)$', name)
            if mw:
                wid = int(mw.group(1))
            surfaces.append({
                "id": nid, "name": name, "win": wid, "pid": n["pid"],
                "z": int(RX_Z.search(l).group(1)) if RX_Z.search(l) else -1,
                "alpha": float(RX_ALPHA.search(l).group(1)) if RX_ALPHA.search(l) else 1.0,
                "visible": int(RX_VIS.search(l).group(1)) if RX_VIS.search(l) else 1,
                "vis": region_rects(l, "VisibleRegion"),
                "opq": region_rects(l, "OpaqueRegion"),
                "state": RX_STATE.search(l).group(1) if RX_STATE.search(l) else "",
                "depth": n["depth"], "special": bool(re.search(r'SpecialLayer: [1-9]', l)),
                "nested": False,
            })

    for nid in hwc_ids:
        if nid in nodes:
            nodes[nid]["hwc"] = True
    # the uni pass: which drawable slots a node has. 28 is the content pass,
    # 29 the children-only one.
    for l in lines[cut:]:
        m = RX_UNINODE.match(l)
        if not m:
            continue
        dv = RX_DV.search(l)
        if not dv:
            continue
        nid = int(m.group(2))
        if nid not in nodes:
            continue
        slots = [int(x) for x in dv.group(1).split(",") if x.strip()]
        nodes[nid]["slots"] = len(slots)
        nodes[nid]["slotset"] = frozenset(slots)
        # Slot 28 is CONTENT_STYLE on this build. The device's drawable
        # vector is longer than any published OpenHarmony release - it emits
        # slot 50 where master's enum ends at MAX = 46 - so the mapping was
        # recovered by alignment rather than read: slots 24 and 31 occur
        # together 3570 times and alone never once, which identifies them as
        # the SAVE_FRAME / RESTORE_FRAME pair, and anchoring there puts the
        # content region at master's numbering plus four. 28 and 29 then fall
        # on CONTENT_STYLE and CHILDREN, which matches what they do: 3506 of
        # 3805 vectors carry exactly one of the two, and the 51 that carry
        # both are drawing containers.
        nodes[nid]["paints"] = 28 in slots
        sp = RX_SHOULD.search(l)
        if sp:
            # the renderer's own verdict on whether it will draw this node
            nodes[nid]["should"] = sp.group(1) == "1"
    compose_geometry(nodes, order)
    return {"nodes": nodes, "order": order, "surfaces": surfaces,
            "vsync": vsync, "animating": animating}


def compose_geometry(nodes, order, transforms=True):
    """Every node's absolute rectangle, from the tree's own layout.

    INV-GEOMETRY-FROM-TREE. A node's rectangle is its Bounds placed by the
    transform composed down the parent chain: at each level the Bounds
    offset and Translate, then Scale about the Pivot. Scroll is already in
    Bounds. The dump's cached draw rectangles, oldDirty and
    innerAbsDrawRect, are left from earlier frames - on the capture they
    disagree with the layout for 43 percent of nodes, and read for position
    they put a gallery 4000 px below the display - so they are not read.

    The transform is axis-aligned, kept as (sx, sy, ox, oy): x maps to
    sx * x + ox. Rotation is not composed. With transforms=False only the
    Bounds offsets are, which is how the check measures what Translate and
    Scale change. Returns {id: rect} when transforms is False, and sets
    each node's "abs" otherwise.
    """
    parent = {}
    for nid in order:
        for k in nodes[nid]["kids"]:
            parent.setdefault(k, nid)
    M, out = {}, {}

    def place(nid):
        chain = []
        while nid is not None and nid not in M:
            chain.append(nid)
            nid = parent.get(nid)
        for c in reversed(chain):
            p = parent.get(c)
            a, d, e, f = M[p] if p in M else (1.0, 1.0, 0.0, 0.0)
            n = nodes[c]
            b = n["bounds"] or (0.0, 0.0, 0.0, 0.0)
            ox, oy, sx, sy = b[0], b[1], 1.0, 1.0
            if transforms:
                px, py = n["pivot"][0] * b[2], n["pivot"][1] * b[3]
                sx, sy = n["sx"], n["sy"]
                ox += n["tx"] + px - sx * px
                oy += n["ty"] + py - sy * py
            M[c] = (a * sx, d * sy, e + a * ox, f + d * oy)

    for nid in order:
        place(nid)
        b = nodes[nid]["bounds"]
        r = None
        if b is not None:
            a, d, e, f = M[nid]
            x0, x1 = sorted((e, e + a * b[2]))
            y0, y1 = sorted((f, f + d * b[3]))
            r = (x0, y0, x1 - x0, y1 - y0)
        if transforms:
            nodes[nid]["abs"] = r
        else:
            out[nid] = r
    return out


def paint_order(tree):
    """Index every node by when it is actually painted.

    Children are drawn in sortedChildren order, reversed where the parent
    says ReverseChildren, and re-sorted by PositionZ where that is set -
    a higher PositionZ paints later, so it lands on top. A node is painted
    before its children.

    The whole point is the comparison "is B painted over A", which the rest
    of the code was answering with position in the dump file. That is only
    correct when no parent reverses and no sibling carries a PositionZ, and
    both occur.
    """
    nodes = tree["nodes"]
    kids_of = {nid: list(n["kids"]) for nid, n in nodes.items()}
    child = set()
    for ks in kids_of.values():
        child.update(ks)
    roots = [nid for nid in tree["order"] if nid not in child]
    idx, seen, i = {}, set(), 0
    stack = list(reversed(roots))
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in nodes:
            continue
        seen.add(nid)
        idx[nid] = i; i += 1
        n = nodes[nid]
        ks = [k for k in kids_of.get(nid, ()) if k in nodes]
        if n.get("reverse"):
            ks = list(reversed(ks))
        # stable: PositionZ decides, order within a z keeps the list order
        ks.sort(key=lambda k: nodes[k].get("posz", 0.0))
        stack.extend(reversed(ks))
    # anything unreachable keeps a late index rather than none
    for nid in tree["order"]:
        if nid not in idx:
            idx[nid] = i; i += 1
    return idx


def parse_client(lines):
    out = {}
    for l in lines:
        m = RX_CLIENT.search(l)
        if m:
            out[int(m.group(1))] = m.group(4)
    return out


def set_ownership(tree):
    """A contributor is a WINDOW: the outermost surface of a chain owns it and
    nested ones merge in, because VisibleRegion is populated only on the
    window surface and reads Empty on the nested one that holds the content."""
    nodes = tree["nodes"]
    by_id = {s["id"]: s for s in tree["surfaces"]}
    for s in sorted(tree["surfaces"], key=lambda s: s["depth"]):
        if s["nested"]:
            continue
        stack = [(k, False) for k in nodes[s["id"]]["kids"]] if s["id"] in nodes else []
        seen = set()
        while stack:
            nid, skip = stack.pop()
            if nid in seen or nid not in nodes:
                continue
            seen.add(nid)
            n = nodes[nid]
            if n["kind"] == "SURFACE_NODE" and nid in by_id:
                by_id[nid]["nested"] = True
            n["surf"] = s["id"]; n["win"] = s["win"]
            n["skipctx"] = skip or n["skipped"]
            for k in n["kids"]:
                stack.append((k, n["skipctx"]))
        if s["id"] in nodes:
            nodes[s["id"]]["surf"] = s["id"]; nodes[s["id"]]["win"] = s["win"]


def window_weight(tree, surf):
    """Nodes this window admits, and the pixels its leaves paint.

    Enough to pick which window owns the display and no more. A node with no
    absolute rect, or none left after clipping to the window, is not admitted:
    it is laid out but off-screen. Only leaves count towards the paint area -
    a container is a box, not a mark.
    """
    own = region_area(surf["vis"])
    mine = [n for n in tree["nodes"].values()
            if n["surf"] == surf["id"] and n["kind"] != "SURFACE_NODE"]
    if own <= 0 or not mine:
        return {"nodes": len(mine), "paint_px": 0}

    adm = []
    for n in mine:
        if not n["abs"] or n["abs"][2] <= 0 or n["abs"][3] <= 0:
            continue
        if overlap(n["abs"], surf["vis"]) <= 0:
            continue
        adm.append(n)
    if not adm:
        return {"nodes": 0, "paint_px": 0}

    paint = [(max(n["abs"][0], 0), max(n["abs"][1], 0), n["abs"][2], n["abs"][3])
             for n in adm if not n["kids"] and n["abs"][2] > 0 and n["abs"][3] > 0]
    return {"nodes": len(adm), "paint_px": round(region_area(paint))}


UNTAGGED_BELOW = 0.5      # ERR-PATTERN-ROOT-UNTAGGED: under half of a window's
                          # nodes named, no structure can be read from it


def window_tag_coverage(tree, tags):
    """Per visible window, how many of its canvas nodes carry a component tag.

    INV-CLIENT-TAG-COVERAGE. The tag comes from the client dump, which every
    process with a UI answers - an app with its whole component tree, the
    system UI with a fixed subset. This does not close that gap; it measures
    it, so a window whose structure cannot be read is named rather than
    silently matched against nothing.

    Visible is a top-level window with a non-empty visible region, the same
    test the owner selection uses. Ownership must already be set.
    """
    out = []
    for s in tree["surfaces"]:
        if s["nested"] or s["visible"] != 1 or s["alpha"] <= 0 \
                or region_area(s["vis"]) <= 0:
            continue
        ids = [nid for nid, n in tree["nodes"].items()
               if n.get("surf") == s["id"] and n["kind"] == "CANVAS_NODE"]
        tagged = sum(1 for nid in ids if nid in tags)
        # The process whose nodes fill the window. The window surface itself
        # is hosted by the system UI for every app, so its own pid would name
        # the system UI for every window there is.
        pids = {}
        for nid in ids:
            p = tree["nodes"][nid].get("pid")
            pids[p] = pids.get(p, 0) + 1
        owner_pid = max(pids, key=pids.get) if pids else s["pid"]
        out.append({"window": s["name"], "pid": owner_pid, "nodes": len(ids),
                    "tagged": tagged,
                    "coverage": round(tagged / len(ids), 3) if ids else None})
    return out


DISPLAY_DEFAULT = {"w": 1316.0, "h": 2832.0, "density": 3.5, "rotation": 0,
                   "refresh": 0, "refresh_max": 0, "power": -1,
                   "internal": True, "cutout": None, "avail": None}


def read_display(path):
    """Geometry, or the fallback - which the caller is told about.

    The fallback is a real device's geometry, so a run continues on it and
    looks healthy. Every fraction in every row is computed against that
    denominator, so on a device of another size every coverage, every height
    and the whole composition is wrong by the ratio, silently.
    """
    d = dict(DISPLAY_DEFAULT)
    if not path or not os.path.exists(path):
        d["fallback"] = True
        return d
    sect = ""
    for l in open(path, encoding="utf-8", errors="replace"):
        if re.match(r'^\s*\[\d\d-\d\d \d\d:\d\d:', l):
            continue
        m = re.match(r'^\s*\[([A-Z][A-Z ]+)\]\s*$', l)
        if m:
            sect = m.group(1); continue
        if re.search(r'-+\s*Client Screen Infos\s*-+', l):
            sect = "CLIENT"; continue
        m = re.search(r'^\s*Bounds<L,T,W,H>:\s*(-?\d+),\s*(-?\d+),\s*(\d+),\s*(\d+)', l)
        if m and d["w"] == 1316.0:
            d["w"], d["h"] = float(m.group(3)), float(m.group(4))
        m = re.search(r'BoundingRects<X,Y,W,H>:\s*\[\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\s*\]', l)
        if m:
            d["cutout"] = tuple(int(v) for v in m.groups())
        if sect == "SCREEN PROPERTY":
            for key, fld, cast in (("Density", "density", float),
                                   ("Rotation", "rotation", int),
                                   ("RefreshRate", "refresh", int)):
                mm = re.match(r'^\s*' + key + r':\s*([\d.]+)', l)
                if mm:
                    d[fld] = cast(float(mm.group(1)))
        mm = re.search(r'^\s*ScreenPowerState:\s*(\d+)', l)
        if mm:
            d["power"] = int(mm.group(1))
        # the mode's rate, stated before any section disagrees about it
        mm = re.search(r'activeModes<[^>]*>:\s*\d+,\s*\d+,\s*\d+,\s*(\d+)', l)
        if mm:
            d["refresh_max"] = int(mm.group(1))
        if sect == "CLIENT":
            mm = re.match(r'^\s*RefreshRate:\s*(\d+)', l)
            if mm and d["refresh_max"] <= 0:
                d["refresh_max"] = int(mm.group(1))
    if d["refresh"] <= 0:
        d["refresh"] = d["refresh_max"]
    return d


