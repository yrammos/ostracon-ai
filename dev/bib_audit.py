#!/usr/bin/env python3
"""Read-only diagnostic audit of a legacy BibDesk .bib file.

Step zero of dev/normalization-plan.md. Writes nothing, parses surgically
(byte spans, never re-serialisation), and proves round-trip byte equality
before reporting a single finding.

    python3 dev/bib_audit.py ~/Documents/Bibdesk/biblio.bib
    python3 dev/bib_audit.py <file> --samples 10 --rule unsplit-title
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field as dc_field

# --------------------------------------------------------------------------
# Surgical scanner
# --------------------------------------------------------------------------


@dataclass
class Field:
    name: str          # as written, e.g. "Title" or "title"
    key: str           # lowercased
    value: str         # inside the outermost delimiters
    span: tuple        # (start, end) of the whole "name = {value}" chunk
    value_span: tuple  # (start, end) of value, exclusive of delimiters


@dataclass
class Entry:
    etype: str         # lowercased, e.g. "article"
    citekey: str
    span: tuple        # (start, end) covering "@type{...}" inclusive
    fields: list = dc_field(default_factory=list)

    def get(self, key: str):
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def has(self, key: str) -> bool:
        return self.get(key) is not None


def _matching_brace(text: str, start: int):
    """Index of the `}` closing the `{` at `start`, or None if unbalanced."""
    depth = 0
    i = start
    n = len(text)
    while i < n:
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


ENTRY_RE = re.compile(r"^@([A-Za-z]+)\s*\{", re.MULTILINE)


def scan(text: str):
    """Yield Entry objects plus a list of opaque (start, end) spans.

    Opaque spans are @comment blocks and anything between entries; they are
    never inspected and never rewritten.
    """
    entries = []
    opaque = []
    pos = 0
    for m in ENTRY_RE.finditer(text):
        if m.start() < pos:
            continue
        brace = m.end() - 1
        close = _matching_brace(text, brace)
        if close is None:
            opaque.append((m.start(), len(text)))
            pos = len(text)
            break
        etype = m.group(1).lower()
        if m.start() > pos:
            opaque.append((pos, m.start()))
        if etype in ("comment", "preamble", "string"):
            opaque.append((m.start(), close + 1))
        else:
            entries.append(_parse_entry(text, etype, m.start(), brace, close))
        pos = close + 1
    if pos < len(text):
        opaque.append((pos, len(text)))
    return entries, opaque


def _parse_entry(text: str, etype: str, start: int, brace: int, close: int) -> Entry:
    body_start, body_end = brace + 1, close
    body = text[body_start:body_end]

    # Citekey: up to the first top-level comma.
    comma = _top_level_comma(body, 0)
    citekey = body[:comma].strip() if comma is not None else body.strip()

    entry = Entry(etype=etype, citekey=citekey, span=(start, close + 1))
    if comma is None:
        return entry

    i = comma + 1
    while i < len(body):
        nxt = _top_level_comma(body, i)
        chunk_end = nxt if nxt is not None else len(body)
        chunk = body[i:chunk_end]
        f = _parse_field(chunk, body_start + i)
        if f:
            entry.fields.append(f)
        if nxt is None:
            break
        i = nxt + 1
    return entry


def _top_level_comma(s: str, start: int):
    depth = 0
    in_quote = False
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == '"' and depth == 0:
            in_quote = not in_quote
        elif c == "," and depth == 0 and not in_quote:
            return i
        i += 1
    return None


FIELD_RE = re.compile(r"^(\s*)([A-Za-z][A-Za-z0-9_+:-]*)(\s*=\s*)(.*)$", re.DOTALL)


def _parse_field(chunk: str, offset: int):
    m = FIELD_RE.match(chunk)
    if not m:
        return None
    name = m.group(2)
    val_start_rel = len(m.group(1)) + len(name) + len(m.group(3))
    raw = chunk[val_start_rel:]
    stripped = raw.strip()
    if stripped.startswith("{"):
        lead = len(raw) - len(raw.lstrip())
        vs = val_start_rel + lead + 1
        closing = _matching_brace(chunk, val_start_rel + lead)
        ve = closing if closing is not None else len(chunk)
    elif stripped.startswith('"'):
        lead = len(raw) - len(raw.lstrip())
        vs = val_start_rel + lead + 1
        ve = chunk.find('"', vs)
        if ve == -1:
            ve = len(chunk)
    else:
        vs = val_start_rel + (len(raw) - len(raw.lstrip()))
        ve = vs + len(stripped)
    return Field(
        name=name,
        key=name.lower(),
        value=chunk[vs:ve],
        span=(offset, offset + len(chunk)),
        value_span=(offset + vs, offset + ve),
    )


def bibtool_errors(path: str):
    """Parse errors reported by an INDEPENDENT parser, or None if unavailable.

    The scanner in this module records byte spans; it never checks that a
    field's value span reaches the field's end. A value that closes its brace
    early therefore round-trips perfectly while being unparsable BibTeX. Only
    a real parser catches that, so any pass that rewrites field values should
    gate on this rather than on the scanner alone.
    """
    import shutil as _sh
    import subprocess as _sp
    if not _sh.which("bibtool"):
        return None
    r = _sp.run(["bibtool", "-i", path, "-o", os.devnull],
                capture_output=True, text=True)
    return [ln for ln in (r.stdout + r.stderr).splitlines()
            if "error" in ln.lower()]


def roundtrip_ok(text: str, entries, opaque) -> tuple:
    """Reassemble the file from recorded spans; must equal the input byte for byte."""
    spans = [e.span for e in entries] + list(opaque)
    spans.sort()
    out = []
    cursor = 0
    for s, e in spans:
        if s != cursor:
            return False, f"gap or overlap at offset {cursor}..{s}"
        out.append(text[s:e])
        cursor = e
    if cursor != len(text):
        return False, f"trailing {len(text) - cursor} chars unaccounted for"
    return ("".join(out) == text), "byte mismatch after reassembly"


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

PROTECTED = re.compile(
    r"^(reference|date-added|date-modified|"
    r"local-url(-\d+)?|remote-url(-\d+)?|devonthink\d*|"
    r"bdsk-file(-\d+)?|bdsk-url(-\d+)?|rating|read)$"
)

FORBIDDEN = {"issn", "isbn"}

TITLE_FIELDS = ("title", "booktitle", "maintitle")

# A colon that sits inside \mkbibquote{...} / \mkbibemph{...} / any brace group
# is not an entry-level title boundary.


def colon_at_top_level(value: str):
    """Offsets of ': ' occurrences at brace depth 0, outside \\cmd{...} groups."""
    hits = []
    depth = 0
    i = 0
    n = len(value)
    while i < n:
        c = value[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ":" and depth == 0:
            if i + 1 < n and value[i + 1] == " ":
                hits.append(i)
        i += 1
    return hits


RANGE_FIELDS = ("pages", "date", "origdate", "eventdate", "urldate", "volumes")
BAD_RANGE = re.compile(r"--|[\u2010\u2011\u2012\u2013\u2014\u2015]")

NON_ASCII = re.compile(r"[^\x00-\x7F]")

SERIES_DIVISION = re.compile(
    r"(\bReihe\b|\b\d+(st|nd|rd|th)\s+ser\.|\bser\.|\bn\.s\.|\bnew series\b|"
    r"\bser[ií]e\b|\bBd\.|\bvol\.|[;:]|\d)",
    re.IGNORECASE,
)


def word_count(value: str) -> int:
    txt = re.sub(r"\\[A-Za-z]+\s*", " ", value)
    txt = re.sub(r"[{}]", " ", txt)
    return len([w for w in txt.split() if w.strip()])


ONLINE_OK_TYPES = {"online"}


def run_rules(entries):
    findings = {}

    def hit(rule, key, detail=""):
        findings.setdefault(rule, []).append((key, detail))

    for e in entries:
        # --- Tier A: unsplit title -------------------------------------
        for tf in TITLE_FIELDS:
            f = e.get(tf)
            if not f:
                continue
            hits = colon_at_top_level(f.value)
            subf = {"title": "subtitle", "booktitle": "booksubtitle",
                    "maintitle": "mainsubtitle"}[tf]
            if hits and not e.has(subf):
                rule = f"unsplit-{tf}"
                if e.etype == "review":
                    rule = f"unsplit-{tf}-REVIEW(exempt?)"
                detail = f"{len(hits)} colon(s)"
                if len(hits) > 1:
                    detail += " MULTI"
                hit(rule, e.citekey, detail)

        # --- Redundant / missing shorttitle ----------------------------
        st = e.get("shorttitle")
        t = e.get("title")
        if st and t:
            if not colon_at_top_level(t.value) and not e.has("subtitle"):
                hit("shorttitle-redundant", e.citekey, "")
            else:
                pre = t.value[: colon_at_top_level(t.value)[0]].strip() if colon_at_top_level(t.value) else ""
                if pre and st.value.strip() != pre:
                    hit("shorttitle-mismatch", e.citekey, f"{st.value[:40]!r} vs {pre[:40]!r}")
        if t and not st:
            if not colon_at_top_level(t.value) and word_count(t.value) > 6:
                hit("shorttitle-missing", e.citekey, f"{word_count(t.value)} words")

        # --- Range punctuation -----------------------------------------
        for rf in RANGE_FIELDS:
            f = e.get(rf)
            if f and BAD_RANGE.search(f.value):
                hit("range-punctuation", e.citekey, f"{rf}={f.value[:40]}")

        # --- Forbidden fields ------------------------------------------
        for f in e.fields:
            if f.key in FORBIDDEN:
                hit("forbidden-field", e.citekey, f.key)
        if e.has("keywords"):
            hit("keywords-present(DECISION)", e.citekey, "")

        # --- Url placement ---------------------------------------------
        url = e.get("url")
        if url:
            online_ref = (
                e.etype in ("inreference", "reference")
                and (e.get("entrysubtype") or Field("", "", "", (0, 0), (0, 0))).value.strip().lower() == "online"
            )
            if e.etype not in ONLINE_OK_TYPES and not online_ref and e.has("date"):
                hit("url-misplaced", e.citekey, e.etype)
        if e.etype in ONLINE_OK_TYPES and not url:
            hit("online-without-url", e.citekey, "")
        if not e.has("date") and not e.has("urldate"):
            hit("undated-no-urldate", e.citekey, e.etype)

        # --- Series / Number -------------------------------------------
        s = e.get("series")
        if s and SERIES_DIVISION.search(s.value):
            hit("series-carries-division", e.citekey, s.value[:50])
        num = e.get("number")
        if s and num and num.value.strip() and num.value.strip() in s.value:
            hit("number-duplicated-in-series", e.citekey, num.value[:20])

        # --- Stamps ------------------------------------------------------
        if not e.has("date-added") or not e.has("date-modified"):
            hit("missing-stamps", e.citekey, "")

        # --- Field-name casing -------------------------------------------
        for f in e.fields:
            if f.name != f.key:
                hit("field-name-casing", e.citekey, f.name)
                break

        # --- Language markers --------------------------------------------
        t = e.get("title")
        if t and NON_ASCII.search(t.value) and not e.has("langid") \
                and "\\foreignlanguage" not in t.value:
            hit("non-ascii-no-langid", e.citekey, t.value[:40])

        # --- Straight quotes in title ------------------------------------
        if t and re.search(r"(?<![A-Za-z])['\"]", t.value) and "\\mkbibquote" not in t.value:
            hit("raw-quotes-in-title", e.citekey, t.value[:50])

        # --- Missing date --------------------------------------------------
        if not e.has("date") and not e.has("year"):
            hit("no-date", e.citekey, e.etype)

    return findings


PROTECTED_NOTE = """
Fields the audit deliberately never inspects or proposes changing
(user-protected): reference, date-added, date-modified, local-url*,
remote-url*, devonthink*, bdsk-file*, bdsk-url*, rating, read.
Note: `reference` appears in the plan's forbidden-fields check but the
user's protection instruction overrides it — it stays.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--rule", help="show all offenders for one rule")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as fh:
        text = fh.read()

    entries, opaque = scan(text)
    ok, why = roundtrip_ok(text, entries, opaque)

    print("=" * 72)
    print(f"AUDIT  {args.path}")
    print("=" * 72)
    print(f"round-trip byte equality : {'PASS' if ok else 'FAIL — ' + why}")
    print(f"entries parsed           : {len(entries)}")
    print(f"opaque spans (@comment,  : {len(opaque)}")
    print(f"  whitespace, preamble)")
    if not ok:
        print("\nAborting: the scanner cannot account for every byte, so no")
        print("finding below can be trusted. Fix the scanner first.")
        return 1

    types = Counter(e.etype for e in entries)
    print("\nentry types: " + ", ".join(f"{t}={n}" for t, n in types.most_common(12)))

    findings = run_rules(entries)

    if args.rule:
        rows = findings.get(args.rule, [])
        print(f"\nAll {len(rows)} offenders for rule '{args.rule}':\n")
        for k, d in rows:
            print(f"  {k:<28} {d}")
        return 0

    print("\n" + "-" * 72)
    print(f"{'rule':<36}{'count':>8}   sample keys")
    print("-" * 72)
    for rule, rows in sorted(findings.items(), key=lambda kv: -len(kv[1])):
        sample = ", ".join(k for k, _ in rows[: args.samples])
        print(f"{rule:<36}{len(rows):>8}   {sample}")
    print("-" * 72)
    print(PROTECTED_NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(main())


def merged_fields(text: str, entries):
    r"""(citekey, field) pairs where two fields were run together.

    A field written without its trailing comma does not fail to parse -- the
    scanner simply runs its span on to the next comma and swallows whatever
    follows, so `author = {X}` + missing comma + `bookauthor = {Y}` becomes one
    `author` field and `bookauthor` disappears. Neither the span round-trip nor
    bibtool notices; biber sees an entry short of a name. Any tool that inserts
    or deletes fields must run this over its output.
    """
    bad = []
    pat = re.compile(r"[{}]|\n\s*[A-Za-z][-A-Za-z0-9]*\s*=\s*[{\"]")
    for e in entries:
        for f in e.fields:
            depth = hits = 0
            for m in pat.finditer(text[f.span[0]:f.span[1]]):
                t = m.group(0)
                if t == "{":
                    depth += 1
                elif t == "}":
                    depth -= 1
                elif depth == 0:
                    hits += 1
            # Every span opens with its own `name = {`, so one hit is normal
            # and only a SECOND one means a field was swallowed.
            if hits > 1:
                bad.append((e.citekey, f.name))
    return bad
