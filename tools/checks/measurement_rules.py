#!/usr/bin/env python3
"""Determinism of the measurement.

    ./measurement_rules.py <capture-root>

  INV-DETERMINISTIC-MEASUREMENT  the same session measured twice, in
                                 separate processes with different hash
                                 seeds, gives identical bytes

Determinism is checked across PYTHONHASHSEED rather than by running twice
in one interpreter, because the realistic way for it to break is a set or
dict iteration order reaching the output, and two runs in one process
share a hash seed and would never show it. It is the property every other
change to the measurement is checked against: it is what let rs_extract.py
go from 1,028 lines to 404 with the cut proved rather than argued.

Exit 0 when it holds, 1 when it does not.
"""
import argparse, hashlib, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER = os.path.join(os.path.dirname(HERE), "cluster")


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
                print("    ERR-NONDETERMINISTIC-MEASUREMENT")
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
    if not same:
        print("    ERR-NONDETERMINISTIC-MEASUREMENT")
    return same


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="a capture root holding sessions")
    a = ap.parse_args()

    print("the measurement rules, over %s" % a.root)
    ok = [check_determinism(a.root)]

    if all(ok):
        print("  all checked rules hold")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
