#!/usr/bin/env python3
"""The scene in one pass: ALG-VISIBILITY and ALG-MOTIF-MATCH.

    Scene(lines, client_lines, disp).run()   -> the row of IF-PATTERNS-ROW

The scene is the display's kept tree (INV-SCENE-PRUNED): the nodes with
visible cells on the display and the containers that hold them, in their
containment. Neither the display nor a window is flattened into a set of
nodes; containment is the structure the patterns are read from.

The dump is indexed - one match per line - and descended once from its
single LOGICAL_DISPLAY_NODE (INV-DISPLAY-ROOT), parent first, children
topmost first (INV-OCCLUSION-PAINT-ORDER). Each node is visited once
(INV-SINGLE-PASS) and its line is parsed only when the descent reaches it
(INV-PRUNED-NOT-PARSED).

  DOWN   effective alpha, the transform (INV-GEOMETRY-FROM-TREE) and the
         clip - the display, every clipping ancestor (INV-CLIP-TO-BOUNDS),
         and at a window node its VisibleRegion. A branch is dropped only
         when its region is provably empty: alpha 0, a clip with nothing
         left, or a clip region already wholly covered by occluders. A
         container that does not clip is descended even when its own box is
         off the display, because only its children decide whether it stays.
  AT     the node's visible cells: its on-display cells not covered by the
         occluders written so far, which are exactly those painted above it
         outside its branch (INV-OPACITY).
  UP     kept if it has visible cells or a kept child, its children reduced
         to the kept ones; its element set and pattern matches; and, when
         it occludes, its cells written for what lies
         beneath. At a window node, tag coverage over its kept nodes.

Visibility is alpha alone: at effective alpha 0 a node is invisible, at 1
with an FF background it occludes, otherwise it is visible however
transparent. No draw state and no size threshold decides anything about
what is on the display.
"""
import math, sys
import numpy as np

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import rs_extract as R                   # noqa: E402

STEP = 4          # px; a cell belongs to a shape when its centre lies inside it
INVISIBLE, OCCLUDING, TRANSLUCENT = "invisible", "occluding", "translucent"

# ---- IF-MOTIF-VOCABULARY -----------------------------------------------------
# Collapsed into the nearest meaningful parent: layout wrappers whose contract
# carries no structure, the page frame, and the navigation shell every app has.
PASS_THROUGH = {"__Common__", "Custom", "Stack", "NodeContainer", "RelativeContainer",
                "Flex", "BuilderProxyNode", "CustomFrameNode", "JsView", "RenderNode",
                "root", "stage", "page", "UIExtensionSurface", "metaballNode",
                "Navigation", "NavigationContent", "NavDestination",
                "NavDestinationContent", "NavBar", ""}
SMALL, MEDIUM = 0.15, 0.5          # video:small under, :medium up to
ICON_DP, ICON_AR = 56.0, (0.7, 1.4)
THUMB = 0.15
VIDEO_TAGS = {"XComponent", "Video"}
TEXT_INPUT_TAGS = {"TextInput", "TextArea", "Search"}
# Containers whose children are items, whatever the item's own tag: a custom
# component has no node of its own (CustomNode derives from UINode, not
# FrameNode - arkui_ace_engine pattern/custom/custom_node.h), so its root
# stands in the item's place. Two or more children of a repeating container
# are parent/item*; the one child of a single-child container is parent/item.
REPEATING = {"List", "ListItemGroup", "Grid", "WaterFlow", "Swiper", "SideBarContainer"}
SINGLE_CHILD = {"Scroll", "Refresh"}

# ---- IF-MOTIF-DICTIONARY (INV-DICTIONARY-DESIGNED, INV-DICTIONARY-COMPLETE) ---
# (name, root, element set, source). Each motif comes from an ArkUI component
# contract - the "Child Components" section of the OpenHarmony ArkUI reference,
# docs/en/application-dev/reference/apis-arkui/arkui-ts - and, where the engine
# builds the component differently, from its runtime form in arkui_ace_engine.
# Never from a capture.
DICTIONARY = (
    ("list", "List", {"List", "List/item*"},
     "ts-container-list: only ListItem and ListItemGroup, custom components supported"),
    ("list", "ListItemGroup", {"ListItemGroup", "ListItemGroup/item*"},
     "ts-container-listitemgroup: ListItem"),
    ("grid", "Grid", {"Grid", "Grid/item*"},
     "ts-container-grid: GridItem and custom components"),
    ("waterflow", "WaterFlow", {"WaterFlow", "WaterFlow/item*"},
     "ts-container-waterflow: FlowItem and custom components"),
    ("tabs", "Tabs", {"Tabs", "Tabs/Swiper", "Tabs/TabBar"},
     "ts-container-tabs: only TabContent; tabs_model_ng InitTabsNode mounts Swiper, Divider, TabBar"),
    ("carousel", "Swiper", {"Swiper", "Swiper/item*"},
     "ts-container-swiper: built-in and custom components; not the Swiper of a Tabs"),
    ("stepper", "Stepper", {"Stepper", "Stepper/StepperItem*"},
     "ts-basic-components-stepper: only StepperItem"),
    ("menu", "Menu", {"Menu", "Menu/MenuItem*"},
     "ts-basic-components-menu: MenuItem and MenuItemGroup"),
    ("grid_row", "GridRow", {"GridRow", "GridRow/GridCol*"},
     "ts-container-gridrow: GridCol"),
    ("scroll_page", "Scroll", {"Scroll", "Scroll/item"},
     "ts-container-scroll: only one child"),
    ("refresh", "Refresh", {"Refresh", "Refresh/item"},
     "ts-container-refresh: only one child"),
    ("sidebar", "SideBarContainer", {"SideBarContainer", "SideBarContainer/item*"},
     "ts-container-sidebarcontainer: two children, sidebar and content"),
    ("video_large", "video:large", {"video:large"},
     "ts-basic-components-xcomponent, ts-media-components-video: no children"),
    ("video_medium", "video:medium", {"video:medium"},
     "ts-basic-components-xcomponent, ts-media-components-video: no children"),
    ("video_small", "video:small", {"video:small"},
     "ts-basic-components-xcomponent, ts-media-components-video: no children"),
    ("web", "Web", {"Web"}, "ArkWeb Web: no children"),
    ("text_input", "text_input", {"text_input"},
     "TextInput, TextArea, ts-basic-components-search: no children"),
    ("button", "Button", {"Button"}, "ts-basic-components-button: a leaf of the scene"),
    ("slider", "Slider", {"Slider"}, "ts-basic-components-slider: no children"),
    ("index_bar", "AlphabetIndexer", {"AlphabetIndexer"},
     "ts-container-alphabet-indexer: no children"),
    ("icon_row", "Row", {"Row", "Row/icon*"}, "kept from the designed dictionary: a Row of icons"),
    ("icon_grid", "Column", {"Column", "Column/icon_row*"}, "composed: a Column of icon rows"),
    ("icon_grid", "Grid", {"Grid", "Grid/item*", "GridItem/icon"}, "composed: a Grid of icon items"),
)
MOTIFS = tuple(dict.fromkeys(p[0] for p in DICTIONARY))          # in order
PATTERNS = MOTIFS                 # the name the row's columns were written under
# Every catalogue container with a typed or fixed child contract, and every
# leaf label, must root a motif (INV-DICTIONARY-COMPLETE).
CATALOGUE = ("List", "ListItemGroup", "Grid", "WaterFlow", "Tabs", "Swiper", "Stepper",
             "Menu", "GridRow", "Scroll", "Refresh", "SideBarContainer",
             "video:large", "video:medium", "video:small", "Web", "text_input",
             "Button", "Slider", "AlphabetIndexer")
FOUND = 0.2       # d at or under this is a match; set by the human 2026-10-28


def _depth(root, elems):
    """0 for a leaf, 1 when every pair hangs from the root, 2 when one hangs
    below it."""
    if not any("/" in e for e in elems):
        return 0
    return 2 if any("/" in e and e.split("/")[0] != root for e in elems) else 1


SHAPES = tuple((n, r, frozenset(s), _depth(r, s)) for n, r, s, _ in DICTIONARY)
BY_ROOT = {}
for _s in SHAPES:
    BY_ROOT.setdefault(_s[1], []).append(_s)
# a motif that appears as a child in another's set is presented by its name
# when matched, so the composed motif above can see it
TOKENS = {e.split("/")[1].rstrip("*") for _, _, s, _ in SHAPES for e in s
          if "/" in e} & set(MOTIFS)
COMPOSED = {}                     # over every variant of a motif
for _n, _, _s, _ in SHAPES:
    COMPOSED.setdefault(_n, set()).update(
        {e.split("/")[1].rstrip("*") for e in _s if "/" in e} & TOKENS)

def jaccard_distance(a, b):
    """1 - |a & b| / |a | b|. Sets, so the order of either is irrelevant
    (INV-DISTANCE-ORDER-FREE)."""
    a, b = frozenset(a), frozenset(b)
    u = a | b
    return 1.0 - (len(a & b) / len(u) if u else 1.0)


def _span(lo, hi, n, step):
    """The cells whose centre lies in [lo, hi), clamped to [0, n)."""
    return (max(0, int(math.ceil(lo / step - 0.5))),
            min(n, int(math.ceil(hi / step - 0.5))))


class NoDisplay(Exception):
    """ERR-NO-DISPLAY-NODE: no single LOGICAL_DISPLAY_NODE to start from."""


class Scene:
    def __init__(self, lines, client_lines, disp, step=STEP):
        self.lines, self.disp, self.step = lines, disp, step
        self.ix = R.index_dump(lines)
        self.tags = R.parse_client(client_lines)
        self.dw, self.dh = float(disp["w"]), float(disp["h"])
        self.H, self.W = int(math.ceil(self.dh / step)), int(math.ceil(self.dw / step))
        self.cover = np.zeros((self.H, self.W), dtype=bool)      # occluded cells
        self.stamp = np.full((self.H, self.W), -1, dtype=np.int32)  # last visitor
        self.N = {}                  # parsed nodes
        self.visits = {}             # INV-SINGLE-PASS
        self.kept = {}               # nid -> {"cells": n, "box": .., "free": ..}
        self.windows = []            # every top-level surface met
        self.untagged = []           # windows under half tagged
        self.state = {}
        self.order = 0

    # -- parsing on demand ---------------------------------------------------
    def node(self, nid):
        n = self.N.get(nid)
        if n is None:
            n = self.N[nid] = R.parse_node(self.lines, self.ix, nid)
            n["tag"] = self.tags.get(nid, "")
        return n

    # -- one cell set ----------------------------------------------------------
    def _cells(self, n, clip):
        r = n["abs"]
        if not r or r[2] <= 0 or r[3] <= 0 or clip is None:
            return None
        x0, y0 = max(r[0], clip[0]), max(r[1], clip[1])
        x1, y1 = min(r[0] + r[2], clip[2]), min(r[1] + r[3], clip[3])
        cx0, cx1 = _span(x0, x1, self.W, self.step)
        cy0, cy1 = _span(y0, y1, self.H, self.step)
        if cx1 <= cx0 or cy1 <= cy0:
            return None
        return (cy0, cy1, cx0, cx1, np.ones((cy1 - cy0, cx1 - cx0), dtype=bool))

    # -- labels (IF-MOTIF-VOCABULARY) ------------------------------------------
    def label(self, n, area):
        t = n["tag"]
        if t in PASS_THROUGH:
            return None
        a = area / (self.dw * self.dh)
        if t in VIDEO_TAGS:
            return "video:" + ("small" if a < SMALL else "medium" if a <= MEDIUM else "large")
        if t in TEXT_INPUT_TAGS:
            return "text_input"
        if t in ("Image", "SymbolGlyph"):
            d = self.disp.get("density", 3.5) or 3.5
            w, h = n["abs"][2] / d, n["abs"][3] / d
            if max(w, h) <= ICON_DP and ICON_AR[0] <= w / max(h, 1e-9) <= ICON_AR[1]:
                return "icon"
            return "thumb" if a < THUMB else "image_large"
        return t

    # -- the pass --------------------------------------------------------------
    def run(self):
        ldn = [i for i, k in self.ix["kind"].items() if k == "LOGICAL_DISPLAY_NODE"]
        if len(ldn) != 1:
            raise NoDisplay("%d LOGICAL_DISPLAY_NODE" % len(ldn))
        chain, q = [], self.ix["parent"].get(ldn[0])
        while q is not None:
            chain.append(q)
            q = self.ix["parent"].get(q)
        M, ea = R.IDENT, 1.0
        for a in reversed(chain):                 # RS_NODE, SCREEN_NODE
            n = self.node(a)
            M = R.affine(M, R.local_transform(n))
            ea *= n["alpha"]
        top = self.visit(ldn[0], M, ea, (0.0, 0.0, self.dw, self.dh), None, False, "")
        return self.row(top)

    def visit(self, nid, Mp, eap, clip, region, under_surface, ptag):
        self.visits[nid] = self.visits.get(nid, 0) + 1
        n = self.node(nid)
        ea = eap * n["alpha"]
        st = INVISIBLE if ea <= 0 else (OCCLUDING if ea >= 1 and n["bga"] == 0xFF else TRANSLUCENT)
        self.state[nid] = st
        if st == INVISIBLE:
            return None                            # the branch is never read
        window = None
        if n["kind"] == "SURFACE_NODE":
            if not under_surface:
                window = R.parse_surface(self.lines, self.ix, n)
                self.windows.append(window)
                if window["visible"] != 1 or window["alpha"] <= 0 or not window["vis"]:
                    return None                    # decided from its line alone
                region = np.zeros((self.H, self.W), dtype=bool)
                for r in window["vis"]:
                    x0, x1 = _span(r[0], r[0] + r[2], self.W, self.step)
                    y0, y1 = _span(r[1], r[1] + r[3], self.H, self.step)
                    region[y0:y1, x0:x1] = True
                vx0 = min(r[0] for r in window["vis"]); vy0 = min(r[1] for r in window["vis"])
                vx1 = max(r[0] + r[2] for r in window["vis"]); vy1 = max(r[1] + r[3] for r in window["vis"])
                clip = _meet(clip, (vx0, vy0, vx1, vy1))
            under_surface = True
        M = R.affine(Mp, R.local_transform(n))
        n["abs"] = R.place(n, M)

        # AT: the node's own visible cells
        c = self._cells(n, clip)
        free = None
        if c:
            y0, y1, x0, x1, mask = c
            if region is not None:
                mask = mask & region[y0:y1, x0:x1]
            free = mask & ~self.cover[y0:y1, x0:x1]
            if not free.any():
                free = None
            c = (y0, y1, x0, x1, mask)
        v = self.order
        self.order += 1
        if free is not None:
            y0, y1, x0, x1, _ = c
            self.stamp[y0:y1, x0:x1][free] = v

        # DOWN: the clip the children inherit
        cclip, descend = clip, bool(n["kids"])
        if n["clip"] and descend:
            b = n["abs"]
            cclip = _meet(clip, (b[0], b[1], b[0] + b[2], b[1] + b[3])) if b and b[2] > 0 and b[3] > 0 else None
            if cclip is None:
                descend = False
            else:
                x0, x1 = _span(cclip[0], cclip[2], self.W, self.step)
                y0, y1 = _span(cclip[1], cclip[3], self.H, self.step)
                if x1 <= x0 or y1 <= y0:
                    descend = False
                else:
                    open_ = ~self.cover[y0:y1, x0:x1]
                    if region is not None:
                        open_ &= region[y0:y1, x0:x1]
                    descend = bool(open_.any())     # wholly covered: dropped
        kids = []
        if descend:
            # sortedChildren is the paint order, last on top; ReverseChildren
            # is the prepare stage's traversal and is not read
            # (INV-OCCLUSION-PAINT-ORDER)
            ks = [k for k in n["kids"] if k in self.ix["first"]]
            for k in reversed(ks):                              # topmost first
                s = self.visit(k, M, ea, cclip, region, under_surface, n["tag"])
                if s is not None:
                    kids.append(s)

        # UP
        if free is None and not kids:
            if window is not None:            # visible, and nothing of it shows
                window.update(coverage=None, window=window["name"], nodes=0,
                              tagged=0, pid=window["pid"])
            return None
        self.kept[nid] = {"cells": int(free.sum()) if free is not None else 0,
                          "on": int(c[4].sum()) if c else 0,
                          "box": c[:4] if free is not None else None, "free": free}
        if st == OCCLUDING and c:
            y0, y1, x0, x1, mask = c
            self.cover[y0:y1, x0:x1] |= mask
        return self._up(n, nid, v, kids, window, free, ptag)

    # -- ALG-MOTIF-MATCH: the element set and the matches on the way up ---------
    def _branch_mask(self, v):
        return self.stamp >= v

    def _up(self, n, nid, v, kids, window, free, ptag):
        heads, headpairs, matches, dmin, pids = [], set(), [], {}, {}
        canvas = tagged = 0
        for s in kids:
            heads.extend(s["heads"])
            headpairs |= s["pairs"]
            matches.extend(s["matches"])
            for p, d in s["dmin"].items():
                dmin[p] = min(d, dmin.get(p, 1.0))
            canvas += s["canvas"]
            tagged += s["tagged"]
            for p, k in s["pids"].items():
                pids[p] = pids.get(p, 0) + k
        if n["kind"] == "CANVAS_NODE":
            canvas += 1
            tagged += 1 if n["tag"] else 0
            pids[n["pid"]] = pids.get(n["pid"], 0) + 1

        mask = None
        if n["tag"] in ("Image", "SymbolGlyph"):
            area = float(free.sum()) * self.step ** 2 if free is not None else 0.0
        elif n["tag"] in VIDEO_TAGS:
            mask = self._branch_mask(v)
            area = float(mask.sum()) * self.step ** 2
        else:
            area = 0.0
        L = self.label(n, area)
        if L is None:
            out_heads, out_pairs = heads, headpairs           # pass-through
        else:
            items = ((L in REPEATING and len(heads) >= 2)
                     or (L in SINGLE_CHILD and len(heads) == 1))
            count = {}
            for h, _ in heads:
                h = "item" if items else h        # whatever each item is
                count[h] = count.get(h, 0) + 1
            pairs = {"%s/%s%s" % (L, h, "*" if k >= 2 else "") for h, k in count.items()}
            S0 = {L}
            S1 = S0 | pairs
            S2 = S1 | headpairs
            presented = L
            for name, root, shape, depth in BY_ROOT.get(L, ()):
                if name == "carousel" and ptag == "Tabs":
                    continue                  # the Tabs' own Swiper
                d = jaccard_distance((S0, S1, S2)[depth], shape)
                dmin[name] = min(d, dmin.get(name, 1.0))
                if d <= FOUND and not any(m["node"] == nid and m["pattern"] == name for m in matches):
                    if mask is None:
                        mask = self._branch_mask(v)
                    # outermost: an inner match of the same pattern is dropped;
                    # composition: the token children it is made of are consumed
                    used = {hid for h, hid in heads if h in COMPOSED[name]}
                    matches = [m for m in matches if m["pattern"] != name
                               and not (m["node"] in used and m["pattern"] in COMPOSED[name])]
                    matches.append({"pattern": name, "node": nid, "d": d,
                                    "mask": mask.copy()})
                    if name in TOKENS:
                        presented = name
            out_heads, out_pairs = [(presented, nid)], pairs
        if window is not None:
            cov = tagged / canvas if canvas else None
            # the process whose nodes fill the window, not the system UI
            # that hosts every window surface
            window.update(coverage=cov, window=window["name"], nodes=canvas,
                          tagged=tagged, pid=max(pids, key=pids.get) if pids else window["pid"])
            if cov is not None and cov < R.UNTAGGED_BELOW:
                # ERR-MOTIF-ROOT-UNTAGGED: no structure can be read here
                self.untagged.append((window["name"], cov))
                matches, dmin = [], {}
        return {"heads": out_heads, "pairs": out_pairs, "matches": matches,
                "dmin": dmin,
                "canvas": canvas, "tagged": tagged, "pids": pids}

    # -- the row ---------------------------------------------------------------
    def row(self, top):
        cell = float(self.step * self.step)
        tot = self.dw * self.dh
        out = {}
        matches = top["matches"] if top else []
        dmin = top["dmin"] if top else {}
        for p in PATTERNS:
            ms = [m for m in matches if m["pattern"] == p]
            u = np.zeros((self.H, self.W), dtype=bool)
            for m in ms:
                u |= m["mask"]
            out[p + "_d"] = round(dmin.get(p, 1.0), 4)
            out[p + "_share"] = round(float(u.sum()) * cell / tot, 4)
            out[p + "_n"] = round(len(ms) / (len(ms) + 1.0), 4)
        out["untagged_windows"] = len(self.untagged)
        # windows whose structure could be read: on the display, with kept
        # canvas nodes, at least half of them tagged
        out["read_windows"] = sum(1 for w in self.windows if w.get("coverage") is not None
                                  and w["coverage"] >= R.UNTAGGED_BELOW)
        self.matches = matches
        return out


def _meet(a, b):
    if a is None or b is None:
        return None
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None
