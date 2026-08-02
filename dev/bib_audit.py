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

# Non-ASCII that says nothing about language. Curly quotes, dashes, an ellipsis
# and a non-breaking hyphen are typography, not evidence of a foreign title, and
# an English title full of them needs no Langid. They were 91 of the rule's 193
# hits until 2026-08-02 -- very nearly half the finding, all of it noise.
TYPOGRAPHIC_NON_ASCII = "‘’“”–—…‑‐" \
                        "‒― ­′″°− " \
                        " ‹›†‡©®™·"


# U+FB00-FB06 (ff fi fl ffi ffl st) and U+0132/0133 (IJ ij). Deliberately NOT
# Æ/æ/Œ/œ, which are letters of the alphabet in the languages that use them --
# `Mediæval` is an editorial choice, `Eﬃcient` is damage.
LIGATURE = re.compile(r"[ﬀ-ﬆĲĳ]")

# Bibstring names written as though they were commands. biblatex-chicago
# defines these as localisation strings, never as macros, so `\reviewof{X}`
# is an undefined control sequence that halts the build. The trap is that the
# name is real -- only the calling convention is wrong.
UNDEFINED_MACRO = re.compile(
    r"\\(reviewof|byeditor|bytranslator|byauthor|nodate|newseries|volume|"
    r"number|edition|reprint|translation)\{")


def substantive_non_ascii(value: str):
    """Non-ASCII characters that actually bear on the title's language."""
    return [c for c in value
            if ord(c) > 127 and c not in TYPOGRAPHIC_NON_ASCII]

# A whole field value that is nothing but an ascending numeric range. Deliberately
# anchored: a range *inside* a title is ordinary, a range that IS the value of
# `Volume`, `Volumes` or `Number` is the shape that needs looking at.
WHOLE_NUMERIC_RANGE = re.compile(
    r"^\s*(\d+)\s*(?:--|-|[\u2010\u2011\u2012\u2013\u2014\u2015])\s*(\d+)\s*$")

# `boxer:china` in notes-test.bib: "the name of the series alone goes in series,
# the rest in number". What counts as "the rest" is a named division or a volume
# number, NOT any colon or digit -- "Harmonologia: Studies in Music Theory" and
# "California Studies in 20th-Century Music" are whole series names, and firing
# on those made 11 of the rule's 13 hits noise.
SERIES_DIVISION = re.compile(
    r"(\bReihe\b|\bAbt(eilung)?\b|\bFolge\b|\bser[ií]e\b|"
    r"\b\d+(st|nd|rd|th)\s+ser\.|\bser\.|\bn\.\s?s\.|\bnew series\b|"
    r"\bBd\.|\bvol\.|\bno\.|"
    r"[,;]\s*[IVXLC0-9]+\s*$|"          # trailing ", 135" or "; IV"
    r"\s+[IVXLC]{1,6}\s*$|"             # trailing Roman numeral, e.g. "... X"
    r"\s+\d{1,3}\s*$)",                 # trailing bare volume number
    re.IGNORECASE,
)


WHOLE_WRAPPER = re.compile(r"^\\(?:foreignlanguage\{[a-zA-Z]+\}|mkbibquote|mkbibemph)\{")


def unwrap(value: str) -> str:
    """The payload of a macro wrapping the WHOLE value, else the value itself.

    Rules anchored on the end of a value are otherwise defeated by the closing
    brace: `\\foreignlanguage{ngerman}{... Jahrhundert X}` does not end in `X`.
    """
    v = value.strip()
    m = WHOLE_WRAPPER.match(v)
    if not m:
        return v
    close = _matching_brace(v, m.end() - 1)
    return v[m.end():close] if close == len(v) - 1 else v


def word_count(value: str) -> int:
    txt = re.sub(r"\\[A-Za-z]+\s*", " ", value)
    txt = re.sub(r"[{}]", " ", txt)
    return len([w for w in txt.split() if w.strip()])


def _flatten(value: str) -> str:
    """Comparable form: markup, brace and whitespace differences discounted."""
    return re.sub(r"\s+", " ",
                  re.sub(r"[{}\\]", "", value)).strip().lower().rstrip(".,;:?!")


def repeated_ngram(value: str, n: int = 5):
    """The first n-word run that occurs twice in `value`, or None.

    Case- and punctuation-blind, because the doubling seen in this file is not
    a clean copy: Sears2020 repeats its second half in lowercase and drops a
    bracket. Five words rather than four: four fires on genuine anaphora.
    """
    words = re.sub(r"\s+", " ",
                   re.sub(r"[^a-z0-9 ]", " ",
                          re.sub(r"[{}\\]", " ", value).lower())).strip().split()
    seen = set()
    for i in range(len(words) - n + 1):
        gram = " ".join(words[i:i + n])
        if gram in seen:
            return gram
        seen.add(gram)
    return None


ONLINE_OK_TYPES = {"online"}


# --------------------------------------------------------------------------
# Shared predicates
#
# These live here, below the scanner, rather than in bib_normalize.py, so that
# the audit and the normalizer answer the same question the same way. They were
# written in the normalizer first, and for one day the two files disagreed:
# three audit counts (1,730 / 216 / 263) were phantoms of rules the decisions of
# 2026-08-01 had already superseded. The dependency arrow runs audit -> normalize
# and cannot be reversed -- normalize already imports the scanner from here, so
# importing back would be a module-level cycle. Hence: predicates move DOWN.
# --------------------------------------------------------------------------

# A title may be joined to its subtitle by terminal punctuation rather than a
# colon. biblatex-chicago handles that itself: \subtitlepunct emits a bare space
# when \ifterm is true and ": " otherwise, so a title ending in `?` or `!` gets
# no interpolated colon. See `batson` in notes-test.bib.
TERMINAL_SPLIT = re.compile(r"[?!](?=\s+[A-Z\\])")

# `:`, `?`, `!` anywhere -- including sealed inside \foreignlanguage{} or
# \mkbibquote{}, which is exactly the case that earns a Shorttitle.
BOUNDARY_MARK = re.compile(r"[:?!]")

# A full stop is the treacherous case: TeX's own spacefactor rule reads a period
# after a capital as an abbreviation, and so must we. "J.\,S. Bach" and
# "Pitch vs. Timbre" are not sentence boundaries.
ABBREV = {"vs", "cf", "ed", "eds", "no", "op", "vol", "pt", "st", "ca",
          "trans", "rev", "ser", "fig", "chap", "mr", "mrs", "dr", "jr", "sr"}


def _at_top_level(value: str, pattern: re.Pattern):
    """First match of `pattern` sitting at brace depth 0, else None.

    Depth awareness is the whole point: a `?` inside
    \\foreignlanguage{french}{... faire ? Dramatiser ...} punctuates the quoted
    phrase, not the entry's title, and splitting there would tear a macro in half.
    """
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
        elif depth == 0:
            m = pattern.match(value, i)
            if m:
                return m
        i += 1
    return None


def count_terminal(value: str) -> int:
    """How many `?`/`!` sit at brace depth 0."""
    depth = n = i = 0
    while i < len(value):
        c = value[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and c in "?!":
            n += 1
        i += 1
    return n


def _token_before(value: str, i: int) -> str:
    return re.split(r"[\s~{}(),]", value[:i].rstrip("."))[-1]


def full_stop_boundary(value: str):
    """Offset of a full stop that genuinely ends a sentence, else None.

    Deliberately conservative: this only ever feeds a report, never an edit,
    because deciding where a period ends a title and where it abbreviates a name
    is judgment the renderer cannot make on our behalf.
    """
    pat = re.compile(r"\.(?=\s+[A-Z\\])")
    depth = 0
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and pat.match(value, i):
            tok = _token_before(value, i)
            if not (len(tok) <= 1 or tok.isupper()
                    or tok.lower().strip(".") in ABBREV):
                return i                   # a real sentence end, not an initial
        i += 1
    return None


def would_split(entry: Entry, value: str) -> bool:
    """True if the splitter would act on this title in this pass."""
    if entry.etype == "review":
        return False
    hits = colon_at_top_level(value)
    if len(hits) == 1:
        return True
    if hits:
        return False                      # multi-colon: declined
    if ": " in value:
        return False                      # sealed inside a macro: declined
    if full_stop_boundary(value) is not None:
        return False                      # reported, never split
    return bool(_at_top_level(value, TERMINAL_SPLIT)) and count_terminal(value) == 1


def keeps_shorttitle(entry: Entry, title_value: str) -> bool:
    r"""True where Shorttitle is earned rather than redundant.

    CLAUDE.md: use Shorttitle "only when the title could not be split and the
    full title is more than six words long". Both halves matter, and the first is
    the one that is easy to get wrong.

    "Could not be split" is not the same as "has no colon". A title whose
    boundary mark is sealed inside a macro -- `\foreignlanguage{russian}{О
    воспитании дирижера: Очерки}`, or Kretschmer2008's `\mkbibquote{...Idee?}` --
    carries a boundary the splitter deliberately declines to act on, so the
    Shorttitle stays and does real work. Likewise a title ending in `?`, which
    has nothing after the mark to hoist into a Subtitle.

    A title with no boundary mark at all is a different case: there is nothing to
    shorten it *to*, so the Shorttitle is simply redundant.
    """
    if entry.etype == "review":
        # The Title carries the reviewed work, and the house short form
        # truncates it at its colon and reduces the author to a surname. Where
        # the reviewed title has neither -- `\bibstring{reviewof}
        # \mkbibemph{Music Encoding Initiative}`, three words and no author --
        # there is nothing to compress, and a Shorttitle would simply duplicate
        # the Title. That is the same defect as Vanhandel2009's, which the
        # maintainer ruled against on 2026-08-02, so the same test applies here.
        return (":" in title_value
                or "\\bibstring{by" in title_value.replace(" ", ""))
    if word_count(title_value) <= 6:
        return False
    if would_split(entry, title_value):
        return False                      # Title will lose its subtitle anyway
    return (has_hoistable_boundary(title_value)
            or full_stop_boundary(title_value) is not None)


def has_hoistable_boundary(value: str) -> bool:
    """A `:`, `?` or `!` with something after it to shorten the title TO.

    Maintainer's ruling, 2026-08-02. A title whose only mark is the closing
    question mark -- "When Is the Brilliant Style Not the Brilliant Style?" --
    has no shortened form: the guidelines say a Shorttitle is the title "up to
    the mark", and here that is the whole title. Two entries had already
    resolved it by storing a Shorttitle byte-identical to the Title, which
    shortens nothing. Such a title is now treated exactly like one carrying no
    mark at all, which is the redundant case.
    """
    for m in BOUNDARY_MARK.finditer(value):
        if value[m.end():].strip().strip("}").strip():
            return True
    return False


def shorttitle_verdict(entry: Entry, title_value: str, already_split: bool) -> str:
    """`earned` | `redundant` | `deferred`, for an entry that HAS a Shorttitle.

    `deferred` means the Title still carries a top-level colon: the splitter will
    act on it, and the disposition follows from that, not from here.
    """
    if not already_split and keeps_shorttitle(entry, title_value):
        return "earned"
    if already_split or not colon_at_top_level(title_value):
        return "redundant"
    return "deferred"


def url_is_earned(entry: Entry) -> bool:
    """True where the entry's `Url` is the locator biblatex-chicago would print.

    The one-canonical-locator rule of 2026-08-01, not the old test by entry type:
    Chicago cites a DOI in preference to a URL, so a `Url` beside a `Doi` is
    redundant; where there is no DOI the address is the only locator the style
    has, so it stays whatever the type.
    """
    sub = entry.get("entrysubtype")
    online_ref = (
        entry.etype in ("inreference", "reference")
        and sub is not None
        and sub.value.strip().lower() == "online"
    )
    return (entry.etype == "online" or online_ref
            or not (entry.has("date") or entry.has("year"))
            or not entry.has("doi"))


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
        # Both tests defer to the shared predicates above. Testing "has no
        # colon" here instead -- as this rule did until 2026-08-02 -- inflated
        # `shorttitle-missing` to 1,730 and `shorttitle-redundant` to 216, the
        # latter naming precisely the entries the normalizer correctly keeps.
        st = e.get("shorttitle")
        t = e.get("title")
        if st and t:
            verdict = shorttitle_verdict(e, t.value, e.has("subtitle"))
            if verdict == "redundant":
                hit("shorttitle-redundant", e.citekey, t.value[:50])
            # A Shorttitle equal to the Title shortens nothing. It slips past
            # the redundancy rule because keeps_shorttitle sees a boundary mark
            # and says "earned" -- true of the mark, false of this title, whose
            # only mark is the final `?` with nothing after it to hoist.
            if _flatten(st.value) == _flatten(t.value):
                hit("shorttitle-equals-title", e.citekey, t.value[:60])
            hits = colon_at_top_level(t.value)
            if hits:
                pre = t.value[: hits[0]].strip()
                if pre and st.value.strip() != pre:
                    hit("shorttitle-mismatch", e.citekey,
                        f"{st.value[:40]!r} vs {pre[:40]!r}")
        # `and not e.has("subtitle")`: an entry must never carry both. Where a
        # Subtitle exists the Title is already the short form and short notes
        # fall back to it, so there is nothing for a Shorttitle to do. Omitting
        # this test made the rule ask for two Shorttitles that would have
        # violated the never-both rule the moment they were written.
        if t and not st and not e.has("subtitle") and keeps_shorttitle(e, t.value):
            # @Review is asymmetric: keeps_shorttitle answers "keep the one it
            # has?", and for a review that is unconditionally yes because the
            # Title encodes the reviewed work. Repurposed as "should it have
            # one?" that flags every review without a Shorttitle -- a different
            # finding from the Tier A sense, so it gets its own bucket.
            rule = ("shorttitle-missing-REVIEW" if e.etype == "review"
                    else "shorttitle-missing")
            hit(rule, e.citekey, f"{word_count(t.value)} words: {t.value[:40]}")

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
        # One canonical locator per entry, not the old test by entry type,
        # which flagged 263 entries the relaxed policy of 2026-08-01 permits.
        url = e.get("url")
        if url and not url_is_earned(e):
            hit("url-misplaced", e.citekey, f"{e.etype}, has doi")
        if e.etype in ONLINE_OK_TYPES and not url:
            hit("online-without-url", e.citekey, "")
        if not e.has("date") and not e.has("urldate"):
            hit("undated-no-urldate", e.citekey, e.etype)

        # --- A value in the wrong field ----------------------------------
        # Nothing in Tier A checks whether a value BELONGS in the field that
        # holds it. Smalley1997 carries a page range in `Volume`; Williams1976
        # carries one in `Volumes`, the field meaning *number of volumes in a
        # set*. The elided-range expansion then applied the literal-field rule
        # correctly to both, which made the corruption look deliberate.
        #
        # Precision matters here: `Number = {1--2}` is house style for a double
        # issue and must not be reported. The discriminators are the field's own
        # meaning and the absence of the field the value probably belongs in.
        for nf in ("volumes", "volume", "number"):
            f = e.get(nf)
            if not f:
                continue
            m = WHOLE_NUMERIC_RANGE.match(f.value)
            if not m:
                continue
            lo, hi = int(m.group(1)), int(m.group(2))
            detail = (f"{nf}={f.value.strip()} [{e.etype}"
                      f"{'' if e.has('pages') else ', NO pages'}"
                      f"{'' if e.has('number') or nf == 'number' else ', no number'}]")
            if nf == "volumes":
                # A count cannot be a range. Always wrong.
                hit("range-in-count-field", e.citekey, detail)
            elif not e.has("pages") and (hi - lo) >= 3:
                # A wide range with no Pages field is the Smalley shape: the
                # page range has been parked in a numbering field.
                hit("range-in-numbering-field", e.citekey, detail)
            elif nf == "number":
                hit("number-range(double-issue?)", e.citekey, detail)
            else:
                hit("volume-range-REVIEW", e.citekey, detail)

        # `Issuetitle` is the *title* of a themed issue -- "The Baroque Body",
        # not "49". A bare number there is the tell for a whole field set shifted
        # one place left: volume -> issuetitle, number -> volume, pages -> number.
        it = e.get("issuetitle")
        if it and re.match(r"^\s*\d+\s*$", it.value):
            g = lambda k: (e.get(k).value.strip() if e.get(k) else "-")  # noqa: E731
            hit("numeric-issuetitle(rotation?)", e.citekey,
                f"issuetitle={g('issuetitle')} volume={g('volume')} "
                f"number={g('number')} pages={g('pages')}")

        # --- Text doubled by a botched import ----------------------------
        # Sibling of the rotation above, same root cause and same disguise: the
        # value parses, renders, and reads as an over-long title. A repeated
        # five-word run is the discriminator -- four words catches genuine
        # anaphora ("The Washing of the Word, the Washing of the World").
        for tf in ("title", "subtitle", "booktitle", "booksubtitle"):
            f = e.get(tf)
            if f and repeated_ngram(f.value):
                hit("doubled-text-in-title", e.citekey, f"{tf}: {f.value[:60]}")

        # --- Series / Number -------------------------------------------
        s = e.get("series")
        if s and SERIES_DIVISION.search(unwrap(s.value)):
            hit("series-carries-division", e.citekey, s.value[:60])
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
        # Two distinct populations, and the second is the larger one the rule
        # could not see: a title already wrapped in \foreignlanguage but with no
        # Langid recorded. CLAUDE.md wants both -- the wrapper for typography,
        # Langid for sorting and for tools that read it -- so a wrapper alone is
        # a real gap, not a pass.
        t = e.get("title")
        if t and not e.has("langid"):
            if "\\foreignlanguage" in t.value:
                hit("wrapped-title-no-langid", e.citekey, t.value[:50])
            elif substantive_non_ascii(t.value):
                hit("non-ascii-no-langid", e.citekey,
                    f"{''.join(sorted(set(substantive_non_ascii(t.value))))[:12]} | "
                    f"{t.value[:40]}")

        # --- PDF extraction artefacts ------------------------------------
        # A typographic ligature is a glyph, not a character: "Eﬃcient" with
        # U+FB03 is what a PDF copy-paste leaves behind. It renders acceptably
        # and so survives every other check, but it defeats search, sorting and
        # hyphenation. Same family as the `hĴp://` and `hps://` URLs already on
        # the url-malformed list, where the tt ligature was mangled or dropped.
        for f in e.fields:
            if f.key.startswith(("bdsk-", "local-url", "remote-url",
                                 "devonthink", "abstract", "keywords")):
                continue
            m = LIGATURE.search(f.value)
            if m:
                hit("ligature-artefact", e.citekey,
                    f"{f.key}: {m.group(0)!r} in "
                    f"{f.value[max(0, m.start() - 24):m.start() + 24]}")

        # --- Macros the style does not define ----------------------------
        # `\reviewof{...}` looks entirely plausible -- `reviewof` IS a name in
        # biblatex-chicago -- but only as a bibstring and a relatedtype. There
        # is no such command, so the entry is a build-breaker. It hid in
        # `shorttitle` and `titleaddon` after the `title` occurrences were
        # fixed, which is why this scans every field rather than the titles.
        for f in e.fields:
            if f.key.startswith(("bdsk-", "local-url", "remote-url",
                                 "devonthink", "abstract")):
                continue
            for m in UNDEFINED_MACRO.finditer(f.value):
                hit("undefined-macro", e.citekey,
                    f"{f.key}: {m.group(0)} -- did you mean "
                    f"\\bibstring{{{m.group(1)}}}?")

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
    # The lookahead is load-bearing. Consuming the opening `{` as part of the
    # field header -- which this pattern did until 2026-08-02 -- means the brace
    # never increments the depth, the closing `}` drives it to -1, and every
    # subsequent header is rejected by the `depth == 0` test. The function then
    # returns [] unconditionally: it reported clean on a synthetic entry whose
    # `bookauthor` had demonstrably been swallowed. A gate that cannot fail is
    # worse than no gate, because it is trusted.
    pat = re.compile(r"[{}]|\n\s*[A-Za-z][-A-Za-z0-9]*\s*=\s*(?=[{\"])")
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
