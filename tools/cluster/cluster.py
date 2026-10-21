#!/usr/bin/env python3
"""Cluster captured screens on geometry alone, and say where ArkUI is blind.

The classifier in scene_class.ps1 is a DISTILLED form: twenty-three class
names written by hand before the data existed.  This does it the other way
round - take what real screens actually look like, group them on structure
alone, and let the class set fall out of the groups.

Two rules it is built around:

  Cluster SCREENS, not apps.  One app shows a video player, a chat, a
  settings page and a feed, often from one window.  Nothing about the app
  predicts the screen, so nothing identifying the app goes into the vector.
  The package name appears exactly once, in the purity alarm, AFTER the
  clustering - if a cluster turns out to be 90% one app, that cluster found
  a design language instead of a shape, and it should be thrown out.

  The hand-written taxonomy is HELD OUT.  `shape` is my guess at the answer.
  Feeding the guess in and reading it back out proves nothing, so it is
  scored against the clusters instead: a cluster that is cleanly one shape
  confirms it, a cluster split between two says those two are really one,
  and one shape spread over four clusters says it is too coarse.

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
    # column          transform   weight  what it separates
    ("rep_frac",      "permille", 1.0),   # how much of the band repeats
    ("rep_n",         "log1p",    0.7),   # 8 rows vs 40 rows: a ratio, not a gap
    ("cols",          "clip6",    1.2),   # gallery vs feed. The single best axis.
    ("item_h",        "permille", 1.0),   # a 70-permille row vs a 400-permille card
    ("item_aspect",   "logratio", 1.0),   # log, so 0.5 and 2.0 sit equally from square
    ("split_n",       "clip4",    0.7),
    ("split_ratio",   "permille", 0.7),
    ("overlay",       "permille", 0.7),   # a sheet, a dialog, a snackbar
    ("bar_top",       "permille", 0.5),
    ("bar_bot",       "permille", 0.5),
    ("bar_edit",      "flag",     0.5),
    ("pager",         "flag",     0.5),
    ("surf_frac",     "permille", 1.0),
    ("surf_visible",  "permille", 1.0),   # surface the user can actually see
    ("cover_web",     "permille", 0.7),
    ("cover_xc",      "permille", 0.7),
    ("cover_emb",     "permille", 0.7),
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
HELD_OUT = ("shape", "split_main", "scene", "name", "window", "pid", "described",
            "jitter", "still", "session", "rs_top_name", "rs_vsync")


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


def app_of(row):
    """Which app this screen belongs to.

    `window` is the numeric window id; `name` is the app. They were read the
    wrong way round, which made every purity figure a count of window ids -
    stable while an app stays up, and silently splitting it in two the moment
    it is killed and relaunched. That is exactly the shape of evidence the
    design-language alarm keys on, so it had to be wrong in the alarm's favour.
    """
    return row.get("name") or row.get("window") or "?"


def dedupe(rows):
    """One entry per distinct screen; how many samples it had is its dwell.

    Without this, half an hour parked on one feed outvotes every other screen
    in the session and the clusters describe how the phone was used rather
    than what screens exist.
    """
    groups = defaultdict(list)
    for r in rows:
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
    print("  %d distinct screens (deduplicated by layout signature)" % len(rows))
    if len(rows) < 10:
        sys.exit("too few distinct screens to cluster. Capture more.")

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

    coords = PCA(n_components=2).fit_transform(Z) if Z.shape[1] >= 2 else np.zeros((len(rows), 2))

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

        apps = Counter(app_of(m) for m in members)
        top_app, top_n = apps.most_common(1)[0]
        purity = top_n / len(members)
        shapes = Counter(m.get("shape", "?") for m in members)
        scenes = Counter(m.get("scene", "?") for m in members)

        med = {}
        for name, kind, _ in FEATURES:
            med[name] = round(float(np.median([transform(kind, m.get(name)) for m in members])), 3)

        jit = [int(float(m["jitter"])) for m in members
               if str(m.get("jitter", "")).strip() not in ("", "None")]
        jitter = int(np.median(jit)) if jit else None

        out_clusters.append({
            "id": int(cid),
            "n": len(members),
            "dwell": sum(int(m.get("dwell", 1)) for m in members),
            "medoid": rows[order[0]].get("shot", ""),
            "apps": apps.most_common(8),
            "purity": round(purity, 3),
            "top_app": top_app,
            "shapes": shapes.most_common(),
            "scenes": scenes.most_common(),
            "medians": med,
            "jitter": jitter,
            "moving": int(moved_to.get(int(cid), 0)),
            "members": [{
                "shot": rows[i].get("shot", ""),
                "shape": rows[i].get("shape", ""),
                "scene": rows[i].get("scene", ""),
                "window": app_of(rows[i]),
                "dwell": int(rows[i].get("dwell", 1)),
                "x": round(float(coords[i][0]), 4),
                "y": round(float(coords[i][1]), 4),
                "sil": (None if sil_per is None or math.isnan(sil_per[i])
                        else round(float(sil_per[i]), 3)),
                "feat": {name: rows[i].get(name, "") for name, _, _ in FEATURES},
                "held": {name: rows[i].get(name, "") for name in HELD_OUT},
            } for i in order],
        })

    flagged = verdicts(out_clusters)
    report(out_clusters, rows, sil, flagged)

    if not args.no_thumbs:
        thumbs(out, rows)

    payload = {
        "session": " + ".join(os.path.basename(d) for d in dirs),
        "sessions": [os.path.basename(d) for d in dirs],
        "n_samples": len(raw),
        "n_screens": len(rows),
        "silhouette": sil,
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


def verdicts(clusters):
    """Which apps are being separated by their house style rather than shape.

    Purity on its own flags two very different things, and loudly flagging
    both made it useless: in test it fired on eight clusters of twelve when
    exactly one app was the planted trap.

    A cluster can be one app because nobody else in the session showed that
    shape - a gap in the capture, not a fault in the clustering.  Or it can
    be one app because that app's design language separates ALL of its
    screens from everyone else's.  The second is the failure this guards
    against, and the signature is not one cluster: it is one app carved out
    ACROSS SEVERAL SHAPES AT ONCE, while other apps produce those same
    shapes together elsewhere.  So the test is per app, not per cluster.
    """
    own = defaultdict(list)          # app -> clusters where it is >=90%
    total = Counter()                # app -> screens anywhere
    shape_apps = defaultdict(set)    # hand shape -> apps producing it
    for c in clusters:
        if c["id"] == -1:
            continue
        for app, k in c["apps"]:
            total[app] += k
        if c["purity"] >= 0.9 and c["shapes"]:
            own[c["top_app"]].append(c)
        for shp, _ in c["shapes"]:
            for app, _k in c["apps"]:
                shape_apps[shp].add(app)

    for c in clusters:
        c["verdict"] = ""
        c["rival_apps"] = []

    flagged = []
    for app, cs in own.items():
        shapes = {c["shapes"][0][0] for c in cs}
        held = sum(c["n"] for c in cs)
        if len(shapes) < 2:
            continue                       # one shape: a real geometric split
        if held < 0.8 * max(1, total[app]):
            continue                       # it mixes with others elsewhere
        rivals = set()
        for shp in shapes:
            rivals |= (shape_apps.get(shp, set()) - {app})
        if not rivals:
            continue                       # nothing to compare against
        for c in cs:
            c["verdict"] = "design-language"
            c["rival_apps"] = sorted(rivals)[:6]
        flagged.append((app, sorted(shapes), [c["id"] for c in cs], held, sorted(rivals)[:6]))

    for app, cs in own.items():
        for c in cs:
            if not c["verdict"]:
                c["verdict"] = "sole-app"
    return flagged


def quality(s):
    if s >= 0.5:
        return "- clusters are well separated"
    if s >= 0.25:
        return "- clusters are real but overlapping; expect boundary disputes"
    return "- WEAK. The vector may not carry what separates these screens."


def blind_spots(rows, clusters):
    """Where the tree cannot describe what the screenshot shows.

    This is the number the power team needs: it decides whether an ArkWeb
    producer is worth building, and nothing else in this pipeline produces it.
    """
    def pm(r, k):
        try:
            return float(r.get(k) or 0)
        except ValueError:
            return 0.0

    n = len(rows)
    web = [r for r in rows if pm(r, "cover_web") >= 500]
    xc = [r for r in rows if pm(r, "cover_xc") >= 500]
    emb = [r for r in rows if pm(r, "cover_emb") >= 500]
    opaque = [r for r in rows if pm(r, "cover_web") + pm(r, "cover_xc") + pm(r, "cover_emb") >= 500]
    thin = [r for r in rows if float(r.get("total") or 0) < 15]

    # A cluster tight in feature space but mixed in hand-label is the worse
    # failure: ArkUI cannot tell apart screens that look different.
    mixed = []
    for c in clusters:
        if c["id"] == -1 or c["n"] < 5:
            continue
        shapes = dict(c["shapes"])
        top = max(shapes.values())
        if top / c["n"] < 0.6:
            mixed.append({"id": c["id"], "n": c["n"], "shapes": c["shapes"]})

    return {
        "n": n,
        "describable": n - len(opaque),
        "mostly_web": len(web),
        "mostly_xcomponent": len(xc),
        "mostly_embedded": len(emb),
        "mostly_opaque": len(opaque),
        "almost_no_nodes": len(thin),
        "mixed_clusters": mixed,
    }


def report(clusters, rows, sil, flagged):
    print("\n%-5s %-6s %-7s %-7s %-22s %-7s %-7s %s"
          % ("id", "n", "dwell", "moving", "dominant shape", "purity", "jitter", "top app"))
    for c in clusters:
        if c["id"] == -1:
            continue
        shp = ", ".join("%s %d" % (k, v) for k, v in c["shapes"][:3])
        flag = {"design-language": "  <-- SUSPECT, see below",
                "sole-app": "  <-- only app showing this"}.get(c.get("verdict"), "")
        jit = "-" if c.get("jitter") is None else str(c["jitter"])
        print("%-5d %-6d %-7d %-7d %-22s %-7.2f %-7s %s%s"
              % (c["id"], c["n"], c["dwell"], c.get("moving", 0), shp[:22], c["purity"],
                 jit, c["top_app"][:18], flag))
    out = [c for c in clusters if c["id"] == -1]
    if out:
        print("%-5s %-6d %-7d %-7s %-22s %-7s %-7s %s"
              % ("none", out[0]["n"], out[0]["dwell"], "-", "(outliers)", "-", "-",
                 "worth looking at by eye"))

    if flagged:
        print("\nSUSPECT - these apps are being separated by house style, not shape.")
        print("Each is carved out across several shapes at once while other apps")
        print("produce those same shapes together:")
        for app, shapes, ids, held, rivals in flagged:
            print("  %s: %d screens, alone in clusters %s, spanning %s"
                  % (app, held, ",".join(str(i) for i in ids), ", ".join(shapes)))
            print("     the same shapes are produced by %s" % ", ".join(rivals))
        print("  -> look at the screenshots. If they look like everyone else's, the")
        print("     vector is splitting on decoration and these clusters should go.")

    sole = [c for c in clusters if c.get("verdict") == "sole-app"]
    if sole:
        print("\none app only, nothing yet to compare against. A gap in the capture,")
        print("not necessarily a fault in the clustering:")
        for c in sole:
            print("  cluster %d: all %s, %s" % (c["id"], c["top_app"], c["shapes"][0][0]))

    # The headline: how the measured groups line up against the hand taxonomy.
    hand = {}
    for c in clusters:
        if c["id"] == -1:
            continue
        for shp, k in c["shapes"]:
            hand.setdefault(shp, {"screens": 0, "clusters": set()})
            hand[shp]["screens"] += k
            hand[shp]["clusters"].add(c["id"])
    print("\nthe hand-written taxonomy against the measured clusters:")
    print("  %-10s %-8s %s" % ("shape", "screens", "clusters it is spread over"))
    for shp in sorted(hand, key=lambda k: -hand[k]["screens"]):
        ids = sorted(hand[shp]["clusters"])
        note = ""
        if len(ids) >= 3:
            note = "  <- too coarse: the data wants this split"
        print("  %-10s %-8d %s%s" % (shp, hand[shp]["screens"],
                                     ",".join(str(i) for i in ids), note))

    # The same question asked of the scorer's own answer. `scene` is one of
    # twenty-three class names written by hand before any data existed, and
    # nothing it produces - not the class, not the score, not the confidence -
    # enters the vector. So this table is a genuine test of it rather than a
    # restatement: where it agrees with a cluster it earned that, and where it
    # does not, the cluster is the measurement and the name is the guess.
    scen = {}
    for c in clusters:
        if c["id"] == -1:
            continue
        for sc, k in c.get("scenes", []):
            scen.setdefault(sc, {"screens": 0, "clusters": set()})
            scen[sc]["screens"] += k
            scen[sc]["clusters"].add(c["id"])
    if scen:
        split = sum(1 for v in scen.values() if len(v["clusters"]) >= 3)
        print("\nthe scorer's class against the measured clusters")
        print("  (held out of the vector entirely - this is a test, not a restatement):")
        print("  %-16s %-8s %s" % ("scene", "screens", "clusters it is spread over"))
        for sc in sorted(scen, key=lambda k: -scen[k]["screens"]):
            ids = sorted(scen[sc]["clusters"])
            note = ""
            if len(ids) == 1:
                note = "  <- clean"
            elif len(ids) >= 3:
                note = "  <- too coarse, or firing on the wrong evidence"
            print("  %-16s %-8d %s%s" % (sc, scen[sc]["screens"],
                                         ",".join(str(i) for i in ids), note))

        # And the other direction, which is the more damning one: a cluster is
        # ONE kind of screen by construction, so several class names inside it
        # are several names for the same thing.
        muddled = [c for c in clusters
                   if c["id"] != -1 and len(c.get("scenes", [])) >= 3]
        if muddled:
            print("\n  clusters the scorer could not keep to one name:")
            for c in muddled:
                names = ", ".join("%s %d" % (k, v) for k, v in c["scenes"][:5])
                print("    cluster %-3d %s" % (c["id"], names))
            print("    these screens group tightly on geometry, so either those")
            print("    names are one class, or the rule firing is the wrong one.")
        if split:
            print("\n  %d of %d class names are spread over 3+ clusters. The class"
                  % (split, len(scen)))
            print("  list was written before any data existed; this is the data.")

    b = blind_spots(rows, clusters)
    print("\nwhere ArkUI is blind, over %d distinct screens:" % b["n"])
    print("  %d (%.0f%%) describable from the tree alone"
          % (b["describable"], 100.0 * b["describable"] / max(1, b["n"])))
    print("  %d mostly behind a Web region      - recoverable in-process by an ArkWeb producer"
          % b["mostly_web"])
    print("  %d mostly behind an XComponent     - a buffer queue, outside ArkUI permanently"
          % b["mostly_xcomponent"])
    print("  %d mostly behind an embedded UI    - another ArkUI instance; closed by routing, not a new producer"
          % b["mostly_embedded"])
    if b["almost_no_nodes"]:
        print("  %d dumps with almost no nodes at all" % b["almost_no_nodes"])
    for m in b["mixed_clusters"]:
        print("  cluster %d groups %d screens that the hand taxonomy calls %s"
              % (m["id"], m["n"], ", ".join("%s(%d)" % (k, v) for k, v in m["shapes"])))
        print("    -> either those names are one shape, or the vector is missing what splits them")


if __name__ == "__main__":
    main()
