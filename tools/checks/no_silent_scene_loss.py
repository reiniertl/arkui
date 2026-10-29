#!/usr/bin/env python3
"""INV-NO-SILENT-SCENE-LOSS — every picture is a scene or is reported.

    ./no_silent_scene_loss.py <capture-root> --patterns patterns.csv --payload two_payload.json

A scene is a photographed sample. Every picture a session's samples.csv
names either became a scene - a row of patterns.csv - or is in
patterns.lost.csv with its reason; and every row reaches the payload with
its own picture. The arithmetic is the whole invariant:

    pictures named  ==  rows  +  lost
    rows            ==  scenes in the payload, each with its picture

The pictures are counted here from samples.csv itself, not from either
output, so a picture both outputs forgot is still found.

It is written as a check because the rule it replaces failed silently for
as long as it existed: vectors_build.py once resolved screenshots from a
directory no code built and dropped 71 of 250 scenes uncounted.

Exit 0 when the invariant holds, 1 when it does not.
"""
import argparse, csv, glob, json, os, sys

REASONS = {"missing on disk", "no next sample", "no display node", "display changed"}


def named(root):
    """session/sample -> session/picture, for every picture samples.csv names."""
    out = {}
    for idx in sorted(glob.glob(os.path.join(root, "*", "samples.csv"))):
        ses = os.path.basename(os.path.dirname(idx))
        with open(idx, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if (r.get("shot") or "").strip():
                    out["%s/%s" % (ses, r["raw"].strip())] = ses + "/" + r["shot"].strip()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="the capture tree")
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--payload", required=True)
    a = ap.parse_args()

    pictures = named(a.root)
    rows = {"%s/%s" % (r["session"], r["sample"]): r
            for r in csv.DictReader(open(a.patterns, encoding="utf-8-sig"))}
    lp = os.path.splitext(a.patterns)[0] + ".lost.csv"
    lost = ({"%s/%s" % (x["session"], x["sample"]): x
             for x in csv.DictReader(open(lp, encoding="utf-8-sig"))}
            if os.path.exists(lp) else None)
    d = json.load(open(a.payload, encoding="utf-8"))
    scenes = d.get("_scenes") or {}

    bad = []
    if lost is None:
        bad.append("%s is not there: no lost picture can be accounted for" % lp)
        lost = {}
    both = set(rows) & set(lost)
    if both:
        bad.append("%d picture(s) are both a scene and lost, e.g. %s" % (len(both), sorted(both)[0]))
    unaccounted = set(pictures) - set(rows) - set(lost)
    if unaccounted:
        bad.append("%d picture(s) named in samples.csv are neither a scene nor lost, e.g. %s"
                   % (len(unaccounted), ", ".join(sorted(unaccounted)[:5])))
    stray = (set(rows) | set(lost)) - set(pictures)
    if stray:
        bad.append("%d row(s) name a sample with no picture in samples.csv, e.g. %s"
                   % (len(stray), sorted(stray)[0]))
    for k, r in rows.items():
        if k in pictures and r["shot"] != pictures[k]:
            bad.append("%s carries %s, its sample's picture is %s" % (k, r["shot"], pictures[k]))
    odd = sorted({x["reason"] for x in lost.values()} - REASONS)
    if odd:
        bad.append("lost pictures with no recognised reason: %s" % odd)
    no_tile = sorted(k for k in rows if not (scenes.get(k) or {}).get("img"))
    if no_tile:
        bad.append("%d scene(s) reach the payload without their picture, e.g. %s"
                   % (len(no_tile), ", ".join(no_tile[:5])))
    extra = sorted(set(scenes) - set(rows))
    if extra:
        bad.append("%d scene(s) in the payload were never measured, e.g. %s"
                   % (len(extra), extra[0]))

    by = {}
    for x in lost.values():
        by[x["reason"]] = by.get(x["reason"], 0) + 1
    print("INV-NO-SILENT-SCENE-LOSS")
    print("  pictures named in samples.csv: %d; scenes: %d; lost: %d%s"
          % (len(pictures), len(rows), len(lost),
             "".join(" - %d %s" % (n, w) for w, n in sorted(by.items()))))
    print("  scenes in the payload with their own picture: %d"
          % sum(1 for k in rows if (scenes.get(k) or {}).get("img")))
    for b in bad[:8]:
        print("    " + b)
    if lost:
        print("  ERR-PICTURE-LOST: %d picture(s) became no scene, each named with its reason"
              % len(lost))
    if bad:
        print("  ERR-PICTURE-LOST\n  VIOLATED")
        return 1
    print("  holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
