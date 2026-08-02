#!/usr/bin/env python3
"""Tier A normalization of a legacy BibDesk .bib file.

Surgical: every change is a byte-span rewrite applied back-to-front, so
untouched bytes -- BibDesk's tab indentation, its alphabetical field order,
the multi-KB base64 in bdsk-file-*, and the @comment group plists -- come
through the pass literally unchanged.

Dry run (writes nothing, prints a rule-grouped report):
    python3 dev/bib_normalize.py ~/Documents/Bibdesk/biblio.bib

Show every proposal for one rule:
    python3 dev/bib_normalize.py <file> --rule split-title --samples 0

Apply (requires BibDesk to be closed). Saves a suffixed snapshot of the
current file first -- `biblio_<date>_pre-<label>.bib` -- and prints the one
command that reverts to it:
    python3 dev/bib_normalize.py <file> --apply --label tier-a
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bib_audit import (  # noqa: E402
    Entry,
    bibtool_errors,
    colon_at_top_level,
    roundtrip_ok,
    scan,
)

# --------------------------------------------------------------------------
# Protection
# --------------------------------------------------------------------------

# Fields the pass must never alter. `url` is deliberately absent: the Url/Doi
# rule legitimately removes it. `doi` IS here -- no rule touches it, and the
# gate should say so rather than leave it to inspection.
PROTECTED = re.compile(
    r"^(reference|date-added|date-modified|keywords|doi|"
    r"local-url(-\d+)?|remote-url(-\d+)?|devonthink\d*|"
    r"bdsk-file(-\d+)?|bdsk-url(-\d+)?|rating|read)$"
)

PARENT_OF_SUB = {
    "title": "subtitle",
    "booktitle": "booksubtitle",
    "maintitle": "mainsubtitle",
}

# `pages` is the ONLY field biblatex declares datatype=range, and range fields
# have every dash normalized to \bibrangedash (an en dash). So the separator
# there is cosmetic -- 67-97 and 67--97 render identically -- and collapsing to
# a single hyphen is safe house style.
#
# It would NOT be safe anywhere else. `volume`/`volumes` are datatype=integer
# and everything else is datatype=literal, "printed as is": in those fields the
# separator you write is the one that prints, and Chicago wants an en dash,
# i.e. `--`. Collapsing those to a hyphen would silently degrade the typography,
# which is why `volumes` is deliberately absent from this tuple.
RANGE_FIELDS = ("pages",)
DATE_RANGE_FIELDS = ("date", "origdate", "eventdate", "urldate")
BAD_RANGE = re.compile(r"\s*(?:--|[‐‑‒–—―])\s*")
FORBIDDEN = ("isbn", "issn")

# Fields biblatex prints literally (datatype=literal) or as integers, where the
# separator you type is the one that prints. A numeric range in any of these
# needs `--` so LaTeX sets an en dash, which is what Chicago wants. `pages` is
# deliberately absent -- it is the one range-datatype field and normalizes
# itself -- as are `doi` (verbatim) and `url` (uri), whose hyphens belong to
# the identifier.
EN_DASH_FIELDS = ("number", "volume", "volumes", "title", "subtitle",
                  "booktitle", "booksubtitle", "maintitle", "mainsubtitle",
                  "issuetitle", "titleaddon", "series", "note")
NUM_RANGE = re.compile(r"(?<![\d-])(\d+)(\s*)-(\s*)(\d+)(?![\d-])")


# A title may also be joined to its subtitle by terminal punctuation rather
# than a colon. biblatex-chicago handles that itself: \subtitlepunct emits a
# bare space when \ifterm is true and ": " otherwise, so a title ending in
# `?`, `!` or `.` gets no interpolated colon. The package's own test suite
# says so outright -- see `batson` in notes-test.bib, whose annote reads
# "you no longer need to include the subtitle in the title field when the
# latter ends in a question mark, as the styles now do the right thing
# automatically." So these ARE splittable, and the title keeps its mark.
TERMINAL_SPLIT = re.compile(r"[?!](?=\s+[A-Z\\])")

# French typography puts a non-breaking space before a colon, so the colon we
# drop can strand a `~` or `\,` at the end of the title. Absorb it with the
# colon rather than leaving it dangling.
TRAILING_GLUE = re.compile(r"(?:~|\\,|\\ |\s)+$")


def _at_top_level(value: str, pattern: re.Pattern):
    """First match of `pattern` sitting at brace depth 0, else None.

    Depth awareness is the whole point: a `?` inside
    \\foreignlanguage{french}{... faire ? Dramatiser ...} punctuates the
    quoted phrase, not the entry's title, and splitting there would tear a
    macro in half.
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


# A full stop is the treacherous case: TeX's own spacefactor rule reads a
# period after a capital as an abbreviation, and so must we. "J.\,S. Bach"
# and "Pitch vs. Timbre" are not sentence boundaries.
ABBREV = {"vs", "cf", "ed", "eds", "no", "op", "vol", "pt", "st", "ca",
          "trans", "rev", "ser", "fig", "chap", "mr", "mrs", "dr", "jr", "sr"}


def word_count(value: str) -> int:
    txt = re.sub(r"\\[A-Za-z]+\s*", " ", value)
    return len(re.sub(r"[{}]", " ", txt).split())


def _token_before(value: str, i: int) -> str:
    return re.split(r"[\s~{}(),]", value[:i].rstrip("."))[-1]


def full_stop_boundary(value: str):
    """Offset of a full stop that genuinely ends a sentence, else None.

    Deliberately conservative: this only ever feeds a report, never an edit,
    because deciding where a period ends a title and where it abbreviates a
    name is judgment the renderer cannot make on our behalf.
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


# `:`, `?`, `!` anywhere -- including inside \foreignlanguage{} or \mkbibquote{}.
BOUNDARY_MARK = re.compile(r"[:?!]")


def keeps_shorttitle(entry: Entry, title_value: str) -> bool:
    r"""True where Shorttitle is earned rather than redundant.

    CLAUDE.md: use Shorttitle "only when the title could not be split and the
    full title is more than six words long". Both halves matter, and the first
    is the one that is easy to get wrong.

    "Could not be split" is not the same as "has no colon". A title whose
    boundary mark is sealed inside a macro -- `\foreignlanguage{russian}{О
    воспитании дирижера: Очерки}`, or Kretschmer2008's `\mkbibquote{...Idee?}`
    -- carries a boundary the splitter deliberately declines to act on, so the
    Shorttitle stays and does real work. Likewise a title ending in `?`, which
    has nothing after the mark to hoist into a Subtitle.

    A title with no boundary mark at all is a different case: there is nothing
    to shorten it *to*, so the Shorttitle is simply redundant.
    """
    if entry.etype == "review":
        return True                       # Title carries the reviewed work
    if word_count(title_value) <= 6:
        return False
    if would_split(entry, title_value):
        return False                      # Title will lose its subtitle anyway
    return bool(BOUNDARY_MARK.search(title_value)
                or full_stop_boundary(title_value) is not None)


def snapshot_path(path: str, label: str) -> str:
    """`biblio.bib` -> `biblio_2026-08-01_pre-<label>.bib`, never clobbering.

    Version control by filename suffix rather than by git: the target lives in
    an iCloud-synced folder, where a `.git` object database is liable to be
    corrupted by the sync client, and the multi-KB base64 in `bdsk-file-*`
    makes textual diffing largely useless anyway.
    """
    stem, ext = os.path.splitext(path)
    day = datetime.date.today().isoformat()
    base = f"{stem}_{day}_pre-{label}"
    candidate = base + ext
    n = 2
    while os.path.exists(candidate):
        candidate = f"{base}-{n}{ext}"
        n += 1
    return candidate


class Edit:
    """A single byte-span replacement, tagged with the rule that produced it."""

    __slots__ = ("start", "end", "text", "rule", "citekey", "before", "after")

    def __init__(self, start, end, text, rule, citekey, before="", after=""):
        self.start, self.end, self.text = start, end, text
        self.rule, self.citekey = rule, citekey
        self.before, self.after = before, after


# --------------------------------------------------------------------------
# Field-level span helpers
# --------------------------------------------------------------------------


def _leading_break(text: str, fld):
    """The `\\n<indent>` a field chunk opens with, or None if it has none.

    The parser hands back spans that already include everything between the
    previous field's comma and this field's own end, so the newline and tab
    BibDesk writes are at the *front* of the span, not behind it.
    """
    chunk = text[fld.span[0]:fld.span[1]]
    m = re.match(r"\n([ \t]*)", chunk)
    return m.group(1) if m else None


def field_removal_span(text: str, entry: Entry, fld):
    """Span covering a whole field line, including its separator.

    Handles both the ordinary `,\\n\\tname = {v}` case and the final field,
    which BibDesk writes as `\\tname = {v}}` with the entry's closing brace
    glued on. Returns None if the shape is unrecognised -- caller then skips
    rather than guessing.
    """
    if _leading_break(text, fld) is None:
        return None
    start, end = fld.span
    if end < entry.span[1] and text[end] == ",":
        return (start, end + 1)          # ordinary field: take its comma too
    if end == entry.span[1] - 1 and text[end] == "}":
        # Last field: its own separator sits *behind* it, so reach back over
        # the preceding comma. Taking only (start, end) would strand a
        # trailing `,}` -- legal BibTeX, but not BibDesk's form.
        if start > entry.span[0] and text[start - 1] == ",":
            return (start - 1, end)
        return (start, end)
    return None


def field_insertion(text: str, entry: Entry, before_field, name: str, value: str):
    """Insert `name = {value}` on its own line immediately before `before_field`."""
    indent = _leading_break(text, before_field)
    if indent is None:
        return None, None
    return before_field.span[0], f"\n{indent}{name} = {{{value}}},"


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def plan_edits(text: str, entries):
    edits: list[Edit] = []
    reports: dict[str, list] = defaultdict(list)

    for e in entries:
        protected_urls = {
            f.value.strip()
            for f in e.fields
            if f.key.startswith(("bdsk-url", "remote-url"))
        }
        split_head = None      # post-split Title, if R1 fired on it

        # ---- R1: split Title / Booktitle / Maintitle at a top-level colon
        for parent, sub in PARENT_OF_SUB.items():
            f = e.get(parent)
            if not f:
                continue
            if e.has(sub):
                # A sub* field already exists. If the parent still carries a
                # colon the two disagree about where the work's title ends --
                # a pre-existing malformation, not something this pass caused.
                if colon_at_top_level(f.value):
                    reports["subtitle-exists-but-parent-still-split"].append(
                        (e.citekey, f.value[:70]))
                continue
            hits = colon_at_top_level(f.value)
            if e.etype == "review":
                if hits:
                    reports["skipped-review-title"].append(
                        (e.citekey, f.value[:70]))
                continue
            if len(hits) > 1:
                reports["skipped-multi-colon"].append(
                    (e.citekey, f.value[:70]))
                continue

            if hits:
                # Colon boundary: biblatex supplies the colon, so drop it here.
                head = TRAILING_GLUE.sub("", f.value[: hits[0]])
                tail = f.value[hits[0] + 1:].strip()
            else:
                if ": " in f.value:
                    # Sealed inside \foreignlanguage{...} or \mkbibquote{...};
                    # not an entry-level boundary. Not acting is right, but an
                    # unreported skip would read as coverage it doesn't have.
                    reports["colon-inside-macro-NOT-SPLIT"].append(
                        (e.citekey, f.value[:70]))
                    continue
                stop = full_stop_boundary(f.value)
                if stop is not None:
                    reports["full-stop-boundary-REVIEW"].append(
                        (e.citekey, f.value[:70]))
                    continue
                term = _at_top_level(f.value, TERMINAL_SPLIT)
                if not term:
                    continue
                if count_terminal(f.value) > 1:
                    # More than one `?`/`!` at top level means the title is a
                    # multi-part rhetorical construction, not a title plus a
                    # subtitle -- "Aesthetics---What? Why? and Wherefore?" is
                    # one continuous thought. Which mark is the boundary (if
                    # any) is a decision, exactly as with multi-colon titles.
                    reports["multi-terminal-mark-REVIEW"].append(
                        (e.citekey, f.value[:70]))
                    continue
                # Terminal boundary: the `?`/`!` stays with the title, and
                # \subtitlepunct suppresses the colon because \ifterm is true.
                head = f.value[: term.end()]
                tail = f.value[term.end():].strip()
            if not head or not tail:
                reports["skipped-empty-half"].append((e.citekey, f.value[:70]))
                continue

            pos, ins = field_insertion(text, e, f, sub, tail)
            if pos is None:
                reports["skipped-unparsable-layout"].append((e.citekey, parent))
                continue
            edits.append(Edit(pos, pos, ins, f"split-{parent}", e.citekey,
                              before=f.value, after=f"{head}  ||  {sub}={tail}"))
            edits.append(Edit(*f.value_span, head, f"split-{parent}", e.citekey))
            if parent == "title":
                split_head = head

        # ---- R2/R3: Shorttitle is unnecessary once Title no longer carries a
        #             subtitle -- but only then. It is still earned wherever the
        #             title could not be split in the first place.
        st = e.get("shorttitle")
        t = e.get("title")
        if st and t:
            effective = split_head if split_head is not None else t.value
            already_split = split_head is not None or e.has("subtitle")
            if not already_split and keeps_shorttitle(e, effective):
                reports["shorttitle-earned-KEPT"].append(
                    (e.citekey, effective[:70]))
            elif already_split or not colon_at_top_level(t.value):
                span = field_removal_span(text, e, st)
                rule = ("drop-shorttitle-after-split" if already_split
                        else "drop-shorttitle-redundant")
                if span:
                    edits.append(Edit(span[0], span[1], "", rule,
                                      e.citekey, before=st.value))
                else:
                    reports["skipped-unparsable-layout"].append(
                        (e.citekey, "shorttitle"))

        # ---- R4: range punctuation -> single hyphen
        for rf in RANGE_FIELDS:
            f = e.get(rf)
            if not f or not BAD_RANGE.search(f.value):
                continue
            new = BAD_RANGE.sub("-", f.value)
            if new != f.value:
                edits.append(Edit(*f.value_span, new, "range-punctuation",
                                  e.citekey, before=f.value, after=new))

        # ---- R4b: date ranges take a solidus, not a hyphen
        for rf in DATE_RANGE_FIELDS:
            f = e.get(rf)
            if not f or not BAD_RANGE.search(f.value):
                continue
            new = BAD_RANGE.sub("/", f.value)
            edits.append(Edit(*f.value_span, new, "date-range-solidus",
                              e.citekey, before=f.value, after=new))

        # ---- R4c: numeric ranges in literal fields take an en dash
        for lf in EN_DASH_FIELDS:
            f = e.get(lf)
            if not f:
                continue
            out, changed = [], False
            last = 0
            for m in NUM_RANGE.finditer(f.value):
                a, b = int(m.group(1)), int(m.group(4))
                if b <= a:
                    # Not ascending, so not reliably a range: `Op.15-2` is an
                    # opus number and `3-2 Cohn Cycle` is a name. Report only.
                    reports["hyphen-not-a-range-REVIEW"].append(
                        (e.citekey, f"{lf}: {m.group(0)} in {f.value[:44]}"))
                    continue
                out.append(f.value[last:m.start()])
                out.append(f"{m.group(1)}--{m.group(4)}")
                last = m.end()
                changed = True
            if changed:
                out.append(f.value[last:])
                edits.append(Edit(*f.value_span, "".join(out),
                                  "en-dash-numeric-range", e.citekey,
                                  before=f.value[:46], after="".join(out)[:46]))

        # ---- R5: forbidden fields
        for key in FORBIDDEN:
            f = e.get(key)
            if not f:
                continue
            span = field_removal_span(text, e, f)
            if span:
                edits.append(Edit(span[0], span[1], "", f"drop-{key}",
                                  e.citekey, before=f.value))

        # ---- R6: Url on an entry that has no business carrying one
        url = e.get("url")
        if url and not re.match(r"https?://\S", url.value.strip()):
            reports["url-malformed-REVIEW"].append(
                (e.citekey, url.value[:66]))
        if url:
            sub = e.get("entrysubtype")
            online_ref = (
                e.etype in ("inreference", "reference")
                and sub is not None
                and sub.value.strip().lower() == "online"
            )
            # Chicago cites a DOI in preference to a URL, so a `Url` beside a
            # `Doi` is redundant and goes. Where there is no DOI the address
            # is the only locator the style would print, so it stays -- even
            # on an entry type that would not otherwise carry one.
            keep = (e.etype == "online" or online_ref
                    or not (e.has("date") or e.has("year"))
                    or not e.has("doi"))
            if not keep:
                if url.value.strip() not in protected_urls:
                    reports["url-sole-copy-KEPT"].append(
                        (e.citekey, url.value[:60]))
                else:
                    span = field_removal_span(text, e, url)
                    if span:
                        edits.append(Edit(span[0], span[1], "",
                                          "drop-mirrored-url", e.citekey,
                                          before=url.value))
                        ud = e.get("urldate")
                        if ud:
                            uspan = field_removal_span(text, e, ud)
                            if uspan:
                                edits.append(Edit(uspan[0], uspan[1], "",
                                                  "drop-orphaned-urldate",
                                                  e.citekey, before=ud.value))

        # ---- Report-only: needs a maintainer decision, never rewritten
        s = e.get("series")
        if s and re.search(r"(\bReihe\b|\bser\.|\bn\.s\.|[;:])", s.value, re.I):
            reports["series-division-REVIEW"].append((e.citekey, s.value[:70]))

    return edits, reports


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------


def apply_edits(text: str, edits):
    """Apply back-to-front so earlier offsets stay valid. Rejects overlaps."""
    ordered = sorted(edits, key=lambda ed: (ed.start, ed.end), reverse=True)
    last_start = len(text) + 1
    out = text
    for ed in ordered:
        if ed.end > last_start:
            raise RuntimeError(
                f"overlapping edits near offset {ed.start} ({ed.rule}, {ed.citekey})")
        out = out[: ed.start] + ed.text + out[ed.end:]
        last_start = ed.start
    return out


def verify(original: str, result: str, edits):
    """Every rule the pass claims must hold on the output, and nothing else moved."""
    problems = []

    entries, opaque = scan(result)
    ok, why = roundtrip_ok(result, entries, opaque)
    if not ok:
        problems.append(f"output fails round-trip scan: {why}")

    before_entries, _ = scan(original)
    if len(entries) != len(before_entries):
        problems.append(
            f"entry count changed: {len(before_entries)} -> {len(entries)}")

    before_keys = [e.citekey for e in before_entries]
    if before_keys != [e.citekey for e in entries]:
        problems.append("citekeys changed or reordered")

    # @comment blocks must be byte-identical.
    def comments(t):
        return [t[s:e] for s, e in scan(t)[1] if t[s:e].lstrip().startswith("@")]
    if comments(original) != comments(result):
        problems.append("@comment blocks (BibDesk groups) were modified")

    # No protected field may have changed anywhere.
    def protected_map(ents):
        return {
            (e.citekey, f.key): f.value
            for e in ents for f in e.fields if PROTECTED.match(f.key)
        }
    pb, pa = protected_map(before_entries), protected_map(entries)
    if pb != pa:
        diff = set(pb.items()) ^ set(pa.items())
        problems.append(
            f"{len(diff)} protected field value(s) changed, e.g. "
            f"{list(diff)[:3]}")

    # Idempotence: a second pass must propose nothing.
    again, _ = plan_edits(result, entries)
    if again:
        by_rule = defaultdict(int)
        for ed in again:
            by_rule[ed.rule] += 1
        problems.append(f"not idempotent; second pass still proposes {dict(by_rule)}")

    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--label", default="normalize",
                    help="tag for the pre-write snapshot filename")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="write without first saving a suffixed copy")
    ap.add_argument("--rule", help="show proposals for one rule only")
    ap.add_argument("--samples", type=int, default=6,
                    help="examples per rule; 0 means all")
    args = ap.parse_args()

    raw = open(args.path, "rb").read()
    original = raw.decode("utf-8")
    # Python's text mode translates newlines on both read and write, so a file
    # with any CRLF would come back all-LF with every scanner gate still green.
    # Prove decode/encode is the identity before trusting anything downstream.
    if original.encode("utf-8") != raw:
        print("ABORT: file does not survive a utf-8 decode/encode round trip "
              "(mixed or non-LF line endings). Writing it back would rewrite "
              "every line.")
        return 1

    entries, opaque = scan(original)
    ok, why = roundtrip_ok(original, entries, opaque)
    if not ok:
        print(f"ABORT: input fails round-trip scan ({why})")
        return 1

    edits, reports = plan_edits(original, entries)

    if args.rule:
        rows = [ed for ed in edits if ed.rule == args.rule]
        rows = rows if args.samples == 0 else rows[: args.samples]
        print(f"\n{args.rule} -- {len(rows)} shown\n")
        for ed in rows:
            print(f"  {ed.citekey}")
            if ed.before:
                print(f"    before : {ed.before}")
            if ed.after:
                print(f"    after  : {ed.after}")
            elif not ed.text:
                print("    action : field removed")
        return 0

    by_rule = defaultdict(list)
    for ed in edits:
        by_rule[ed.rule].append(ed)

    print("=" * 74)
    print(f"TIER A {'APPLY' if args.apply else 'DRY RUN'}  {args.path}")
    print("=" * 74)
    print(f"entries {len(entries)}   edits proposed {len(edits)}"
          f"   entries touched {len({ed.citekey for ed in edits})}\n")

    print(f"{'rule':<34}{'edits':>7}   sample")
    print("-" * 74)
    for rule, rows in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        ex = rows[0]
        detail = (ex.after or ex.before or "")[:30]
        print(f"{rule:<34}{len(rows):>7}   {ex.citekey}: {detail}")
    print("-" * 74)

    if reports:
        print("\nLEFT UNTOUCHED, REPORTED FOR YOU (never rewritten):")
        for rule, rows in sorted(reports.items(), key=lambda kv: -len(kv[1])):
            print(f"\n  {rule}  ({len(rows)})")
            for k, d in rows[: args.samples if args.samples else len(rows)]:
                print(f"    {k:<26} {d}")

    result = apply_edits(original, edits)
    problems = verify(original, result, edits)

    # Arithmetic check, deliberately independent of the scanner: the output
    # must be longer or shorter by exactly the sum of the planned deltas. The
    # scanner validates its own parse, so it structurally cannot catch a byte
    # moving somewhere no edit claimed to touch. This can.
    delta = sum(len(ed.text) - (ed.end - ed.start) for ed in edits)
    if len(result) != len(original) + delta:
        problems.append(
            f"length arithmetic: expected {len(original) + delta}, "
            f"got {len(result)} ({len(result) - len(original) - delta:+d})")
    if result.encode("utf-8").decode("utf-8") != result:
        problems.append("output is not utf-8 clean")

    # An INDEPENDENT parser on the output bytes. The gates above are all built
    # on this module's scanner, which records spans and never checks that a
    # value span reaches its field's end -- so a value whose brace closes early
    # round-trips perfectly while being unparsable BibTeX. Only a real parser
    # catches that class, and it has bitten this project once already.
    import tempfile as _tf
    _before = bibtool_errors(args.path)
    with _tf.NamedTemporaryFile("wb", suffix=".bib", delete=False) as _fh:
        _fh.write(result.encode("utf-8"))
        _tmp = _fh.name
    _after = bibtool_errors(_tmp)
    os.unlink(_tmp)
    if _before is None:
        problems.append("bibtool not installed -- cannot verify parseability")
    elif len(_after) > len(_before):
        problems.append(f"independent parser: {len(_before)} errors before, "
                        f"{len(_after)} after -- e.g. {_after[:2]}")

    print("\n" + "=" * 74)
    print("VERIFICATION")
    print("=" * 74)
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        print("\nNothing written.")
        return 1
    print("  PASS  output re-scans cleanly, byte-accounted")
    print("  PASS  entry count and citekey order unchanged")
    print("  PASS  @comment blocks (BibDesk groups) byte-identical")
    print("  PASS  no protected field altered")
    print("  PASS  idempotent (second pass proposes nothing)")
    print(f"  PASS  length arithmetic exact ({delta:+d} bytes, scanner-independent)")
    print("  PASS  utf-8 round trip is the identity (line endings preserved)")
    print(f"  PASS  independent parser (bibtool): {len(_after)} errors, same as input")

    if not args.apply:
        print("\nDry run: nothing written. Re-run with --apply to commit.")
        return 0

    if subprocess.run(["pgrep", "-x", "BibDesk"],
                      capture_output=True).returncode == 0:
        print("\nABORT: BibDesk is running. It holds biblio.bib in memory and "
              "would\noverwrite these changes on its next save. Quit BibDesk "
              "and re-run.")
        return 1

    if not edits:
        print("\nNothing to do: no rule fires on this file. Left untouched.")
        return 0

    if not args.no_snapshot:
        snap = snapshot_path(args.path, args.label)
        with open(snap, "wb") as fh:
            fh.write(raw)                  # the exact bytes we read, unaltered
        if open(snap, "rb").read() != raw:
            print(f"\nABORT: snapshot {snap} did not verify. Nothing written.")
            return 1
        print(f"\nSnapshot: {snap}")

    with open(args.path, "wb") as fh:      # binary: no newline translation
        fh.write(result.encode("utf-8"))
    print(f"Written:  {args.path}")
    if not args.no_snapshot:
        print(f"\nTo revert:  cp '{snap}' '{args.path}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
