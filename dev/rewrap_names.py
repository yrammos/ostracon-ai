#!/usr/bin/env python3
r"""Re-wrap name fields in `\foreignlanguage` per component, not as a whole.

Wrapping a whole name field hides the structure biber needs: the commas and
` and ` separators end up inside the macro's argument, so the value becomes one
opaque name -- no first/last inversion, and a list of people collapses into a
single person.

Wrapping each *component* separately keeps those separators at brace depth
zero, so biber parses the name normally, while every component still carries
its language. Verified by compilation:

    Author = {Акопян, Л.~О.}                                    -> Л. О. Акопян
    Author = {\foreignlanguage{russian}{Акопян},
              \foreignlanguage{russian}{Л.~О.}}                 -> Л. О. Акопян
    Author = {\foreignlanguage{russian}{Акопян, Л.~О.}}         -> Акопян, Л. О.   (wrong)

and it is not merely cosmetic: with `autolang=none` a bare Cyrillic surname
gets English hyphenation patterns, which never match, so a long name will not
break at all and overflows the measure. Wrapped per component it hyphenates
correctly (`Константино-польский`).

The language for each field is taken from the pre-unwrap snapshot rather than
guessed, so this is exact rather than inferred.

    python3 dev/rewrap_names.py <file.bib> --from <snapshot.bib>
    python3 dev/rewrap_names.py <file.bib> --from <snapshot.bib> --apply
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bib_audit import bibtool_errors, roundtrip_ok, scan  # noqa: E402
from unwrap_names import NAME_FIELDS, unwrap  # noqa: E402


def split_top(value: str, sep: str):
    """Split on `sep` at brace depth zero."""
    parts, depth, i, last = [], 0, 0, 0
    while i < len(value):
        c = value[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and value.startswith(sep, i):
            parts.append(value[last:i])
            i += len(sep)
            last = i
            continue
        i += 1
    parts.append(value[last:])
    return parts


def rewrap(value: str, lang: str) -> str:
    r"""Wrap every name component of `value` in \foreignlanguage{lang}{...}."""
    names = []
    for name in split_top(value, " and "):
        comps = []
        for comp in split_top(name, ","):
            stripped = comp.strip()
            if not stripped:
                continue
            comps.append(f"\\foreignlanguage{{{lang}}}{{{stripped}}}")
        names.append(", ".join(comps))
    return " and ".join(names)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--from", dest="source", required=True,
                    help="snapshot holding the original wrapped values")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--samples", type=int, default=5)
    args = ap.parse_args()

    # Learn (citekey, field) -> language from the snapshot. Exact, not inferred.
    langs = {}
    snap_text = open(args.source, "rb").read().decode("utf-8")
    for e in scan(snap_text)[0]:
        for f in e.fields:
            if f.key in NAME_FIELDS and "\\foreignlanguage" in f.value:
                u = unwrap(f.value)
                if u and len(u[0]) == 1:
                    langs[(e.citekey, f.key)] = next(iter(u[0]))
    print(f"languages recovered from {args.source}: {len(langs)} fields")

    raw = open(args.path, "rb").read()
    text = raw.decode("utf-8")
    entries, opaque = scan(text)
    ok, why = roundtrip_ok(text, entries, opaque)
    if not ok:
        print(f"ABORT: input fails round-trip scan ({why})")
        return 1

    edits, skipped = [], []
    for e in entries:
        for f in e.fields:
            key = (e.citekey, f.key)
            if key not in langs:
                continue
            if "\\foreignlanguage" in f.value:
                skipped.append((key, "already wrapped"))
                continue
            new = rewrap(f.value, langs[key])
            if new != f.value:
                edits.append((f.value_span[0], f.value_span[1], new,
                              e.citekey, f.key, f.value))

    print(f"name fields re-wrapped per component: {len(edits)}")
    if skipped:
        print(f"skipped: {len(skipped)}  e.g. {skipped[:3]}")
    print("\n  examples:")
    for _, _, new, ck, fk, before in edits[: args.samples]:
        print(f"     {ck}.{fk}")
        print(f"        {before[:78]}")
        print(f"     -> {new[:78]}")

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

    # Unwrapping the result must reproduce the original bare text -- otherwise
    # a name component was dropped or mangled. Malformed input (a trailing
    # comma, `, and` for ` and `, stray whitespace) does get normalised, which
    # is desirable but must never be silent, so it is reported rather than
    # folded away.
    def canonical(v: str) -> str:
        v = v.replace(", and ", " and ")
        v = re.sub(r"\s+", " ", v).strip().rstrip(",").strip()
        return re.sub(r"\s*,\s*", ", ", v)

    originals = {(x[3], x[4]): x[5] for x in edits}
    cleanups = []
    for e in after:
        for f in e.fields:
            key = (e.citekey, f.key)
            if key not in originals:
                continue
            bare = unwrap(f.value)
            if bare is None:
                problems.append(f"{e.citekey}.{f.key}: wrapper vanished")
                continue
            if bare[1] == originals[key]:
                continue
            if canonical(bare[1]) == canonical(originals[key]):
                cleanups.append((key, originals[key], bare[1]))
            else:
                problems.append(
                    f"{e.citekey}.{f.key}: content changed -- "
                    f"{originals[key]!r} -> {bare[1]!r}")

    if cleanups:
        print(f"\n  malformed name fields normalised ({len(cleanups)}) -- "
              f"reported, not silent:")
        for (ck, fk), old, new in cleanups:
            print(f"     {ck}.{fk}: {old!r} -> {new!r}")

    import tempfile
    before_errs = bibtool_errors(args.path)
    with tempfile.NamedTemporaryFile("wb", suffix=".bib", delete=False) as tf:
        tf.write(out.encode("utf-8"))
        tmp = tf.name
    after_errs = bibtool_errors(tmp)
    os.unlink(tmp)
    if before_errs is None:
        problems.append("bibtool not installed -- cannot verify parseability")
    elif len(after_errs) > len(before_errs):
        problems.append(f"independent parser: {len(before_errs)} -> "
                        f"{len(after_errs)} errors, e.g. {after_errs[:2]}")

    print("\n" + "=" * 70)
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        return 1
    print("  PASS  round-trip, entry count, citekeys, length arithmetic")
    print("  PASS  unwrapping the result reproduces the bare text exactly")
    print(f"  PASS  independent parser (bibtool): {len(after_errs)} errors")

    if not args.apply:
        print("\nDry run: nothing written.")
        return 0

    stem, ext = os.path.splitext(args.path)
    snap = f"{stem}_{datetime.date.today().isoformat()}_pre-rewrap-names{ext}"
    shutil.copy(args.path, snap)
    with open(args.path, "wb") as fh:
        fh.write(out.encode("utf-8"))
    print(f"\nSnapshot: {snap}")
    print(f"Written:  {args.path}")
    print(f"\nTo revert:  cp '{snap}' '{args.path}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
