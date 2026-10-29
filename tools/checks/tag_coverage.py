#!/usr/bin/env python3
"""INV-CLIENT-TAG-COVERAGE — how much of each scene can be named.

    ./tag_coverage.py <capture-root> [--session NAME]

For every scene, the share of the display's visible cells on kept canvas
nodes that carry a component tag from the client dump, each cell once:
the scene's tag_coverage. The tag is the node's component identity; it
comes from a client dump read apart from the render tree, so it can be
stale (a node created after the dump has none) or missing (the system UI
answers only in part). The coverage says how far the motif comparison can
be trusted. It is a confidence, never a filter: no node and no match is
removed for it.

ERR-MOTIF-ROOT-UNTAGGED names every scene under one half, as a report.

Exit 0 when every scene's coverage is a number in [0, 1], 1 otherwise.
"""
import argparse, os, statistics, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "cluster"))
import rs_sample as PAIR                 # noqa: E402
import rs_scene as S                     # noqa: E402

REPORT_BELOW = 0.5


def lines(path):
    return open(path, encoding="utf-8", errors="replace").read().splitlines() if path else []


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="the capture tree")
    ap.add_argument("--session", default="")
    a = ap.parse_args()

    cov, low, bad = [], [], []
    for ses, sample, tf, cf, disp in PAIR.scenes(a.root, a.session or None):
        sid = "%s/%s" % (ses, sample)
        row = S.Scene(lines(tf), lines(cf), disp).run()
        c = row.get("tag_coverage")
        if not isinstance(c, float) or not 0.0 <= c <= 1.0:
            bad.append("%s: tag_coverage is %r" % (sid, c))
            continue
        cov.append(c)
        if c < REPORT_BELOW:
            low.append((sid, c, cf is None))

    print("INV-CLIENT-TAG-COVERAGE")
    if cov:
        q = sorted(cov)
        print("  %d scene(s); tag coverage median %.2f, p10 %.2f, p90 %.2f, min %.2f; "
              "%d with no client dump at or before them"
              % (len(q), statistics.median(q), q[len(q) // 10], q[int(0.9 * len(q)) - 1 if len(q) > 1 else 0],
                 q[0], sum(1 for _, _, none in low if none)))
    if low:
        print("  ERR-MOTIF-ROOT-UNTAGGED: %d scene(s) under one half; their matches "
              "are kept: %s" % (len(low), ", ".join("%s %.2f" % (s, c) for s, c, _ in low[:6])
                                + (" and %d more" % (len(low) - 6) if len(low) > 6 else "")))
    for b in bad[:8]:
        print("    " + b)
    print("  VIOLATED" if bad else "  holds")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
