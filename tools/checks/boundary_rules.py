#!/usr/bin/env python3
"""Four rules about the shape of the repository, not about a measurement.

    ./boundary_rules.py

  INV-DEVICE-COMPUTES-NOTHING        the capture drives the device and
                                     writes files; it measures nothing
  INV-NO-CAPTURES-TRACKED            no picture and no dump is in git
  INV-GENERATED-HAS-TRACKED-PRODUCER everything the repository says it
                                     generates, something here can build
  INV-TEXT-STORED-LF                 line endings are declared by the
                                     repository, not by the machine

All four read the tree alone, so they run anywhere a clone exists, with
no capture and no device. The third is the one that was nearly lost: the
page generator rendered a payload nothing here wrote, and the 8 MB page it
made could not be rebuilt from a clone.

Exit 0 when all four hold, 1 when any does not.
"""
import argparse, os, re, subprocess, sys

# Nothing from a capture may be tracked. These are the names a capture
# produces, not a guess at what an image might be called.
CAPTURE_PATTERNS = [
    re.compile(r"\.jpe?g$", re.I), re.compile(r"\.png$", re.I),
    re.compile(r"\.webp$", re.I), re.compile(r"\.bmp$", re.I),
    re.compile(r"\.rstree\.txt$"), re.compile(r"\.client\.txt$"),
    re.compile(r"(^|/)display\.txt$"), re.compile(r"(^|/)samples\.csv$"),
    re.compile(r"(^|/)dwell\.csv$"),
]

# What the capture script is allowed to carry: the index it writes, and
# nothing that measures or names.
CAPTURE_INDEX = {"ts", "sig", "stem", "shot", "raw", "nsig", "ms"}
MEASURING = re.compile(
    r"function\s+(Get-Features|Get-Shape\w*|Get-SceneClass|Measure-\w*Shape"
    r"|Get-Modifiers|Convert-RowToFeatures|Invoke-(Fit|Classify)|Get-RuleMatrix)",
    re.I)
VECTOR_COLUMNS = ["pat1_cover", "pat1_h", "pat1_aspect", "pat1_cols", "pat1_n",
                  "overlay_frac", "pat_n_groups", "vis_xcomp", "vis_image",
                  "vis_text", "vis_glyph", "vis_content", "vis_substrate"]


def tracked(root):
    out = subprocess.run(["git", "-C", root, "ls-files"],
                         capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l]


def check_device_computes_nothing(root):
    path = os.path.join(root, "tools", "rs_capture.ps1")
    if not os.path.exists(path):
        print("  INV-DEVICE-COMPUTES-NOTHING")
        print("    tools/rs_capture.ps1 is not there")
        return False
    text = open(path, encoding="utf-8", errors="replace").read()
    body = "\n".join(l for l in text.splitlines()
                     if not l.strip().startswith("#"))
    bad = []
    for m in MEASURING.finditer(body):
        bad.append("defines %s" % m.group(1))
    for c in VECTOR_COLUMNS:
        if re.search(r"\b%s\b" % re.escape(c), body):
            bad.append("names the vector column %s" % c)
    # the index it writes
    m = re.search(r"\$row\s*=\s*\[PSCustomObject\]@\{(.*?)\}", body, re.S)
    cols = set(re.findall(r"^\s*(\w+)\s*=", m.group(1), re.M)) if m else set()
    extra = cols - CAPTURE_INDEX
    print("  INV-DEVICE-COMPUTES-NOTHING")
    print("    index written: %s" % (", ".join(sorted(cols)) or "not found"))
    if extra:
        bad.append("writes %s beyond the index" % ", ".join(sorted(extra)))
    print("    measuring or naming constructs: %d" % len(bad))
    for b in bad[:5]:
        print("    %s" % b)
    return not bad


def check_no_captures_tracked(root):
    files = tracked(root)
    bad = [f for f in files if any(p.search(f) for p in CAPTURE_PATTERNS)]
    ign = os.path.join(root, ".gitignore")
    rules = open(ign, encoding="utf-8").read() if os.path.exists(ign) else ""
    missing = [n for n in ("*.jpeg", "*.png", "captures/")
               if n not in rules]
    print("  INV-NO-CAPTURES-TRACKED")
    print("    %d tracked file(s); %d look like a capture" % (len(files), len(bad)))
    print("    ignore rules present: %s"
          % ("all" if not missing else "missing " + ", ".join(missing)))
    for f in bad[:5]:
        print("    %s" % f)
    return not bad and not missing


def check_generated_has_producer(root):
    """Each path the ignore file declares generated has something to make it.

    The declaration lives in .gitignore because that is where the artefacts
    are already named, and a second list would drift from it. One comment
    line per pattern: `#   <pattern>  <- <producer>`.
    """
    import fnmatch
    ign = open(os.path.join(root, ".gitignore"), encoding="utf-8").read()
    lines = ign.splitlines()
    try:
        start = next(i for i, l in enumerate(lines)
                     if l.startswith("# Generated deliverables"))
    except StopIteration:
        print("  INV-GENERATED-HAS-TRACKED-PRODUCER")
        print("    .gitignore declares no generated block")
        return False

    producers, declared = {}, []
    for l in lines[start + 1:]:
        t = l.strip()
        if t.startswith("#"):
            m = re.match(r"#\s+(\S+)\s+<-\s+(\S+)$", t)
            if m:
                producers[m.group(1)] = m.group(2)
            continue
        if not t:
            break
        declared.append(t)

    files = set(tracked(root))
    bad = []
    for art in declared:
        who = producers.get(art)
        if who is None:
            bad.append("%s is declared generated but names no producer" % art)
        elif who not in files:
            bad.append("%s names %s, which git does not track" % (art, who))
    for pat in producers:
        if pat not in declared:
            bad.append("%s names a producer but is not ignored" % pat)

    print("  INV-GENERATED-HAS-TRACKED-PRODUCER")
    print("    %d declared artefact(s), %d distinct producer(s)"
          % (len(declared), len(set(producers.values()))))
    for prod in sorted(set(producers.values())):
        print("      %-32s %s" % (prod, "tracked" if prod in files else "NOT TRACKED"))
    for b in bad[:6]:
        print("    %s" % b)
    return not bad


def check_text_stored_lf(root):
    """Line endings are the repository's, and the mutation log is binary.

    `git ls-files --eol` reports the index form and the working-tree form
    of every tracked file. A text file must be i/lf; a .cbm or .cbs must be
    declared binary rather than merely detected as such, because they are
    gzip and they are the source of truth for the spec graph - spec.db is a
    rebuildable cache of them.

    This is checked rather than trusted because its absence is silent. The
    capture machine has core.autocrlf=true: git wrote CRLF on checkout,
    every tool that rewrote a file wrote LF back, git called that a
    modification though the bytes were identical, and `pull --ff-only`
    then refused to touch it. The machine stopped receiving commits without
    any error, fell four commits behind, and an item that was already
    finished was read from its stale copy and run again.
    """
    attrs = os.path.join(root, ".gitattributes")
    print("  INV-TEXT-STORED-LF")
    if not os.path.exists(attrs):
        print("    .gitattributes is absent, so line endings are whatever the")
        print("    machine's core.autocrlf happens to be")
        return False
    text = open(attrs, encoding="utf-8").read()
    bad = []
    for want in ("* text=auto eol=lf", "*.cbm binary", "*.cbs binary"):
        if want not in text:
            bad.append("'%s' is not declared" % want)

    out = subprocess.run(["git", "-C", root, "ls-files", "--eol"],
                         capture_output=True, text=True)
    counts = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        i_form, path = parts[0], parts[-1]
        counts[i_form] = counts.get(i_form, 0) + 1
        if i_form == "i/crlf":
            bad.append("%s is stored with CRLF" % path)
        if path.endswith((".cbm", ".cbs")) and i_form != "i/-text":
            bad.append("%s is the mutation log and is not binary (%s)"
                       % (path, i_form))
    print("    index forms: %s"
          % ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    print("    .gitattributes declares the three rules: %s"
          % ("yes" if not any("declared" in b for b in bad) else "NO"))
    for b in bad[:5]:
        print("    %s" % b)
    return not bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    print("the boundary and artefact rules, over %s" % os.path.abspath(a.root))
    ok = [check_device_computes_nothing(a.root),
          check_no_captures_tracked(a.root),
          check_generated_has_producer(a.root),
          check_text_stored_lf(a.root)]
    if all(ok):
        print("  all four hold")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
