#!/usr/bin/env python3
"""The photographed samples of a capture: ALG-PICTURE-PAIR and ALG-TAG-BORROW.

A scene is a photographed sample: a row of samples.csv whose shot names a
picture, with the render tree read in that same sample (raw/<raw>.rstree.txt,
read before the shot). It is a scene only when

  - the picture is on disk,
  - the session has a client dump at or before the sample, whose tags it
    takes by node id (ALG-TAG-BORROW),
  - the display did not change across the shot: the pass on this sample's
    render tree and on the next sample's keeps the same nodes, every kept
    node's visible cell box within one cell per edge.

Every other picture is lost, with its reason, and is never dropped
uncounted (INV-NO-SILENT-SCENE-LOSS). Identical scenes are not merged.

The capture's own notion of a scene - its signature and its stem files - is
not read for identity; a stem file is only where a client dump is stored.
"""
import csv, glob, os

import rs_extract as R
import rs_scene as S

TOL = 1     # cells per edge a kept node's visible box may move across the shot

MISSING, NO_CLIENT, NO_NEXT, NO_DISPLAY, CHANGED = (
    "missing on disk", "no earlier client dump", "no next sample",
    "no display node", "display changed")


def _lines(path):
    return open(path, encoding="utf-8", errors="replace").read().splitlines()


def sessions(root, only=None):
    """(session, directory, display, samples.csv rows in recorded order)."""
    for sdir in sorted(glob.glob(os.path.join(root, "*"))):
        idx = os.path.join(sdir, "samples.csv")
        if not os.path.isdir(sdir) or not os.path.exists(idx):
            continue
        ses = os.path.basename(sdir)
        if only and ses != only:
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        if not os.path.exists(os.path.join(sdir, "display.txt")):
            disp = R.read_display(os.path.join(root, "display.txt"))
        with open(idx, encoding="utf-8-sig", newline="") as fh:
            yield ses, sdir, disp, list(csv.DictReader(fh))


def tag_source(sdir, rows, i):
    """ALG-TAG-BORROW: the session's last client dump at or before sample i."""
    for j in range(i, -1, -1):
        stem = (rows[j].get("stem") or "").strip()
        if stem:
            cf = os.path.join(sdir, stem + ".client.txt")
            if os.path.exists(cf):
                return cf
    return None


def kept_boxes(sc):
    """The pass's kept tree as node -> visible cell box (None for a node
    kept only for its kept children)."""
    return {nid: k["box"] for nid, k in sc.kept.items()}


def same_display(a, b, tol=TOL):
    if a.keys() != b.keys():
        return False
    for nid, x in a.items():
        y = b[nid]
        if (x is None) != (y is None):
            return False
        if x is not None and any(abs(p - q) > tol for p, q in zip(x, y)):
            return False
    return True


def candidates(root, only=None):
    """Every picture named in samples.csv, with what the pairing needs or
    the reason it cannot be paired. Cheap: nothing is parsed."""
    for ses, sdir, disp, rows in sessions(root, only):
        for i, r in enumerate(rows):
            shot = (r.get("shot") or "").strip()
            if not shot:
                continue
            c = {"session": ses, "sample": (r.get("raw") or "").strip(),
                 "shot": ses + "/" + shot, "picture": os.path.join(sdir, shot),
                 "disp": disp, "reason": None}
            c["tree"] = os.path.join(sdir, "raw", c["sample"] + ".rstree.txt")
            nxt = (rows[i + 1].get("raw") or "").strip() if i + 1 < len(rows) else ""
            c["next"] = os.path.join(sdir, "raw", nxt + ".rstree.txt") if nxt else None
            c["client"] = tag_source(sdir, rows, i)
            if not os.path.exists(c["picture"]):
                c["reason"] = MISSING
            elif c["client"] is None:
                c["reason"] = NO_CLIENT
            elif not c["next"] or not os.path.exists(c["next"]) or not os.path.exists(c["tree"]):
                c["reason"] = NO_NEXT
            yield c


def pair(root, only=None):
    """ALG-PICTURE-PAIR. Yields (candidate, scene, row): scene and row are
    the pass on the picture's own sample when the pair is accepted, and
    None when it is not, the candidate then carrying its reason."""
    for c in candidates(root, only):
        if c["reason"]:
            yield c, None, None
            continue
        client = _lines(c["client"])
        sc = S.Scene(_lines(c["tree"]), client, c["disp"])
        try:
            row = sc.run()
        except S.NoDisplay:
            c["reason"] = NO_DISPLAY
            yield c, None, None
            continue
        nx = S.Scene(_lines(c["next"]), client, c["disp"])
        try:
            nx.run()
            same = same_display(kept_boxes(sc), kept_boxes(nx))
        except S.NoDisplay:
            same = False
        if not same:
            c["reason"] = CHANGED
            yield c, None, None
            continue
        yield c, sc, row


def scenes(root, only=None):
    """The scenes rs_pattern.py measures, as (session, sample, render tree,
    client dump, display) - for the checks that run the pass themselves."""
    for c, sc, _ in pair(root, only):
        if sc is not None:
            yield c["session"], c["sample"], c["tree"], c["client"], c["disp"]
