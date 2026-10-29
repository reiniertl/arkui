#!/usr/bin/env python3
"""Measure every photographed sample into one row of IF-PATTERNS-ROW.

    ./rs_pattern.py <capture-root> [--out patterns.csv]

A scene is a photographed sample accepted by rs_sample.pair
(ALG-PICTURE-PAIR): a picture and the render tree read in the same sample,
with the display unchanged in the next. Each is measured by rs_scene.Scene in
one pass over that render tree. Every picture that became no scene is written
beside the output, <out>.lost.csv, with its reason. This file only runs the
pairing and writes the rows; it measures nothing itself.
"""
import argparse, csv, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rs_sample as P                    # noqa: E402


def listed(ids, limit=5):
    head = ", ".join(ids[:limit])
    return head + (" and %d more" % (len(ids) - limit) if len(ids) > limit else "")


REPORT_BELOW = 0.5   # ERR-MOTIF-ROOT-UNTAGGED is reported under this coverage


def report(lost, low):
    """Say what the run did not measure. Each of these is a picture named in
    samples.csv that became no scene, or a window that could not be read."""
    by = {}
    for c in lost:
        by.setdefault(c["reason"], []).append("%s/%s" % (c["session"], c["sample"]))
    for why, ids in sorted(by.items()):
        print("ERR-PICTURE-LOST: %d picture(s), %s: %s" % (len(ids), why, listed(ids)))
    changed = by.get(P.CHANGED, [])
    if changed:
        print("ERR-PICTURE-NOT-SAME-MOMENT: %d picture(s) refused, the display changed "
              "across the shot" % len(changed))
    nodisp = by.get(P.NO_DISPLAY, [])
    if nodisp:
        print("ERR-NO-DISPLAY-NODE: %d sample(s) have no single LOGICAL_DISPLAY_NODE "
              "to start from: %s" % (len(nodisp), listed(nodisp)))
    if low:
        print("ERR-MOTIF-ROOT-UNTAGGED: %d scene(s) have under half of their visible "
              "area tagged; their matches are kept, the coverage is reported: %s"
              % (len(low), listed(["%s %.2f" % x for x in low])))


def lost_path(out):
    return os.path.splitext(out)[0] + ".lost.csv"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="directory holding capture sessions")
    ap.add_argument("--out", default="patterns.csv")
    a = ap.parse_args()
    rows, lost, low = [], [], []
    for c, sc, row in P.pair(a.root):
        if sc is None:
            lost.append(c)
            continue
        r = {"session": c["session"], "sample": c["sample"], "shot": c["shot"]}
        r.update(row)
        rows.append(r)
        if row["tag_coverage"] < REPORT_BELOW:
            low.append(("%s/%s" % (c["session"], c["sample"]), row["tag_coverage"]))
    report(lost, low)
    with open(lost_path(a.out), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["session", "sample", "shot", "reason"])
        w.writeheader()
        for c in lost:
            w.writerow({k: c[k] for k in ("session", "sample", "shot", "reason")})
    if not rows:
        print("nothing to do"); return 1
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("  %d pictures: %d scenes -> %s, %d lost -> %s"
          % (len(rows) + len(lost), len(rows), a.out, len(lost), lost_path(a.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
