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
import numpy as np
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
OVERSIZE = 1.5         # a cell bigger than this many windows is not a cell


def _area(r):
    return r[2] * r[3] if r and r[2] > 0 and r[3] > 0 else 0.0


def _inside(a, b):
    """area of a that lies within b"""
    x = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return x * y


def clipped(r, dw, dh):
    """The part of a rect that is on the display. A card scrolled half off
    display covers half a window, not a whole one."""
    x0, y0 = max(r[0], 0.0), max(r[1], 0.0)
    x1, y1 = min(r[0] + r[2], dw), min(r[1] + r[3], dh)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


SURF_TAGS = ("XComponent", "Web", "Video", "UIExtension", "Embedded", "Plugin")
SURF_MIN = 0.25        # a surface under this much of the window is furniture
FULLBLEED_H = 0.80     # a repeating page this tall is a media page, not a cell


def surface_kind(n):
    """Only an externally rendered surface counts.

    This block first accepted any large painting node, ranked image over
    plain box. Checked against the screenshots that was wrong: 77 'painted
    box' and 90 'image surface' scenes all reported cover 1.00, on comment
    pages, shopping pages and list displays alike, because what it found was
    the window's background fill or a blurred backdrop drawn as one
    full-window Image leaf. A backdrop paints, covers everything, and says
    nothing.

    Two rules were tried to tell a backdrop from content and neither worked.
    Counting painting descendants catches a container backdrop - a Scroll
    holding 53 of them - but not a leaf Image. Measuring how much is painted
    on top catches the backdrops and also condemns the real surfaces: in a
    short-form feed the next page overlaps the current one, so the
    XComponent reads 83% and 100% covered.

    So the kind is taken from the tag alone, where it is stated and
    reliable, and the geometry of THAT is what this block contributes -
    rs_surface_tag already counts these, but not how much of the window one
    fills, which was 0.26 on one scene and 0.89 on another."""
    t = n["tag"] or ""
    return 3 if any(k in t for k in SURF_TAGS) else 0


# RSDrawableSlot on this build. The number is an inference, not a published
# fact: the device emits slot 50 while the longest published OpenHarmony
# enum ends at 46, so the mapping was recovered by bracket-pair alignment -
# four pairs never appear apart, (0,50), (3,22), (11,12) and (24,31),
# matching the enum's four save/restore pairs, and anchoring on
# SAVE_FRAME/RESTORE_FRAME = (24,31) puts the content region at master+4.
# ALG-VISIBLE-SURFACE carries the full derivation and what invalidates it.
#
# Only this one is read. The bracket, background and border sets were
# defined and never used: they were the working notes of that derivation,
# and the lesson in them - that 11 and 12 are clip brackets on 31.6% of
# nodes, which is why background once dominated 195 of 250 scenes - belongs
# to the rule that substrate is the complement, not to a constant nothing
# touches.
SLOT_CONTENT = 28           # CONTENT_STYLE

# Only content competes for pixels. A background is the substrate: it is
# what you see where nothing else was drawn, so letting it claim the
# remainder makes it dominant BY CONSTRUCTION - it read 0.95 at the median
# and won 195 of 250 scenes, which says nothing about any of them. It is
# reported as the complement instead, which is the same number without the
# pretence that it competed for it.
VIS_KINDS = ("xcomp", "image", "text", "glyph", "content")


def is_composed(n):
    """An externally rendered surface. It has no content drawable at all -
    an XComponent's slots are [11, 12, 24, 29, 31, 47], no 28 - because the
    compositor draws it, not the render pass. A decomposition keyed on slot
    28 therefore reports video as zero, which is how this was found."""
    return any(k in (n["tag"] or "") for k in SURF_TAGS)


def is_drawn(n):
    """A node that puts a mark on the display this frame.

    Slot 28 is the content pass, and shouldPaint_ is the renderer's verdict
    on whether it happens. NOT IsSubTreeSkipped: that means the subtree was
    not re-traversed, which is caching, not absence - on one scene 75 of 77
    content nodes are skipped while the display is plainly full of images.
    Using it as a visibility filter would discard most of the content.
    """
    if not n.get("should", True):
        return False
    sl = n.get("slotset") or frozenset()
    return SLOT_CONTENT in sl


def is_structural(n):
    """Slot 29 is the children-only pass: the node positions and clips its
    children and draws nothing itself. The skeleton, never a mark."""
    return not is_drawn(n) and not is_composed(n)


NO_SLOTS = os.environ.get("RS_NO_SLOTS") == "1"


def mark_kind(n, density):
    """What a node puts on the display, or "" if it puts nothing.

    Replaces the old leaf_kind, which asked whether a node had children.
    That was wrong in both directions: 73% of leaves draw nothing and 16%
    of marks are containers painting their own background.
    """
    if is_composed(n):
        return "V"
    if NO_SLOTS:
        # the primitive before slot 28 existed: a tag that names content, or
        # a shape that looks like an icon or a line of text
        if not n.get("should", True):
            return ""
        t = n["tag"] or ""
        if not TAG_KIND.get(t):
            a = n["abs"]
            if not a or a[2] <= 0 or a[3] <= 0:
                return ""
            w, h = a[2] / density, a[3] / density
            ar = w / max(1.0, h)
            if 0.7 <= ar <= 1.4 and max(w, h) <= 56:
                return "i"
            if ar >= 2.5 and h <= 30:
                return "t"
            return ""
        return TAG_KIND[t]
    if not is_drawn(n):
        return ""
    t = n["tag"] or ""
    k = TAG_KIND.get(t)
    if k:
        return k
    a = n["abs"]
    if not a or a[2] <= 0 or a[3] <= 0:
        return ""
    w, h = a[2] / density, a[3] / density
    ar = w / max(1.0, h)
    if 0.7 <= ar <= 1.4 and max(w, h) <= 56:
        return "i"
    if ar >= 2.5 and h <= 30:
        return "t"
    return "b"


def paints_behind(sc, nid):
    """Does anything below this node draw?

    If so, whatever this node paints is behind it, and what is behind
    everything is background. The test is the tree, not a name: there is
    no list of container tags to author, nothing to fit, and nothing that
    drifts when the capture changes.

    Measured on the capture of 2026-10-27: 578 of 6,151 content-bucket
    nodes answer yes, 9.4% by count but 0.159 of the display by area -
    almost all of it WaterFlow, whose 103 nodes are 103 of 103 such, and
    whose backdrop was 40% of everything the surface vector called
    content it could not name.
    """
    stack, seen = [nid], set()
    while stack:
        for k in sc.kids.get(stack.pop(), ()):
            if k in seen:
                continue
            seen.add(k)
            kn = sc.N.get(k)
            if kn is not None and is_drawn(kn):
                return True
            stack.append(k)
    return False


def vis_kind(n, sc=None, nid=None):
    """What a node puts on the display, by what it actually draws.

    The type comes from two independent places and both are needed. The
    component tag says image, text or glyph, and nothing else identifies an
    external surface. The drawable slots say background and border, which no
    tag reports - 32% of nodes draw a background and they were all landing in
    one "fill" bucket that averaged 0.39 of the display and meant nothing.

    Within one node the content pass sits above its own background, so a node
    that draws both is attributed to its content.
    """
    if is_composed(n):
        return "xcomp"
    sl = n.get("slotset") or frozenset()
    if SLOT_CONTENT in sl:
        t = n["tag"] or ""
        if "Image" in t:
            return "image"
        if "Text" in t:
            return "text"
        if "SymbolGlyph" in t or "Divider" in t:
            return "glyph"
        # Untyped. Only here does descent decide: a tag is direct evidence
        # of what the paint IS, descent only of where it sits, so a tagged
        # node keeps its kind whatever is under it. Without that restriction
        # an Image with any painting child would vanish from the measurement.
        if sc is not None and nid is not None and paints_behind(sc, nid):
            return ""             # background: claims nothing, falls to the
                                  # complement, which is what substrate is
        return "content"          # draws, and nothing says what
    return ""                      # background, border, or structural:
                                   # none of them compete for pixels


def visible_surface(sc, dw, dh, own_area, order, step=4):
    """What you can actually SEE, by content type.

    A painter's pass, topmost first: every mark claims only the area that
    nothing above it has already claimed. An Image inside a container that
    also paints wins the pixels it covers, because it is above its
    container - the nesting case resolved by construction rather than by a
    rule. The result is a composition of the display that sums to one.

    Rasterised at `step` pixels rather than unioned rectangle by rectangle,
    because the incremental union is O(n^2) in sweep-line calls and costs
    more than the whole rest of the extractor. At 4 px the area error is
    under a percent.
    """
    W, H = int(dw // step) + 1, int(dh // step) + 1
    grid = np.zeros((H, W), dtype=np.uint8)
    idx = {k: i + 1 for i, k in enumerate(VIS_KINDS)}
    marks = []
    for nid, n in sc.N.items():
        if not sc._mine(nid):
            continue
        if not (is_drawn(n) or is_composed(n)):
            continue
        k = vis_kind(n, sc, nid)
        if not k:
            continue
        # Size is judged on the node's OWN rect, never on the clipped one:
        # an 8198x8198 blur source clips to exactly the window and so slips
        # through a clipped-area test, which is how five of them ended up
        # painted over a video and the display read as 87% plain fill.
        if _area(n["abs"]) > OVERSIZE * own_area:
            continue
        q = clipped(n["abs"], dw, dh) if n["abs"] and n["abs"][2] > 0 else None
        if not q:
            continue
        # A node with no client-dump entry has no tag because none exists:
        # it is a CANVAS_NODE below the component layer. Carried so the
        # share it claims can be reported apart from content the dump named
        # and the mapping failed to cover (INV-UNNAMED-CONTENT-REPORTED).
        marks.append((order.get(nid, 0), q, k, not (n["tag"] or "")))
    marks.sort(key=lambda m: -m[0])              # topmost first
    # A second grid, same shape, marking only the content cells whose node
    # was unnameable. Painted in lockstep so it cannot drift from the first.
    unnamed = np.zeros((H, W), dtype=np.uint8)
    for _, q, k, anon in marks:
        x0, y0 = int(q[0] // step), int(q[1] // step)
        x1, y1 = int((q[0] + q[2]) // step) + 1, int((q[1] + q[3]) // step) + 1
        sub = grid[y0:y1, x0:x1]
        if k == "content" and anon:
            np.putmask(unnamed[y0:y1, x0:x1], sub == 0, 1)
        np.putmask(sub, sub == 0, idx[k])
    cell = float(step * step)
    tot = float(dw * dh)
    out = {}
    for k in VIS_KINDS:
        out["vis_" + k] = round(min(1.0, int((grid == idx[k]).sum()) * cell / tot), 3)
    # everything no content mark claimed: substrate, chrome, blur, blank
    out["vis_substrate"] = round(max(0.0, 1.0 - sum(out.values())), 3)
    # INV-SURFACE-SUMS-TO-ONE, checked where it could break rather than only
    # by a script someone remembers to run. True by construction above, which
    # is the point: it is the day the construction changes that matters. The
    # tolerance is the rounding of six values at three decimals.
    total = sum(out["vis_" + k] for k in VIS_KINDS) + out["vis_substrate"]
    if abs(total - 1.0) > 0.01:
        print("ERR-SURFACE-SUM-OFF: the surface columns sum to %.4f, not one; "
              "the composition was not built as a partition of the display"
              % total)
    # Not a term of the partition: a subset of vis_content, reported so a
    # composition made of drawing nobody can name is visible as that rather
    # than read as a gap in the tag mapping. On the capture this was written
    # against it is 0.184 of the display, 46.8% of all content.
    out["vis_unnamed"] = round(min(out["vis_content"],
                                   int(unnamed.sum()) * cell / tot), 3)
    if out["vis_content"] and out["vis_unnamed"] / max(out["vis_content"], 1e-9) > 0.5:
        print("ERR-UNNAMED-CONTENT: %.0f%% of this scene's content is nodes with "
              "no client-dump entry; the pixels are measured, nothing names them"
              % (100.0 * out["vis_unnamed"] / out["vis_content"]))
    out["vis_marks"] = len(marks)
    return out


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
    def __init__(self, nodes, owner, density, order=None):
        self.N = nodes
        self.own = owner
        self.d = density
        self.kids = {}
        self.overlay = set()
        self._collapse()
        self._split(order)

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

    def _split(self, order=None):
        """Mark every child that is an overlay on an earlier sibling.

        'Earlier' means earlier in PAINT order. The sibling list is the
        declared order, which ReverseChildren inverts and PositionZ
        re-sorts, so on a reversed parent this had the relationship exactly
        backwards and called the background an overlay on the content.
        """
        for nid, ks in self.kids.items():
            if order:
                ks = sorted(ks, key=lambda k: order.get(k, 0))
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
        """The kinds of content this branch puts on display, sorted.

        Every mark in the subtree counts, wherever it sits - a container
        that paints its own background is a mark as well as a joint, and a
        childless node that draws nothing is neither. The old version
        descended to childless nodes and read those, which on one scene
        meant reading 131 leaves of which 2 drew anything.
        """
        out, stack = [], [nid]
        while stack and len(out) <= BUDGET:
            c = self.N.get(stack.pop())
            if c is None:
                continue
            k = mark_kind(c, self.d)
            if k:
                out.append(k)
            stack.extend(self.base(c["id"]))
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


SURF_BANDS = (0.25, 0.50, 0.75, 1.00)


def media_surface(sc, dw, dh, own_area, used, order):
    """The externally rendered surface holding the scene, graded by how much
    of the window you can actually SEE of it.

    Two things this gets right that the first version did not.

    It measures the surface UNOCCLUDED. A player with a comment sheet pulled
    half over it is a different scene from the same player uncovered, and the
    raw rect says they are identical. Everything painted after the surface
    that lands on it is subtracted, so what is reported is the media you can
    see rather than the media that exists.

    And the coverage is graded rather than thresholded. A quarter of the
    window and the whole of it are different scenes with different costs;
    one flag said they were the same. Four soft bands at 0.25, 0.50, 0.75
    and 1.00 carry it, interpolated between neighbours so a surface growing
    across a boundary moves mass gradually instead of jumping - the same
    treatment the item-height bands get, for the same reason.

    The kind still comes from the component tag alone. Nothing else in the
    tree marks media: hwc never fires on a node here (RenderService reports
    it as a list of other ids) and headroom stays at 1.0 throughout, so
    there is no attribute-based confirmation available to cross-check the
    tag against.
    """
    empty = {"surf_kind": 0, "surf_cover": "", "surf_vis": "", "surf_aspect": "",
             "surf_h": "", "surf_over": "", "surf_full": 0, "_surf_id": None}
    for i in range(len(SURF_BANDS)):
        empty["surf_b%d" % i] = ""
    best = None
    for nid, n in sc.N.items():
        if nid in used or not sc._mine(nid):
            continue
        a = n["abs"]
        if not a or a[2] <= 0 or a[3] <= 0:
            continue
        if surface_kind(n) != 3:
            continue
        c = clipped(a, dw, dh)
        if not c:
            continue
        area = c[2] * c[3]
        if area < SURF_MIN * own_area:
            continue
        if best is None or area > best[0]:
            best = (area, nid, n, c)
    if best is None:
        return empty
    area, nid, n, c = best

    # everything painted after it that lands on it, whether it was classed as
    # an overlay or is simply a later sibling: a sheet dragged over a player
    # hides it either way
    on_top = []
    mine_after = order.get(nid, 0)
    for mid, m in sc.N.items():
        if mid == nid or not sc._mine(mid) or order.get(mid, 0) <= mine_after:
            continue
        # Leaves only. A BRANCH that paints is almost always a scrim or a
        # wrapper whose children are the real content: on one scene two
        # untagged full-window branches with paints=True covered the player
        # 100% each, and the surface was reported as entirely invisible. A
        # sheet genuinely dragged over the player is still caught, because
        # its leaves overlap too.
        if m["kids"]:
            continue
        # It must actually be drawn this frame. shouldPaint_ alone: the
        # Image that reported a plainly visible player as covered carries
        # shouldPaint_[0], while IsSubTreeSkipped marks cached content that
        # is still on display and would discard most of it.
        if not (is_drawn(m) or is_composed(m)):
            continue
        q = clipped(m["abs"], dw, dh) if m["abs"] and m["abs"][2] > 0 else None
        if not q:
            continue
        # A node sharing the surface's box is the surface's own content, not
        # something over it: a player's poster frame is drawn as an Image at
        # exactly the XComponent's rect, and counting it hid the video
        # completely on 70 of 88 scenes. Same rule as the coincident wrapper
        # collapse - the same box is the same thing.
        # The same blur and shadow sources the pattern block bounds: Rect
        # nodes of 8198x8198 px at negative origins, four times the window.
        # They cover everything and occlude nothing.
        if _area(m["abs"]) > OVERSIZE * own_area:
            continue
        qa, ca2 = q[2] * q[3], c[2] * c[3]
        if ca2 > 0 and qa / ca2 >= COINCIDENT and abs(q[0] - c[0]) < 4 and abs(q[1] - c[1]) < 4:
            continue
        x0, y0 = max(q[0], c[0]), max(q[1], c[1])
        x1, y1 = min(q[0] + q[2], c[0] + c[2]), min(q[1] + q[3], c[1] + c[3])
        if x1 > x0 and y1 > y0:
            on_top.append((x0, y0, x1 - x0, y1 - y0))
    hidden = R.region_area(on_top)
    visible = max(0.0, area - hidden)

    out = {"surf_kind": 3, "_surf_id": nid,
           "surf_cover": round(min(1.0, area / own_area), 3),
           "surf_vis": round(min(1.0, visible / own_area), 3),
           "surf_aspect": round(c[2] / max(1.0, c[3]), 3),
           "surf_h": round(c[3] / dh, 3),
           "surf_over": round(min(1.0, hidden / max(1.0, area)), 3),
           "surf_full": 1 if visible >= 0.90 * own_area else 0}
    # graded, on what is visible
    v = min(1.0, visible / own_area)
    w = [0.0] * len(SURF_BANDS)
    if v <= SURF_BANDS[0]:
        w[0] = 1.0
    elif v >= SURF_BANDS[-1]:
        w[-1] = 1.0
    else:
        for i in range(len(SURF_BANDS) - 1):
            lo, hi = SURF_BANDS[i], SURF_BANDS[i + 1]
            if lo <= v <= hi:
                t = (v - lo) / (hi - lo)
                w[i], w[i + 1] = 1.0 - t, t
                break
    for i, x in enumerate(w):
        out["surf_b%d" % i] = round(x, 3)
    return out


def draw_state(sc, dw, dh, own_area, surf_id):
    """What RenderService says it is going to draw.

    Everything else here reasons about the scene geometrically and infers
    what is visible. This asks instead. shouldPaint_ is the renderer's own
    verdict on a node, and IsSubTreeSkipped says a whole subtree was passed
    over. Both are already in the dump and neither was used.

    It answers a different question from coverage, and the one that bears
    most directly on cost: a surface the renderer has skipped is not being
    composed, whatever its rectangle says, and a scene where a third of the
    tree is skipped is not the same workload as one where none of it is.
    """
    n_all = n_mark = n_cached = n_nopaint = 0
    for nid, n in sc.N.items():
        if not sc._mine(nid):
            continue
        n_all += 1
        if is_drawn(n) or is_composed(n):
            n_mark += 1
            # cached: the subtree was not re-traversed. Still composited,
            # so it costs composition but not rasterisation - which is cost
            # information, and the reason it must not be a visibility test.
            if n["skipped"]:
                n_cached += 1
        if not n.get("should", True):
            n_nopaint += 1
    out = {"draw_mark_frac": round(n_mark / n_all, 3) if n_all else "",
           "draw_cached_frac": round(n_cached / n_mark, 3) if n_mark else "",
           "draw_nopaint_frac": round(n_nopaint / n_all, 3) if n_all else "",
           "surf_painting": "", "surf_skipped": ""}
    if surf_id is not None and surf_id in sc.N:
        n = sc.N[surf_id]
        out["surf_painting"] = 1 if is_drawn(n) or is_composed(n) else 0
        out["surf_skipped"] = 1 if n["skipped"] else 0
    return out


def analyse(tree, owner, density, dw, dh):
    order = R.paint_order(tree)
    sc = Scene(tree["nodes"], owner, density, order)
    own_area = R.region_area(owner["vis"]) or 1.0

    groups = []
    for nid in list(sc.kids):
        ks = sc.base(nid)
        if len(ks) < 2:
            continue
        by = defaultdict(list)
        for k in ks:
            # A cell larger than the window is not a cell. Without this the
            # detector reported 30 scenes as a repeating row of plain boxes,
            # and the boxes were Rect nodes of 8359x8360 px sitting at
            # (-2731, -3172) - blur and shadow sources three displays wide,
            # which win on area because nothing bounded them. The band
            # feature had a ceiling that excluded these; removing it for
            # branches let them back in.
            if _area(sc.N[k]["abs"]) > OVERSIZE * own_area:
                continue
            p = sc.pattern(k)
            if p is None:
                continue
            inner, _, _ = sc.arrangement(sc.base(k)) if len(sc.base(k)) > 1 else ("stack", 1, 1)
            by[(p, inner)].append(k)
        for (p, inner), mem in by.items():
            if len(mem) < 2:
                continue
            # clipped to the window: a card scrolled half off display covers
            # half a window, not a whole one
            rects = []
            for m in mem:
                a = sc.N[m]["abs"]
                x0, y0 = max(a[0], 0.0), max(a[1], 0.0)
                x1 = min(a[0] + a[2], dw)
                y1 = min(a[1] + a[3], dh)
                if x1 > x0 and y1 > y0:
                    rects.append((x0, y0, x1 - x0, y1 - y0))
            if not rects:
                continue
            area = R.region_area(rects)
            arr, cols, rows = sc.arrangement(mem)
            hs = sorted(sc.N[m]["abs"][3] for m in mem)
            ws = sorted(sc.N[m]["abs"][2] for m in mem)
            # where the group sits, and how far down the window it runs.
            # The detector says WHAT repeats; without this it cannot say
            # whether the grid is the page or a strip under a player.
            ys = [sc.N[m]["abs"][1] for m in mem]
            ye = [sc.N[m]["abs"][1] + sc.N[m]["abs"][3] for m in mem]
            groups.append({"pat": p, "inner": inner, "arr": arr, "cols": cols,
                           "rows": rows, "n": len(mem), "area": area,
                           "h": hs[len(hs) // 2], "w": ws[len(ws) // 2],
                           "top": max(0.0, min(ys)) / dh,
                           "bot": min(dh, max(ye)) / dh,
                           "ids": list(mem)})
    out = {"pat_n_groups": len(groups)}
    ov = [(sc.N[i]["abs"][0], sc.N[i]["abs"][1], sc.N[i]["abs"][2], sc.N[i]["abs"][3])
          for i in sc.overlay if sc.N[i]["abs"] and sc.N[i]["abs"][2] > 0]
    out["overlay_frac"] = round(min(1.0, R.region_area(ov) / own_area), 3)
    out["overlay_n"] = len(ov)

    # A full-bleed repeating page - the short-form feed - is a media surface
    # wearing a repeat's clothes. It goes to the surface block, and the
    # pattern block describes whatever else is on the display, so the two
    # never claim the same pixels.
    def fullbleed(g):
        return (g["h"] / dh >= FULLBLEED_H
                and g["area"] / own_area >= 0.5
                and g["cols"] <= 1
                and any(c in g["pat"] for c in "IiVW"))
    # Only hand the page over if there is a surface to hand it to. With the
    # image and painted-box kinds gone, a feed with no tagged surface would
    # otherwise lose its page from the pattern slots and gain nothing.
    pages = [g for g in groups if fullbleed(g)]
    has_surface = any(surface_kind(n) == 3 for nid, n in sc.N.items()
                      if sc._mine(nid) and n["abs"] and n["abs"][2] > 0)
    if has_surface:
        groups = [g for g in groups if not fullbleed(g)]
    else:
        pages = []

    # Ordered by coverage, but ties broken on vertical position rather than
    # on the quantity that is nearly tied. Two groups of similar size would
    # otherwise swap slots between samples and the whole vector would jump -
    # the argmax failure, one level up.
    #
    # Clamped, so the number that decides the order is the number the row
    # reports. Without the clamp two groups that both fill the window were
    # separated by how far each spilled past it, which is an artefact of
    # groups extending beyond the viewport and not a difference in what
    # repeats: on one scene of 250 it put the lower group in slot 1 while
    # the row showed both at cover 1.0, so the file could not explain its
    # own ordering.
    groups.sort(key=lambda g: (-round(min(1.0, g["area"] / own_area), 1),
                               g["top"]))

    used = set()
    for g in pages:
        used |= set(g["ids"])
    for g in groups[:2]:
        used |= set(g["ids"])

    for i in (1, 2):
        p = "pat%d_" % i
        if len(groups) >= i:
            g = groups[i - 1]
            cov = min(1.0, g["area"] / own_area)
            out.update({p + "pat": g["pat"], p + "arr": g["arr"],
                        p + "cols": g["cols"], p + "rows": g["rows"],
                        p + "n": g["n"], p + "cover": round(cov, 3),
                        p + "h": round(g["h"] / dh, 3),
                        p + "aspect": round(g["w"] / max(1.0, g["h"]), 3),
                        p + "top": round(g["top"], 3)})
        else:
            # Empty, not zero. Writing a zero coverage and a zero height for
            # an absent second group would place it at the small end of both
            # scales instead of nowhere, which is the mistake item_h made on
            # 1,266 samples. build_matrix imputes these to the column mean.
            out.update({p + k: "" for k in ("pat", "arr", "cols", "rows", "n",
                                            "cover", "h", "aspect", "top")})
    out["has_slot2"] = 1 if len(groups) >= 2 else 0

    ms = media_surface(sc, dw, dh, own_area, used, order)
    surf_id = ms.pop("_surf_id", None)
    out.update(ms)
    out.update(draw_state(sc, dw, dh, own_area, surf_id))
    out.update(visible_surface(sc, dw, dh, own_area, order))
    if False:
        # the feed page itself, when nothing else qualified as a surface
        g = max(pages, key=lambda g: g["area"])
        out.update(surf_kind=3 if "V" in g["pat"] else 2,
                   surf_cover=round(min(1.0, g["area"] / own_area), 3),
                   surf_aspect=round(g["w"] / max(1.0, g["h"]), 3),
                   surf_h=round(g["h"] / dh, 3),
                   surf_over=out["overlay_frac"],
                   surf_full=1 if g["area"] >= 0.90 * own_area else 0)

    # kept for reading: the single best group, as before
    if groups or pages:
        g = max(groups + pages, key=lambda g: g["area"])
        cov = min(1.0, g["area"] / own_area)
        out.update(pat=g["pat"], pat_inner=g["inner"], pat_arr=g["arr"],
                   pat_cols=g["cols"], pat_rows=g["rows"], pat_n=g["n"],
                   pat_cover=round(cov, 3), pat_top=round(g["top"], 3),
                   pat_bot=round(g["bot"], 3),
                   pat_band=round(max(0.0, g["bot"] - g["top"]), 3),
                   pat_h=round(g["h"] / dh, 3),
                   pat_aspect=round(g["w"] / max(1.0, g["h"]), 3))
    else:
        out.update(pat="", pat_inner="", pat_arr="", pat_cols="", pat_rows="",
                   pat_n="", pat_cover="", pat_h="", pat_aspect="",
                   pat_top="", pat_bot="", pat_band="")
    return out


def owner_of(tree, disp):
    R.set_ownership(tree)
    live = [s for s in tree["surfaces"] if not s["nested"] and s["visible"] == 1
            and s["alpha"] > 0 and R.region_area(s["vis"]) > 0]
    best = None
    for s in sorted(live, key=lambda s: s["z"]):
        sh = R.window_weight(tree, s)
        w = min(1.0, sh.get("paint_px", 0) / (disp["w"] * disp["h"]))
        if sh.get("nodes", 0) > 0 and w >= 0.2:
            return s
        if sh.get("nodes", 0) > 0 and (best is None or w > best[1]):
            best = (s, w)
    return best[0] if best else None


def report(fell_back, no_client, no_owner):
    """Say what the run skipped. Each of these used to be a bare continue."""
    def listed(ids, limit=5):
        head = ", ".join(ids[:limit])
        return head + (" and %d more" % (len(ids) - limit) if len(ids) > limit else "")

    if fell_back:
        print("ERR-DISPLAY-DUMP-MISSING: %d session(s) have no display.txt and "
              "no fallback at the capture root; geometry defaults to "
              "%gx%g at density %g, and every fraction is computed against it: %s"
              % (len(fell_back), R.DISPLAY_DEFAULT["w"], R.DISPLAY_DEFAULT["h"],
                 R.DISPLAY_DEFAULT["density"], listed(fell_back)))
    if no_client:
        print("ERR-CLIENT-DUMP-MISSING: %d render tree(s) have no matching "
              "client dump and are skipped; without it no node can say what "
              "component it is: %s" % (len(no_client), listed(no_client)))
    if no_owner:
        print("ERR-NO-OWNER-WINDOW: %d dump(s) carry no window that paints "
              "anything, and are skipped: %s" % (len(no_owner), listed(no_owner)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="directory holding capture sessions")
    ap.add_argument("--out", default="patterns.csv")
    a = ap.parse_args()
    rows = []
    no_client, no_owner, fell_back = [], [], []
    for sdir in sorted(glob.glob(os.path.join(a.root, "*"))):
        if not os.path.isdir(sdir):
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        if not os.path.exists(os.path.join(sdir, "display.txt")):
            disp = R.read_display(os.path.join(a.root, "display.txt"))
        if disp.get("fallback"):
            fell_back.append(os.path.basename(sdir))
        n = 0
        for tf in sorted(glob.glob(os.path.join(sdir, "*.rstree.txt"))):
            stem = os.path.basename(tf).split(".")[0]
            cf = os.path.join(sdir, stem + ".client.txt")
            if not os.path.exists(cf):
                no_client.append("%s/%s" % (os.path.basename(sdir), stem))
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
                no_owner.append("%s/%s" % (os.path.basename(sdir), stem))
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
    report(fell_back, no_client, no_owner)
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
