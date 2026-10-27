#!/usr/bin/env python3
"""Build the payload behind the two-vector page.

    ./vectors_build.py patterns.csv --reps patreps --out two_payload.json
    ./vectors_page.py  two_payload.json --out vectors.html

Two vectors are under development. Each is clustered on its own, over the
scenes rs_pattern.py covers, and every scene appears in the output - ordered
from its cluster's centre outwards, so the last tiles are where the cluster
frays.

Nothing here names a scene. There is no motif table, no class list and no
label taxonomy: a cluster is reported by its id, its size, its cohesion and
the median of its own columns, and a scene by the values measured from it.
What the groups mean, if anything, is read off the tiles by a person.

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
import argparse, base64, csv, glob, io, json, math, os, re

import numpy as np
from PIL import Image
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score, silhouette_samples


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


def psi_pattern(row):
    """Which cell of the pattern space this scene falls in.

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
    return "%s/%s" % (arr, "major" if float(cov) > 0.5 else "minor")


def psi_pattern_cells():
    """Every cell the partition declares, whether or not any scene is in it.

    Declared rather than collected from the data: a cell that no scene
    reaches has to be nameable in order to be reported as empty, and a
    partition whose cells are whatever turned up is not a partition.
    """
    return [CELL_NONE] + ["%s/%s" % (a, b) for a in ARRANGEMENTS
                          for b in ("major", "minor")]


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


def build(rows, img, kind, key, mcs):
    V = VECTORS[key]
    F = [(n, k, w) for n, k, w, _ in V["cols"]]

    X = np.array([[transform(k, r.get(n)) for n, k, _ in F] for r in rows], float)
    for j in range(X.shape[1]):
        m = np.isnan(X[:, j])
        if m.any():
            X[m, j] = np.nanmean(X[:, j]) if not m.all() else 0.0
    sd = X.std(0)
    flat = [F[j][0] for j in range(X.shape[1]) if sd[j] < 1e-9]
    if flat:
        print("ERR-COLUMN-CONSTANT: %s measured nothing on this capture - no "
              "variance, so %s dropped from the %s matrix before clustering"
              % (", ".join(flat), "they are" if len(flat) > 1 else "it is", key))
    sd[sd < 1e-9] = 1.0
    Z = (X - X.mean(0)) / sd * np.array([w for _, _, w in F])
    Z = Z[:, Z.std(0) > 1e-9]

    lab = HDBSCAN(min_cluster_size=mcs).fit_predict(Z)
    m = lab >= 0
    sil = float(silhouette_score(Z[m], lab[m]))
    per = np.full(len(Z), np.nan)
    per[m] = silhouette_samples(Z[m], lab[m])
    pca = PCA(n_components=min(3, Z.shape[1])).fit(Z)

    groups = []
    for cid in sorted(set(lab)):
        idx = [i for i in range(len(rows)) if lab[i] == cid]
        # Centre outwards: the tiles at the end are where the cluster frays.
        idx.sort(key=lambda i: -(per[i] if not np.isnan(per[i]) else -9))
        # The cluster's own columns, at their median. This replaces the label
        # histogram that used to sit here: a name would be a scene class, and
        # the clustering is not here to confirm one.
        centre = []
        for n, k, _ in F[:4]:
            vals = [float(rows[i][n]) for i in idx
                    if str(rows[i].get(n, "")).strip() not in ("", "-")]
            if vals:
                centre.append("%s %s" % (n, round(float(np.median(vals)), 2)))
        if not centre:
            # Every member left these fields empty - no repeating group was
            # found at all. That is a measurement, so it is reported as one.
            centre = ["nothing measured on these columns"]
        groups.append({
            "id": int(cid),
            "n": len(idx),
            "cohesion": (None if cid < 0
                         else round(float(np.nanmean([per[i] for i in idx])), 2)),
            "centre": centre,
            "reps": [{"scene": row_identity(rows[i]),
                      "img": img.get(row_identity(rows[i])),
                      "imgsrc": kind.get(row_identity(rows[i])),
                      "cell": psi_pattern(rows[i]),
                      "sig": (rows[i].get("pat1_pat") or "").strip(),
                      "cap": V["caption"](rows[i]),
                      "sil": (None if np.isnan(per[i]) else round(float(per[i]), 2))}
                     for i in idx],
        })
    groups.sort(key=lambda g: (g["id"] < 0, -g["n"]))

    return {"title": V["title"], "blurb": V["blurb"],
            "cols": [{"name": "rs_" + n, "t": k, "w": w, "what": d}
                     for n, k, w, d in V["cols"]],
            "n": len(rows), "k": len(set(lab) - {-1}),
            "outliers": int((lab < 0).sum()),
            "silhouette": round(sil, 3), "mcs": mcs,
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
        })
    empty = [c["cell"] for c in cells if not c["n"]]
    print("\nPSI_p: %d scenes over %d declared cell(s); %d empty"
          % (len(rows), len(cells), len(empty)))
    for c in cells:
        print("  %-14s %4d" % (c["cell"], c["n"]))
    if empty:
        print("  INV-PSI-MINIMAL: empty cell(s): %s" % ", ".join(empty))

    out = {"pattern_cells": cells,
           "_audit": {"rows": len(rows), "shown": len(img),
                      "own": len(img) - borrowed, "backstop": borrowed,
                      "missing": missing,
                      "captures": a.captures or None, "reps": a.reps or None}}
    for key in VECTORS:
        out[key] = build(rows, img, kind, key, a.min_cluster)
        v = out[key]
        print("%-16s %d clusters, %d fitting none (%.0f%%), silhouette %s"
              % (v["title"], v["k"], v["outliers"],
                 100 * v["outliers"] / v["n"], v["silhouette"]))
        for g in v["groups"]:
            nm = "none" if g["id"] < 0 else "c%d" % g["id"]
            print("     %-5s n=%3d coh %-5s %s"
                  % (nm, g["n"], g["cohesion"], " · ".join(g["centre"])))

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print("\nwrote %s  %.1f MB" % (a.out, os.path.getsize(a.out) / 1048576))


if __name__ == "__main__":
    main()
