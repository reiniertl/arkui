#!/usr/bin/env python3
"""INV-EVERY-DISTINCT-SCENE-PHOTOGRAPHED — every scene has a picture.

    ./capture_coverage.py <capture-root> [--session NAME]

Per session, over the scenes the capture recorded:

  settled   <stem>.jpeg, written when the same scene was seen on two
            consecutive samples, so the screen is known to have held
            across the dump and the picture shows what it described
  backstop  p<nnnnnn>.jpeg, taken on arrival at a scene with no picture
            yet, attributed through the sig of the sample its number
            names. Weaker: nothing confirms the screen had not already
            moved on
  none      a scene nobody can look at. ERR-SCENE-WITHOUT-REP

The two are counted apart because they are not the same evidence, and a
check that added them would hide the capture degrading from one into the
other.

This exists because the rule it checks was wrong for as long as it
existed. The backstop fired on a 5-second wall clock, and at 0.3-0.5s per
sample that is every 10 to 16 samples - so it photographed whatever sat
on the display longest, which is the scene that had already earned a
settled frame, and never the short-lived ones. Over six sessions on
2026-10-27: 273 scenes, 198 settled, 73 with nothing, and 167 of the 169
backstop frames of the two October 22 sessions spent on a scene that
already had its own picture.

The rule now fires on arrival at a scene with no picture yet. On the first
capture taken under it: 27 scenes, 18 settled, 9 carried by a backstop
frame, 0 with none.

Exit 0 when every scene in every session has a picture, 1 when any does
not. A session captured before the rule changed will fail, and should.
"""
import argparse, csv, glob, os, re, sys


def session_rows(sdir):
    path = os.path.join(sdir, "samples.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def cover(sdir):
    """settled, backstop, none — by stem, for one session."""
    rows = session_rows(sdir)
    stem_of_sig = {}
    for r in rows:
        st = (r.get("stem") or "").strip()
        sg = (r.get("sig") or "").strip()
        if st and sg:
            stem_of_sig.setdefault(sg, st)

    # Scenes are the dumps on disk, not the index: the dump is what gets
    # measured, so a scene with no dump is not a scene this has to cover.
    scenes = sorted(os.path.basename(f).split(".")[0]
                    for f in glob.glob(os.path.join(sdir, "*.rstree.txt")))

    backstop, frame_of = {}, {}
    def claim(sig, fname):
        st = stem_of_sig.get(sig)
        if st and fname.startswith("p") and os.path.exists(os.path.join(sdir, fname)):
            backstop.setdefault(st, fname)
            frame_of[fname] = st          # every frame, not one per scene
    for r in rows:
        claim((r.get("sig") or "").strip(), (r.get("shot") or "").strip())
    for f in sorted(glob.glob(os.path.join(sdir, "p*.jpeg"))):
        m = re.match(r"p(\d{6})\.jpeg$", os.path.basename(f))
        if m and 1 <= int(m.group(1)) <= len(rows):
            claim((rows[int(m.group(1)) - 1].get("sig") or "").strip(),
                  os.path.basename(f))

    settled, borrowed, none = [], [], []
    for st in scenes:
        if os.path.exists(os.path.join(sdir, st + ".jpeg")):
            settled.append(st)
        elif st in backstop:
            borrowed.append(st)
        else:
            none.append(st)
    # Backstop frames of a scene that also has a settled one, so the frame
    # is not the picture anyone sees. Counted per FRAME, not per scene: one
    # long-lived scene could absorb dozens.
    #
    # It means two different things and the number alone does not say which.
    # Under the wall clock it meant the frame was taken of the wrong scene -
    # one that already had its picture while a short-lived scene went without.
    # Under the arrival rule it means the scene settled AFTER its arrival
    # frame was taken, so the frame was provisional and a better one replaced
    # it. The first is waste, the second is the cost of covering a scene
    # before knowing whether it will last. What separates them is the `none`
    # column: frames spent while scenes go unphotographed is the failure.
    superseded = sum(1 for st in frame_of.values() if st in settled)
    return settled, borrowed, none, superseded, len(glob.glob(os.path.join(sdir, "p*.jpeg")))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--session", default="", help="one session, instead of all")
    a = ap.parse_args()

    dirs = [d for d in sorted(glob.glob(os.path.join(a.root, "*")))
            if os.path.isdir(d) and (not a.session
                                     or os.path.basename(d) == a.session)]
    if not dirs:
        raise SystemExit("no session under %s" % a.root)

    print("INV-EVERY-DISTINCT-SCENE-PHOTOGRAPHED, over %s" % a.root)
    print("  %-20s %7s %8s %9s %6s %11s"
          % ("session", "scenes", "settled", "backstop", "none", "superseded"))
    T = [0, 0, 0, 0, 0]
    bad = []
    for sdir in dirs:
        settled, borrowed, none, superseded, nfiles = cover(sdir)
        n = len(settled) + len(borrowed) + len(none)
        print("  %-20s %7d %8d %9d %6d %11d"
              % (os.path.basename(sdir), n, len(settled), len(borrowed),
                 len(none), superseded))
        for v, k in ((n, 0), (len(settled), 1), (len(borrowed), 2),
                     (len(none), 3), (superseded, 4)):
            T[k] += v
        if none:
            bad.append((os.path.basename(sdir), none))
    print("  %-20s %7d %8d %9d %6d %11d"
          % ("TOTAL", T[0], T[1], T[2], T[3], T[4]))

    if bad:
        print("\nERR-SCENE-WITHOUT-REP: %d scene(s) have no picture at all"
              % sum(len(x[1]) for x in bad))
        for sess, none in bad:
            print("  %s: %s%s" % (sess, ", ".join(none[:8]),
                                  " and %d more" % (len(none) - 8)
                                  if len(none) > 8 else ""))
        print("  VIOLATED")
        return 1
    print("\n  every scene in every session has a picture")
    print("  holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
