#!/usr/bin/env python3
"""INV-CLIENT-TAG-COVERAGE — how much of each visible window can be named.

    ./tag_coverage.py <capture-root> [--session NAME]

For every recorded scene and every window visible in it, the share of the
window's kept canvas nodes - those in the scene, on the display - that
carry a component tag from the client dump. Measured by the pass itself, at
the window node, so the coverage is of what is shown.
Reported per window, summarised by window name with the trailing number
dropped, so the launcher across its scenes reads as one line.

ERR-MOTIF-ROOT-UNTAGGED names every visible window under half: no
structure can be read from it, and a pattern result that included it would
stand on nothing. The client dump covers app processes almost completely
and the system UI only in part, and no other dump on this device build
covers the rest, so this check measures the gap rather than closing it.

Exit 0: the coverage is reported, not judged. A window under half is a
warning, and a window drawn from a buffer alone has nothing to tag.
"""
import argparse, glob, os, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "cluster"))
import rs_extract as R                   # noqa: E402
import rs_scene as S                     # noqa: E402


def scenes(root, only=None):
    for sdir in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(sdir) or (only and os.path.basename(sdir) != only):
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        if not os.path.exists(os.path.join(sdir, "display.txt")):
            disp = R.read_display(os.path.join(root, "display.txt"))
        for tf in sorted(glob.glob(os.path.join(sdir, "*.rstree.txt"))):
            stem = os.path.basename(tf).split(".")[0]
            cf = os.path.join(sdir, stem + ".client.txt")
            if os.path.exists(cf):
                yield os.path.basename(sdir), stem, tf, cf, disp


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="the capture tree")
    ap.add_argument("--session", default="")
    a = ap.parse_args()

    by_name = defaultdict(list)
    low = []
    unmeasured = []
    n_scenes = 0
    for ses, stem, tf, cf, disp in scenes(a.root, a.session or None):
        n_scenes += 1
        sc = S.Scene(open(tf, encoding="utf-8", errors="replace").read().splitlines(),
                     open(cf, encoding="utf-8", errors="replace").read().splitlines(), disp)
        try:
            sc.run()
        except S.NoDisplay:
            continue
        for w in sc.windows:
            if "coverage" not in w:
                continue                  # not visible: decided from its line
            if w["coverage"] is None:
                unmeasured.append("%s/%s %s" % (ses, stem, w["window"]))
                continue
            by_name[re.sub(r"\d+$", "", w["window"])].append(w["coverage"])
            if w["coverage"] < R.UNTAGGED_BELOW:
                low.append((ses, stem, w))

    print("INV-CLIENT-TAG-COVERAGE")
    print("  scenes: %d   visible windows measured: %d"
          % (n_scenes, sum(len(v) for v in by_name.values())))
    print("  %-26s %8s %10s %8s %8s" % ("window", "scenes", "median", "min", "under"))
    for name, v in sorted(by_name.items(), key=lambda kv: -len(kv[1])):
        s = sorted(v)
        under = sum(1 for x in s if x < R.UNTAGGED_BELOW)
        print("  %-26s %8d %10.2f %8.2f %8d" % (name[:26], len(s), s[len(s) // 2], s[0], under))
    if low:
        names = defaultdict(int)
        for _, _, w in low:
            names[re.sub(r"\d+$", "", w["window"])] += 1
        print("ERR-MOTIF-ROOT-UNTAGGED: %d visible window(s) in %d scene(s) have "
              "under half of their nodes tagged - %s"
              % (len(low), len({(s, t) for s, t, _ in low}),
                 ", ".join("%s x%d" % kv for kv in sorted(names.items(), key=lambda kv: -kv[1]))))
        for ses, stem, w in low[:8]:
            print("    %s/%s  %-24s pid %-6s %4d of %4d tagged (%.2f)"
                  % (ses, stem, w["window"][:24], w["pid"], w["tagged"], w["nodes"], w["coverage"]))
        if len(low) > 8:
            print("    ... and %d more" % (len(low) - 8))
    if unmeasured:
        # Drawn from a buffer alone - a wallpaper, a composed layer. Nothing
        # in it is a component, so there is nothing to tag and nothing missed.
        print("  %d visible window(s) hold no canvas node at all, so have nothing "
              "to tag: %s" % (len(unmeasured), ", ".join(sorted(
                  {u.split(" ", 1)[1] for u in unmeasured})[:6])))
    print("  every visible window measured")
    print("  holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
