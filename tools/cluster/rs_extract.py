#!/usr/bin/env python3
"""Build scenes from RenderService dumps - the same construction as
rs_scene.ps1, in a language suited to doing it ten thousand times.

PowerShell takes about 860 ms a sample: 394 to parse 740 KB of text and 466
to walk 500 nodes, because every property access on a dynamic object costs
roughly a microsecond.  The same work here is about 5 ms, so an hour-long
capture rebuilds in under a minute instead of three hours.

This is a PORT, not a redesign.  Where the two disagree the PowerShell is
the specification, because the captures already measured were measured by
it; --check compares the two outputs column by column.

What it is not: a second extractor to maintain forever.  It exists because
the offline rebuild is bulk work and the capture loop is not.
"""
import re, os, csv, sys, math, hashlib, argparse, glob
from collections import defaultdict

SIG_GRID = 16.0        # rects are quantised to this before comparison
SIG_FLOOR = 20.0       # permille of the display: below this a rect is noise
SIG_CEIL = 600.0       # and above it a rect is a container every app has

RX_NODE = re.compile(r'^(\s*\|?\s*)([A-Z_]+NODE)\[(\d+)\]')
RX_ABS = re.compile(r'innerAbsDrawRect: \[\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\s*\]')
RX_OLD = re.compile(r'oldDirty: \[\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\s*\]')
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

RX_LOCAL = re.compile(r'Properties:\s*Bounds\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\]')


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
        a = RX_ABS.search(l)
        od = RX_OLD.search(l)
        k = RX_KIDS.search(l)
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
            "abs": tuple(int(v) for v in a.groups()) if a else None,
            "old": tuple(int(v) for v in od.groups()) if od else None,
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
    return {"nodes": nodes, "order": order, "surfaces": surfaces,
            "vsync": vsync, "animating": animating}


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


def quick_sig(lines, disp_area):
    """The scene's signature, straight off the text: every absolute rect
    quantised, plus the contributor set.  Rects over 60% of the display are
    left out - they are roots and bars that every app has, and a set built
    from size alone merged fourteen apps into seven scenes."""
    allk, bigk = set(), set()
    for l in lines:
        if "NODE[" not in l:
            continue
        m = RX_ABS.search(l)
        if m:
            x, y, w, h = (float(v) for v in m.groups())
            if w > 0 and h > 0:
                k = (round(x / SIG_GRID), round(y / SIG_GRID),
                     round((x + w) / SIG_GRID), round((y + h) / SIG_GRID))
                allk.add(k)
                if disp_area > 1:
                    share = 1000.0 * w * h / disp_area
                    if SIG_FLOOR <= share <= SIG_CEIL:
                        bigk.add(k)
        if "SURFACE_NODE[" in l:
            nm = RX_NAME.search(l)
            if nm:
                tok = "W:" + nm.group(1)
                allk.add(tok); bigk.add(tok)
    src = bigk if bigk else allk
    sb = ";".join(str(k) for k in sorted(map(str, src))) + f";N:{len(allk)//4}"
    hexd = hashlib.md5(sb.encode()).hexdigest()[:16].upper()
    return {"hash": f"{len(src)}/{len(allk)}-{hexd}", "all": allk, "big": bigk}



# Band centres for the repeat-shape distribution. Geometric, because item
# heights are: a 40px row and an 80px row differ as much as a 400px card and
# an 800px one. Heights are in display heights, aspects in width/height.
ITEM_H_BANDS = (0.03, 0.08, 0.18, 0.40, 0.85)
ITEM_A_BANDS = (0.35, 1.0, 3.0)


def soft_bins(value, centres):
    """Spread one measurement over the two band centres nearest to it.

    A hard bin has exactly the defect the argmax had: a card a pixel either
    side of an edge lands in a different band and the vector jumps. Linear
    interpolation in log space between the two neighbouring centres makes the
    encoding continuous in the measurement - a card that drifts across an
    edge moves its mass gradually instead of teleporting - while still
    leaving the bands themselves far enough apart to cut between.
    """
    w = [0.0] * len(centres)
    if not (value > 0.0):
        w[0] = 1.0
    elif value <= centres[0]:
        w[0] = 1.0
    elif value >= centres[-1]:
        w[-1] = 1.0
    else:
        for i in range(len(centres) - 1):
            a, b = centres[i], centres[i + 1]
            if a <= value <= b:
                t = (math.log(value) - math.log(a)) / (math.log(b) - math.log(a))
                w[i], w[i + 1] = 1.0 - t, t
                break
    return w


def measure(tree, surf, density, disp_area, dw=1316.0, dh=2832.0):
    nodes = tree["nodes"]
    own = region_area(surf["vis"])
    sh = {"win": surf["win"], "name": surf["name"], "pid": surf["pid"],
          "z": surf["z"], "alpha": round(surf["alpha"], 3), "vis_px": round(own)}
    mine = [n for n in nodes.values()
            if n["surf"] == surf["id"] and n["kind"] != "SURFACE_NODE"]
    if own <= 0 or not mine:
        sh.update(nodes=len(mine), paint_px=0, drop_skip=0, drop_odd=0, dropped=0)
        return sh

    adm = []
    for n in mine:
        if not n["abs"] or n["abs"][2] <= 0 or n["abs"][3] <= 0:
            continue
        ov = overlap(n["abs"], surf["vis"])
        if ov <= 0:
            continue
        n["visarea"] = ov
        adm.append(n)
    sh["nodes"] = len(adm)
    sh["dropped"] = len(mine) - len(adm)
    sh["drop_skip"] = sum(1 for n in mine
                          if (not n["abs"] or n["abs"][2] <= 0) and n["skipctx"])
    # a node with no absolute rect but a real local one: something we failed
    # to read, as opposed to something the renderer declined to draw
    sh["drop_odd"] = sum(1 for n in mine
                         if not n["abs"] and not n["skipctx"] and n["localw"] > 0)
    if not adm:
        sh["paint_px"] = 0
        return sh

    vp2 = (own / (density * density)) / 1000.0 or 1.0
    sh["node_den"] = round(len(adm) / vp2, 2)
    depths = [n["depth"] - surf["depth"] for n in adm]
    sh["depth_max"] = max(depths)
    sh["depth_mean"] = round(sum(depths) / len(depths), 2)
    leaves = [n for n in adm if not n["kids"]]
    sh["leaf_n"] = len(leaves)
    sh["leaf_frac"] = round(len(leaves) / len(adm), 3)
    kidc = [len(n["kids"]) for n in adm if n["kids"]]
    sh["fanout"] = round(sum(kidc) / len(kidc), 2) if kidc else 0.0

    # repeats: scored by the area a group covers, not how many members it has
    grp = defaultdict(list)
    for n in leaves:
        wv, hv = n["abs"][2] / density, n["abs"][3] / density
        if wv < 12.0 or hv < 12.0:
            continue
        # Judged on the node OWN rect, not on how much of it is visible.
        #
        # The visible area moves as the display scrolls and the rule is a
        # hard line, so a full-height card slipped under it when scrolled
        # and sat above it when settled. Two samples of one feed, one pixel
        # apart in card height - 1316x2563 against 1316x2562 - took
        # opposite sides: the settled card excluded at 90% visible, the
        # scrolled one kept at 56%. Whichever way it fell decided which
        # group won, so item_h alternated between 0.061 and 0.905 and one
        # display flipped between two clusters sixteen times without
        # changing. A card is the same card either way, and its own rect
        # does not move.
        if (n["abs"][2] * n["abs"][3]) > 0.6 * disp_area:
            continue
        grp[(round(wv), round(hv))].append(n)
    # Union, not sum: members overlap, and summing their areas counted the
    # overlaps again and again - repeat_frac reported 2.551 of a
    # contributor, which is not a fraction of anything.
    score = {k: region_area([(x["abs"][0], x["abs"][1], x["abs"][2], x["abs"][3])
                             for x in v])
             for k, v in grp.items() if len(v) > 1}
    sh["repeat_n"], sh["repeat_size"], sh["repeat_frac"] = 0, "", 0.0
    if score:
        top_score = max(score.values())
        near = [k for k, v in score.items() if v >= 0.8 * top_score]
        best = max(near, key=lambda k: (k[1], k[0]))
        sh["repeat_n"] = len(grp[best])
        sh["repeat_size"] = f"{best[0]}x{best[1]}"
        sh["repeat_frac"] = round(min(1.0, score[best] / own), 3)

    # ---- arrangement -----------------------------------------------------
    #
    # How the display is laid out, as opposed to how much is on it. The vector
    # had densities, fractions and counts and nothing about arrangement, so
    # displays built the same way and carrying different amounts of text were
    # separating while displays laid out differently collapsed onto one point.
    #
    # These were uitest features once, which is a historical accident: when
    # they were written uitest was the only source of per-component rects.
    # What they actually need is geometry, and innerAbsDrawRect carries it.
    # Empty, not zero. item_h = 0.0 did not mean "items of no height", it
    # meant "no repeating group here" - and on a continuous scale the vector
    # read that as the smallest possible items, the opposite end from a
    # full-height card. item_aspect = 1.0 was worse: the no-group default was
    # indistinguishable from a genuinely square item, and 1,266 samples
    # carried it. A sixth of the dataset was feeding the clustering three
    # fabricated coordinates and sitting as a dense lump in the middle of the
    # space. has_repeat says whether there was anything to measure.
    sh["cols"], sh["rows_n"] = "", ""
    sh["item_h"], sh["item_aspect"] = "", ""
    sh["item_cols"], sh["item_rows"] = "", ""
    for i in range(len(ITEM_H_BANDS)):
        sh["item_h%d" % i] = ""
    for i in range(len(ITEM_A_BANDS)):
        sh["item_ar%d" % i] = ""
    sh["has_repeat"] = 0
    if score:
        sh["has_repeat"] = 1
        # The repeat group, chosen so the choice does not flip.
        #
        # Taking whichever group covered the most area made these features
        # discontinuous: when two groups cover similar area - a feed's cards
        # against its action rail - the argmax alternates between samples and
        # the reported item height jumps to a different group's geometry. One
        # display flipped between two clusters sixteen times across 55
        # samples, and item_h was among the three features responsible in ten
        # of the eleven pictures that appeared in more than one cluster.
        #
        # Averaging over all groups fixed the flipping and destroyed the
        # result: 2 clusters, 565 of 695 points in one of them. The argmax
        # was producing discrete levels - 41 distinct item heights across the
        # session - and those gaps are the density valleys the clustering
        # separates on. Averaging turned 41 levels into 250 and left one
        # continuum with nothing to cut.
        #
        # So: keep the winner, but let a near-rival win only on a stable
        # rule. Among groups within 20% of the best coverage, take the one
        # with the tallest item. Small changes in coverage no longer change
        # the answer, because the tie-break does not depend on them.
        top_score = max(score.values())
        near = [k for k, v in score.items() if v >= 0.8 * top_score]
        best = max(near, key=lambda k: (k[1], k[0]))
        iw, ih = best
        mem = grp[best]
        # positions tolerated to 40px: a grid's columns are not pixel-exact
        sh["cols"] = len({round(n["abs"][0] / 40) for n in mem})
        sh["rows_n"] = len({round(n["abs"][1] / 40) for n in mem})
        sh["item_h"] = round(ih * density / dh, 3)
        sh["item_aspect"] = round(iw / max(1, ih), 3)

    # ---- the same shape, as a distribution instead of a winner -------
    #
    # Everything above picks ONE group and reports its geometry. That is
    # a winner-take-all read of a display that usually has several
    # repeating things on it at once - a feed has cards and an action
    # rail, a gallery has tiles and a tab row - and the loser's geometry
    # is discarded entirely. Worse, the pick is unstable: when two groups
    # cover similar area the winner alternates between samples and all
    # four numbers jump together. Ablation and SHAP agreed that cols,
    # item_h and item_aspect carry the partition, and a correlation
    # audit found the repeat block holding a third of the features but
    # 47% of the squared pairwise distance - so when the pick flips,
    # half the geometry of the scene flips with it. One display moved
    # between clusters sixteen times across 55 samples without changing.
    #
    # The fix is to stop choosing. Every group contributes, weighted by
    # the area it covers, to a distribution over item shape: five bands
    # of item height and three of aspect. A feed reports mass in the
    # card band and a little in the rail band, which is what it is. A
    # near-tie between two groups now shifts mass smoothly from one band
    # to the other rather than swapping the entire descriptor.
    #
    # The earlier attempt at this - a plain mean over groups - failed
    # because it turned 41 discrete item heights into 250 and left one
    # continuum with no density valley to cut. The bands are what keeps
    # that from happening: they are coarse on purpose, so displays still
    # pile onto a handful of shapes, and the soft edges only decide what
    # happens between them.
    # The two size filters above are cliffs of the same kind as the
    # argmax. A card that grows past 60% of the display, or a row that
    # shrinks past 12vp, does not get smaller in the descriptor - it
    # leaves the set, taking its whole band with it. That turned out to
    # be what was left of the instability once the winner-take-all pick
    # was gone: the jumps that remained were groups entering and leaving,
    # not groups trading places. So the distribution is built over every
    # group, with the filters applied as a ramp on the area each one
    # contributes rather than as a gate on whether it exists.
    grp_all = defaultdict(list)
    for n in leaves:
        wv, hv = n["abs"][2] / density, n["abs"][3] / density
        grp_all[(round(wv), round(hv))].append(n)
    hmass = [0.0] * len(ITEM_H_BANDS)
    amass = [0.0] * len(ITEM_A_BANDS)
    colw = roww = tot = 0.0
    for k, mem_k in grp_all.items():
        if len(mem_k) < 2:
            continue
        gw, gh = k
        # 0 below 8vp, 1 above 16vp: too small to be an item, fading in
        small = min(1.0, max(0.0, (min(gw, gh) - 8.0) / 8.0))
        # 1 below 45% of the display, 0 above 75%: a panel, fading out
        frac = (gw * density) * (gh * density) / max(1.0, disp_area)
        large = min(1.0, max(0.0, (0.75 - frac) / 0.30))
        ramp = small * large
        if ramp <= 0.0:
            continue
        a = ramp * region_area([(x["abs"][0], x["abs"][1],
                                 x["abs"][2], x["abs"][3]) for x in mem_k])
        if a <= 0.0:
            continue
        for i, f in enumerate(soft_bins(gh * density / dh, ITEM_H_BANDS)):
            hmass[i] += a * f
        for i, f in enumerate(soft_bins(gw / max(1.0, gh), ITEM_A_BANDS)):
            amass[i] += a * f
        colw += a * len({round(n["abs"][0] / 40) for n in mem_k})
        roww += a * len({round(n["abs"][1] / 40) for n in mem_k})
        tot += a
    if tot > 0:
        # shares, so the bands say what shape the repeats are and
        # repeat_frac alone says how much of the display they cover.
        # Keeping magnitude out of them stops the block from carrying
        # the same fact twice, which is what the prune was removing.
        for i, v in enumerate(hmass):
            sh["item_h%d" % i] = round(v / tot, 4)
        for i, v in enumerate(amass):
            sh["item_ar%d" % i] = round(v / tot, 4)
        sh["item_cols"] = round(colw / tot, 2)
        sh["item_rows"] = round(roww / tot, 2)

    # bars: full width, thin, against an edge. Chrome, and the clearest
    # structural difference between a player and a page.
    bt = bb = 0.0
    for n in leaves:
        x, y, w, h = n["abs"]
        if w >= 0.9 * dw and h <= 0.12 * dh:
            if y <= 0.1 * dh:
                bt = max(bt, h / dh)
            elif y + h >= 0.9 * dh:
                bb = max(bb, h / dh)
    sh["bar_top"], sh["bar_bot"] = round(bt, 3), round(bb, 3)

    # stacked divisions of the content band: distinct horizontal bands among
    # the wide, tall leaves. A split display and a single pane differ here.
    bands = {(round(n["abs"][1] / 60), round((n["abs"][1] + n["abs"][3]) / 60))
             for n in leaves if n["abs"][2] >= 0.6 * dw and n["abs"][3] >= 0.05 * dh}
    sh["split_n"] = len(bands)

    acc = defaultdict(float)
    cnt = defaultdict(int)
    anims = []
    dirty = []
    for n in adm:
        va = n["visarea"]
        t = n["tag"]
        if t:
            cnt["tagged"] += 1
            if t == "Text": acc["text"] += va
            elif t == "Image": acc["image"] += va
            elif t == "SymbolGlyph": acc["sym"] += va
            if t in SCROLLERS: cnt["scroller"] += 1
            if RX_SURFTAG.search(t): cnt["surface_tag"] += 1
        for f in ("blur", "shadow", "corner", "blend"):
            if n[f]: acc[f] += va
        if n["paints"]: cnt["paints"] += 1; acc["paint"] += va
        if n["redrawn"]: cnt["redrawn"] += 1
        if n["hwc"]: cnt["hwc"] += 1; acc["hwc"] += va
        if n["headroom"] > 1.0: cnt["hdr"] += 1
        if n["skipped"]: cnt["skipped"] += 1
        if n["slots"]: acc["dv"] += n["slots"]; cnt["dv"] += 1
        if n["old"] and n["old"][2] > 0: dirty.append(n["old"])
        if n["anim"]: anims.append(n)

    f = lambda v: round(min(1.0, v / own), 3)
    for k in ("text", "image", "sym", "blur", "shadow", "corner", "blend"):
        sh[k + "_frac"] = f(acc[k])
    sh["paint_frac"] = f(acc["paint"])
    sh["hwc_frac"] = f(acc["hwc"])
    for k in ("tagged", "scroller", "surface_tag", "paints", "redrawn", "hwc",
              "hdr", "skipped"):
        sh[k] = cnt[k]
    sh["draw_slots"] = round(acc["dv"] / cnt["dv"], 2) if cnt["dv"] else 0.0
    sh["dirty_frac"] = round(min(1.0, region_area(dirty) / own), 3) if dirty else 0.0
    sh["anim_n"] = len(anims)
    sh["anim_forever"] = sum(1 for n in anims if n["anim"]["repeat"] < 0)
    sh["fps_pref"] = max([n["anim"]["fps"] for n in anims], default=0)
    sh["fps_scene"] = ""
    for n in anims:
        s = n["anim"]["scene"]
        if s and s != "UNKNOWN_SCENE":
            sh["fps_scene"] = s; break
        if s and not sh["fps_scene"]:
            sh["fps_scene"] = s

    # weight: what this contributor actually paints. Leaves only - a
    # container is a box, not a mark.
    paint = [(max(n["abs"][0], 0), max(n["abs"][1], 0), n["abs"][2], n["abs"][3])
             for n in leaves if n["abs"][2] > 0 and n["abs"][3] > 0]
    sh["paint_px"] = round(region_area(paint))
    return sh


def build_scene(tree, cmap, disp):
    for n in tree["nodes"].values():
        if n["id"] in cmap:
            n["tag"] = cmap[n["id"]]
    set_ownership(tree)

    dw, dh = disp["w"], disp["h"]
    usable = dw * dh
    if disp.get("avail"):
        a = disp["avail"]
        if a[2] > 0:
            usable = a[2] * a[3]
    if disp.get("cutout"):
        usable -= disp["cutout"][2] * disp["cutout"][3]
    if usable <= 0:
        usable = dw * dh

    live = [s for s in tree["surfaces"]
            if not s["nested"] and s["visible"] == 1 and s["alpha"] > 0
            and region_area(s["vis"]) > 0]
    contrib = []
    for s in sorted(live, key=lambda s: s["z"]):
        sh = measure(tree, s, disp["density"], dw * dh, dw, dh)
        w = min(1.0, sh.get("paint_px", 0) / usable)
        sh["weight"] = round(w, 4)
        if sh.get("nodes", 0) > 0 and w > 0:
            contrib.append(sh)

    live_ids = {s["id"] for s in live}
    opq = []
    for s in tree["surfaces"]:
        owner = tree["nodes"].get(s["id"], {}).get("surf", 0)
        if owner in live_ids or s["id"] in live_ids:
            opq.extend(s["opq"])
    covered = region_area(opq)

    sc = {"vsync": tree["vsync"], "disp_w": dw, "disp_h": dh,
          "density": disp["density"], "rotation": disp["rotation"],
          "refresh": disp["refresh"], "refresh_max": disp["refresh_max"],
          "power": disp["power"], "internal": disp["internal"],
          "contributors": len(contrib), "z_span": 0,
          "cover_opaque": round(covered / (dw * dh), 3),
          "uncovered": round(max(0.0, 1.0 - covered / (dw * dh)), 3),
          "protected": sum(1 for s in live if s["special"]),
          "anim_live": len(tree["animating"])}
    zs = [s["z"] for s in live if s["z"] >= 0]
    if zs:
        sc["z_span"] = max(zs) - min(zs)

    wsum = sum(c["weight"] for c in contrib) or 1.0
    for fld in ("node_den", "depth_mean", "leaf_frac", "fanout", "text_frac", "image_frac", "sym_frac", "blur_frac", "shadow_frac",
                "corner_frac", "blend_frac", "dirty_frac", "draw_slots",
                "paint_frac", "hwc_frac"):
        sc[fld] = round(sum(float(c.get(fld, 0) or 0) * c["weight"] for c in contrib) / wsum, 3)
    for fld in ("nodes", "dropped", "drop_skip", "drop_odd", "paints", "redrawn",
                "scroller", "surface_tag", "hwc", "hdr", "anim_n",
                "anim_forever", "skipped"):
        sc[fld] = sum(int(c.get(fld, 0)) for c in contrib)
    seen_n = sc["nodes"] + sc["dropped"]
    sc["admit_frac"] = round(sc["nodes"] / seen_n, 3) if seen_n else 1.0
    sc["fps_pref"] = max([int(c.get("fps_pref", 0)) for c in contrib], default=0)
    sc["fps_scene"] = ""
    for c in contrib:
        s = c.get("fps_scene", "")
        if s and s != "UNKNOWN_SCENE":
            sc["fps_scene"] = s; break
        if s and not sc["fps_scene"]:
            sc["fps_scene"] = s

    real = [c for c in contrib if c["weight"] >= 0.2]
    top = min(real, key=lambda c: c["z"]) if real else (
        max(contrib, key=lambda c: c["weight"]) if contrib else None)
    # Arrangement comes from the contributor that owns the scene, not summed
    # across them and not averaged with them: columns counted over a grid, a
    # status bar and a nav bar add up to six columns of nothing. A status bar
    # has a layout, but it is not the display's layout.
    # All of these describe ONE display's layout, so all come from the
    # contributor that owns the scene. repeat_n and repeat_frac used to be
    # summed and weighted across contributors instead, which is how 740
    # samples ended up reporting four repeats with no columns: the status bar
    # had a group and the app did not.
    for fld in (("cols", "rows_n", "split_n", "item_h", "item_aspect",
                 "bar_top", "bar_bot", "repeat_n", "repeat_frac", "has_repeat",
                 "item_cols", "item_rows")
                + tuple("item_h%d" % i for i in range(len(ITEM_H_BANDS)))
                + tuple("item_ar%d" % i for i in range(len(ITEM_A_BANDS)))):
        sc[fld] = (top.get(fld, "") if top else "")

    if top:
        sc["top_win"] = top["win"]; sc["top_name"] = top["name"]
        sc["top_weight"] = top["weight"]; sc["top_z"] = top["z"]
        sc["top_pid"] = top["pid"]
        sc["transition"] = 1 if re.search(r'(?i)blur|transition|splash|animation|freeze',
                                          top["name"]) else 0
    return sc


ROW_FIELDS = ["contributors", "z_span", "cover_opaque", "uncovered", "protected",
              "node_den", "depth_mean", "leaf_frac", "fanout", "repeat_frac",
              "repeat_n", "text_frac", "image_frac", "sym_frac", "blur_frac",
              "shadow_frac", "corner_frac", "blend_frac", "dirty_frac",
              "draw_slots", "nodes", "scroller", "surface_tag", "hwc", "hdr",
              "anim_n", "anim_forever", "fps_pref", "skipped", "rotation",
              "refresh", "refresh_max", "power", "top_weight", "dropped",
              "admit_frac", "drop_skip", "drop_odd", "paints", "redrawn",
              "paint_frac", "hwc_frac", "top_win", "top_z", "top_pid",
              "transition", "cols", "rows_n", "item_h", "item_aspect",
              "bar_top", "bar_bot", "split_n", "has_repeat",
              "item_cols", "item_rows"] \
             + ["item_h%d" % i for i in range(len(ITEM_H_BANDS))] \
             + ["item_ar%d" % i for i in range(len(ITEM_A_BANDS))]


def row_of(scene):
    r = {("rs_" + k): scene.get(k, "") for k in ROW_FIELDS}
    r["rs_top_name"] = scene.get("top_name", "")
    r["rs_fps_scene"] = scene.get("fps_scene", "")
    r["rs_vsync"] = scene.get("vsync", 0)
    for k in ("shape", "scene", "described", "truth", "truth_src", "name",
              "window", "pid", "dev_mono"):
        r[k] = ""
    return r


def read_display(path):
    d = {"w": 1316.0, "h": 2832.0, "density": 3.5, "rotation": 0, "refresh": 0,
         "refresh_max": 0, "power": -1, "internal": True, "cutout": None,
         "avail": None}
    if not path or not os.path.exists(path):
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


def session_rows(sdir, per_sample):
    """One row per scene, or one per sample when the raw stream is wanted."""
    # A fast capture does not write display.txt, so fall back to one left at
    # the capture root. Without it refresh, power and rotation read as
    # defaults and differ from a PowerShell rebuild, which asks the device.
    dpath = os.path.join(sdir, "display.txt")
    if not os.path.exists(dpath):
        alt = os.path.join(os.path.dirname(sdir.rstrip("/\\")), "display.txt")
        if os.path.exists(alt):
            dpath = alt
    disp = read_display(dpath)
    name = os.path.basename(sdir.rstrip("/\\"))
    samp = os.path.join(sdir, "samples.csv")
    stem_of, dwell_of = {}, defaultdict(int)
    if os.path.exists(samp):
        with open(samp, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r.get("stem"):
                    stem_of[r["sig"]] = r["stem"]
                dwell_of[r.get("sig", "")] += 1
    cmap_of, shot_of = {}, {}
    for sg, st in stem_of.items():
        cf = os.path.join(sdir, st + ".client.txt")
        if os.path.exists(cf):
            cmap_of[sg] = parse_client(open(cf, encoding="utf-8", errors="replace").read().split("\n"))
        for ext in (".jpeg", ".png", ".jpg"):
            if os.path.exists(os.path.join(sdir, st + ext)):
                shot_of[sg] = f"{name}/{st}{ext}"
                break

    out = []
    if per_sample:
        raw = os.path.join(sdir, "raw")
        if not os.path.isdir(raw) or not os.path.exists(samp):
            return out
        with open(samp, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        prev = None
        for r in rows:
            if not r.get("raw"):
                continue
            f = os.path.join(raw, r["raw"] + ".rstree.txt")
            if not os.path.exists(f):
                continue
            lines = open(f, encoding="utf-8", errors="replace").read().split("\n")
            if len(lines) < 5:
                continue
            tree = parse_tree(lines)
            sc = build_scene(tree, cmap_of.get(r["sig"], {}), disp)

            # The delta against the previous read.
            #
            # A scene is THIS read; the delta modifies it and never defines
            # it, and the old pipeline used it to hold changing scenes out of
            # the clustering - 718 captures became 584 before deduplication.
            # The fast capture path dropped the stillness guard and kept
            # every sample, so transitions, scroll states and mid-animation
            # frames went into the clustering as though they were displays
            # somebody was looking at. The clusterer still asks for this
            # column and defaults to 800 permille; it was simply never
            # written.
            sig = quick_sig(lines, disp["w"] * disp["h"])
            if prev is None:
                hold = 1000
            else:
                inter = len(sig["all"] & prev)
                union = len(sig["all"]) + len(prev) - inter
                hold = int(1000.0 * inter / union) if union else 0
            prev = sig["all"]

            row = row_of(sc)
            row.update(session=name, stem=r["raw"], sig=f'{name}/{r["raw"]}',
                       scene_sig=r["sig"], dwell=1, win_sig="",
                       jitter=1000 - hold, still=hold,
                       # The picture this sample actually took, by timestamp.
                       #
                       # This used to look the picture up by signature, so
                       # every sample of a scene got whichever frame that
                       # signature was eventually photographed at - binding a
                       # dump from 12:34 to a picture from 12:42, two
                       # separate visits 1,523 samples apart, because the
                       # geometry matched. The capture had recorded it
                       # correctly against the sample that took it; the
                       # lookup threw that away.
                       # samples.csv stores the bare filename; the picture
                       # lives in the session directory and the viewer sits
                       # beside it, so the session has to be on the path.
                       shot=(f'{name}/{r["shot"]}' if r.get("shot") else ""),
                       shot_gap=0 if r.get("shot") else "")
            out.append(row)

        # A sample without its own picture borrows the nearest one in TIME,
        # within three samples - about a second at this rate - and records
        # how far it reached. Anything further away is left without a
        # picture, because a frame from another moment is not evidence about
        # this one however well its geometry matches.
        shot_idx = [i for i, r in enumerate(out) if r["shot"]]
        if shot_idx:
            import bisect
            for i, r in enumerate(out):
                if r["shot"]:
                    continue
                j = bisect.bisect_left(shot_idx, i)
                best, bd = None, 10 ** 9
                for k in (j - 1, j):
                    if 0 <= k < len(shot_idx):
                        d = abs(shot_idx[k] - i)
                        if d < bd:
                            bd, best = d, shot_idx[k]
                if best is not None and bd <= 3:
                    r["shot"] = out[best]["shot"]
                    r["shot_gap"] = bd
        return out

    for f in sorted(glob.glob(os.path.join(sdir, "*.rstree.txt"))):
        stem = os.path.basename(f).split(".")[0]
        lines = open(f, encoding="utf-8", errors="replace").read().split("\n")
        if len(lines) < 5:
            continue
        tree = parse_tree(lines)
        cf = os.path.join(sdir, stem + ".client.txt")
        cmap = parse_client(open(cf, encoding="utf-8", errors="replace").read().split("\n")) \
            if os.path.exists(cf) else {}
        sc = build_scene(tree, cmap, disp)
        sig = quick_sig(lines, disp["w"] * disp["h"])["hash"]
        row = row_of(sc)
        row.update(session=name, stem=stem, sig=sig, dwell=dwell_of.get(sig, 1),
                   win_sig="", jitter="", still="",
                   shot=shot_of.get(sig, f"{name}/{stem}.jpeg"
                                    if os.path.exists(os.path.join(sdir, stem + ".jpeg")) else ""))
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="directory holding capture sessions")
    ap.add_argument("--samples", action="store_true",
                    help="one row per sample from raw/, with no deduplication")
    ap.add_argument("--out", default="", help="output csv")
    a = ap.parse_args()

    sessions = sorted(d for d in glob.glob(os.path.join(a.root, "*"))
                      if os.path.isdir(d) and os.path.basename(d) != "thumbs")
    out = a.out or os.path.join(a.root,
                                "all_samples.csv" if a.samples else "all_scenes.csv")
    rows, t0 = [], __import__("time").perf_counter()
    for s in sessions:
        got = session_rows(s, a.samples)
        if got:
            rows.extend(got)
            print(f"  {os.path.basename(s):<22} {len(got):>6} rows")
    if not rows:
        print("nothing to rebuild"); return 1
    cols = list(rows[0].keys())
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    el = __import__("time").perf_counter() - t0
    print(f"\n  {len(rows)} rows in {el:.1f}s  ({1000*el/max(1,len(rows)):.1f} ms each)")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
