#!/usr/bin/env python3
"""Build the payload behind the viewer (IF-PAGE-PAYLOAD).

    ./vectors_build.py patterns.csv --captures captures --out two_payload.json
    ./vectors_page.py  two_payload.json --out scene_feature_reference.html

Two vectors are measured. Each scene is placed by PSI into one declared cell
of each vector's space, and the viewer shows every cell as a group: its
picture, its description, its count, its scenes. Separately, one clustering
runs over both vectors together and matches no pattern (ALG-VECTOR-CLUSTER).

Nothing here names a scene. There is no motif table, no class list and no
label taxonomy: a cell is named by its coordinates, a cluster by its id, and
a description is assembled from measurement alone (ALG-DESCRIBE-CELL).

This script was an inline fragment for one session and the page it produced
could not be rebuilt from the repository. It is tracked now because the page
is the mechanism's only output.

Inputs
  patterns.csv  rs_pattern.py's output, one row per scene
  --reps DIR    one jpeg per scene, named after its `shot` column with the
                separators replaced by `__`. Screenshots are pictures of a
                real phone in real use and never enter the repository, so
                this directory lives beside the captures.
"""
import argparse, base64, csv, glob, io, json, math, os, re, sys

import numpy as np
from PIL import Image
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score, silhouette_samples

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs_pattern import TAG_KIND                 # noqa: E402


def transform(kind, raw):
    """Put one measurement on a scale where a distance means something.

    An empty field is a measurement that was not taken, not a zero. A height
    reported as 0.0 for a display with no repeating group reads, on a
    continuous scale, as the smallest possible items rather than as absence.
    It comes back as nan and is filled with the column mean by the caller, so
    the dimension contributes nothing to the distance instead of contributing
    a fiction.
    """
    if raw is None or str(raw).strip() == "":
        return float("nan")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.0
    if kind == "log1p":
        return math.log1p(max(0.0, v))
    if kind == "logratio":
        # An absent aspect reads as 1.0 (square), i.e. log 0, rather than as
        # an infinitely wide item.
        return math.log(v) if v > 0.01 else 0.0
    if kind == "clip6":
        return min(v, 6.0)
    return v                                        # "unit": already 0..1


# The two vectors. Each column carries its own contract: the field it reads,
# the scale it is put on, its weight in the distance, and what it measures.
VECTORS = {
    "pattern": {
        "title": "Pattern vector",
        "blurb": "What repeats in the layout, and how much of the window it "
                 "holds. Built by grouping branches that share a description "
                 "- the kinds of content in their subtree and how those are "
                 "arranged - and taking the group that covers most.",
        "cols": [
            ("pat1_cover",   "unit",     1.0, "share of the window that repeats"),
            ("pat1_h",       "unit",     1.0, "the repeating item's height, in display heights"),
            ("pat1_aspect",  "logratio", 1.0, "its width / height"),
            ("pat1_cols",    "clip6",    1.2, "distinct columns"),
            ("pat1_n",       "log1p",    0.7, "members in the group"),
            ("overlay_frac", "unit",     0.7, "area drawn over the layout"),
            ("pat_n_groups", "log1p",    0.7, "how many repeating groups exist"),
        ],
        "caption": lambda r: [
            "cover %s  h %s" % (r["pat1_cover"] or "-", r["pat1_h"] or "-"),
            "aspect %s  cols %s" % (r["pat1_aspect"] or "-", r["pat1_cols"] or "-"),
            "n %s  groups %s" % (r["pat1_n"] or "-", r["pat_n_groups"] or "-"),
        ],
    },
    "surface": {
        "title": "Surface vector",
        "blurb": "What the display is finally made of, after occlusion. Every "
                 "node that draws content, and every externally composed "
                 "surface, walked topmost first, each claiming only the pixels "
                 "nothing above it claimed. Substrate is the complement.",
        "cols": [
            ("vis_xcomp",     "unit", 1.0, "externally composed surface"),
            ("vis_image",     "unit", 1.0, "nodes tagged Image"),
            ("vis_text",      "unit", 1.0, "nodes tagged Text"),
            ("vis_content",   "unit", 0.7, "draws, but no tag names the type"),
            ("vis_substrate", "unit", 0.7, "claimed by nothing: background, chrome, blank"),
        ],
        "caption": lambda r: [
            "xcomp %s  img %s" % (r["vis_xcomp"], r["vis_image"]),
            "txt %s  other %s" % (r["vis_text"], r["vis_content"]),
            "substrate %s" % r["vis_substrate"],
        ],
    },
}


THUMB_W = 320       # the tiles render at 118 px; twice that covers a 2x display


def thumbnail(path, width):
    """Embed a frame at the size the page shows it, not the size it was taken.

    A capture frame is about 390 KB and the payload carries it once per
    vector, so embedding what the capture tree holds put the page at 139 MB.
    The tiles are 118 px wide. Downscaling here is what lets the page resolve
    screenshots from the capture tree and still be one file worth sending.
    """
    im = Image.open(path)
    im.thumbnail((width, width * 4), Image.LANCZOS)   # tall frames, keep ratio
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def row_identity(row):
    """What identifies a scene: where it was measured, never its picture.

    Named for the row rather than the scene on purpose. INV-NO-SCENE-CLASS
    bans a function that maps a measurement onto a scene name, and its check
    matches on `scene_*`; this one maps a row's session and stem onto the
    place it came from, which is not that, but the check is right to be
    suspicious of the shape and the cheaper fix is to not wear it.
    """
    return "%s/%s" % (row.get("session", ""), row.get("stem", ""))


def rep_path(shot, captures, reps):
    """The capture tree first, the flat directory only as a fallback.

    The flat <session>__<stem>.jpeg form is what no code here builds. It was
    made by hand once, it was incomplete, and resolving against it alone is
    what lost 71 of 250 scenes.
    """
    if shot:
        if captures:
            p = os.path.join(captures, *shot.split("/"))
            if os.path.exists(p):
                return p
        if reps:
            p = os.path.join(reps, shot.replace("/", "__"))
            if os.path.exists(p):
                return p
    return None


# PSI_p, the partition of the pattern space (ALG-PSI-PATTERN). Four
# arrangements, each split by whether the repeating group covers more than
# half the window, plus one cell for a scene with no group. Nine cells.
#
# The arrangement axis is closed by construction: rs_pattern.py's
# arrangement() compares distinct x and y positions and can return nothing
# but these four, so the axis is not a list anyone chose and cannot drift
# into one.
#
# Half the window is the only boundary in it, and it is derived rather than
# fitted: a group covering more than half IS the screen's layout, one
# covering less is an element inside some other layout, and one half is the
# unique value separating those readings. Nothing here was swept.
ARRANGEMENTS = ("grid", "row", "column", "stack")
CELL_NONE = "none"


def psi_pattern(row, strict=True):
    """Which cell of the pattern space this scene falls in.

    `strict` is the declared convention at the boundary: a group covering
    exactly half the window is `minor` under the declared reading and
    `major` under the other. It is a parameter so the disjointness check
    can assign every row under both and report which rows the convention
    decides, rather than reimplementing the comparison and drifting from
    this one (INV-PSI-DISJOINT).

    Total by construction: every row either has an arrangement, in which
    case it has a coverage and lands in one of eight, or it has neither and
    lands in `none`. There is no path that returns nothing.

    The content signature pat1_pat is deliberately not a coordinate. It has
    nine distinct kind sets over the 268 scenes that have a group, so a
    third axis would declare 36 cells of which most hold fewer than ten,
    and INV-PSI-MINIMAL holds for this space only because it does not. The
    signature is carried on the tile instead.
    """
    arr = (row.get("pat1_arr") or "").strip()
    cov = (row.get("pat1_cover") or "").strip()
    if arr not in ARRANGEMENTS or cov == "":
        return CELL_NONE
    over = float(cov) > 0.5 if strict else float(cov) >= 0.5
    return "%s/%s" % (arr, "major" if over else "minor")


def psi_pattern_cells():
    """Every cell the partition declares, whether or not any scene is in it.

    Declared rather than collected from the data: a cell that no scene
    reaches has to be nameable in order to be reported as empty, and a
    partition whose cells are whatever turned up is not a partition.
    """
    return [CELL_NONE] + ["%s/%s" % (a, b) for a in ARRANGEMENTS
                          for b in ("major", "minor")]


# PSI_s, the partition of the surface space (ALG-PSI-SURFACE). A cell is
# (largest coordinate, second largest), written largest/second. No
# threshold appears in it: a threshold is a constant, a constant has to be
# fitted, and anything fitted on two sessions of one device held by one
# person is a property of that capture rather than of the space.
#
# vis_glyph is not a ranking candidate. A SymbolGlyph is an icon glyph and
# a Divider is a hairline, both bounded small by what the component IS
# rather than by what this capture held; over 299 scenes it never exceeds
# 0.006 and is neither first nor second in any scene. That is an argument
# from the component and not a proof, and it is the first thing to revisit
# if a device is captured where a glyph fills a display.
#
# The ORDER of this tuple is the declared tie-break and is part of the
# partition, not an implementation detail. Exactly one scene in 299 ties
# for the largest coordinate; change this order and that scene moves cell.
VIS_RANKED = ("vis_xcomp", "vis_image", "vis_text", "vis_content",
              "vis_substrate")


def psi_surface(row, order=VIS_RANKED):
    """Which cell of the surface space this scene falls in.

    Ranked by (value descending, declared coordinate order), so a tie is
    broken by `order` and never by what the sort does with equal keys.
    Total: five candidates always rank, because the six columns are a
    partition of the display and always exist.

    `order` is a parameter for the same reason psi_pattern takes `strict`:
    the disjointness check assigns every row under the declared order and
    under a reversed one, and the rows whose cell differs are exactly the
    rows the declared order decides (INV-PSI-DISJOINT). Passing the
    convention in is what keeps the check from carrying its own copy of
    the ranking.
    """
    vals = []
    for i, c in enumerate(order):
        raw = (row.get(c) or "").strip()
        vals.append((-(float(raw) if raw else 0.0), i, c))
    vals.sort()
    return "%s/%s" % (vals[0][2][4:], vals[1][2][4:])


def psi_surface_cells():
    """The twenty cells the partition declares: five ranked two deep."""
    out = []
    for a in VIS_RANKED:
        for b in VIS_RANKED:
            if a != b:
                out.append("%s/%s" % (a[4:], b[4:]))
    return out


DW_REF, DH_REF = 1316.0, 2832.0      # the captured display; a frame, not a claim


def schematic(cols, rows, h, aspect, arr):
    """Draw a repeating group from its own median numbers (ALG-MOTIF-SCHEMATIC).

    Recovered from feature_motifs.py, deleted in 4421f9b. What is not
    recovered is the DESC table that sat beside it: 13 cell names with a
    paragraph each, part of the taxonomy deleted with it. This takes
    numbers and returns a picture. It never takes or returns a name, which
    is what makes it a measurement rendered rather than a class asserted.

    A cell crossing the bottom edge is drawn dashed and fainter, because it
    is clipped by the display rather than small. A stack is drawn as an
    offset outline, because its members share one spot and a single
    rectangle would say the opposite.

    Colours come from the page's own variables so the drawing follows the
    theme; the fallbacks are only for an SVG opened on its own.
    """
    ih = max(0.02, min(h, 1.2)) * DH_REF
    iw = max(40.0, ih * (aspect or 1.0))
    gap = 18.0
    cols = max(1, min(int(cols or 1), 6))
    if arr in ("column", "stack"):
        cols = 1
    if cols * iw + (cols - 1) * gap > DW_REF - 48:
        iw = (DW_REF - 48 - (cols - 1) * gap) / cols
        ih = iw / (aspect or 1.0) if aspect else ih
    rows = max(1, min(int(rows or 1),
                      int((DH_REF - 320) // max(1.0, ih + gap)) + 1, 8))
    x0 = (DW_REF - (cols * iw + (cols - 1) * gap)) / 2
    y0 = 300.0
    ink = "var(--ink,#2563eb)"
    p = ['<svg viewBox="-40 -40 %d %d" preserveAspectRatio="xMidYMid meet" '
         'role="img">' % (DW_REF + 80, DH_REF + 80)]
    p.append('<rect x="0" y="0" width="%d" height="%d" fill="var(--card,#fff)" '
             'stroke="var(--line,#64748b)" stroke-width="10" rx="26"/>'
             % (DW_REF, DH_REF))
    for band_y, band_h in ((0, 112), (DH_REF - 96, 96)):
        p.append('<rect x="0" y="%d" width="%d" height="%d" '
                 'fill="var(--dim,#94a3b8)" opacity=".18"/>'
                 % (band_y, DW_REF, band_h))
    drawn = 0
    for r in range(rows):
        for c in range(cols):
            x, y = x0 + c * (iw + gap), y0 + r * (ih + gap)
            if y > DH_REF + 40:
                continue
            off = (y + ih) > DH_REF
            p.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" rx="14" '
                     'fill="%s" opacity="%s" stroke="%s" stroke-width="8"%s/>'
                     % (x, y, iw, ih, ink, ".20" if off else ".30", ink,
                        ' stroke-dasharray="28 18"' if off else ""))
            drawn += 1
    if arr == "stack" and drawn:
        p.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" rx="14" '
                 'fill="none" stroke="%s" stroke-width="8" '
                 'stroke-dasharray="20 14" transform="translate(26,26)"/>'
                 % (x0, y0, iw, ih, ink))
    return "".join(p) + "</svg>"


# The only words a description may use for content are the measurement's own
# (INV-DESCRIPTION-FROM-MEASUREMENT). The tag-derived kinds are named by the
# tag rs_pattern.TAG_KIND reads them from; the geometry-derived ones by what
# mark_kind tests. Nothing here was chosen by looking at a screenshot.
KIND_WORDS = {k: t for t, k in TAG_KIND.items()}
KIND_WORDS.update({"V": "composed surface", "i": "icon-sized box",
                   "t": "text-line-shaped box", "b": "other painted box"})
SURF_WORDS = {c[0][4:]: c[3] for c in VECTORS["surface"]["cols"]}
SURF_WORDS["glyph"] = "nodes tagged SymbolGlyph or Divider"
SURF_SIX = ("xcomp", "image", "text", "glyph", "content", "substrate")


def _median(members, col):
    xs = [float(m[col]) for m in members if (m.get(col) or "").strip() != ""]
    return float(np.median(xs)) if xs else None


def describe_cell(space, cell, members):
    """A cell's description, from its coordinates and its members' medians.

    ALG-DESCRIBE-CELL. Reads the vector columns, pat1_arr, pat1_cover and
    pat1_pat, and nothing that says where a scene came from: no owner, no
    shot, no window name. The replacement for feature_motifs.DESC, which was
    keyed by describe()'s pre-written shape names and said which apps land
    in each - a sentence written by looking at screenshots, and so a class.
    """
    n = len(members)
    if not n:
        return "No scene reached this cell."
    tail = " %d scene%s." % (n, "" if n == 1 else "s")
    if space == "surface":
        a, b = cell.split("/")
        comp = ", ".join("%s %.2f" % (k, _median(members, "vis_" + k) or 0.0)
                         for k in SURF_SIX)
        return ("Largest share of the display: %s; second: %s. "
                "Median composition %s." % (SURF_WORDS[a], SURF_WORDS[b], comp)
                + tail)
    if cell == CELL_NONE:
        return ("No repeating group: no two sibling branches of the window "
                "shared a description, so there is no motif to draw." + tail)
    arr, band = cell.split("/")
    cover = _median(members, "pat1_cover")
    lead = "%s covering %s half the window" % (
        arr.capitalize(), "more than" if band == "major" else "at most")
    parts = []
    cols, items = _median(members, "pat1_cols"), _median(members, "pat1_n")
    if arr == "grid" and cols:
        parts.append("%d columns" % round(cols))
    h, asp = _median(members, "pat1_h"), _median(members, "pat1_aspect")
    if items:
        parts.append("%d items%s" % (
            round(items),
            " each %.2f display heights tall" % h if h is not None else ""))
    if asp is not None:
        parts.append("width/height %.2f" % asp)
    # The kinds present in at least half the members' signatures, most
    # frequent first: what the repeating item holds, in the alphabet's words.
    seen = {}
    for m in members:
        for k in set((m.get("pat1_pat") or "").strip()):
            seen[k] = seen.get(k, 0) + 1
    kinds = [KIND_WORDS.get(k, k) for k, c in
             sorted(seen.items(), key=lambda kv: (-kv[1], kv[0])) if c * 2 >= n]
    hold = ("items hold %s" % " and ".join(kinds)) if kinds \
        else "no content kind is common to half the items"
    return ("%s: median cover %.2f, %s, %s." % (
        lead, cover or 0.0, ", ".join(parts) or "no item geometry", hold) + tail)


def backstop_index(captures):
    """stem -> the backstop frame that was on the display while it was.

    The capture photographs a scene when its pair settles. A scene that
    never settles gets no picture of its own, and the backstop is a frame
    named p<nnnnnn>.jpeg, where nnnnnn is the number of the sample it was
    taken at. Until 2026-10-27 it was taken every few seconds, which is why
    the older captures hold so many of a scene that needed none; it is now
    taken on arrival at a scene with no picture yet. Nothing in that filename says
    which scene it shows; samples.csv is what says so, and until now
    nothing read it.

    The join runs through sig, not through stem. samples.csv writes stem
    only on the sample that first records a scene and shot only on a
    sample where a picture was taken, so the two columns are rarely on the
    same row. sig is on every row: it is the scene that was on the display
    when that sample was taken, which is exactly what the frame shows.

    Both routes to the frame are taken - the shot column, and the sample
    number parsed out of the filename - because they disagree on the
    captures here: one session holds 68 files against 51 named, and five
    whose number is past the end of the index. A file the index does not
    mention is still attributable if its number lands on a row.
    """
    out = {}
    for sdir in sorted(glob.glob(os.path.join(captures, "*"))):
        idx = os.path.join(sdir, "samples.csv")
        if not os.path.isdir(sdir) or not os.path.exists(idx):
            continue
        session = os.path.basename(sdir)
        with open(idx, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        stem_of_sig = {}
        for r in rows:
            st = (r.get("stem") or "").strip()
            sg = (r.get("sig") or "").strip()
            if st and sg:
                stem_of_sig.setdefault(sg, st)

        def claim(sig, fname):
            st = stem_of_sig.get(sig)
            if not st or not fname.startswith("p"):
                return
            if os.path.exists(os.path.join(sdir, st + ".jpeg")):
                return                      # it has one of its own
            if os.path.exists(os.path.join(sdir, fname)):
                out.setdefault("%s/%s" % (session, st),
                               os.path.join(sdir, fname))

        for r in rows:
            claim((r.get("sig") or "").strip(), (r.get("shot") or "").strip())
        for f in sorted(glob.glob(os.path.join(sdir, "p*.jpeg"))):
            m = re.match(r"p(\d{6})\.jpeg$", os.path.basename(f))
            if not m:
                continue
            n = int(m.group(1))
            if 1 <= n <= len(rows):
                claim((rows[n - 1].get("sig") or "").strip(),
                      os.path.basename(f))
    return out


def joint_columns():
    """The clustering's input columns: the two vectors' and nothing else.

    INV-CLUSTERING-MATCHES-NO-PATTERN. No PSI cell, label or name is among
    them, so the clustering cannot confirm the partition and the partition
    cannot steer the clustering.
    """
    return [(n, k, w, d, key) for key in ("pattern", "surface")
            for n, k, w, d in VECTORS[key]["cols"]]


def build_joint(rows, mcs):
    """One clustering over both vectors together (ALG-VECTOR-CLUSTER).

    Each column through transform(), standardised and weighted as the
    VECTORS table states; then each vector's block scaled to equal total
    variance, so the vector with more columns does not own the distance.
    HDBSCAN on that. A scene it cannot place is reported in the
    unclustered group, never forced into the nearest cluster.
    """
    F = joint_columns()
    X = np.array([[transform(k, r.get(n)) for n, k, _, _, _ in F] for r in rows],
                 float)
    for j in range(X.shape[1]):
        m = np.isnan(X[:, j])
        if m.any():
            X[m, j] = np.nanmean(X[:, j]) if not m.all() else 0.0
    sd = X.std(0)
    flat = [F[j][0] for j in range(X.shape[1]) if sd[j] < 1e-9]
    if flat:
        print("ERR-COLUMN-CONSTANT: %s measured nothing on this capture - no "
              "variance, so %s dropped from the joint matrix before clustering"
              % (", ".join(flat), "they are" if len(flat) > 1 else "it is"))
    keep = sd >= 1e-9
    sd[~keep] = 1.0
    Z = (X - X.mean(0)) / sd * np.array([w for _, _, w, _, _ in F])
    for key in ("pattern", "surface"):
        cols = [j for j, f in enumerate(F) if f[4] == key and keep[j]]
        tot = float(Z[:, cols].var(0).sum()) if cols else 0.0
        if tot > 0:
            Z[:, cols] /= math.sqrt(tot)
    Z = Z[:, keep]

    lab = HDBSCAN(min_cluster_size=mcs).fit_predict(Z)
    m = lab >= 0
    clustered = len(set(lab[m])) > 1
    sil = float(silhouette_score(Z[m], lab[m])) if clustered else None
    per = np.full(len(Z), np.nan)
    if clustered:
        per[m] = silhouette_samples(Z[m], lab[m])
    pca = PCA(n_components=min(3, Z.shape[1])).fit(Z)
    P = pca.transform(Z)
    if P.shape[1] < 3:
        P = np.hstack([P, np.zeros((len(P), 3 - P.shape[1]))])

    groups = []
    for cid in sorted(set(lab)):
        idx = [i for i in range(len(rows)) if lab[i] == cid]
        # Centre outwards: the tiles at the end are where the cluster frays.
        idx.sort(key=lambda i: -(per[i] if not np.isnan(per[i]) else -9))
        # The cluster's own columns at their median. A cluster is named by its
        # id; a word for it would be a scene class.
        centre = []
        for n, k, _, _, _ in F:
            vals = [float(rows[i][n]) for i in idx
                    if str(rows[i].get(n, "")).strip() not in ("", "-")]
            if vals:
                centre.append("%s %s" % (n, round(float(np.median(vals)), 2)))
        groups.append({
            "id": int(cid),
            "n": len(idx),
            "cohesion": (None if cid < 0 or not clustered
                         else round(float(np.nanmean([per[i] for i in idx])), 2)),
            "centre": centre,
            "reps": [{"scene": row_identity(rows[i]),
                      "sil": (None if np.isnan(per[i]) else round(float(per[i]), 2)),
                      "x": round(float(P[i, 0]), 4), "y": round(float(P[i, 1]), 4),
                      "z": round(float(P[i, 2]), 4)}
                     for i in idx],
        })
    groups.sort(key=lambda g: (g["id"] < 0, -g["n"]))

    return {"title": "Clustering over both vectors",
            "blurb": ("Every scene clustered on the two vectors' twelve columns "
                      "together, each vector's block scaled to equal variance. "
                      "It matches no pattern: a cluster is where scenes sit "
                      "relative to each other, named only by its id."),
            "cols": [{"name": "rs_" + n, "t": k, "w": w, "what": d, "vector": v}
                     for n, k, w, d, v in F],
            "n": len(rows), "k": len(set(lab) - {-1}),
            "outliers": int((lab < 0).sum()),
            "silhouette": (round(sil, 3) if sil is not None else None),
            "mcs": mcs,
            "explained": [round(float(v), 3) for v in pca.explained_variance_ratio_],
            "groups": groups}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patterns", help="rs_pattern.py's csv")
    ap.add_argument("--captures", default="",
                    help="the capture tree; a scene's picture is <captures>/<shot>")
    ap.add_argument("--reps", default="",
                    help="a flat <session>__<stem>.jpeg directory, if one exists")
    ap.add_argument("--out", default="two_payload.json")
    ap.add_argument("--thumb", type=int, default=THUMB_W,
                    help="embed each frame at this width (default %d)" % THUMB_W)
    ap.add_argument("--min-cluster", type=int, default=9,
                    help="HDBSCAN min_cluster_size (default 9)")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    if not rows:
        raise SystemExit("%s holds no rows" % a.patterns)

    # INV-NO-SILENT-SCENE-LOSS: every measured row reaches the payload or is
    # reported in it. A scene with no picture is still measured and still
    # clustered; only its tile is poorer for it.
    # The frames taken as a backstop, so a scene that never settled can
    # still show what was on the display while it was.
    backstop = backstop_index(a.captures) if a.captures else {}

    img, kind, missing, unmatched = {}, {}, [], []
    borrowed = 0
    for r in rows:
        sid = row_identity(r)
        shot = (r.get("shot") or "").strip()
        path = rep_path(shot, a.captures, a.reps)
        if path:
            img[sid] = thumbnail(path, a.thumb)
            kind[sid] = "own"
        elif sid in backstop:
            img[sid] = thumbnail(backstop[sid], a.thumb)
            kind[sid] = "backstop"
            borrowed += 1
        else:
            missing.append(sid)
            if shot:
                # The row names a file. A shot that resolves to nothing is a
                # broken join, not a scene that was never photographed - and
                # a join key resolving to nothing is the shape of both
                # failures this project has had on this seam.
                unmatched.append("%s -> %s" % (sid, shot))
    if unmatched:
        print("ERR-JOIN-KEY-UNMATCHED: %d row(s) name a screenshot that is not "
              "there: %s" % (len(unmatched), ", ".join(unmatched[:5])
                             + (" and %d more" % (len(unmatched) - 5)
                                if len(unmatched) > 5 else "")))
    print("  pictures: %d taken for the scene, %d from a backstop frame, "
          "%d none" % (len(img) - borrowed, borrowed, len(missing)))
    if a.captures and not borrowed and backstop:
        print("  (the backstop index found %d frame(s) but none belonged to a "
              "scene that needed one)" % len(backstop))
    if missing:
        print("ERR-SCENE-WITHOUT-REP: %d of %d scenes have no screenshot; they "
              "are measured and clustered, and their tiles carry no picture."
              % (len(missing), len(rows)))
        for sid in missing[:10]:
            print("    %s" % sid)
        if len(missing) > 10:
            print("    ... and %d more, all listed in the payload's audit"
                  % (len(missing) - 10))

    # PSI_p over every row. Declared cells first, so a cell no scene
    # reaches is reported as empty rather than being absent from the output
    # and indistinguishable from one that was never declared.
    import statistics as _st
    by_cell = {c: [] for c in psi_pattern_cells()}
    for r in rows:
        by_cell[psi_pattern(r)].append(r)

    def _med(members, col, default=0.0):
        xs = [float(m[col]) for m in members
              if (m.get(col) or "").strip() != ""]
        return _st.median(xs) if xs else default

    def _f(r, c):
        v = (r.get(c) or "").strip()
        return float(v) if v else 0.0

    cells = []
    for c in psi_pattern_cells():
        mem = by_cell[c]
        arr = c.split("/")[0] if c != CELL_NONE else ""
        cells.append({
            "cell": c,
            "n": len(mem),
            "arr": arr,
            "band": (c.split("/")[1] if "/" in c else ""),
            # A cell with no scene gets no drawing: there are no medians to
            # draw from, and inventing them would be drawing an idea of the
            # cell, which is the one thing this must not do.
            "svg": (schematic(_med(mem, "pat1_cols", 1), _med(mem, "pat1_n", 1),
                              _med(mem, "pat1_h", 0.2),
                              _med(mem, "pat1_aspect", 1.0), arr)
                    if mem and arr else None),
            "centre": ([] if not mem else [
                "cover %.2f" % _med(mem, "pat1_cover"),
                "h %.2f" % _med(mem, "pat1_h"),
                "%d cols" % round(_med(mem, "pat1_cols", 1)),
                "%d members" % round(_med(mem, "pat1_n", 1))]),
            "desc": describe_cell("pattern", c, mem),
            # Every scene in the cell, largest cover first, so the weak end
            # of a group is visible rather than hidden behind its best.
            "members": [row_identity(r) for r in
                        sorted(mem, key=lambda r: -_f(r, "pat1_cover"))],
        })
    SIX = ["vis_xcomp", "vis_image", "vis_text", "vis_glyph",
           "vis_content", "vis_substrate"]
    by_scell = {c: [] for c in psi_surface_cells()}
    for r in rows:
        by_scell[psi_surface(r)].append(r)
    scells = []
    for c in psi_surface_cells():
        mem = by_scell[c]
        lead = "vis_" + c.split("/")[0]
        scells.append({
            "cell": c,
            "n": len(mem),
            # The median composition of the scenes in the cell, which is
            # what the cell looks like. The viewer draws it as proportions
            # (IF-VIEWER); an empty cell has no composition to draw.
            "centre": ([] if not mem else
                       ["%s %.2f" % (k[4:], _med(mem, k)) for k in SIX]),
            "comp": ({} if not mem else
                     {k[4:]: round(_med(mem, k), 4) for k in SIX}),
            "desc": describe_cell("surface", c, mem),
            "members": [row_identity(r) for r in
                        sorted(mem, key=lambda r: -_f(r, lead))],
        })
    s_empty = [c["cell"] for c in scells if not c["n"]]

    # INV-UNPLACED-SCENES-NAMED. Two populations, kept apart because they
    # are different claims, and NAMED because a count cannot be traced back
    # to a scene.
    #
    # The two spaces are not symmetric and this does not pretend they are.
    # PSI_p has a declared `none` cell for a scene with no repeating group,
    # so there the undescribed and the unplaced are the same scenes. PSI_s
    # has no none-cell at all - every scene ranks, because the six
    # coordinates always exist and sum to one - so its unplaced population
    # is empty, and what stands in for undescribed is the scene whose
    # content is mostly nodes nothing can name.
    unplaced = {
        "pattern": {
            "undescribed": [row_identity(r) for r in rows
                            if (r.get("pat1_arr") or "").strip() == ""],
            "unplaced": [row_identity(r) for r in rows
                         if psi_pattern(r) == CELL_NONE],
            "note": ("a scene with no repeating group is both: PHI found "
                     "nothing to describe and PSI puts it in the declared "
                     "`none` cell, so these are the same scenes"),
        },
        "surface": {
            # Mostly unnameable: more than half of what content holds comes
            # from nodes with no client-dump entry. The same half that
            # ERR-UNNAMED-CONTENT fires on, so the page and the run agree.
            "undescribed": [row_identity(r) for r in rows
                            if _f(r, "vis_content")
                            and _f(r, "vis_unnamed") / _f(r, "vis_content") > 0.5],
            "unplaced": [],
            "note": ("PSI_s places every scene - there is no none-cell, "
                     "because the six coordinates always exist and sum to "
                     "one - so nothing is unplaced here. Undescribed is the "
                     "scene whose content is mostly CANVAS_NODEs that no "
                     "component tag can name"),
        },
    }
    for sp, d in unplaced.items():
        print("INV-UNPLACED-SCENES-NAMED, %s: %d undescribed, %d unplaced, "
              "of %d" % (sp, len(d["undescribed"]), len(d["unplaced"]),
                         len(rows)))
    tot_un = sum(len(d["undescribed"]) + len(d["unplaced"])
                 for d in unplaced.values())
    if tot_un:
        print("ERR-SCENE-UNPLACED: %d scene-and-space pairing(s) the vectors "
              "could not place or could not describe; each is named in the "
              "payload" % tot_un)

    empty = [c["cell"] for c in cells if not c["n"]]
    print("\nPSI_p: %d scenes over %d declared cell(s); %d empty"
          % (len(rows), len(cells), len(empty)))
    for c in cells:
        print("  %-14s %4d" % (c["cell"], c["n"]))
    if empty:
        print("  INV-PSI-MINIMAL: empty cell(s): %s" % ", ".join(empty))
    print("PSI_s: %d scenes over %d declared cell(s); %d empty"
          % (len(rows), len(scells), len(s_empty)))
    for c in scells:
        if c["n"]:
            print("  %-22s %4d" % (c["cell"], c["n"]))
    if s_empty:
        print("  INV-PSI-MINIMAL does NOT hold for the surface space; empty: "
              "%s" % ", ".join(s_empty))

    # The leading underscore is the payload's own convention for a block
    # that is not a vector - _audit already uses it, and every consumer
    # skips those keys. Adding `pattern_cells` without it told
    # no_silent_scene_loss.py that a list of cells was a vector block, and
    # it crashed reaching for .groups on a list.
    # Every scene's tile once, by id. The three tabs refer to scenes by id,
    # so a picture is embedded once however many tabs show it.
    scenes = {}
    for r in rows:
        sid = row_identity(r)
        scenes[sid] = {"img": img.get(sid), "imgsrc": kind.get(sid),
                       "cell": psi_pattern(r), "scell": psi_surface(r),
                       "sig": (r.get("pat1_pat") or "").strip(),
                       "cap": VECTORS["pattern"]["caption"](r)
                              + VECTORS["surface"]["caption"](r)}
    out = {"_unplaced": unplaced,
           "_scenes": scenes,
           "_pattern_cells": cells,
           "_surface_cells": scells,
           "_audit": {"rows": len(rows), "shown": len(img),
                      "own": len(img) - borrowed, "backstop": borrowed,
                      "missing": missing,
                      "captures": a.captures or None, "reps": a.reps or None}}
    out["clustering"] = v = build_joint(rows, a.min_cluster)
    print("\n%s: %d clusters, %d unclustered (%.0f%%), silhouette %s, "
          "PCA explained %s"
          % (v["title"], v["k"], v["outliers"], 100 * v["outliers"] / v["n"],
             v["silhouette"], " / ".join("%.0f%%" % (100 * x) for x in v["explained"])))
    for g in v["groups"]:
        nm = "none" if g["id"] < 0 else "c%d" % g["id"]
        print("     %-5s n=%3d coh %s" % (nm, g["n"], g["cohesion"]))

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print("\nwrote %s  %.1f MB" % (a.out, os.path.getsize(a.out) / 1048576))


if __name__ == "__main__":
    main()
