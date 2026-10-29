#!/usr/bin/env python3
"""Measure every captured scene into one row of IF-PATTERNS-ROW.

    ./rs_pattern.py <capture-root> [--out patterns.csv]

Each scene is measured by rs_scene.Scene in one pass over its dump: the
scene is the display's kept tree, and the pattern vector and the surface
vector are read from it as it is built. This file only finds the dumps,
runs the pass on each and writes the rows; it measures nothing itself.

The repeating-group detector that lived here is gone. It flattened a single
window - the one it called the owner - into a bag of nodes and grouped
siblings by a description of their subtrees, so it threw away the
containment that the structure is made of, and ignored every other window
on the display.
"""
import argparse, csv, glob, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rs_extract as R                   # noqa: E402
import rs_scene as S                     # noqa: E402


def report(fell_back, no_client, no_display, untagged):
    """Say what the run skipped. Each of these used to be a bare continue."""
    def listed(ids, limit=5):
        head = ", ".join(ids[:limit])
        return head + (" and %d more" % (len(ids) - limit) if len(ids) > limit else "")

    if fell_back:
        print("ERR-DISPLAY-DUMP-MISSING: %d session(s) have no display.txt and "
              "no fallback at the capture root; geometry defaults to "
              "%gx%g at density %g, and every fraction is computed against it: %s"
              % (len(fell_back), R.DISPLAY_DEFAULT["w"], R.DISPLAY_DEFAULT["h"],
                 R.DISPLAY_DEFAULT["density"], listed(fell_back)))
    if no_client:
        print("ERR-CLIENT-DUMP-MISSING: %d render tree(s) have no matching "
              "client dump and are left out; without it no node can say what "
              "component it is: %s" % (len(no_client), listed(no_client)))
    if no_display:
        print("ERR-NO-DISPLAY-NODE: %d dump(s) have no single "
              "LOGICAL_DISPLAY_NODE to start from, and yield no row: %s"
              % (len(no_display), listed(no_display)))
    if untagged:
        print("ERR-PATTERN-ROOT-UNTAGGED: %d window(s) in %d scene(s) have under "
              "half of their kept canvas nodes tagged; their patterns are not "
              "matched" % (sum(len(w) for _, w in untagged), len(untagged)))


def measure(tf, cf, disp):
    """One scene: the row, and the pass that produced it."""
    lines = open(tf, encoding="utf-8", errors="replace").read().splitlines()
    client = open(cf, encoding="utf-8", errors="replace").read().splitlines()
    sc = S.Scene(lines, client, disp)
    return sc.run(), sc


def captures(root):
    """Every scene with a client dump: (session, stem, rstree, client, display)."""
    no_client, fell_back = [], []
    out = []
    for sdir in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(sdir):
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        if not os.path.exists(os.path.join(sdir, "display.txt")):
            disp = R.read_display(os.path.join(root, "display.txt"))
        if disp.get("fallback"):
            fell_back.append(os.path.basename(sdir))
        for tf in sorted(glob.glob(os.path.join(sdir, "*.rstree.txt"))):
            stem = os.path.basename(tf).split(".")[0]
            cf = os.path.join(sdir, stem + ".client.txt")
            if not os.path.exists(cf):
                no_client.append("%s/%s" % (os.path.basename(sdir), stem))
                continue
            out.append((os.path.basename(sdir), stem, tf, cf, disp))
    return out, no_client, fell_back


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="directory holding capture sessions")
    ap.add_argument("--out", default="patterns.csv")
    a = ap.parse_args()
    scenes, no_client, fell_back = captures(a.root)
    rows, no_display, untagged = [], [], []
    for ses, stem, tf, cf, disp in scenes:
        try:
            row, sc = measure(tf, cf, disp)
        except S.NoDisplay:
            no_display.append("%s/%s" % (ses, stem))
            continue
        if sc.untagged:
            untagged.append(("%s/%s" % (ses, stem), sc.untagged))
        r = {"session": ses, "stem": stem}
        shot = os.path.join(os.path.dirname(tf), stem + ".jpeg")
        r["shot"] = ses + "/" + stem + ".jpeg" if os.path.exists(shot) else ""
        r.update(row)
        rows.append(r)
    report(fell_back, no_client, no_display, untagged)
    if not rows:
        print("nothing to do"); return 1
    cols = list(rows[0])
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("  %d scenes -> %s" % (len(rows), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
