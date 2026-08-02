#!/usr/bin/env python3
"""Apply a list of per-entry edits to a BibDesk .bib file, under the full gates.

The bulk tools (bib_normalize.py) act by rule: every edit is derived from a
predicate that fires across the corpus. This one acts by name -- a hand-written
list of "in entry X, set field Y to Z" -- for the residue that judgement, not
rules, has to settle. Same surgical discipline: byte-span rewrites applied
back-to-front, so BibDesk's tab indentation, its alphabetical field order, the
multi-KB base64 in bdsk-file-* and the @comment group plists all come through
untouched.

    python3 dev/bib_apply.py <file> <edits.json>            # dry run
    python3 dev/bib_apply.py <file> <edits.json> --apply --label <tag>

The edit list is JSON: a list of operations, each naming its entry and carrying
a `why` string that is echoed in the report so a reviewer sees the reasoning
beside the change.

    {"op": "set",    "key": "Smalley1997", "field": "volume", "value": "2",  "why": "..."}
    {"op": "add",    "key": "Smalley1997", "field": "pages",  "value": "107-126", "why": "..."}
    {"op": "delete", "key": "Etkind2014",  "field": "issuetitle", "why": "..."}
    {"op": "retype", "key": "Feldman1976", "value": "article", "why": "..."}

`add` inserts in BibDesk's alphabetical position among the ordinary fields,
never after the bdsk-* block, so the file's own ordering convention survives.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bib_audit import (  # noqa: E402
    bibtool_errors,
    merged_fields,
    roundtrip_ok,
    scan,
)
from bib_normalize import (  # noqa: E402
    PROTECTED,
    Edit,
    _leading_break,
    apply_edits,
    field_removal_span,
    snapshot_path,
)

# Fields BibDesk forces to the end of an entry. An inserted field must land
# before them, whatever the alphabet says.
TRAILING = re.compile(r"^(bdsk-|local-url|remote-url|devonthink)")

# The maintainer's protection list, exactly. It is deliberately NOT
# bib_normalize.PROTECTED, which additionally guards `doi` -- that entry is a
# self-imposed guard for the RULE-based pass, whose rules have no business
# touching a DOI. This tool is driven by a hand-written list naming each entry
# and field, so a deliberate DOI repair (Cone1982 held a full http:// URL with
# %2F percent-encoding, which broke the build) is legitimate here and must not
# be blocked by a guard the maintainer never asked for.
PROTECTED_HERE = re.compile(
    r"^(reference|date-added|date-modified|keywords|"
    r"local-url(-\d+)?|remote-url(-\d+)?|devonthink\d*|"
    r"bdsk-file(-\d+)?|bdsk-url(-\d+)?|rating|read)$"
)


def plan(text: str, entries, ops):
    edits, notes, problems = [], [], []
    by_key = {e.citekey: e for e in entries}

    # A field being deleted in this same batch cannot serve as the anchor for an
    # insertion: the two spans would overlap and apply_edits would refuse. This
    # is not hypothetical -- `number` -> `pages` is the commonest repair here,
    # and they are alphabetically adjacent.
    doomed = defaultdict(set)
    adding = defaultdict(set)
    pending_adds = []
    for o in ops:
        if o["op"] == "delete":
            doomed[o["key"]].add(o["field"].lower())
        elif o["op"] == "add":
            adding[o["key"]].add(o["field"].lower())

    for op in ops:
        key = op["key"]
        e = by_key.get(key)
        if e is None:
            problems.append(f"{key}: no such entry")
            continue
        kind = op["op"]
        fname = op.get("field", "")
        fld = e.get(fname.lower()) if fname else None

        if fname and PROTECTED_HERE.match(fname.lower()) and kind != "noop":
            problems.append(f"{key}.{fname}: protected field, refusing")
            continue

        if kind == "retype":
            m = re.match(r"@([A-Za-z]+)", text[e.span[0]:e.span[1]])
            if not m:
                problems.append(f"{key}: cannot locate the type token")
                continue
            s = e.span[0] + 1
            edits.append(Edit(s, s + len(m.group(1)), op["value"], "retype", key,
                              before=f"@{m.group(1)}", after=f"@{op['value']}"))

        elif kind == "set":
            if fld is None:
                problems.append(f"{key}.{fname}: no such field to set")
                continue
            edits.append(Edit(*fld.value_span, op["value"], f"set-{fname.lower()}",
                              key, before=fld.value[:70], after=op["value"][:70]))

        elif kind == "delete":
            if fld is None:
                notes.append(f"{key}.{fname}: already absent, nothing to do")
                continue
            span = field_removal_span(text, e, fld)
            if not span:
                problems.append(f"{key}.{fname}: unrecognised layout, refusing")
                continue
            edits.append(Edit(span[0], span[1], "", f"delete-{fname.lower()}",
                              key, before=fld.value[:70]))

        elif kind == "dropentry":
            # Removing a whole record, not a field. Deliberately awkward to
            # reach for: BibDesk's static groups key on citekeys, so a deletion
            # can break a group reference silently. Check the @comment plists
            # for the bare key before using this, and check the entry carries no
            # bdsk-file/local-url/devonthink attachment that would be orphaned.
            start, end = e.span
            while end < len(text) and text[end] in "\r\n":
                end += 1                     # take the blank line after it too
            edits.append(Edit(start, end, "", "dropentry", key,
                              before=f"@{e.etype}, {len(e.fields)} fields"))

        elif kind == "add":
            if fld is not None:
                problems.append(f"{key}.{fname}: already present; use `set`")
                continue
            pending_adds.append((key, e, fname, op["value"]))
        else:
            problems.append(f"{key}: unknown op {kind!r}")

    # Additions are emitted last and grouped by the field they anchor to, so
    # that several new fields belonging between the same pair of existing ones
    # become ONE insertion carrying them in alphabetical order. Emitting them
    # separately at nudged offsets -- the obvious shortcut -- writes into the
    # middle of a neighbouring field: it swallowed three `pages` fields and
    # doubled five commas before the gates stopped it.
    grouped = defaultdict(list)
    for key, e, fname, value in pending_adds:
        anchor = _anchor(e, fname, doomed[key])
        if anchor is None and e.fields:
            anchor = "FIRST"
        grouped[(key, id(e), anchor.key if hasattr(anchor, "key") else anchor)].append(
            (fname, value, e, key, anchor))
    for rows in grouped.values():
        rows.sort(key=lambda r: r[0].lower())
        _, _, e, key, anchor = rows[0]
        first = anchor == "FIRST" or anchor is None
        target = _first_ordinary(e, doomed[key]) if first else anchor
        indent = _leading_break(text, target)
        if indent is None:
            problems.append(f"{key}: unrecognised layout, refusing to insert")
            continue
        body = "".join(f"\n{indent}{n} = {{{v}}}," for n, v, *_ in rows)
        if first:
            pos, ins = target.span[0], body
        else:
            pos, ins = target.span[1], "," + body.rstrip(",")
        edits.append(Edit(pos, pos, ins, "add-" + "+".join(n for n, *_ in rows),
                          key, after="; ".join(f"{n} = {{{v[:44]}}}" for n, v, *_ in rows)))

    return edits, notes, problems


def _first_ordinary(entry, skip):
    for f in entry.fields:
        if not TRAILING.match(f.key) and f.key not in skip:
            return f
    return entry.fields[0]


def _anchor(entry, name: str, skip):
    """The existing field a new `name` should follow, or None to go first."""
    after = None
    for f in entry.fields:
        if TRAILING.match(f.key) or f.key in skip:
            continue
        if f.key < name.lower():
            after = f
        else:
            break
    return after


def _insertion(text: str, entry, name: str, value: str, skip=frozenset(),
               pending=frozenset()):
    """Insert `name = {value}` in BibDesk's alphabetical position.

    `skip` names fields being deleted in the same batch; anchoring on one would
    produce two overlapping spans and apply_edits would (rightly) refuse.
    """
    ordinary = [f for f in entry.fields
                if not TRAILING.match(f.key) and f.key not in skip]
    after = None
    for f in ordinary:
        if f.key < name.lower():
            after = f
        else:
            break
    # `pending` names other fields being added to this entry in the same batch.
    # They do not exist in `entry.fields` yet, so two additions that belong
    # between the same pair of existing fields would both anchor on the earlier
    # one and land in arrival order rather than alphabetical order. Counting the
    # ones that sort before this field restores the intended sequence.
    earlier_pending = sorted(p for p in pending if p < name.lower()
                             and (after is None or p > after.key))
    anchor = ordinary[0] if after is None else after
    indent = _leading_break(text, anchor)
    if indent is None:
        return None, None
    if after is None and not earlier_pending:   # goes first: before the anchor
        return anchor.span[0], f"\n{indent}{name} = {{{value}}},"
    # Goes after `anchor` (and after any same-batch additions that sort ahead of
    # it). The nudge keeps the insertion points distinct so apply_edits, which
    # sorts by offset, emits them in alphabetical order.
    base = anchor.span[0] if after is None else anchor.span[1]
    lead = "" if after is None else ","
    return base + len(earlier_pending), f"{lead}\n{indent}{name} = {{{value}}}"


def verify(original: str, result: str, edits, dropped=frozenset()):
    problems = []
    entries, opaque = scan(result)
    ok, why = roundtrip_ok(result, entries, opaque)
    if not ok:
        problems.append(f"output fails round-trip scan: {why}")

    before_entries, _ = scan(original)
    # Entry count and citekey order must be unchanged EXCEPT for keys the edit
    # list explicitly drops. Stated as a set difference rather than relaxed to a
    # count, so a deletion the list did not ask for is still caught.
    if len(entries) != len(before_entries) - len(dropped):
        problems.append(f"entry count changed: {len(before_entries)} -> "
                        f"{len(entries)} (expected {len(before_entries) - len(dropped)})")
    want = [e.citekey for e in before_entries if e.citekey not in dropped]
    if want != [e.citekey for e in entries]:
        problems.append("citekeys changed or reordered beyond the requested drops")

    def comments(t):
        return [t[s:e] for s, e in scan(t)[1] if t[s:e].lstrip().startswith("@")]
    if comments(original) != comments(result):
        problems.append("@comment blocks (BibDesk groups) were modified")

    def protected_map(ents):
        # A dropped entry takes its own protected fields with it; that is the
        # deletion, not a modification. Everything else must still match.
        return {(e.citekey, f.key): f.value
                for e in ents if e.citekey not in dropped
                for f in e.fields if PROTECTED_HERE.match(f.key)}
    pb, pa = protected_map(before_entries), protected_map(entries)
    if pb != pa:
        problems.append(f"{len(set(pb.items()) ^ set(pa.items()))} protected value(s) changed")

    nb, na = merged_fields(original, before_entries), merged_fields(result, entries)
    if len(na) > len(nb):
        problems.append(f"field(s) swallowed by a missing comma: "
                        f"{[m for m in na if m not in nb][:3]}")

    doubled = [e.citekey for e in entries
               if re.search(r",\s*,", result[e.span[0]:e.span[1]])]
    if doubled:
        problems.append(f"doubled comma in {len(doubled)} entry/entries: {doubled[:3]}")

    # Every entry NOT named in the edit list must be byte-identical. This is the
    # gate the rule-based tool cannot offer, and the one that matters most here:
    # a hand-written list should touch exactly what it names and nothing else.
    touched = {ed.citekey for ed in edits}
    before_map = {e.citekey: original[e.span[0]:e.span[1]] for e in before_entries}
    after_map = {e.citekey: result[e.span[0]:e.span[1]] for e in entries}
    strayed = [k for k in before_map
               if k not in touched and k not in dropped
               and before_map[k] != after_map.get(k)]
    if strayed:
        problems.append(f"{len(strayed)} unnamed entry/entries changed: {strayed[:5]}")

    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("edits")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--label", default="apply")
    args = ap.parse_args()

    raw = open(args.path, "rb").read()
    original = raw.decode("utf-8")
    if original.encode("utf-8") != raw:
        print("ABORT: file does not survive a utf-8 round trip")
        return 1

    entries, opaque = scan(original)
    ok, why = roundtrip_ok(original, entries, opaque)
    if not ok:
        print(f"ABORT: input fails round-trip scan ({why})")
        return 1

    ops = json.load(open(args.edits, encoding="utf-8"))
    edits, notes, problems = plan(original, entries, ops)

    print("=" * 76)
    print(f"{'APPLY' if args.apply else 'DRY RUN'}  {args.path}  <- {args.edits}")
    print("=" * 76)
    print(f"{len(ops)} operations -> {len(edits)} edits across "
          f"{len({ed.citekey for ed in edits})} entries\n")

    grouped = defaultdict(list)
    for ed in edits:
        grouped[ed.citekey].append(ed)
    why_of = {}
    for op in ops:
        why_of.setdefault(op["key"], op.get("why", ""))
    for key in sorted(grouped, key=lambda k: [o["key"] for o in ops].index(k)):
        print(f"  {key}")
        if why_of.get(key):
            print(f"      why: {why_of[key]}")
        for ed in grouped[key]:
            if ed.rule.startswith("delete"):
                print(f"      - {ed.rule[7:]:<14} was {ed.before}")
            elif ed.rule.startswith("add"):
                print(f"      + {ed.after}")
            elif ed.rule == "retype":
                print(f"      ~ {ed.before} -> {ed.after}")
            else:
                print(f"      ~ {ed.rule[4:]:<14} {ed.before}")
                print(f"      {'':<17}-> {ed.after}")
        print()

    for n in notes:
        print(f"  note: {n}")
    if problems:
        print("\nPLANNING PROBLEMS:")
        for p in problems:
            print(f"  FAIL  {p}")
        print("\nNothing written.")
        return 1

    result = apply_edits(original, edits)
    dropped = {o["key"] for o in ops if o["op"] == "dropentry"}
    vproblems = verify(original, result, edits, dropped)

    delta = sum(len(ed.text) - (ed.end - ed.start) for ed in edits)
    if len(result) != len(original) + delta:
        vproblems.append(f"length arithmetic off by "
                         f"{len(result) - len(original) - delta:+d}")

    import tempfile
    before_errs = bibtool_errors(args.path)
    with tempfile.NamedTemporaryFile("wb", suffix=".bib", delete=False) as fh:
        fh.write(result.encode("utf-8"))
        tmp = fh.name
    after_errs = bibtool_errors(tmp)
    os.unlink(tmp)
    if before_errs is None:
        vproblems.append("bibtool not installed -- cannot verify parseability")
    elif len(after_errs) > len(before_errs):
        vproblems.append(f"independent parser: {len(before_errs)} -> "
                         f"{len(after_errs)} errors, e.g. {after_errs[:2]}")

    print("=" * 76)
    print("VERIFICATION")
    print("=" * 76)
    if vproblems:
        for p in vproblems:
            print(f"  FAIL  {p}")
        print("\nNothing written.")
        return 1
    for line in ("output re-scans cleanly, byte-accounted",
                 "entry count and citekey order unchanged",
                 "@comment blocks (BibDesk groups) byte-identical",
                 "no protected field altered",
                 "no field swallowed by a missing comma",
                 "no doubled commas",
                 "every entry NOT named in the edit list is byte-identical",
                 f"{len(dropped)} entry/entries dropped, exactly as named",
                 f"length arithmetic exact ({delta:+d} bytes)",
                 f"independent parser (bibtool): {len(after_errs)} errors, same as input"):
        print(f"  PASS  {line}")

    if not args.apply:
        print("\nDry run: nothing written. Re-run with --apply to commit.")
        return 0

    if subprocess.run(["pgrep", "-x", "BibDesk"], capture_output=True).returncode == 0:
        print("\nABORT: BibDesk is running and would overwrite this from memory.")
        return 1

    snap = snapshot_path(args.path, args.label)
    with open(snap, "wb") as fh:
        fh.write(raw)
    if open(snap, "rb").read() != raw:
        print(f"\nABORT: snapshot {snap} did not verify. Nothing written.")
        return 1
    print(f"\nSnapshot: {snap}")
    with open(args.path, "wb") as fh:
        fh.write(result.encode("utf-8"))
    print(f"Written:  {args.path}")
    # `/bin/cp -f`, not `cp`: an interactive alias (cp -i) is common in a login
    # profile, and with no tty it declines the overwrite, prints "not
    # overwritten" on STDOUT and exits 1. A revert that quietly does nothing is
    # the worst possible failure for this command. Verified in this environment.
    print(f"\nTo revert:  /bin/cp -f '{snap}' '{args.path}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
