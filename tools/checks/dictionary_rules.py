#!/usr/bin/env python3
"""The motif dictionary against its specification.

    ./dictionary_rules.py

  INV-DICTIONARY-DESIGNED   the dictionary in rs_scene.py is a literal, is
                            IF-MOTIF-DICTIONARY's motif for motif - root and
                            element set - every motif names the component
                            contract it comes from, and nothing in the
                            tools writes to it or to the threshold
  INV-DICTIONARY-COMPLETE   every catalogue container with a typed or fixed
                            child contract, and every leaf label, roots a
                            motif; 21 motifs, 63 columns

Read from source with ast, not imported, so this runs where numpy does not.
The expected dictionary below is IF-MOTIF-DICTIONARY written out; the code
is held to it, never the reverse.

Exit 0 when both hold, 1 when either does not.
"""
import ast, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER = os.path.join(os.path.dirname(HERE), "cluster")

# IF-MOTIF-DICTIONARY: (motif, root, element set), in order
SPEC = (
    ("list", "List", {"List", "List/item*"}),
    ("list", "ListItemGroup", {"ListItemGroup", "ListItemGroup/item*"}),
    ("grid", "Grid", {"Grid", "Grid/item*"}),
    ("waterflow", "WaterFlow", {"WaterFlow", "WaterFlow/item*"}),
    ("tabs", "Tabs", {"Tabs", "Tabs/Swiper", "Tabs/TabBar"}),
    ("carousel", "Swiper", {"Swiper", "Swiper/item*"}),
    ("stepper", "Stepper", {"Stepper", "Stepper/StepperItem*"}),
    ("menu", "Menu", {"Menu", "Menu/MenuItem*"}),
    ("grid_row", "GridRow", {"GridRow", "GridRow/GridCol*"}),
    ("scroll_page", "Scroll", {"Scroll", "Scroll/item"}),
    ("refresh", "Refresh", {"Refresh", "Refresh/item"}),
    ("sidebar", "SideBarContainer", {"SideBarContainer", "SideBarContainer/item*"}),
    ("video_large", "video:large", {"video:large"}),
    ("video_medium", "video:medium", {"video:medium"}),
    ("video_small", "video:small", {"video:small"}),
    ("web", "Web", {"Web"}),
    ("text_input", "text_input", {"text_input"}),
    ("button", "Button", {"Button"}),
    ("slider", "Slider", {"Slider"}),
    ("index_bar", "AlphabetIndexer", {"AlphabetIndexer"}),
    ("icon_row", "Row", {"Row", "Row/icon*"}),
    ("icon_grid", "Column", {"Column", "Column/icon_row*"}),
    ("icon_grid", "Grid", {"Grid", "Grid/item*", "GridItem/icon"}),
)
# INV-DICTIONARY-COMPLETE: the containers with a typed or fixed child
# contract, and IF-MOTIF-VOCABULARY's leaf labels
CONTAINERS = ("List", "ListItemGroup", "Grid", "WaterFlow", "Tabs", "Swiper", "Stepper",
              "Menu", "GridRow", "Scroll", "Refresh", "SideBarContainer")
LEAVES = ("video:large", "video:medium", "video:small", "Web", "text_input",
          "Button", "Slider", "AlphabetIndexer")
MOTIFS, COLUMNS = 21, 63
# IF-SCENE-STATS: the families of the area columns, in order, and the tree
# columns' constants (INV-SCENE-STATS-FROM-KEPT-TREE)
SPEC_FAMILIES = (("text", {"Text", "TextClock", "Span", "RichText", "RichEditor", "Marquee"}),
                 ("image", {"Image", "SymbolGlyph", "ImageSpan"}),
                 ("video", {"XComponent", "Video"}),
                 ("input", {"TextInput", "TextArea", "Search"}),
                 ("web", {"Web"}),
                 ("button", {"Button", "Toggle", "Checkbox", "Radio"}))
SPEC_K = (200, 20)
FROZEN = ("FAMILIES", "FAMILY_OF", "STATS", "KEPT_K", "DEPTH_K",
          "DICTIONARY", "SHAPES", "BY_ROOT", "TOKENS", "COMPOSED", "MOTIFS",
          "CATALOGUE", "FOUND")


def literal(tree, name):
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == name for t in n.targets):
            return n
    return None


def writes(path):
    """Writes to the dictionary or what is derived from it: in rs_scene.py,
    any outside the module-level definitions; elsewhere, any made through
    the module (S.DICTIONARY = .., SC.SHAPES[k] = .., SC.TOKENS.add(..))."""
    here = path.endswith("rs_scene.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    # the module-level definitions, loops building BY_ROOT and COMPOSED
    # included; a function or class body is not a definition
    top = set()
    if here:
        for st in tree.body:
            if not isinstance(st, (ast.FunctionDef, ast.ClassDef)):
                top |= {id(n) for n in ast.walk(st)}

    def frozen(e):
        if isinstance(e, ast.Subscript):
            e = e.value
        if here and isinstance(e, ast.Name):
            return e.id if e.id in FROZEN else None
        if isinstance(e, ast.Attribute) and e.attr in FROZEN:
            return e.attr
        return None
    out = []
    for n in ast.walk(tree):
        if id(n) in top:
            continue
        targets = (n.targets if isinstance(n, ast.Assign) else
                   [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign)) else [])
        for t in targets:
            if frozen(t):
                out.append("%s:%d writes %s" % (os.path.basename(path), n.lineno, frozen(t)))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in ("append", "extend", "update", "add", "pop", "remove",
                                    "setdefault", "clear", "insert") and frozen(n.func.value):
            out.append("%s:%d mutates %s" % (os.path.basename(path), n.lineno,
                                             frozen(n.func.value)))
    return out


OLD_NAMES = ("PATTERN" + "S", "PATTERN" + "_COLS")    # spelled apart: this file is scanned too


def old_names_and_passthroughs(path):
    """The dictionary is read under its own names only: no alias of the old
    pattern names is defined or read; and no function in tools/checks
    only passes a call through to another module (a wrapper kept to spare
    its callers a new name)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out, rel = [], os.path.basename(path)
    for n in ast.walk(tree):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
        if name in OLD_NAMES:
            out.append("%s:%d names %s" % (rel, n.lineno, name))
    if os.path.dirname(os.path.abspath(path)) == HERE:
        modules = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        for f in ast.walk(tree):
            if not isinstance(f, ast.FunctionDef):
                continue
            body = [b for b in f.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))]
            if (len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(body[0].value, ast.Call)
                    and isinstance(body[0].value.func, ast.Attribute)
                    and getattr(body[0].value.func.value, "id", "") in modules):
                out.append("%s:%d %s() only returns %s.%s(...)" % (rel, f.lineno, f.name,
                           body[0].value.func.value.id, body[0].value.func.attr))
    return out


def main():
    src_path = os.path.join(CLUSTER, "rs_scene.py")
    tree = ast.parse(open(src_path, encoding="utf-8").read())
    failed = False
    print("the motif dictionary, against IF-MOTIF-DICTIONARY")

    # -- INV-DICTIONARY-DESIGNED ------------------------------------------------
    bad = []
    node = literal(tree, "DICTIONARY")
    D = ()
    if node is None:
        bad.append("rs_scene.py has no DICTIONARY")
    else:
        try:
            D = ast.literal_eval(node.value)
        except ValueError:
            bad.append("DICTIONARY is not a literal")
    for e in D:
        if len(e) != 4 or not isinstance(e[3], str) or not e[3].strip():
            bad.append("%s %s names no source" % (e[0], e[1]))
    got = [(e[0], e[1], set(e[2])) for e in D]
    want = [(n, r, set(s)) for n, r, s in SPEC]
    for w in want:
        if w not in got:
            bad.append("IF-MOTIF-DICTIONARY's %s %s %s is not in the code"
                       % (w[0], w[1], sorted(w[2])))
    for g in got:
        if g not in want:
            bad.append("the code's %s %s %s is not IF-MOTIF-DICTIONARY's"
                       % (g[0], g[1], sorted(g[2])))
    if not bad and got != want:
        bad.append("the motifs are not in IF-MOTIF-DICTIONARY's order")
    for p in sorted(glob.glob(os.path.join(CLUSTER, "*.py")) +
                    glob.glob(os.path.join(HERE, "*.py"))):
        if os.path.basename(p) != "dictionary_rules.py":
            bad += writes(p)
    tools = os.path.dirname(HERE)
    scanned = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(tools) for f in fs if f.endswith(".py"))
    names = [b for p in scanned for b in old_names_and_passthroughs(p)]
    bad += names
    print("  INV-DICTIONARY-DESIGNED")
    print("    %d file(s) under tools/ read; old pattern names and pass-through "
          "wrappers: %d" % (len(scanned), len(names)))
    print("    %d variant(s) in the code, %d in the spec; every one with its "
          "source: %s" % (len(got), len(want), "yes" if all(len(e) == 4 and e[3].strip()
                                                         for e in D) else "no"))
    for b in bad:
        print("    " + b)
    if bad:
        print("    ERR-MOTIF-ORACLE-MISMATCH")
        failed = True
    else:
        print("    holds")

    # -- INV-DICTIONARY-COMPLETE ------------------------------------------------
    bad = []
    roots = {e[1] for e in D}
    cnode = literal(tree, "CATALOGUE")
    cat = ast.literal_eval(cnode.value) if cnode is not None else ()
    for c in CONTAINERS + LEAVES:
        if c not in cat:
            bad.append("%s is not in rs_scene.py's CATALOGUE" % c)
        if c not in roots:
            bad.append("%s roots no motif" % c)
    names = tuple(dict.fromkeys(e[0] for e in D))
    if len(names) != MOTIFS:
        bad.append("%d motifs, the dictionary holds %d" % (len(names), MOTIFS))
    if 3 * len(names) != COLUMNS:
        bad.append("%d columns, the vector holds %d" % (3 * len(names), COLUMNS))
    print("  INV-DICTIONARY-COMPLETE")
    print("    %d container(s) and %d leaf label(s) of the catalogue, each a "
          "motif's root; %d motifs, %d columns"
          % (len(CONTAINERS), len(LEAVES), len(names), 3 * len(names)))
    for b in bad:
        print("    " + b)
    if bad:
        print("    ERR-DICTIONARY-INCOMPLETE")
        failed = True
    else:
        print("    holds")

    # -- INV-SCENE-STATS-FROM-KEPT-TREE: the families literal --------------------
    bad = []
    fnode, knode = literal(tree, "FAMILIES"), None
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Tuple) \
                and [getattr(t, "id", "") for t in n.targets[0].elts] == ["KEPT_K", "DEPTH_K"]:
            knode = n
    try:
        fams = ast.literal_eval(fnode.value) if fnode is not None else None
    except ValueError:
        fams = None
        bad.append("FAMILIES is not a literal")
    if fnode is None:
        bad.append("rs_scene.py has no FAMILIES")
    elif fams is not None and [(f, set(t)) for f, t in fams] != [(f, set(t)) for f, t in SPEC_FAMILIES]:
        bad.append("the families in code are %s, IF-SCENE-STATS' are %s"
                   % ([(f, sorted(t)) for f, t in fams], [(f, sorted(t)) for f, t in SPEC_FAMILIES]))
    ks = ast.literal_eval(knode.value) if knode is not None else None
    if tuple(ks or ()) != SPEC_K:
        bad.append("the tree constants are %s, IF-SCENE-STATS' are %s" % (ks, SPEC_K))
    print("  INV-SCENE-STATS-FROM-KEPT-TREE, the families and constants")
    print("    %d famil(ies) in code, %d in the spec; constants %s"
          % (len(fams or ()), len(SPEC_FAMILIES), ks))
    for b in bad:
        print("    " + b)
    if bad:
        print("    ERR-SCENE-STATS-MISMATCH")
        failed = True
    else:
        print("    holds")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
