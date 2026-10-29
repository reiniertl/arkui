#!/usr/bin/env python3
"""INV-NO-SCENE-CLASS — the check.

    ./no_scene_class.py                      # the tracked tree only
    ./no_scene_class.py --patterns p.csv     # and the columns of one output

Two halves, because the invariant has two halves.

SOURCE. No tracked file may define a table, enum or function that maps a
measurement onto a scene name. Three such things were deleted before this
check was written - a C enum of twenty classes, a PowerShell classifier of
twenty-three, and a naming function mapping every measured repeat onto one
of twenty-eight pre-written shapes - and the point of the check is that a
fourth cannot arrive unnoticed. It looks for the shapes those three had:
a name-producing function, an identifier from the deleted vocabularies, or
several scene names standing together in one file as a list would leave
them.

COLUMNS. No column may hold a string that names a scene. Every column
rs_pattern.py writes is a number, except the three IF-PATTERNS-ROW declares
as strings, which identify the scene.

PATTERNS. A dictionary pattern's name names a structure of components - a
grid of items, a row of icons - and is permitted (INV-NO-SCENE-CLASS). Three
of them share a word with the deleted taxonomies, gallery_grid, icon_grid
and list; in the dictionary they name the structure, and nothing maps a
measurement onto them but the match of that structure. They are taken from
rs_scene.DICTIONARY, not listed here, so a name added to the dictionary is
exempt only by being in it.

Exit 0 when the invariant holds, 1 when it does not. The output names the
file and the line, because a check that only says "failed" gets disabled.
"""
import argparse, csv, os, re, subprocess, sys

import ast


def dictionary_names():
    """The dictionary's pattern names, read from rs_scene.py's DICTIONARY
    literal rather than imported, so this runs where numpy does not."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "cluster", "rs_scene.py"), encoding="utf-8").read()
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "DICTIONARY" for t in n.targets):
            return tuple(dict.fromkeys(e[0] for e in ast.literal_eval(n.value)))
    return ()


PATTERNS = dictionary_names()

# The vocabularies of the three deleted taxonomies. A name here appearing in
# tracked source is not proof of a class table on its own - prose mentions
# them, this file included - so the source check requires several together,
# or one of the structural shapes below.
SCENE_NAMES = {
    # ark_scene_class_t, the C enum
    "unclassified", "sparse", "media_player", "audio_player", "call_video",
    "capture", "immersive_surface", "web_content", "chat", "form", "editor",
    "media_view", "gallery_grid", "icon_pager", "feed", "reading", "paging",
    "list", "dialog", "splash_loading",
    # the PowerShell classifier's additions
    "map", "icon_grid", "icon_list", "settings",
    # describe()'s cell kinds
    "media card", "icon and label", "label and glyph", "plain box",
}

# A function that returns a name for a measurement. These are the shapes the
# deleted ones had, not a guess: describe() in rs_pattern.py, Get-SceneClass
# in scene_class.ps1.
NAMING_DEFS = re.compile(
    r"^\s*(?:def\s+(describe|classify\w*|name_\w*|scene_\w*|motif\w*)\s*\("
    r"|function\s+(Get-SceneClass|Get-Scene\w*Class|ConvertTo-Scene\w*))",
    re.IGNORECASE)

# The deleted C enum and its members, by identifier rather than by prose.
ENUM_IDENT = re.compile(r"\bark_scene_class_t\b|\bARK_SCENE_[A-Z_]+\b")

SOURCE_EXT = {".py", ".ps1", ".sh", ".c", ".h", ".cpp", ".hpp", ".js", ".ts"}

# IF-PATTERNS-ROW declares these as strings. Everything else is a number.
STRING_COLUMNS = {"session", "sample", "shot"}


def tracked_sources(root):
    out = subprocess.run(["git", "-C", root, "ls-files"],
                         capture_output=True, text=True)
    for rel in out.stdout.splitlines():
        if os.path.splitext(rel)[1].lower() in SOURCE_EXT:
            yield rel, os.path.join(root, rel)


def check_source(root, verbose=False):
    """A name-producing definition, the deleted enum, or a cluster of names."""
    bad = []
    for rel, path in tracked_sources(root):
        if rel.startswith("tools/checks/"):
            continue                      # this file names them on purpose
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue

        for i, line in enumerate(lines, 1):
            m = NAMING_DEFS.match(line)
            if m:
                bad.append((rel, i, "a function that names a measurement: "
                            + line.strip()[:70]))
            if ENUM_IDENT.search(line):
                bad.append((rel, i, "the deleted scene-class enum: "
                            + line.strip()[:70]))

        # Several names standing together, as a list or table would leave
        # them. Prose mentions one or two; a table carries many.
        quoted = re.findall(r"""['"]([a-z][a-z _]{2,24})['"]""", "\n".join(lines))
        hits = sorted({q for q in quoted if q in SCENE_NAMES} - set(PATTERNS))
        if len(hits) >= 3:
            bad.append((rel, 0, "%d scene names quoted together (%s)"
                        % (len(hits), ", ".join(hits[:5]))))
        elif verbose and hits:
            print("    note  %s: %s quoted, below the threshold of 3"
                  % (rel, ", ".join(hits)))
    return bad


def check_columns(path):
    """Every column a number or empty, except the ones declared as strings."""
    bad = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rdr = csv.DictReader(fh)
        cols = rdr.fieldnames or []
        for lineno, row in enumerate(rdr, 2):
            for c in cols:
                if c in STRING_COLUMNS:
                    continue
                v = (row.get(c) or "").strip()
                if v == "":
                    continue              # absent, which is a measurement
                try:
                    float(v)
                except ValueError:
                    bad.append((os.path.basename(path), lineno,
                                "column %r holds the string %r" % (c, v[:40])))
    return bad, cols


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repository root")
    ap.add_argument("--patterns", default="",
                    help="a patterns.csv to check the columns of")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    print("INV-NO-SCENE-CLASS")
    src = check_source(a.root, a.verbose)
    n_src = sum(1 for _ in tracked_sources(a.root))
    print("  source : %d tracked files, %d finding(s)" % (n_src, len(src)))
    for rel, line, why in src:
        print("    %s:%s  %s" % (rel, line or "-", why))

    col = []
    if a.patterns:
        col, cols = check_columns(a.patterns)
        print("  columns: %d in %s, %d string column(s) declared, %d finding(s)"
              % (len(cols), os.path.basename(a.patterns),
                 len([c for c in cols if c in STRING_COLUMNS]), len(col)))
        for f, line, why in col:
            print("    %s:%d  %s" % (f, line, why))
    else:
        print("  columns: not checked (pass --patterns to include them)")

    if src or col:
        print("  ERR-SCENE-CLASS")
        print("  VIOLATED")
        return 1
    print("  holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
