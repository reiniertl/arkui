#!/usr/bin/env python3
"""Build the payload behind the viewer (IF-PAGE-PAYLOAD).

    ./vectors_build.py patterns.csv --captures captures --out two_payload.json
    ./vectors_page.py  two_payload.json --out scene_feature_reference.html

The motif vector holds, per dictionary motif, its distance, share and
count on the display (IF-MOTIF-VECTOR): 21 motifs, 63 columns. The viewer
shows one group per dictionary motif with every scene it was found in.
Separately, one clustering runs over all 63 columns and matches no motif
(ALG-VECTOR-CLUSTER).

Nothing here names a scene. A motif names a structure of components and a
cluster is named by its id.

This script was an inline fragment for one session and the page it produced
could not be rebuilt from the repository. It is tracked now because the page
is the mechanism's only output.

Inputs
  patterns.csv       rs_pattern.py's output, one row per scene - a
                     photographed sample - and beside it patterns.lost.csv,
                     every picture that became no scene, with its reason
  --captures DIR     the capture tree; a scene's picture is <captures>/<shot>,
                     its own sample's. Pictures of a real phone in real use
                     never enter the repository.
"""
import argparse, base64, csv, io, json, math, os, sys

import numpy as np
from PIL import Image
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score, silhouette_samples

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rs_scene as SC                           # noqa: E402


# The motif vector's columns (IF-MOTIF-VECTOR), in dictionary order: per motif its
# distance, its share of the display and its count written n/(n+1). Each is
# in [0, 1] by construction and is used as measured - no transform, no
# weight, no standardisation and no imputation (INV-NO-FEATURE-WEIGHTS,
# INV-FEATURE-TOTAL).
MOTIF_COLS = [(p + suf, what % p) for p in SC.MOTIFS for suf, what in (
    ("_d", "%s: 1 - Jaccard at its closest root"),
    ("_share", "%s: share of the display its matches show"),
    ("_n", "%s: matches n, as n/(n+1)"))]

def found(row):
    """The dictionary motifs found on this scene's display, in order."""
    return [p for p in SC.MOTIFS if float(row.get(p + "_d") or 1.0) <= SC.FOUND]


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
    """What identifies a scene: the session and the sample it was read at.

    Named for the row rather than the scene on purpose. INV-NO-SCENE-CLASS
    bans a function that maps a measurement onto a scene name, and its check
    matches on `scene_*`; this one maps a row's session and sample onto the
    place it came from, which is not that, but the check is right to be
    suspicious of the shape and the cheaper fix is to not wear it.
    """
    return "%s/%s" % (row.get("session", ""), row.get("sample", ""))


DW_REF, DH_REF = 1316.0, 2832.0      # the captured display; a frame, not a claim


def definition(name):
    """A motif's definition as text, from IF-MOTIF-DICTIONARY alone: every
    variant's element set and the contract it comes from."""
    return "; or ".join("%s {%s} (%s)" % (root, ", ".join(sorted(shape)), src)
                        for n, root, shape, src in SC.DICTIONARY if n == name)


def schematic(name):
    """Draw a dictionary motif from its definition alone (ALG-MOTIF-SCHEMATIC).

    The root is a labelled box. Its children are laid out by what the root
    is: across for a Row, down for a Column or a List, in two columns for a
    Grid or a WaterFlow. A repeated child (`*`) is drawn three times with an
    ellipsis; a single one once. A child that is itself a pattern is drawn
    as its own small schematic. A leaf motif is its root alone. Sizes come
    from the size labels. Nothing
    here reads a capture: the drawing is the definition, not a scene.
    """
    shape = next(s for n, r, s, _ in SC.SHAPES if n == name)
    root = next(r for n, r, s, _ in SC.SHAPES if n == name)
    W, H = DW_REF, DH_REF
    ink = "var(--ink,#2563eb)"
    p = ['<svg viewBox="-40 -40 %d %d" preserveAspectRatio="xMidYMid meet" role="img">'
         % (W + 80, H + 80),
         '<rect x="0" y="0" width="%d" height="%d" fill="var(--card,#fff)" '
         'stroke="var(--line,#64748b)" stroke-width="10" rx="26"/>' % (W, H)]
    base = root.split(":")[0]
    size = root.split(":")[1] if ":" in root else ""
    rh = {"small": 0.12, "medium": 0.35, "large": 0.85}.get(size, 0.6) * H
    rx, ry, rw = 60.0, 160.0, W - 120.0
    p.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" rx="18" fill="%s" '
             'opacity=".10" stroke="%s" stroke-width="8"/>' % (rx, ry, rw, rh, ink, ink))
    p.append('<text x="%.0f" y="%.0f" font-size="64" fill="%s">%s</text>'
             % (rx + 24, ry + 80, ink, root))
    kids = [e.split("/")[1] for e in sorted(shape) if "/" in e and e.split("/")[0] == root]
    for k in kids:
        rep_ = k.endswith("*")
        lab = k.rstrip("*")
        n = 3 if rep_ else 1
        if base in ("Row",):
            cw, ch, cols = (rw - 80 - (n - 1) * 30) / n, min(rh - 160, 220.0), n
        elif base in ("Grid", "WaterFlow"):
            cw, ch, cols = (rw - 110) / 2, (rh - 200) / 2, 2
        else:                                   # Column, List, Tabs and the rest
            cw, ch, cols = rw - 80, (rh - 160 - (n - 1) * 30) / n, 1
        for i in range(n):
            x = rx + 40 + (i % cols) * (cw + 30)
            y = ry + 120 + (i // cols) * (ch + 30)
            p.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" rx="12" '
                     'fill="%s" opacity=".28" stroke="%s" stroke-width="6"/>'
                     % (x, y, cw, max(ch, 40.0), ink, ink))
            p.append('<text x="%.0f" y="%.0f" font-size="44" fill="%s">%s</text>'
                     % (x + 16, y + 56, ink, lab))
        if rep_:
            p.append('<text x="%.0f" y="%.0f" font-size="96" fill="%s">&#8230;</text>'
                     % (rx + rw - 140, ry + rh - 40, ink))
    deeper = [e for e in sorted(shape) if "/" in e and e.split("/")[0] != root]
    for i, e in enumerate(deeper):
        p.append('<text x="%.0f" y="%.0f" font-size="48" fill="%s">%s</text>'
                 % (rx + 24, ry + rh + 90 + 60 * i, ink, e))
    return "".join(p) + "</svg>"


def build_clustering(rows, mcs):
    """One clustering over the pattern vector (ALG-VECTOR-CLUSTER).

    The columns as measured: each already lies in [0, 1], so nothing is
    transformed, weighted, standardised or filled (INV-NO-FEATURE-WEIGHTS,
    INV-FEATURE-TOTAL). All 63 columns, on every capture: a column with no
    variance changes no distance and no PCA component, so it is reported
    and kept, and the column set never depends on the capture. HDBSCAN on
    that. A scene it
    cannot place is reported in the unclustered group, never forced into the
    nearest cluster.
    """
    F = MOTIF_COLS
    X = np.array([[float(r[n]) for n, _ in F] for r in rows], float)
    sd = X.std(0)
    flat = [F[j][0] for j in range(X.shape[1]) if sd[j] < 1e-9]
    if flat:
        print("ERR-COLUMN-CONSTANT: %d column(s) have no variance on this capture "
              "and change no distance; they stay in the clustering: %s"
              % (len(flat), ", ".join(flat)))
    Z = X

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
        # The patterns found in at least half the cluster's scenes. A cluster
        # is named by its id; a word for it would be a scene class.
        seen = {}
        for i in idx:
            for p in found(rows[i]):
                seen[p] = seen.get(p, 0) + 1
        centre = ["%s in %d" % (p, c) for p, c in
                  sorted(seen.items(), key=lambda kv: -kv[1]) if 2 * c >= len(idx)]
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

    return {"title": "Clustering over the motif vector",
            "blurb": ("Every scene clustered on all %d of the motif vector's columns "
                      "as measured. It matches no motif: a cluster is where scenes "
                      "sit relative to each other, named only by its id." % len(F)),
            "cols": [{"name": n, "what": d} for n, d in F],
            "constant": flat,
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
    ap.add_argument("--captures", required=True,
                    help="the capture tree; a scene's picture is <captures>/<shot>")
    ap.add_argument("--out", default="two_payload.json")
    ap.add_argument("--thumb", type=int, default=THUMB_W,
                    help="embed each frame at this width (default %d)" % THUMB_W)
    ap.add_argument("--min-cluster", type=int, default=9,
                    help="HDBSCAN min_cluster_size (default 9)")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    if not rows:
        raise SystemExit("%s holds no rows" % a.patterns)

    # INV-NO-SILENT-SCENE-LOSS: every scene is a photographed sample and
    # carries its own picture; every picture that became no scene is in the
    # lost list rs_pattern.py wrote, with its reason.
    lp = os.path.splitext(a.patterns)[0] + ".lost.csv"
    lost = list(csv.DictReader(open(lp, encoding="utf-8-sig"))) if os.path.exists(lp) else None
    if lost is None:
        print("ERR-PICTURE-LOST: %s is not there, so the pictures that became no "
              "scene cannot be accounted for" % lp)
        lost = []
    img, unmatched = {}, []
    for r in rows:
        sid = row_identity(r)
        path = os.path.join(a.captures, *(r.get("shot") or "").strip().split("/"))
        if r.get("shot") and os.path.exists(path):
            img[sid] = thumbnail(path, a.thumb)
        else:
            # a scene is a photographed sample: its picture not being there
            # is a broken join, never a scene without a picture
            unmatched.append("%s -> %s" % (sid, r.get("shot")))
    if unmatched:
        print("ERR-JOIN-KEY-UNMATCHED: %d row(s) name a picture that is not "
              "there: %s" % (len(unmatched), ", ".join(unmatched[:5])
                             + (" and %d more" % (len(unmatched) - 5)
                                if len(unmatched) > 5 else "")))
    reasons = {}
    for x in lost:
        reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1
    print("  pictures: %d named, %d scenes each with its own, %d lost%s"
          % (len(rows) + len(lost), len(img), len(lost),
             "".join(" - %d %s" % (n, w) for w, n in sorted(reasons.items()))))

    import statistics as _st

    def _med(members, col, default=0.0):
        xs = [float(m[col]) for m in members
              if (m.get(col) or "").strip() != ""]
        return _st.median(xs) if xs else default

    def _f(r, c):
        v = (r.get(c) or "").strip()
        return float(v) if v else 0.0

    # One group per dictionary pattern, in dictionary order, found or not:
    # a pattern no scene reached is a result and keeps its place.
    patterns = []
    for name in SC.MOTIFS:
        mem = [r for r in rows if name in found(r)]
        patterns.append({
            "cell": name,
            "n": len(mem),
            "svg": schematic(name),
            "desc": "%s: %s. Found where 1 - Jaccard <= %.1f." % (
                name, definition(name), SC.FOUND),
            # largest share first, so the weak end of a group is visible
            "members": [row_identity(r) for r in
                        sorted(mem, key=lambda r: -_f(r, name + "_share"))],
        })
    # INV-UNPLACED-SCENES-NAMED: one population, the scenes with no motif
    # found, each NAMED - a count cannot be traced back to a scene - and shown
    # with its tag coverage, the confidence its absence of a motif carries
    # (INV-CLIENT-TAG-COVERAGE). No scene is set aside as unreadable.
    none = sorted((r for r in rows if not found(r)), key=lambda r: -_f(r, "tag_coverage"))
    no_motif = [row_identity(r) for r in none]
    patterns.append({"cell": "none", "n": len(no_motif), "svg": None,
                     "desc": ("No dictionary motif was found on the display. Each "
                              "tile shows its tag coverage: how much of what is "
                              "shown could be named."),
                     "members": no_motif})
    unplaced = {"pattern": {
        "unplaced": no_motif,
        "tag_coverage": {row_identity(r): _f(r, "tag_coverage") for r in none},
        "note": "no dictionary motif found on the display; each with its tag coverage",
    }}
    print("INV-UNPLACED-SCENES-NAMED: %d of %d scenes with no motif, each named"
          % (len(no_motif), len(rows)))
    if no_motif:
        print("ERR-SCENE-UNPLACED: %d scene(s) with no motif found; each is named "
              "in the payload with its tag coverage" % len(no_motif))

    print("\nmotifs: %d scenes" % len(rows))
    for c in patterns:
        print("  %-14s %4d" % (c["cell"], c["n"]))
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
        scenes[sid] = {"img": img.get(sid), "found": found(r),
                       "tag_coverage": _f(r, "tag_coverage")}
    out = {"_unplaced": unplaced,
           "_scenes": scenes,
           "_patterns": patterns,
           "_audit": {"pictures": len(rows) + len(lost),
                      "on_disk": len(rows) + sum(1 for x in lost if x["reason"] != "missing on disk"),
                      "rows": len(rows), "shown": len(img),
                      "unmatched": unmatched,
                      "lost": [{"scene": "%s/%s" % (x["session"], x["sample"]),
                                "shot": x["shot"], "why": x["reason"]} for x in lost],
                      "lost_by_reason": reasons,
                      "captures": a.captures}}
    out["clustering"] = v = build_clustering(rows, a.min_cluster)
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
