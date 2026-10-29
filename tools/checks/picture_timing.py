#!/usr/bin/env python3
"""A scene's picture is from the moment of its render tree.

    ./picture_timing.py <capture-root> --patterns patterns.csv --payload two_payload.json

  INV-PICTURE-SAME-MOMENT  every scene's picture and render tree come from
                           the same row of samples.csv, and the display is
                           unchanged in the next sample: the pass keeps the
                           same nodes there, every visible cell box within
                           one cell per edge; every picture lost as "display
                           changed" does differ; the payload shows every
                           scene with its own picture
  INV-NO-FILE-TIME         no code under tools/ orders, pairs or filters
                           samples by a file's modification or creation
                           time - those are transfer times

The pairing is recomputed here from samples.csv and the raw render trees,
with its own comparison of the two kept trees, and held against the rows
rs_pattern.py wrote, the lost pictures beside them and the payload.

Exit 0 when both hold, 1 when either does not.
"""
import argparse, csv, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "cluster"))
import rs_extract as R                  # noqa: E402
import rs_scene as S                    # noqa: E402

FILE_TIME = re.compile(r"getmtime|getctime|st_mtime|st_ctime|LastWriteTime|CreationTime|LastAccessTime")


def lines(path):
    return open(path, encoding="utf-8", errors="replace").read().splitlines()


def unchanged(sdir, rows, i, client, disp):
    """Is the display of sample i the display of sample i + 1? Both passes
    keep the same nodes, and no visible cell box moves by more than one
    cell on any edge. None when there is no next sample."""
    if i + 1 >= len(rows):
        return None
    kept = []
    for j in (i, i + 1):
        sc = S.Scene(lines(os.path.join(sdir, "raw", rows[j]["raw"].strip() + ".rstree.txt")),
                     client, disp)
        try:
            sc.run()
        except S.NoDisplay:
            return False
        kept.append({nid: k["box"] for nid, k in sc.kept.items()})
    a, b = kept
    if set(a) != set(b):
        return False
    return all((a[x] is None and b[x] is None) or
               (a[x] is not None and b[x] is not None and
                max(abs(p - q) for p, q in zip(a[x], b[x])) <= 1)
               for x in a)


def client_before(sdir, rows, i):
    for j in range(i, -1, -1):
        stem = (rows[j].get("stem") or "").strip()
        if stem and os.path.exists(os.path.join(sdir, stem + ".client.txt")):
            return lines(os.path.join(sdir, stem + ".client.txt"))
    return []                                  # no client dump: no tags


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="the capture tree")
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--payload", required=True)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    lp = os.path.splitext(a.patterns)[0] + ".lost.csv"
    lost = list(csv.DictReader(open(lp, encoding="utf-8-sig"))) if os.path.exists(lp) else []
    d = json.load(open(a.payload, encoding="utf-8"))
    shown = {sid for sid, sc in (d.get("_scenes") or {}).items() if sc.get("img")}

    index = {}
    for ses in sorted({r["session"] for r in rows} | {x["session"] for x in lost}):
        sdir = os.path.join(a.root, ses)
        with open(os.path.join(sdir, "samples.csv"), encoding="utf-8-sig", newline="") as fh:
            sr = list(csv.DictReader(fh))
        index[ses] = (sdir, sr, {(r.get("raw") or "").strip(): i for i, r in enumerate(sr)},
                      R.read_display(os.path.join(sdir, "display.txt")))

    bad, ok_n, changed_n = [], 0, 0
    for r in rows:
        sid = "%s/%s" % (r["session"], r["sample"])
        sdir, sr, at, disp = index[r["session"]]
        i = at.get(r["sample"])
        if i is None:
            bad.append("%s: the sample is not in samples.csv" % sid)
            continue
        if r["session"] + "/" + (sr[i].get("shot") or "").strip() != r["shot"]:
            bad.append("%s: its picture %s is not the one taken in its sample (%s)"
                       % (sid, r["shot"], sr[i].get("shot")))
            continue
        client = client_before(sdir, sr, i)
        if not unchanged(sdir, sr, i, client, disp):
            bad.append("%s: the display changed across the shot" % sid)
            continue
        if sid not in shown:
            bad.append("%s: the page shows no picture for it" % sid)
            continue
        ok_n += 1
    for x in lost:
        if x["reason"] != "display changed":
            continue
        sdir, sr, at, disp = index[x["session"]]
        i = at[x["sample"]]
        if unchanged(sdir, sr, i, client_before(sdir, sr, i), disp):
            bad.append("%s/%s: lost as display changed, and the display did not change"
                       % (x["session"], x["sample"]))
        else:
            changed_n += 1
    print("INV-PICTURE-SAME-MOMENT")
    print("  %d scene(s): picture and render tree from the same sample, the display "
          "unchanged in the next, shown with that picture: %d; %d picture(s) refused "
          "because the display changed, each confirmed" % (len(rows), ok_n, changed_n))
    for b in bad[:8]:
        print("    " + b)
    if changed_n:
        print("  ERR-PICTURE-NOT-SAME-MOMENT: %d picture(s) refused, named in %s"
              % (changed_n, os.path.basename(lp)))
    print("  ERR-PICTURE-NOT-SAME-MOMENT\n  VIOLATED" if bad else "  holds")

    hits = []
    # walked, not listed by git: the capture machine runs a copy of tools/
    tools = os.path.dirname(HERE)
    files = [os.path.join(dp, f) for dp, _, fs in os.walk(tools) for f in fs
             if f.endswith((".py", ".ps1")) and f != "picture_timing.py"]
    for path in sorted(files):
        rel = os.path.relpath(path, tools)
        for i, l in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
            if FILE_TIME.search(l.split("#")[0]):
                hits.append("%s:%d %s" % (rel, i, l.strip()[:80]))
    print("INV-NO-FILE-TIME")
    print("  %d file(s) under tools/ read; file-time reads: %d" % (len(files), len(hits)))
    for h in hits[:8]:
        print("    " + h)
    print("  ERR-PICTURE-NOT-SAME-MOMENT\n  VIOLATED" if hits else "  holds")
    return 1 if bad or hits else 0


if __name__ == "__main__":
    sys.exit(main())
