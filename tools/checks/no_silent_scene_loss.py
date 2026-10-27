#!/usr/bin/env python3
"""INV-NO-SILENT-SCENE-LOSS — the check, and the reproduction it came from.

    ./no_silent_scene_loss.py --patterns patterns.csv --payload two_payload.json

Every row measured into patterns.csv either reaches the payload or is
reported there with a reason. The arithmetic is the whole invariant:

    rows in the csv  ==  scenes in the payload  +  scenes reported missing

It is written as a check rather than as prose because it failed silently
for as long as it existed. vectors_build.py resolved each screenshot from
a flat directory named <session>__<stem>.jpeg that no code in this
repository builds - it was made by hand, once, and was incomplete - and
dropped every row whose file it could not find, with no count and no list.
On the capture this was written against that was 71 of 250 scenes, and the
cluster count, the outlier rate and the silhouette the page published were
all computed over the remaining 179 without saying so.

With --captures it also audits the premise directly: how many of the rows
have a picture in the capture tree at <captures>/<shot>, and how many have
one only under the flat form. Those two numbers are what showed the loss
was an addressing mistake rather than missing data.

It also reports where each shown picture came from. A tile can hold a
frame taken for its own scene or a periodic frame borrowed from a sample
while that scene was on the display, and those are not the same evidence.
The split is checked against the payload's audit so the two cannot drift:
a page that fills a tile from a periodic frame and does not say so is
showing something it did not measure.

Exit 0 when the invariant holds, 1 when it does not.
"""
import argparse, csv, json, os, sys


def payload_scenes(path):
    """Every scene the page will show, and every one it reports missing."""
    d = json.load(open(path, encoding="utf-8"))
    shown, missing = set(), set()
    audit = d.get("_audit") or {}
    anonymous = 0
    for key, v in d.items():
        if key.startswith("_"):
            continue
        for g in v.get("groups", []):
            for r in g.get("reps", []):
                sid = r.get("scene")
                if sid:
                    shown.add(sid)
                else:
                    anonymous += 1
    for m in audit.get("missing", []):
        missing.add(m)
    return shown, missing, audit, anonymous


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--payload", default="",
                    help="vectors_build.py's json; omit to audit the premise only")
    ap.add_argument("--captures", default="",
                    help="the capture tree, to audit where the pictures are")
    ap.add_argument("--reps", default="",
                    help="the flat <session>__<stem>.jpeg directory, if one exists")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    print("INV-NO-SILENT-SCENE-LOSS")
    print("  rows in %s: %d" % (os.path.basename(a.patterns), len(rows)))

    # The premise. Where are the pictures actually?
    if a.captures or a.reps:
        in_tree = in_flat = 0
        for r in rows:
            shot = (r.get("shot") or "").strip()
            if not shot:
                continue
            if a.captures and os.path.exists(os.path.join(a.captures, *shot.split("/"))):
                in_tree += 1
            if a.reps and os.path.exists(os.path.join(a.reps, shot.replace("/", "__"))):
                in_flat += 1
        if a.captures:
            print("  with a picture at <captures>/<shot>      : %d" % in_tree)
        if a.reps:
            print("  with a picture under the flat form       : %d" % in_flat)

    if not a.payload:
        print("  payload not given; premise audited only, invariant not checked")
        return 0

    shown, missing, audit, anonymous = payload_scenes(a.payload)
    if anonymous:
        print("  %d tile(s) in the payload identify no scene" % anonymous)
        print("  a page that cannot name what it shows cannot be reconciled")
        print("  with what was measured, so no row can be accounted for.")
        print("  VIOLATED")
        return 1
    print("  scenes in the payload   : %d" % len(shown))
    print("  reported missing        : %d" % len(missing))

    # Where the shown pictures came from, and whether the tiles agree with
    # the audit. A borrowed frame that is not marked is the failure this
    # reports: the page would be showing another sample as if it were this
    # scene's own.
    own, per = audit.get("own"), audit.get("periodic")
    if own is not None or per is not None:
        print("  of those shown          : %s taken for the scene, %s borrowed "
              "from a periodic frame" % (own, per))
        d = json.load(open(a.payload, encoding="utf-8"))
        marked, unmarked = set(), set()
        for key, v in d.items():
            if key.startswith("_"):
                continue
            for g in v.get("groups", []):
                for r in g.get("reps", []):
                    if not r.get("img"):
                        continue
                    (marked if r.get("imgsrc") == "periodic" else unmarked).add(
                        r.get("scene"))
        print("  tiles marked periodic   : %d" % len(marked))
        if per is not None and len(marked) != per:
            print("  the audit says %s borrowed but %d tile(s) say so"
                  % (per, len(marked)))
            print("  VIOLATED")
            return 1
    if audit.get("rows") is not None:
        print("  payload says it measured: %d" % audit["rows"])

    # Shown and reported are not disjoint: a scene with no picture still gets
    # a tile and is also listed in the audit. The invariant is that the union
    # covers everything measured, not that the counts sum to it.
    measured = {"%s/%s" % (r.get("session", ""), r.get("stem", "")) for r in rows}
    accounted = shown | missing
    lost = measured - accounted
    if lost:
        print("  %d row(s) measured, %d accounted for - %d vanished with no reason:"
              % (len(measured), len(accounted & measured), len(lost)))
        for sid in sorted(lost)[:10]:
            print("    %s" % sid)
        print("  VIOLATED")
        return 1
    stray = accounted - measured
    if stray:
        print("  %d scene(s) in the payload were never measured: %s"
              % (len(stray), ", ".join(sorted(stray)[:5])))
        print("  VIOLATED")
        return 1
    print("  every measured row is shown or reported")
    print("  holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
