#!/usr/bin/env python3
r"""Unwrap `\foreignlanguage{…}{…}` from name fields, preserving the language.

Wrapping a name field makes biblatex treat the whole value as one opaque
string. Two things break, both invisible in the `.bib`:

  * the first/last inversion, so a note prints "Акопян, Л. О., …" where
    Chicago wants "Л. О. Акопян, …" -- and the note is the primary citation
    form in the notes-and-bibliography style;
  * the name-list split, so `{A, B and C, D}` becomes a single person rather
    than two, losing Chicago's "X, and Y" conjunction entirely.

Both verified by compiling under biblatex-chicago and reading the rendered
characters back. The language is not lost: it moves to `Langid`, which is
where this project's guidelines put it anyway, and Cyrillic renders correctly
from an unwrapped field.

    python3 dev/unwrap_names.py <file.bib>            # dry run
    python3 dev/unwrap_names.py <file.bib> --apply
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bib_audit import bibtool_errors, roundtrip_ok, scan  # noqa: E402

NAME_FIELDS = (
    "author", "editor", "editora", "editorb", "editorc", "translator",
    "bookauthor", "introduction", "foreword", "afterword", "commentator",
    "annotator", "holder", "namea", "nameb", "namec",
)

MACRO = "\\foreignlanguage"


def _matching(text: str, start: int):
    """Index of the `}` closing the `{` at `start`, or None."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def unwrap(value: str):
    r"""Strip every `\\foreignlanguage{lang}{...}` in `value`, keeping the rest.

    A regex will not do this. A name list is often several wrappers joined by
    ` and ` inside one brace pair:

        {\\foreignlanguage{russian}{Денисов, С.} and \\foreignlanguage{russian}{Цивинская, Н.}}

    and a greedy `^\\foreignlanguage\{(\w+)\}\{(.*)\}$` matches the FIRST
    macro against the LAST brace, whose interior happens to balance. The result
    closes the field early and BibTeX then meets `and` at brace depth zero.
    So: walk the string, brace-match each wrapper individually, and splice.

    Returns (languages, unwrapped) or None if nothing was wrapped.
    """
    out, langs, i, changed = [], set(), 0, False
    while i < len(value):
        if value.startswith(MACRO + "{", i):
            j = i + len(MACRO)
            close_lang = _matching(value, j)
            if close_lang is not None:
                k = close_lang + 1
                if k < len(value) and value[k] == "{":
                    close_text = _matching(value, k)
                    if close_text is not None:
                        langs.add(value[j + 1:close_lang])
                        out.append(value[k + 1:close_text])
                        i = close_text + 1
                        changed = True
                        continue
        out.append(value[i])
        i += 1
    if not changed:
        return None
    return langs, "".join(out)


def langid_insertion(text: str, entry):
    """Where to put a new `langid`, keeping BibDesk's alphabetical order."""
    for f in entry.fields:
        if f.key.startswith("bdsk-"):
            break
        if f.key > "langid":
            chunk = text[f.span[0]:f.span[1]]
            m = re.match(r"\n([ \t]*)", chunk)
            if not m:
                return None, None
            return f.span[0], m.group(1)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--samples", type=int, default=6)
    args = ap.parse_args()

    raw = open(args.path, "rb").read()
    text = raw.decode("utf-8")
    if text.encode("utf-8") != raw:
        print("ABORT: file does not survive a utf-8 round trip")
        return 1
    entries, opaque = scan(text)
    ok, why = roundtrip_ok(text, entries, opaque)
    if not ok:
        print(f"ABORT: input fails round-trip scan ({why})")
        return 1

    edits, langs, added_langid, multi, skipped = [], Counter(), [], 0, []
    for e in entries:
        found = None
        for f in e.fields:
            if f.key not in NAME_FIELDS or "\\foreignlanguage" not in f.value:
                continue
            u = unwrap(f.value)
            if u is None or "\\foreignlanguage" in u[1] or len(u[0]) != 1:
                skipped.append((e.citekey, f.key, f.value[:60]))
                continue
            langset, inner = u
            lang = next(iter(langset))
            found = lang
            langs[lang] += 1
            if " and " in inner:
                multi += 1
            edits.append((f.value_span[0], f.value_span[1], inner,
                          e.citekey, f.key, f.value))
        if found and not e.has("langid"):
            pos, indent = langid_insertion(text, e)
            if pos is None:
                skipped.append((e.citekey, "langid", "no insertion point"))
                continue
            edits.append((pos, pos, f"\n{indent}langid = {{{found}}},",
                          e.citekey, "langid", ""))
            added_langid.append((e.citekey, found))

    unwraps = [x for x in edits if x[4] != "langid"]
    print("=" * 70)
    print(f"UNWRAP NAME FIELDS  {args.path}")
    print("=" * 70)
    print(f"  name fields unwrapped     : {len(unwraps)}")
    print(f"    by language             : {dict(langs)}")
    print(f"    multi-name lists rescued: {multi}")
    print(f"  langid added (was absent) : {len(added_langid)}")
    print(f"  left alone (unparsable)   : {len(skipped)}")
    for s in skipped[:5]:
        print(f"     {s}")

    print("\n  examples:")
    for _, _, inner, ck, fk, before in unwraps[: args.samples]:
        print(f"     {ck}.{fk}")
        print(f"        {before[:72]}")
        print(f"     -> {inner[:72]}")

    out = text
    for s, en, t, *_ in sorted(edits, key=lambda x: -x[0]):
        out = out[:s] + t + out[en:]

    after, op2 = scan(out)
    ok, why = roundtrip_ok(out, after, op2)
    delta = sum(len(t) - (en - s) for s, en, t, *_ in edits)
    problems = []
    if not ok:
        problems.append(f"round-trip: {why}")
    if len(after) != len(entries):
        problems.append("entry count changed")
    if [x.citekey for x in after] != [x.citekey for x in entries]:
        problems.append("citekeys changed")
    if len(out) != len(text) + delta:
        problems.append("length arithmetic")
    left = sum(1 for x in after for f in x.fields
               if f.key in NAME_FIELDS and "\\foreignlanguage" in f.value)
    if left:
        problems.append(f"{left} name fields still wrapped")
    if sum(1 for x in after if x.has("langid")) != \
            sum(1 for x in entries if x.has("langid")) + len(added_langid):
        problems.append("langid count mismatch")

    # The decisive gate: an INDEPENDENT parser on the actual output bytes.
    # The scanner above records spans and never checks that a value span
    # reaches the field's end, so a value whose brace closes early round-trips
    # perfectly while being unparsable. Only a real parser sees that.
    import tempfile
    before_errs = bibtool_errors(args.path)
    with tempfile.NamedTemporaryFile("wb", suffix=".bib", delete=False) as tf:
        tf.write(out.encode("utf-8"))
        tmp = tf.name
    after_errs = bibtool_errors(tmp)
    os.unlink(tmp)
    if before_errs is None:
        problems.append("bibtool not installed — cannot verify parseability")
    elif len(after_errs) > len(before_errs):
        problems.append(
            f"independent parser: {len(before_errs)} errors before, "
            f"{len(after_errs)} after -- e.g. {after_errs[:2]}")

    print("\n" + "=" * 70)
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        return 1
    print("  PASS  round-trip, entry count, citekeys, length arithmetic")
    print("  PASS  no name field left wrapped; langid count reconciles")
    print(f"  PASS  independent parser (bibtool): {len(after_errs)} errors, "
          f"same as input")

    if not args.apply:
        print("\nDry run: nothing written.")
        return 0

    stem, ext = os.path.splitext(args.path)
    snap = f"{stem}_{datetime.date.today().isoformat()}_pre-unwrap-names{ext}"
    shutil.copy(args.path, snap)
    with open(args.path, "wb") as fh:
        fh.write(out.encode("utf-8"))
    print(f"\nSnapshot: {snap}")
    print(f"Written:  {args.path}")
    print(f"\nTo revert:  cp '{snap}' '{args.path}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
