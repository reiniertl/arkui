#!/usr/bin/env python3
"""Determinism, and the rule that keeps the slots from swapping.

    ./measurement_rules.py <capture-root> --patterns patterns.csv

  INV-DETERMINISTIC-MEASUREMENT  the same session measured twice, in
                                 separate processes with different hash
                                 seeds, gives identical bytes
  INV-TIEBREAK-NOT-ON-AREA       slots order by coverage to one decimal,
                                 ties broken on vertical position

Determinism is checked across PYTHONHASHSEED rather than by running twice
in one interpreter, because the realistic way for it to break is a set or
dict iteration order reaching the output, and two runs in one process
share a hash seed and would never show it. It is the property every other
change to the measurement is checked against: it is what let rs_extract.py
go from 1,028 lines to 404 with the cut proved rather than argued.

Exit 0 when both hold, 1 when either does not.
"""
import argparse, csv, hashlib, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER = os.path.join(os.path.dirname(HERE), "cluster")

ROUND = 1          # coverage is compared to this many decimals


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_determinism(root, seeds=("1", "7", "4242")):
    """Measure the same session once per hash seed and compare the bytes."""
    outs = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in seeds:
            out = os.path.join(tmp, "p%s.csv" % seed)
            env = dict(os.environ, PYTHONHASHSEED=seed)
            r = subprocess.run([sys.executable,
                                os.path.join(CLUSTER, "rs_pattern.py"),
                                root, "--out", out],
                               capture_output=True, text=True, env=env)
            if r.returncode != 0 or not os.path.exists(out):
                print("  INV-DETERMINISTIC-MEASUREMENT")
                print("    the run failed under PYTHONHASHSEED=%s: %s"
                      % (seed, (r.stderr or "").strip()[:120]))
                return False
            outs.append((seed, digest(out)))
    same = len({d for _, d in outs}) == 1
    print("  INV-DETERMINISTIC-MEASUREMENT")
    for seed, d in outs:
        print("    PYTHONHASHSEED=%-5s sha256 %s" % (seed, d[:24]))
    print("    identical across %d hash seed(s): %s"
          % (len(outs), "yes" if same else "NO"))
    return same


def check_tiebreak(rows):
    """Coverage to one decimal decides; a tie there is decided by position."""
    two = [r for r in rows if (r.get("has_slot2") or "") == "1"]
    tied, order_bad, tie_bad = 0, [], []
    for r in two:
        try:
            d1 = round(float(r["pat1_cover"]), ROUND)
            d2 = round(float(r["pat2_cover"]), ROUND)
            t1, t2 = float(r["pat1_top"]), float(r["pat2_top"])
        except (KeyError, ValueError):
            continue
        if d1 < d2:
            order_bad.append((r.get("session"), r.get("stem"), d1, d2))
        elif d1 == d2:
            tied += 1
            if t1 > t2:
                tie_bad.append((r.get("session"), r.get("stem"), t1, t2))
    print("  INV-TIEBREAK-NOT-ON-AREA")
    print("    %d scene(s) carry two groups; %d tie on coverage to %d decimal"
          % (len(two), tied, ROUND))
    print("    slot 1 covers at least as much: %d violation(s)" % len(order_bad))
    print("    where tied, slot 1 sits no lower: %d violation(s)" % len(tie_bad))
    for s, st, a, b in (order_bad + tie_bad)[:5]:
        print("    %s/%s: %s vs %s" % (s, st, a, b))
    return not order_bad and not tie_bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="a capture root holding sessions")
    ap.add_argument("--patterns", default="",
                    help="a patterns.csv for the tiebreak check")
    a = ap.parse_args()

    print("the measurement rules, over %s" % a.root)
    ok = [check_determinism(a.root)]
    if a.patterns:
        rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
        ok.append(check_tiebreak(rows))
    else:
        print("  INV-TIEBREAK-NOT-ON-AREA: not checked (pass --patterns)")

    if all(ok):
        print("  all checked rules hold")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
