#!/usr/bin/env python3
"""The parsing rules, checked on real dumps and on the code that reads them.

    ./parsing_rules.py <capture-root>

Each of these was a defect that shipped, was found at cost, and was fixed.
What held them afterwards was a comment, and a comment does not survive an
edit that does not read it. So each is checked here, against dumps rather
than against the source: a regex over code can be satisfied by code that
does the wrong thing, and every one of these failures was visible in the
output long before it was visible in the source.

  INV-FIRST-PASS-COUNTS       the index holds exactly the ids before the
                              first repeated one, and every property comes
                              from the first pass alone
  INV-PARSE-ONLY-SELECTION-FIELDS
                              the parser returns and searches only the
                              fields selection and matching need
  INV-STRUCTURE-NOT-DRAW-STATE
                              no element can depend on draw state: the
                              parser does not read it
  INV-SELECTION-AND-DISTANCE-ONLY
                              the row's columns are exactly IF-PATTERNS-ROW's,
                              and the pass names nothing about rendering

Paint order is checked by scene_pass.py against its oracle.

Exit 0 when all hold, 1 when any does not. Each prints what it
measured, so a pass is evidence rather than an assertion.
"""
import argparse, ast, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER = os.path.join(os.path.dirname(HERE), "cluster")
sys.path.insert(0, CLUSTER)
import rs_extract as R            # noqa: E402
import rs_scene as S              # noqa: E402


def sessions(root):
    for sdir in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(sdir):
            continue
        disp = R.read_display(os.path.join(sdir, "display.txt"))
        if not os.path.exists(os.path.join(sdir, "display.txt")):
            disp = R.read_display(os.path.join(root, "display.txt"))
        for tf in sorted(glob.glob(os.path.join(sdir, "*.rstree.txt"))):
            stem = os.path.basename(tf).split(".")[0]
            cf = os.path.join(sdir, stem + ".client.txt")
            if os.path.exists(cf):
                yield sdir, stem, tf, cf, disp


def check_first_pass(dumps):
    """Properties come from the first pass.

    The two passes carry the SAME node ids - the second is the same tree
    re-printed with DrawableVec and shouldPaint_ - so comparing id sets can
    never detect a leak, and an earlier version of this check passed a parse
    that deliberately read both. What a leak actually does is overwrite each
    node's geometry with the second pass's line, where Bounds is absent. So
    the check compares each node's parsed Bounds against the Bounds on its
    FIRST line in the file.
    """
    bad, split_seen, checked = [], 0, 0
    for tf, lines, tree in dumps:
        first_abs, seen_line = {}, set()
        repeat_at = None
        for i, l in enumerate(lines):
            m = R.RX_NODE.match(l)
            if not m:
                continue
            nid = int(m.group(3))
            if nid in seen_line:
                if repeat_at is None:
                    repeat_at = i
                continue                      # a later pass; not the source
            seen_line.add(nid)
            first_abs[nid] = R._bounds(l)
        if repeat_at is not None:
            split_seen += 1
        ix = R.index_dump(lines)
        if list(ix["first"]) != list(first_abs):
            bad.append((os.path.basename(tf), "the index holds %d id(s), the first "
                        "pass %d" % (len(ix["first"]), len(first_abs))))
        for nid, n in tree["nodes"].items():
            if nid not in first_abs:
                bad.append((os.path.basename(tf),
                            "node %d is in the tree but on no first-pass line" % nid))
                continue
            checked += 1
            if n["bounds"] != first_abs[nid]:
                bad.append((os.path.basename(tf),
                            "node %d has Bounds %s; its first-pass line says %s"
                            % (nid, n["bounds"], first_abs[nid])))
    print("  INV-FIRST-PASS-COUNTS")
    print("    %d dump(s); %d carry a second pass; %d node rect(s) compared "
          "against the first-pass line" % (len(dumps), split_seen, checked))
    for f, why in bad[:5]:
        print("    %s: %s" % (f, why))
    if bad:
        print("    ERR-FIRST-PASS-LEAK")
    return not bad


NODE_FIELDS = {"id", "kind", "pid", "tag", "abs", "bounds", "tx", "ty", "sx", "sy",
               "pivot", "alpha", "bga", "clip", "kids", "reverse", "posz"}
WINDOW_FIELDS = {"id", "name", "pid", "alpha", "visible", "vis"}
# What rendering looks like in the dump, and in code that reads it.
RENDER_WORDS = re.compile(r"shouldPaint|DrawableVec|slotset|\bslots?\b|IsSubTreeSkipped|"
                          r"\bskipped\b|\bpaints\b|\bhwc\b|vis_\w+|substrate|"
                          r"\bclaim\w*|drawn|Rotation|\bquad\b")


def code_of(path):
    """A module's code with docstrings and comments taken out."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}
    lines = src.splitlines()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and id(n) in docs:
            for k in range(n.lineno - 1, n.end_lineno):
                lines[k] = ""
    return "\n".join(l.split("#")[0] for l in lines)


def check_parse_fields(passes):
    """INV-PARSE-ONLY-SELECTION-FIELDS: what the parser returns, and what
    its code searches for."""
    bad = []
    tf, lines, client, disp = passes[0]
    ix = R.index_dump(lines)
    nid = ix["order"][0]
    n = R.parse_node(lines, ix, nid)
    if set(n) != NODE_FIELDS:
        bad.append("parse_node returns %s" % sorted(set(n) ^ NODE_FIELDS))
    sid = next(i for i, k in ix["kind"].items() if k == "SURFACE_NODE")
    w = R.parse_surface(lines, ix, R.parse_node(lines, ix, sid))
    if set(w) != WINDOW_FIELDS:
        bad.append("parse_surface returns %s" % sorted(set(w) ^ WINDOW_FIELDS))
    for m in RENDER_WORDS.finditer(code_of(R.__file__)):
        bad.append("rs_extract.py reads %r" % m.group(0))
    print("  INV-PARSE-ONLY-SELECTION-FIELDS, INV-STRUCTURE-NOT-DRAW-STATE")
    print("    parse_node returns %d field(s), parse_surface %d; render fields "
          "searched for: %d" % (len(n), len(w), sum(1 for b in bad if "reads" in b)))
    for b in bad[:6]:
        print("    " + b)
    if bad:
        print("    ERR-PARSE-FIELD-OUTSIDE")
    return not bad


def check_selection_only(passes):
    """INV-SELECTION-AND-DISTANCE-ONLY: the row is selection and distance,
    and the pass names nothing about how a node is rendered."""
    bad = []
    want = {"untagged_windows", "read_windows"} | {
        p + x for p in S.PATTERNS for x in ("_d", "_share", "_n")}
    tf, lines, client, disp = passes[0]
    row = S.Scene(lines, client, disp).run()
    if set(row) != want:
        bad.append("the row's columns differ from IF-PATTERNS-ROW: %s"
                   % sorted(set(row) ^ want))
    for path in (S.__file__, os.path.join(CLUSTER, "rs_pattern.py")):
        for m in RENDER_WORDS.finditer(code_of(path)):
            bad.append("%s names %r" % (os.path.basename(path), m.group(0)))
    print("  INV-SELECTION-AND-DISTANCE-ONLY")
    print("    the row holds %d measured column(s); render names in the pass: %d"
          % (len(row), sum(1 for b in bad if "names" in b)))
    for b in bad[:6]:
        print("    " + b)
    if bad:
        print("    ERR-RENDER-DETAIL-COMPUTED")
    return not bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="a capture root holding sessions")
    ap.add_argument("--limit", type=int, default=12,
                    help="dumps to read (default 12)")
    a = ap.parse_args()

    dumps, passes = [], []
    for sdir, stem, tf, cf, disp in sessions(a.root):
        lines = open(tf, encoding="utf-8", errors="replace").read().splitlines()
        client = open(cf, encoding="utf-8", errors="replace").read().splitlines()
        dumps.append((tf, lines, R.parse_tree(lines)))
        passes.append((tf, lines, client, disp))
        if len(dumps) >= a.limit:
            break
    if not dumps:
        raise SystemExit("no dump under %s" % a.root)

    print("the parsing rules, over %d dump(s) from %s" % (len(dumps), a.root))
    ok = [check_first_pass(dumps),
          check_parse_fields(passes),
          check_selection_only(passes)]

    if all(ok):
        print("  all checked rules hold")
        return 0
    print("  VIOLATED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
