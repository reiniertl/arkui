#!/usr/bin/env python3
"""Cluster captured screens on geometry alone, and say where ArkUI is blind.

The classifier in scene_class.ps1 is a DISTILLED form: twenty-three class
names written by hand before the data existed.  This does it the other way
round - take what real screens actually look like, group them on structure
alone, and let the class set fall out of the groups.

Two rules it is built around:

  Cluster SCENES, not apps.  One app shows a video player, a chat, a
  settings page and a feed, often from one window.  Nothing about the app
  predicts the scene, so nothing identifying the app enters the vector -
  and, since the design-language alarm was removed with the rest of the
  label machinery, nothing identifying it leaves the report either.

  No hand-written class, anywhere.  There used to be one: `shape`, a guess
  written before any data existed, scored against the clusters to see
  whether it held. It did not - SPLIT was the only name that mapped to a
  single cluster - and the reading it came from was window-scoped, which is
  the wrong object. Capture no longer produces it. A cluster is described
  by what it measures and by how well it holds together, and the names are
  written in the viewer, by a person, after looking.

Usage:  cluster.cmd <capture-dir> [--min-cluster N] [--no-thumbs]
"""

import argparse
import csv
import glob
import json
import math
import os
import shutil
import sys
from collections import Counter, defaultdict

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score, silhouette_samples

# ---------------------------------------------------------------------------
# The vector.  Everything here is already normalised against the viewport by
# the extractor, so no panel size and no pixel count reaches this file.
#
# weight exists because standardisation alone inflates a rare binary flag
# into a dominant axis: `bar_edit` is 1 on maybe a twentieth of screens, so
# its z-score swings by 4.5 while `rep_frac` swings by 1.
# ---------------------------------------------------------------------------
FEATURES = [
    # The vector is the render tree, and nothing else. The seventeen uitest
    # columns that used to lead this list described ONE window's layout -
    # cols, item_h, split_n, bar_top - while these describe the display. They
    # measured a different object, and capture no longer reads uitest at all,
    # so on a current session they were seventeen columns of zero.
    #
    # column            transform   weight  what it separates
    ("rs_repeat_frac", "unit",    1.0),   # how much of the display repeats
    ("rs_repeat_n",    "log1p",   0.7),
    ("rs_text_frac",   "unit",    1.0),   # a form and a player differ here most
    ("rs_image_frac",  "unit",    1.0),
    ("rs_sym_frac",    "unit",    0.5),   # glyph chrome: bars, tool rows
    ("rs_surface_tag", "clip4",   1.0),   # a video or web surface in the scene
    ("rs_scroller",    "clip4",   0.7),   # List, Grid, Swiper, Scroll, Tabs
    ("rs_leaf_frac",   "unit",    0.7),   # deep and sparse vs shallow and dense
    ("rs_fanout",      "log1p",   0.7),
    ("rs_depth_mean",  "log1p",   0.7),
    ("rs_node_den",    "log1p",   1.0),   # nodes per 1000 vp2, so it transfers
    ("rs_contributors","clip4",   0.7),   # one window, or five sharing a display
    ("rs_top_weight",  "unit",    0.7),   # does one of them own the scene
    ("rs_cover_opaque","unit",    0.5),
    ("rs_uncovered",   "unit",    0.5),
    ("rs_paint_frac",  "unit",    1.0),   # how much of it carries content, not boxes
]

# Read, reported, never clustered on.
# Read through to the viewer, never into the vector. jitter is how much of
# the tree churned between the two dumps that bracketed the screenshot: a
# measure of animation, which belongs beside a cluster rather than inside the
# definition of one. A still list and an animating list are the same shape.
# rs_top_name identifies the app, and clustering on the app gives one cluster
# per app instead of one per scene - the mistake this whole pipeline exists to
# avoid. rs_vsync is a frame counter: monotonic, so it would order the rows by
# capture time and nothing else.
#
# The render-tree columns that are read but not clustered on, and why each
# one is kept out:
#
#   blur, shadow, corner, blend   house style. The design-language alarm
#       already fires when apps separate by how they look rather than by how
#       they are built; clustering on an app's corner radius is that mistake
#       written into the feature table.
#   dirty, anim, forever, fps     the scene in time. A still list and an
#       animating list are the same shape - the same argument that keeps
#       jitter out. These are modifiers on a class, not part of one.
#   hwc, hdr, draw_slots, power   what the scene costs. Consequences of the
#       structure, and the thing the class set is meant to predict. Folding
#       them in would be clustering on the answer.
#   rotation, refresh             device state, constant across a session.
#   protected, skipped, nodes     reported; nodes is unnormalised and
#       rs_node_den carries it in a form that transfers across panels.
HELD_OUT = ("shape", "split_main", "scene", "name", "window", "pid", "described",
            "jitter", "still", "session", "rs_top_name", "rs_vsync", "rs_fps_scene",
            "rs_blur_frac", "rs_shadow_frac", "rs_corner_frac", "rs_blend_frac",
            "rs_dirty_frac", "rs_anim_n", "rs_anim_forever", "rs_fps_pref",
            "rs_hwc", "rs_hwc_frac", "rs_hdr", "rs_draw_slots", "rs_power", "rs_rotation",
            "rs_refresh", "rs_refresh_max", "rs_protected", "rs_skipped", "rs_nodes", "rs_z_span",
            "rs_dropped", "rs_admit_frac", "rs_drop_skip", "rs_drop_odd",
            "rs_paints", "rs_redrawn", "rs_same_win", "rs_top_win", "rs_top_z", "rs_top_pid", "win_sig",
            "rs_transition")


def transform(kind, raw):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.0
    if kind == "permille":
        return v / 1000.0
    if kind == "log1p":
        return math.log1p(max(0.0, v))
    if kind == "logratio":
        # An absent aspect reads as 1.0 (square), i.e. log 0, rather than as
        # an infinitely wide item.
        return math.log(v) if v > 0.01 else 0.0
    if kind == "clip6":
        return min(v, 6.0)
    if kind == "clip4":
        return min(v, 4.0)
    if kind == "flag":
        return 1.0 if v else 0.0
    if kind == "unit":
        # Already a fraction of the display or of a contributor. The render
        # tree path emits fractions directly; the uitest path emits permille.
        return min(max(v, 0.0), 1.0)
    raise ValueError(kind)


def load_rows(d):
    """Prefer the rebuilt rows: they came from the current extractor."""
    for name in ("rows.rebuilt.csv", "rows.csv"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            with open(p, newline="", encoding="utf-8-sig") as fh:
                return name, list(csv.DictReader(fh))
    sys.exit("no rows.csv or rows.rebuilt.csv in %s" % d)


def rel(path, start):
    """A forward-slash relative path, because it ends up in a browser."""
    return os.path.relpath(path, start).replace(os.sep, "/")


def load_sessions(dirs, out):
    """Several capture sessions, read as one measurement.

    Nothing has to be merged on disk. The layout signature is the join: the
    same screen captured on two different days hashes the same, so dedupe
    folds them together and the dwell adds up by itself. All this does is
    make every screenshot path relative to wherever the viewer will sit, so
    the sessions can stay where they are.
    """
    rows = []
    for d in dirs:
        name, got = load_rows(d)
        tag = os.path.basename(os.path.normpath(d))
        for r in got:
            shot = (r.get("shot") or "").strip()
            if shot:
                r["shot"] = rel(os.path.join(d, shot), out)
            r["session"] = tag
            rows.append(r)
        print("  %-24s %5d samples from %s" % (tag, len(got), name))
    return rows


def thumb_name(shot):
    """Must match thumb() in viewer.html exactly.

    The leading "../" goes first. A merged view sits beside its sessions, so
    every shot path starts with one, and a file called "..__0003.jpg" is
    hidden on anything unix-shaped and confusing everywhere else.
    """
    return shot.replace("../", "").replace("/", "__").replace(".png", ".jpg")


def dedupe(rows):
    """One entry per distinct screen; how many samples it had is its dwell.

    Without this, half an hour parked on one feed outvotes every other screen
    in the session and the clusters describe how the phone was used rather
    than what screens exist.
    """
    groups = defaultdict(list)
    for r in rows:
        # `sig` is the scene signature now: every admitted rect from every
        # contributor, in display coordinates, plus the contributor set. It
        # used to be a hash of the foreground window's uitest layout, which
        # grouped window-scoped under a display-scoped definition - a dialog
        # over an unchanged window was the same key and a different scene.
        # `win_sig` keeps the old one for comparison and is held out.
        key = r.get("sig") or r.get("stem") or ""
        if not key:
            key = "|".join(str(r.get(c, "")) for c, _, _ in FEATURES)
        groups[key].append(r)
    out = []
    for key, members in groups.items():
        pick = next((m for m in members if m.get("shot")), members[0])
        pick = dict(pick)
        # A rebuilt CSV already carries the dwell recovered from rows.csv;
        # a live one has one line per sample and counts them here.
        pick["dwell"] = sum(max(1, int(float(m.get("dwell") or 1))) for m in members)
        pick["sig"] = key
        out.append(pick)
    return out


def build_matrix(rows):
    cols = []
    for name, kind, _ in FEATURES:
        cols.append([transform(kind, r.get(name)) for r in rows])
    X = np.array(cols, dtype=float).T
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    # A feature that never varies in this session carries no information and
    # would otherwise divide by zero.
    dead = std < 1e-9
    std[dead] = 1.0
    Z = (X - mean) / std
    Z[:, dead] = 0.0
    weights = np.array([w for _, _, w in FEATURES], dtype=float)
    return Z * weights, dead, (mean, std, dead, weights)


def apply_matrix(rows, fit):
    """Project rows into a space that was standardised on OTHER rows.

    The moving samples must not shift the mean they are measured against, or
    the clusters would move depending on how much the user happened to scroll.
    """
    mean, std, dead, weights = fit
    cols = []
    for name, kind, _ in FEATURES:
        cols.append([transform(kind, r.get(name)) for r in rows])
    X = np.array(cols, dtype=float).T
    Z = (X - mean) / std
    Z[:, dead] = 0.0
    return Z * weights


def thumbs(out_dir, rows, width=240):
    """Small copies for the viewer.  The full-resolution PNG stays on disk."""
    try:
        from PIL import Image
    except ImportError:
        print("  pillow missing - skipping thumbnails, viewer will use full images")
        return False
    out = os.path.join(out_dir, "thumbs")
    os.makedirs(out, exist_ok=True)
    made = 0
    for r in rows:
        shot = r.get("shot") or ""
        if not shot:
            continue
        src = os.path.join(out_dir, shot)
        dst = os.path.join(out, thumb_name(shot))
        if not os.path.exists(src) or os.path.exists(dst):
            continue
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                h = max(1, int(im.height * width / im.width))
                im.resize((width, h), Image.LANCZOS).save(dst, "JPEG", quality=78)
            made += 1
        except Exception as exc:                      # a truncated capture
            print("  thumbnail failed for %s: %s" % (shot, exc))
    print("  %d thumbnails -> thumbs/" % made)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", nargs="+",
                    help="one or more capture directories written by -Capture. "
                         "Several are read as ONE measurement: dwell accumulates "
                         "across them, because the same screen hashes the same")
    ap.add_argument("--out", default="",
                    help="where clusters.js, viewer.html and thumbs/ are written. "
                         "Default: the session itself, or captures\\merged for several")
    ap.add_argument("--min-cluster", type=int, default=0,
                    help="smallest group that counts as a cluster (default: 2%% of screens, min 5)")
    ap.add_argument("--still", type=int, default=800,
                    help="a sample below this structural hold was taken while the screen "
                         "was moving: it has no screenshot and does not define a screen "
                         "(default 800, matching -MoveTol 200)")
    ap.add_argument("--include-moving", action="store_true",
                    help="cluster the moving samples too, instead of attributing them")
    ap.add_argument("--no-thumbs", action="store_true")
    args = ap.parse_args()

    # cmd.exe does not expand wildcards, so captures\2026* arrives literally.
    dirs = []
    for a in args.session:
        hits = sorted(glob.glob(a)) if any(c in a for c in "*?[") else [a]
        if not hits:
            sys.exit("nothing matches %s" % a)
        dirs.extend(hits)
    dirs = [os.path.abspath(d) for d in dirs]
    for d in dirs:
        if not os.path.isdir(d):
            sys.exit("not a directory: %s" % d)

    if args.out:
        out = os.path.abspath(args.out)
    elif len(dirs) == 1:
        out = dirs[0]
    else:
        # Beside the sessions, not inside one of them: a merged view that
        # lived in the first session would be deleted with it.
        parents = {os.path.dirname(d) for d in dirs}
        if len(parents) != 1:
            sys.exit("sessions are in different folders - say where to write with --out")
        out = os.path.join(parents.pop(), "merged")
    os.makedirs(out, exist_ok=True)

    print("reading %d session%s" % (len(dirs), "" if len(dirs) == 1 else "s"))
    raw = load_sessions(dirs, out)
    print("  %d samples in all" % len(raw))

    # A sample taken mid-scroll has a real tree but no matching picture, and
    # its layout signature is a one-off - the list happened to be at that
    # offset once. Letting those define screens would bury the session in
    # near-identical entries, so they are held aside and attributed to the
    # clusters they resemble after the fact. That keeps the dwell honest
    # without letting scrolling invent screens.
    def hold(r):
        v = str(r.get("still", "")).strip()
        if not v:
            return 1000
        try:
            return int(float(v))
        except ValueError:
            return 1000

    moving = [] if args.include_moving else [r for r in raw if hold(r) < args.still]
    settled = raw if args.include_moving else [r for r in raw if hold(r) >= args.still]
    if moving:
        print("  %d of %d samples were taken while the screen was moving (%.0f%%)"
              % (len(moving), len(raw), 100.0 * len(moving) / len(raw)))

    rows = dedupe(settled)
    print("  %d distinct scenes (deduplicated by scene signature)" % len(rows))
    if len(rows) < 10:
        sys.exit("too few distinct scenes to cluster. Capture more.")

    Z, dead, fit = build_matrix(rows)
    if dead.any():
        names = [FEATURES[i][0] for i in np.flatnonzero(dead)]
        print("  constant in this session, carrying no information: %s" % ", ".join(names))

    mcs = args.min_cluster or max(5, int(round(0.02 * len(rows))))
    print("  HDBSCAN, min_cluster_size=%d over %d features" % (mcs, Z.shape[1]))
    model = HDBSCAN(min_cluster_size=mcs, min_samples=None,
                    cluster_selection_method="eom")
    labels = model.fit_predict(Z)

    uniq = sorted(set(labels) - {-1})
    noise = int((labels == -1).sum())
    print("  %d clusters, %d outliers (%.0f%%)"
          % (len(uniq), noise, 100.0 * noise / len(rows)))

    sil = None
    sil_per = None
    if len(uniq) >= 2:
        keep = labels != -1
        if keep.sum() > len(uniq):
            sil = float(silhouette_score(Z[keep], labels[keep]))
            sil_per = np.full(len(rows), float("nan"))
            sil_per[keep] = silhouette_samples(Z[keep], labels[keep])
            print("  silhouette %.3f %s" % (sil, quality(sil)))

    # Three components, not two. Two flattens the 16 dimensions far enough
    # that clusters well apart in the clustering can land on top of each
    # other on the map, which reads as the clustering being wrong when it is
    # the projection that is lossy. The third axis is carried through and the
    # viewer rotates it; explained_variance_ ratio says how much of the
    # spread the three actually account for.
    ncomp = min(3, Z.shape[1])
    if ncomp >= 2:
        pca = PCA(n_components=ncomp)
        fitted = pca.fit_transform(Z)
        explained = [round(float(v), 4) for v in pca.explained_variance_ratio_]
    else:
        fitted = np.zeros((len(rows), 2))
        explained = []
    coords = np.zeros((len(rows), 3))
    coords[:, :fitted.shape[1]] = fitted

    # Nearest centroid, in the same standardised space. Not a claim that a
    # half-scrolled list IS that cluster - a claim about which shape it most
    # resembles, which is all that is needed to say how much of the session
    # was spent moving through each one.
    moved_to = Counter()
    if moving and uniq:
        cents = np.vstack([Z[[i for i, l in enumerate(labels) if l == c]].mean(axis=0)
                           for c in uniq])
        M = apply_matrix(moving, fit)
        for i in np.argmin(((M[:, None, :] - cents[None, :, :]) ** 2).sum(axis=2), axis=1):
            moved_to[uniq[int(i)]] += 1

    # ---- per cluster ------------------------------------------------------
    out_clusters = []
    # -1 is HDBSCAN's noise label. It only becomes a row in the report when
    # something actually landed there; a clean run has no outlier tray.
    for cid in uniq + ([-1] if noise else []):
        idx = [i for i, l in enumerate(labels) if l == cid]
        members = [rows[i] for i in idx]
        sub = Z[idx]
        centre = sub.mean(axis=0)
        order = sorted(idx, key=lambda i: float(np.linalg.norm(Z[i] - centre)))


        med = {}
        for name, kind, _ in FEATURES:
            # Raw, not transformed. log1p of a repeat count is for the
            # distance metric; a person reading the table wants the count.
            # Named vals, not raw: `raw` is the undeduplicated row list in
            # this same scope, and shadowing it made the payload report the
            # sample count as the length of the last feature's median list.
            vals = []
            for m in members:
                try:
                    vals.append(float(m.get(name) or 0))
                except ValueError:
                    vals.append(0.0)
            med[name] = round(float(np.median(vals)), 3)

        jit = [int(float(m["jitter"])) for m in members
               if str(m.get("jitter", "")).strip() not in ("", "None")]
        jitter = int(np.median(jit)) if jit else None

        out_clusters.append({
            "id": int(cid),
            "n": len(members),
            "dwell": sum(int(m.get("dwell", 1)) for m in members),
            "medoid": rows[order[0]].get("shot", ""),
            "medians": med,
            "jitter": jitter,
            "moving": int(moved_to.get(int(cid), 0)),
            # Cohesion in place of purity. Purity measured agreement with the
            # hand-written shape, which was a guess written before any data
            # existed and is no longer captured at all. The mean silhouette
            # of a cluster's own members says how well it holds together
            # without reference to any name.
            # The outlier tray has no silhouette - its members fit no
            # cluster by definition - so it gets None rather than a nan.
            "cohesion": _cohesion(sil_per, idx),
            "members": [{
                "shot": rows[i].get("shot", ""),
                "dwell": int(rows[i].get("dwell", 1)),
                "x": round(float(coords[i][0]), 4),
                "y": round(float(coords[i][1]), 4),
                "z": round(float(coords[i][2]), 4),
                "sil": (None if sil_per is None or math.isnan(sil_per[i])
                        else round(float(sil_per[i]), 3)),
                "feat": {name: rows[i].get(name, "") for name, _, _ in FEATURES},
            } for i in order],
        })

    report(out_clusters, rows, sil)

    if not args.no_thumbs:
        thumbs(out, rows)

    payload = {
        "session": " + ".join(os.path.basename(d) for d in dirs),
        "sessions": [os.path.basename(d) for d in dirs],
        "samples": len(raw),
        "scenes": len(rows),
        "silhouette": sil,
        "explained": explained,
        "features": [{"name": n, "transform": k, "weight": w} for n, k, w in FEATURES],
        "held_out": list(HELD_OUT),
        "clusters": out_clusters,
        "blind": blind_spots(rows, out_clusters),
    }
    js = os.path.join(out, "clusters.js")
    with open(js, "w", encoding="utf-8") as fh:
        # A script, not a JSON file: the viewer has to work over file://, and
        # fetch() is blocked there while <script src> is not.
        fh.write("const DATA = ")
        json.dump(payload, fh, ensure_ascii=False)
        fh.write(";\n")
    print("  wrote %s" % js)

    here = os.path.dirname(os.path.abspath(__file__))
    viewer_src = os.path.join(here, "viewer.html")
    viewer_dst = os.path.join(out, "viewer.html")
    if os.path.exists(viewer_src):
        shutil.copyfile(viewer_src, viewer_dst)
        print("\nopen this in a browser (no server needed):\n  %s" % viewer_dst)


def quality(s):
    if s >= 0.5:
        return "- clusters are well separated"
    if s >= 0.25:
        return "- clusters are real but overlapping; expect boundary disputes"
    return "- WEAK. The vector may not carry what separates these screens."


def blind_spots(rows, clusters):
    """How much of the scene set sits behind a surface the tree cannot read.

    It used to split this three ways - Web, XComponent, embedded - from the
    uitest coverage columns. Those are gone with the rest of the uitest
    reading, and the render-tree path reports only that a surface component
    is present, not which kind. Splitting it by kind on this path is not
    written yet, so the honest number is the one we have.
    """
    def num(r, k):
        try:
            return float(r.get(k) or 0)
        except ValueError:
            return 0.0

    n = len(rows)
    surf = [r for r in rows if num(r, "rs_surface_tag") >= 1]
    thin = [r for r in rows if num(r, "rs_nodes") and num(r, "rs_nodes") < 15]
    return {
        "n": n,
        "with_surface": len(surf),
        "describable": n - len(surf),
        "almost_no_nodes": len(thin),
    }

def _cohesion(sil_per, idx):
    if sil_per is None:
        return None
    vals = [float(sil_per[i]) for i in idx if not math.isnan(float(sil_per[i]))]
    return round(sum(vals) / len(vals), 3) if vals else None


def report(clusters, rows, sil):
    """What the geometry found, with nothing of what we expected it to find.

    There used to be a purity column, a design-language alarm, and two tables
    scoring the hand-written taxonomy against the clusters. All three read
    `shape` and `scene` - a class list written before any data existed, and a
    rule set firing on a window-scoped reading. Capture no longer produces
    either, and the approach they belonged to was the wrong one. A cluster is
    reported by what it measures and by how well it holds together.
    """
    print()
    print("%-5s %-5s %-7s %-7s %-9s %s"
          % ("id", "n", "dwell", "moving", "cohesion", "median geometry"))
    key = ["rs_node_den", "rs_depth_mean", "rs_leaf_frac", "rs_repeat_n",
           "rs_image_frac", "rs_paint_frac", "rs_scroller"]
    short = {"rs_node_den": "den", "rs_depth_mean": "depth", "rs_leaf_frac": "leaf",
             "rs_repeat_n": "rep", "rs_image_frac": "img", "rs_paint_frac": "paint",
             "rs_scroller": "scroll"}
    for c in clusters:
        geo = "  ".join("%s %s" % (short[k], c["medians"].get(k, "-")) for k in key)
        coh = "-" if c.get("cohesion") is None else "%.2f" % c["cohesion"]
        name = str(c["id"]) if c["id"] != -1 else "none"
        print("%-5s %-5d %-7d %-7d %-9s %s"
              % (name, c["n"], c["dwell"], c.get("moving", 0), coh, geo))
    if any(c["id"] == -1 for c in clusters):
        print("  none = fitted no cluster. Worth looking at by eye;")
        print("  a scene that fits nothing is reported, not forced into the nearest group.")

    print()
    print("  Every scene in every cluster is in viewer.html. The names are")
    print("  yours to write there - that is the step the clustering exists to set up.")


if __name__ == "__main__":
    main()
