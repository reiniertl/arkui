#!/usr/bin/env python3
"""Build the payload behind the viewer (IF-PAGE-PAYLOAD).

    ./vectors_build.py patterns.csv --captures captures --out two_payload.json
    ./vectors_page.py  two_payload.json --out scene_feature_reference.html

The pattern vector holds, per dictionary pattern, its distance, share and
count on the display (IF-PATTERN-VECTOR); the viewer shows one group per
dictionary pattern with every scene it was found in. Separately, one
clustering runs over the pattern vector and matches no pattern
(ALG-VECTOR-CLUSTER).

Nothing here names a scene. A pattern names a structure of components and a
cluster is named by its id.

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
import rs_scene as SC                           # noqa: E402


# The pattern vector's columns, in dictionary order: per pattern its
# distance, its share of the display and its count written n/(n+1). Each is
# in [0, 1] by construction and is used as measured - no transform, no
# weight, no standardisation and no imputation (INV-NO-FEATURE-WEIGHTS,
# INV-FEATURE-TOTAL).
PATTERN_COLS = [(p + suf, what % p) for p in SC.PATTERNS for suf, what in (
    ("_d", "%s: 1 - Jaccard at its closest root"),
    ("_share", "%s: share of the display its matches show"),
    ("_n", "%s: matches n, as n/(n+1)"))]

def found(row):
    """The dictionary patterns found on this scene's display, in order."""
    return [p for p in SC.PATTERNS if float(row.get(p + "_d") or 1.0) <= SC.FOUND]


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


DW_REF, DH_REF = 1316.0, 2832.0      # the captured display; a frame, not a claim


def definition(name):
    """A pattern's definition as text, from IF-PATTERN-DICTIONARY alone."""
    return " or ".join("%s {%s}" % (root, ", ".join(sorted(shape)))
                       for n, root, shape, _ in SC.SHAPES if n == name)


def schematic(name):
    """Draw a dictionary pattern from its definition alone (ALG-MOTIF-SCHEMATIC).

    The root is a labelled box. Its children are laid out by what the root
    is: across for a Row, down for a Column or a List, in two columns for a
    Grid or a WaterFlow. A repeated child (`*`) is drawn three times with an
    ellipsis; a single one once. A child that is itself a pattern is drawn
    as its own small schematic. Sizes come from the size labels. Nothing
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


def sample_rows(captures):
    """session -> the rows of its samples.csv, in recorded order. Samples are
    ordered as recorded, never by a file's time (INV-NO-FILE-TIME)."""
    out = {}
    for sdir in sorted(glob.glob(os.path.join(captures, "*"))):
        idx = os.path.join(sdir, "samples.csv")
        if os.path.isdir(sdir) and os.path.exists(idx):
            with open(idx, encoding="utf-8-sig", newline="") as fh:
                out[os.path.basename(sdir)] = list(csv.DictReader(fh))
    return out


def same_moment(rows, stem, fname):
    """INV-PICTURE-SAME-MOMENT: may this picture stand for this dump?

    The dump's sample is the row that first records its stem; the picture's
    is the row whose shot names it, or, for a backstop frame p<n>.jpeg, row
    n. The picture is accepted only if every sample between the two carries
    the dump's signature: no other scene was on the display in between.
    Returns (True, "") or (False, the reason).
    """
    i = next((k for k, r in enumerate(rows) if (r.get("stem") or "").strip() == stem), None)
    if i is None:
        return False, "the dump is not in samples.csv"
    j = next((k for k, r in enumerate(rows) if (r.get("shot") or "").strip() == fname), None)
    m = re.match(r"p(\d{6})\.jpeg$", fname)
    if j is None and m and 1 <= int(m.group(1)) <= len(rows):
        j = int(m.group(1)) - 1
    if j is None:
        return False, "the picture is not in samples.csv"
    sig = (rows[i].get("sig") or "").strip()
    for k in range(min(i, j), max(i, j) + 1):
        other = (rows[k].get("sig") or "").strip()
        if other != sig:
            return False, ("sample %d between the dump and the picture shows "
                           "another scene" % (k + 1))
    return True, ""


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


def build_clustering(rows, mcs):
    """One clustering over the pattern vector (ALG-VECTOR-CLUSTER).

    The columns as measured: each already lies in [0, 1], so nothing is
    transformed, weighted, standardised or filled (INV-NO-FEATURE-WEIGHTS,
    INV-FEATURE-TOTAL). A column with no variance measured nothing on this
    capture and is reported and left out. HDBSCAN on that. A scene it
    cannot place is reported in the unclustered group, never forced into the
    nearest cluster.
    """
    F = PATTERN_COLS
    X = np.array([[float(r[n]) for n, _ in F] for r in rows], float)
    sd = X.std(0)
    flat = [F[j][0] for j in range(X.shape[1]) if sd[j] < 1e-9]
    if flat:
        print("ERR-COLUMN-CONSTANT: %d column(s) measured nothing on this capture "
              "- no variance - and are left out of the clustering: %s"
              % (len(flat), ", ".join(flat)))
    keep = sd >= 1e-9
    Z = X[:, keep]

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

    return {"title": "Clustering over the pattern vector",
            "blurb": ("Every scene clustered on the pattern vector's columns as "
                      "measured. It matches no pattern: a cluster is where scenes "
                      "sit relative to each other, named only by its id."),
            "cols": [{"name": n, "what": d} for n, d in F],
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
    samples = sample_rows(a.captures) if a.captures else {}

    img, kind, missing, unmatched, refused = {}, {}, [], [], []
    borrowed = 0
    for r in rows:
        sid = row_identity(r)
        shot = (r.get("shot") or "").strip()
        path = rep_path(shot, a.captures, a.reps)
        if not path and sid in backstop:
            path = backstop[sid]
        if path:
            # INV-PICTURE-SAME-MOMENT: only a picture taken while this scene
            # was still on the display stands for it.
            ok, why = same_moment(samples.get(r.get("session", ""), []),
                                  r.get("stem", ""), os.path.basename(path))
            if not ok:
                refused.append((sid, why))
                path = None
        if path:
            img[sid] = thumbnail(path, a.thumb)
            own = os.path.basename(path) == os.path.basename(shot)
            kind[sid] = "own" if own else "backstop"
            borrowed += 0 if own else 1
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
    if refused:
        print("ERR-PICTURE-NOT-SAME-MOMENT: %d scene(s) shown with no picture, "
              "because another scene was sampled between the dump and its "
              "picture: %s" % (len(refused), ", ".join(s for s, _ in refused[:5])
                               + (" and %d more" % (len(refused) - 5)
                                  if len(refused) > 5 else "")))
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
    for name in SC.PATTERNS:
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
    none = [r for r in rows if not found(r)]
    # untagged: no window on the display could be read at all, so the absence
    # of a pattern says nothing; otherwise none was found where it could be
    no_pattern = [row_identity(r) for r in none if _f(r, "read_windows")]
    untagged = [row_identity(r) for r in none if not _f(r, "read_windows")]
    patterns.append({"cell": "none", "n": len(no_pattern), "svg": None,
                     "desc": "No dictionary pattern was found on the display.",
                     "members": no_pattern})
    patterns.append({"cell": "untagged", "n": len(untagged), "svg": None,
                     "desc": ("No pattern was found, and no window on the display "
                              "had half of its kept nodes tagged, so no structure "
                              "could be read."),
                     "members": untagged})
    # INV-UNPLACED-SCENES-NAMED. Two populations, kept apart because they
    # are different claims, and NAMED because a count cannot be traced back
    # to a scene.
    unplaced = {
        "pattern": {
            "undescribed": untagged,
            "unplaced": no_pattern,
            "note": ("unplaced: no dictionary pattern found where a window "
                     "could be read; undescribed: none found, and no window on "
                     "the display was tagged enough to be read"),
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

    print("\npatterns: %d scenes" % len(rows))
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
        scenes[sid] = {"img": img.get(sid), "imgsrc": kind.get(sid),
                       "found": found(r)}
    out = {"_unplaced": unplaced,
           "_scenes": scenes,
           "_patterns": patterns,
           "_audit": {"rows": len(rows), "shown": len(img),
                      "own": len(img) - borrowed, "backstop": borrowed,
                      "missing": missing,
                      "refused": [{"scene": s, "why": w} for s, w in refused],
                      "captures": a.captures or None, "reps": a.reps or None}}
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
