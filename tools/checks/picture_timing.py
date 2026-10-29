#!/usr/bin/env python3
"""A scene's picture is from the moment of its dump.

    ./picture_timing.py <capture-root> --patterns patterns.csv --payload two_payload.json

  INV-PICTURE-SAME-MOMENT  a picture stands for a scene only if no sample
                           with another signature lies between the dump's
                           sample and the picture's in samples.csv; a
                           refused picture is named in the payload with
                           its reason
  INV-NO-FILE-TIME         no code under tools/ orders, pairs or filters
                           samples by a file's modification or creation
                           time - those are transfer times

The rule is recomputed here from samples.csv, then held against the
payload: every scene whose tile shows a picture must pass it, and every
scene the rule refuses must be listed in the payload's audit as refused.

Exit 0 when both hold, 1 when either does not.
"""
import argparse, csv, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "cluster"))
import vectors_build as VB              # noqa: E402

FILE_TIME = re.compile(r"getmtime|getctime|st_mtime|st_ctime|LastWriteTime|CreationTime|LastAccessTime")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="the capture tree")
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--payload", required=True)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    d = json.load(open(a.payload, encoding="utf-8"))
    samples = VB.sample_rows(a.root)
    backstop = VB.backstop_index(a.root)
    shown = {sid for sid, s in (d.get("_scenes") or {}).items() if s.get("img")}
    listed = {x["scene"] for x in (d.get("_audit") or {}).get("refused", [])}

    bad, ok_n, refused = [], 0, []
    for r in rows:
        sid = VB.row_identity(r)
        shot = (r.get("shot") or "").strip()
        path = VB.rep_path(shot, a.root, "") or backstop.get(sid)
        if not path:
            continue
        ok, why = VB.same_moment(samples.get(r["session"], []), r["stem"],
                                 os.path.basename(path))
        if ok:
            ok_n += 1
            if sid not in shown:
                bad.append("%s passes the rule and the page shows no picture" % sid)
        else:
            refused.append(sid)
            if sid in shown:
                bad.append("%s is shown with a picture the rule refuses: %s" % (sid, why))
            if sid not in listed:
                bad.append("%s is refused and not named in the payload" % sid)
    print("INV-PICTURE-SAME-MOMENT")
    print("  %d picture(s) pass the rule; %d refused, each named in the payload: %s"
          % (ok_n, len(refused), ", ".join(refused[:6]) + (" and more" if len(refused) > 6 else "")))
    for b in bad[:8]:
        print("    " + b)
    if refused:
        print("  ERR-PICTURE-NOT-SAME-MOMENT: %d scene(s) shown with no picture" % len(refused))
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
