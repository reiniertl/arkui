#!/usr/bin/env python3
"""Find the repeating LAYOUT pattern of a scene, not its repeating rectangles.

The feature this replaces grouped leaf nodes by size. A leaf in RSTree is
whatever RenderService stopped descending at - a background fill, a text run,
sometimes a whole custom component with its content drawn into it - so the
groups it found had no consistent meaning. On a launcher that accidentally
worked, because every icon is the same size. On a video feed it reported the
like button and the progress rail, because the feed's own full-screen items
were excluded for being too large, and nothing else disagreed.

What actually repeats is a BRANCH: a cell holding an icon and a label, a card
holding an image and two lines. So this walks branches, describes each one by
the kinds of content in its subtree and how they are arranged, and looks for
the same description repeated among siblings.

Three sibling relationships, which the earlier code did not separate:

  coincident  - a node and its child occupy the same box. A wrapper, a clip,
                an effect layer. Collapsed before anything is measured; these
                are most of why the tree is deeper than the layout.
  overlay     - a smaller node contained in an earlier-painted sibling. The
                caption and the like counter over a video. Removed from the
                pattern and reported as its own number, because a player with
                a HUD and a plain gallery differ exactly there.
  tiling      - siblings that share edges rather than area. The layout. Only
                these are candidates for a repeat.
"""
import re, os, sys, csv, glob, argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rs_extract as R

# content kinds. Upper case came from a component tag, lower case was inferred
# from geometry because the tag was a wrapper - on one app 103 of 135 leaves
# are tagged Custom or Stack, so without the fallback the alphabet is empty.
TAG_KIND = {"Text": "T", "Image": "I", "SymbolGlyph": "S", "Divider": "-",
            "Web": "W", "XComponent": "V", "Video": "V", "Canvas": "C"}

COINCIDENT = 0.90      # child/parent area ratio that means "the same box"
OVERLAY_IN = 0.80      # how much of the smaller must sit inside the larger
OVERLAY_MAX = 0.40     # and how small it must be to be decoration
POS_TOL = 40.0         # px: a grid's columns are not pixel-exact
BUDGET = 8             # kinds per cell; past this it is a page, not a cell


def _area(r):
    return r[2] * r[3] if r and r[2] > 0 and r[3] > 0 else 0.0


def _inside(a, b):
    """area of a that lies within b"""
    x = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return x * y


def leaf_kind(n, density):
    """What a leaf draws. The tag when there is a real one, otherwise a guess
    from shape - a small square is icon-like, a wide thin box is text-like."""
    k = TAG_KIND.get(n["tag"] or "")
    if k:
        return k
    if not n["paints"]:
        return ""                       # an empty box draws nothing; ignore it
    a = n["abs"]
    if not a or a[2] <= 0 or a[3] <= 0:
        return ""
    w, h = a[2] / density, a[3] / density
    ar = w / max(1.0, h)
    if 0.7 <= ar <= 1.4 and max(w, h) <= 56:
        return "i"                      # icon-like
    if ar >= 2.5 and h <= 30:
        return "t"                      # text-like
    return "b"                          # a painted box of no obvious kind


class Scene:
    def __init__(self, nodes, owner, density):
        self.N = nodes
        self.own = owner
        self.d = density
        self.kids = {}
        self.overlay = set()
        self._collapse()
        self._split()

    def _mine(self, nid):
        n = self.N.get(nid)
        return n is not None and n["surf"] == self.own["id"] \
            and n["kind"] != "SURFACE_NODE"

    def _effective(self, nid):
        """Descend through coincident single-child wrappers to the node that
        actually holds the layout."""
        seen = 0
        while seen < 12:
            n = self.N.get(nid)
            if not n or len(n["kids"]) != 1:
                return nid
            c = self.N.get(n["kids"][0])
            if not c or not self._mine(c["id"]) or not c["abs"] or not n["abs"]:
                return nid
            pa, ca = _area(n["abs"]), _area(c["abs"])
            if pa <= 0 or ca / pa < COINCIDENT or _inside(c["abs"], n["abs"]) / max(1.0, ca) < 0.95:
                return nid
            nid = c["id"]
            seen += 1
        return nid

    def _collapse(self):
        for nid, n in self.N.items():
            if not self._mine(nid):
                continue
            out = []
            for k in n["kids"]:
                if not self._mine(k):
                    continue
                e = self._effective(k)
                c = self.N.get(e)
                if c and c["abs"] and c["abs"][2] > 0:
                    out.append(e)
            self.kids[nid] = out

    def _split(self):
        """Mark every child that is an overlay on an earlier sibling."""
        for nid, ks in self.kids.items():
            for i, b in enumerate(ks):
                nb = self.N[b]
                ab = _area(nb["abs"])
                if ab <= 0:
                    continue
                for a in ks[:i]:          # painted earlier, so b is on top
                    na = self.N[a]
                    aa = _area(na["abs"])
                    if aa <= 0 or ab >= aa * OVERLAY_MAX:
                        continue
                    if _inside(nb["abs"], na["abs"]) / ab >= OVERLAY_IN:
                        self.overlay.add(b)
                        break

    def base(self, nid):
        return [k for k in self.kids.get(nid, ()) if k not in self.overlay]

    def pattern(self, nid):
        """The kinds of content this branch draws, sorted. None if it holds
        more than a cell's worth."""
        out, stack = [], [nid]
        while stack and len(out) <= BUDGET:
            c = self.N.get(stack.pop())
            if c is None:
                continue
            ks = self.base(c["id"])
            if not ks:
                k = leaf_kind(c, self.d)
                if k:
                    out.append(k)
            else:
                stack.extend(ks)
        if not out or len(out) > BUDGET:
            return None
        return "".join(sorted(out))

    def arrangement(self, ids):
        """How a set of boxes sits: a row, a column, a grid, or on one spot."""
        xs = {round(self.N[i]["abs"][0] / POS_TOL) for i in ids}
        ys = {round(self.N[i]["abs"][1] / POS_TOL) for i in ids}
        if len(xs) > 1 and len(ys) > 1:
            return "grid", len(xs), len(ys)
        if len(ys) > 1:
            return "column", 1, len(ys)
        if len(xs) > 1:
            return "row", len(xs), 1
        return "stack", 1, 1


NAME_FLOOR = 0.15      # below this the repeat describes decoration, not layout


def describe(pat, arr, cols, rows, cover):
    """A readable name, from the cell's content and how it repeats.

    Gated on coverage. Checked against the screenshots, every motif above
    0.6 cover was consistent by eye - 'media card grid 2x2' was a video with
    a recommendation grid under it every time, 'image grid 3x4' a dense
    three-column wall every time. Below 0.1 they were all full-screen video,
    named after whatever small thing happened to repeat on top of it: the
    action rail became 'icon and label list', the caption became 'text
    list'. That is the old feature's failure returning in a new costume, so
    the name says plainly that there is nothing to name. The number stays
    either way; only the label is withheld.

    Overlay density does not help here - it is no higher on these scenes
    than on the rest (r = +0.10 against cover), so coverage is the only
    honest gate.
    """
    if cover < NAME_FLOOR:
        return "no dominant repeat"
    img = sum(pat.count(c) for c in "IiVW")
    txt = sum(pat.count(c) for c in "Tt")
    sym = pat.count("S")
    if img >= 2:
        cell = "media card"
    elif img == 1 and txt >= 1:
        cell = "icon and label"
    elif img == 1:
        cell = "image"
    elif txt >= 1 and sym >= 1:
        cell = "label and glyph"
    elif txt >= 1:
        cell = "text"
    elif sym >= 1:
        cell = "glyph"
    else:
        cell = "plain box"
    if arr == "grid":
        lay = "grid %dx%d" % (cols, rows)
    elif arr == "column":
        lay = "list"
    elif arr == "row":
        lay = "row"
    else:
        lay = "stack"
    return "%s %s" % (cell, lay)


def analyse(tree, owner, density, dw, dh):
    sc = Scene(tree["nodes"], owner, density)
    own_area = R.region_area(owner["vis"]) or 1.0

    groups = []
    for nid in list(sc.kids):
        ks = sc.base(nid)
        if len(ks) < 2:
            continue
        by = defaultdict(list)
        for k in ks:
            p = sc.pattern(k)
            if p is None:
                continue
            inner, _, _ = sc.arrangement(sc.base(k)) if len(sc.base(k)) > 1 else ("stack", 1, 1)
            by[(p, inner)].append(k)
        for (p, inner), mem in by.items():
            if len(mem) < 2:
                continue
            rects = [(sc.N[m]["abs"][0], sc.N[m]["abs"][1],
                      sc.N[m]["abs"][2], sc.N[m]["abs"][3]) for m in mem]
            area = R.region_area(rects)
            arr, cols, rows = sc.arrangement(mem)
            hs = sorted(sc.N[m]["abs"][3] for m in mem)
            ws = sorted(sc.N[m]["abs"][2] for m in mem)
            groups.append({"pat": p, "inner": inner, "arr": arr, "cols": cols,
                           "rows": rows, "n": len(mem), "area": area,
                           "h": hs[len(hs) // 2], "w": ws[len(ws) // 2]})
    out = {"pat_n_groups": len(groups)}
    ov = [(sc.N[i]["abs"][0], sc.N[i]["abs"][1], sc.N[i]["abs"][2], sc.N[i]["abs"][3])
          for i in sc.overlay if sc.N[i]["abs"] and sc.N[i]["abs"][2] > 0]
    out["overlay_frac"] = round(min(1.0, R.region_area(ov) / own_area), 3)
    out["overlay_n"] = len(ov)
    if not groups:
        out.update(pat="", pat_inner="", pat_arr="", pat_cols="", pat_rows="",
                   pat_n="", pat_cover="", pat_h="", pat_aspect="", motif="")
        return out
    # the level that covers the most, which is the level a person would name
    g = max(groups, key=lambda g: g["area"])
    out.update(pat=g["pat"], pat_inner=g["inner"], pat_arr=g["arr"],
               pat_cols=g["cols"], pat_rows=g["rows"], pat_n=g["n"],
               pat_cover=round(min(1.0, g["area"] / own_area), 3),
               pat_h=round(g["h"] / dh, 3),
               pat_aspect=round(g["w"] / max(1.0, g["h"]), 3),
               motif=describe(g["pat"], g["arr"], g["cols"], g["rows"],
                              min(1.0, g["area"] / own_area)))
    return out


def owner_of(tree, disp):
    R.set_ownership(tree)
    live = [s for s in tree["surfaces"] if not s["nested"] and s["visible"] == 1
            and s["alpha"] > 0 and R.region_area(s["vis"]) > 0]
    best = None
    for s in sorted(live, key=lambda s: s["z"]):
        sh = R.measure(tree, s, disp["density"], disp["w"] * disp["h"],
                       disp["w"], disp["h"])
        w = min(1.0, sh.get("paint_px", 0) / (disp["w"] * disp["h"]))
        if sh.get("nodes", 0) > 0 and w >= 0.2:
            return s
        if sh.get("nodes", 0) > 0 and (best is None or w > best[1]):
            best = (s, w)
    return best[0] if best else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="directory holding capture sessions")
    ap.add_argument("--out", default="patterns.csv")
    a = ap.parse_args()
    rows = []
    for sdir in sorted(glob.glob(os.path.join(a.root, "*"))):
        if not os.path.isdir(sdir):
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        if not os.path.exists(os.path.join(sdir, "display.txt")):
            disp = R.read_display(os.path.join(a.root, "display.txt"))
        n = 0
        for tf in sorted(glob.glob(os.path.join(sdir, "*.rstree.txt"))):
            stem = os.path.basename(tf).split(".")[0]
            cf = os.path.join(sdir, stem + ".client.txt")
            if not os.path.exists(cf):
                continue
            lines = open(tf, encoding="utf-8", errors="replace").read().splitlines()
            tree = R.parse_tree(lines)
            cmap = R.parse_client(open(cf, encoding="utf-8",
                                       errors="replace").read().splitlines())
            for nd in tree["nodes"].values():
                if nd["id"] in cmap:
                    nd["tag"] = cmap[nd["id"]]
            own = owner_of(tree, disp)
            if own is None:
                continue
            r = {"session": os.path.basename(sdir), "stem": stem,
                 "owner": own["name"]}
            shot = os.path.join(sdir, stem + ".jpeg")
            r["shot"] = os.path.basename(sdir) + "/" + stem + ".jpeg" \
                if os.path.exists(shot) else ""
            r.update(analyse(tree, own, disp["density"], disp["w"], disp["h"]))
            rows.append(r)
            n += 1
        if n:
            print("  %-22s %4d scenes" % (os.path.basename(sdir), n))
    if not rows:
        print("nothing to do"); return 1
    cols = list(rows[0])
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("  %d scenes -> %s" % (len(rows), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
