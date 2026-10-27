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
import argparse, base64, csv, io, json, math, os

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


def build(rows, img, key, mcs):
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
    img, missing, unmatched = {}, [], []
    for r in rows:
        sid = row_identity(r)
        shot = (r.get("shot") or "").strip()
        path = rep_path(shot, a.captures, a.reps)
        if path:
            img[sid] = thumbnail(path, a.thumb)
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
    if missing:
        print("ERR-SCENE-WITHOUT-REP: %d of %d scenes have no screenshot; they "
              "are measured and clustered, and their tiles carry no picture."
              % (len(missing), len(rows)))
        for sid in missing[:10]:
            print("    %s" % sid)
        if len(missing) > 10:
            print("    ... and %d more, all listed in the payload's audit"
                  % (len(missing) - 10))

    out = {"_audit": {"rows": len(rows), "shown": len(img),
                      "missing": missing,
                      "captures": a.captures or None, "reps": a.reps or None}}
    for key in VECTORS:
        out[key] = build(rows, img, key, a.min_cluster)
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
