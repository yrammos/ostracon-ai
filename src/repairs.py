"""Mechanical repairs for a new entry, from the library cleanup of 2026-11-01/02
("Postprocessing cleanup for ostracon-ai", §3 and §4). No model: each rule
fires on the entry's own text and nothing else.

Each rule is tagged with its evidence, as the spec asks, because only the
[house] rules are the maintainer's to revise:
  [doc]      biblatex-chicago.tex
  [ex]       a worked example in notes-test.bib
  [dm]       biblatex's data model, blx-dm.def
  [compile]  verified by compiling under biblatex-chicago (notes)
  [house]    the maintainer's own decision

repair(entry_text) -> (entry_text, applied), applied being lines of the form
"rule: field before -> after" for the log. Rules run after
bib_normalize's (house_style.normalize) and before the library-spelling snap.
A rule that cannot act safely reports nothing and changes nothing.

Page ranges are deliberately left to bib_normalize's R4 (single hyphen, the
maintainer's ruling of 2026-11-05).
"""
from __future__ import annotations

import re
import unicodedata

import enrich

# Fields no rule here may touch: identifiers, paths, bookkeeping.
PROTECTED = re.compile(
    r"^(doi|url|eprint|file|isbn|issn|reference|date-added|date-modified|keywords|"
    r"local-url(-\d+)?|remote-url(-\d+)?|url-\d+|devonthink-?\d*|bdsk-[\w-]+|rating|read|"
    r"urldate|langid|hyphenation|date|origdate|eventdate|year|origyear|annote|abstract)$", re.I)

# Repositories and aggregators, never the work's own URL (spec §3.9) [house].
AGGREGATORS = re.compile(
    r"(researchgate\.net|academia\.edu|scispace\.com|semanticscholar\.org|scribd\.com|"
    r"dokumen\.pub|studocu\.com|coursehero\.com|pdfcoffee\.com|vdoc\.pub|epdf\.pub)", re.I)

_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
                  "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
# Publishers that keep "Press"/"Verlag" (spec §4) [house].
_KEEPS_PRESS = re.compile(
    r"(university|universit|college|institute|institution|academy|academic press|free press|"
    r"\bmit\b|\bsuny\b|seminary|psycho-analytical press|focal press|studio verlag|psychology press|"
    r"druckerei und verlag)", re.I)

_ACCENTS = {"'": "́", "`": "̀", "^": "̂", '"': "̈", "~": "̃",
            "=": "̄", ".": "̇", "c": "̧", "v": "̌", "u": "̆",
            "H": "̋", "k": "̨", "r": "̊"}


def _fields(text):
    return {k.lower(): v for k, v in enrich.parse_bibtex_fields(text).items()}


def _etype(text):
    return enrich.get_entry_type(text) or ""


def _rename(text, old, new):
    """`old = {…}` renamed `new`, keeping the entry's capitalization style."""
    def sub(m):
        name = new.capitalize() if m.group(2)[:1].isupper() else new
        return m.group(1) + name + m.group(3)
    return re.sub(rf"(?im)^(\s*)({re.escape(old)})(\s*=)", sub, text, count=1)


def _roman(value):
    v = value.strip().lower()
    if not v or not re.fullmatch(r"[ivxlcdm]+", v):
        return None
    total, prev = 0, 0
    for ch in reversed(v):
        n = _ROMAN[ch]
        total = total - n if n < prev else total + n
        prev = max(prev, n)
    return total if total > 0 else None


def _ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _unaccent_macros(value):
    """TeX accent macros as Unicode letters: \\'e, \\'{e}, {\\'e}, \\c{c}."""
    def sub(m):
        mark, letter = m.group(1) or m.group(3), m.group(2) or m.group(4)
        if letter in ("\\i", "\\j"):
            letter = letter[1]
        return unicodedata.normalize("NFC", letter + _ACCENTS[mark])
    pattern = (r"\{?\\([\'`^\"~=.])\s*\{?(\\[ij]|[A-Za-z])\}?\}?"
               r"|\{?\\([cvuHkr])\{(\\[ij]|[A-Za-z])\}\}?")
    return re.sub(pattern, sub, value)


def repair(entry_text, is_house=None):
    """(entry_text, applied): the mechanical repairs applied to one entry.
    `is_house(field, value)`, when given, says whether the library already
    writes that value so; a value the library holds is its own house form, and
    the publisher rule leaves it alone (the library keeps UMI Research Press)."""
    is_house = is_house or (lambda field, value: False)
    applied = []
    text = entry_text

    def note(rule, field, before, after):
        applied.append(f"{rule}: {field} {before!r} -> {after!r}")

    f, t = _fields(text), _etype(text)

    # [house] `journal`, not `journaltitle`: both are biblatex-chicago fields,
    # and BibDesk groups split between them (ruling 2026-11-05).
    if "journaltitle" in f:
        if "journal" in f:
            text = enrich.remove_field(text, "journaltitle")
            note("journal", "journaltitle", f["journaltitle"], "(dropped; journal kept)")
        else:
            text = _rename(text, "journaltitle", "journal")
            note("journal", "journaltitle", "journaltitle", "journal")
        f = _fields(text)

    # [house] BibTeX's legacy names, written by imported or search-made
    # entries: `location`, not `address` (biblatex's own alias); `date`, not
    # `year`, except for Year = {\bibstring{nodate}}. When both are present
    # with the same value the legacy one goes; a differing one is kept.
    for old, new in (("address", "location"), ("year", "date")):
        if old not in f:
            continue
        value = f[old].strip()
        if old == "year" and not re.fullmatch(r"\d{4}", value):
            continue
        if new not in f:
            text = _rename(text, old, new)
            note(new, old, old, new)
        elif f[new].strip() == value or (old == "year" and f[new].strip()[:4] == value):
            text = enrich.remove_field(text, old)
            note(new, old, f[old], f"(dropped; {new} kept)")
        f = _fields(text)

    # [house, CMOS 14.147] volume is an integer: Roman numerals become Arabic.
    vol = f.get("volume", "")
    n = _roman(vol)
    if n:
        text = enrich.set_field(text, "volume", str(n))
        note("volume-arabic", "volume", vol, str(n))
        vol = str(n)

    # [house] 29(2) is volume 29, number 2. 29/2 likewise, unless the two are
    # consecutive (37/38), which is a combined volume, written 37--38.
    m = re.fullmatch(r"\s*(\d+)\s*(?:\((\d+)\)|/\s*(\d+))\s*", vol)
    if m:
        a, b = int(m.group(1)), int(m.group(2) or m.group(3))
        if m.group(3) and b == a + 1:
            text = enrich.set_field(text, "volume", f"{a}--{b}")
            note("volume-combined", "volume", vol, f"{a}--{b}")
        elif "number" not in f:
            text = enrich.set_field(text, "volume", str(a))
            text = enrich.add_field(text, "number", str(b))
            note("volume-number-split", "volume", vol, f"volume {a}, number {b}")

    # [doc] edition is an integer; "2nd ed." is printed by the style.
    ed = f.get("edition", "")
    m = re.fullmatch(r"\s*(\d+)\s*(?:st|nd|rd|th|\.)?\s*(?:ed(?:ition)?\.?|aufl(?:age)?\.?|"
                     r"[ée]d(?:ition)?\.?)?\s*", ed, re.I)
    word = _ORDINAL_WORDS.get(re.sub(r"\s+ed(ition)?\.?$", "", ed.strip().lower()))
    if ed and (m or word) and ed.strip() != str(word or (m and m.group(1))):
        new = str(word or m.group(1))
        text = enrich.set_field(text, "edition", new)
        note("edition-integer", "edition", ed, new)

    f = _fields(text)
    # [dm, house] no Volumes, and no Publisher, on a journal article.
    if t in ("article", "review"):
        for fld in ("volumes", "publisher"):
            if fld in f:
                text = enrich.remove_field(text, fld)
                note(f"article-no-{fld}", fld, f[fld], "(removed)")

    # [compile] fields @unpublished never prints move into Note (edition and
    # volume never reach the .bbl), so nothing is lost.
    if t == "unpublished":
        parts = []
        for fld, fmt in (("edition", lambda v: f"{_ordinal(int(v))} ed." if v.isdigit() else v),
                         ("volume", lambda v: f"\\autocap{{v}}ol.~{v}"),
                         ("version", lambda v: f"version {v}"),
                         ("pages", lambda v: f"pp.~{v}"),
                         ("institution", lambda v: v)):
            if f.get(fld):
                parts.append(fmt(f[fld].strip()))
                text = enrich.remove_field(text, fld)
                note("unpublished-to-note", fld, f[fld], "note")
        if parts:
            old = f.get("note", "")
            new = ", ".join([old] + parts) if old else ", ".join(parts)
            text = enrich.set_field(text, "note", new) if old else enrich.add_field(text, "note", new)

    f = _fields(text)
    # [house] an aggregator's URL is never the work's; a Urldate with no Url
    # records nothing.
    if f.get("url") and AGGREGATORS.search(f["url"]):
        text = enrich.remove_field(text, "url")
        note("aggregator-url", "url", f["url"], "(removed)")
        f = _fields(text)
    if f.get("urldate") and not f.get("url"):
        text = enrich.remove_field(text, "urldate")
        note("urldate-without-url", "urldate", f["urldate"], "(removed)")
        f = _fields(text)

    # [house] Location: the first city only. A list field: cities are joined
    # by " and " at the top level.
    loc = f.get("location", "")
    if " and " in loc and "{" not in loc:
        first = loc.split(" and ")[0].strip()
        text = enrich.set_field(text, "location", first)
        note("first-city", "location", loc, first)

    # [house] publishers: no initial "The"; no Inc., Ltd., Co., Company,
    # Publishing Company; no Press or Verlag, except a university's or an
    # institution's and the names that break without it.
    pub = f.get("publisher", "")
    if pub and "{" not in pub and " and " not in pub and not is_house("publisher", pub):
        new = re.sub(r"^The\s+", "", pub)
        new = re.sub(r",?\s+(Inc\.?|Ltd\.?|Co\.|Company|Publishing Company)$", "", new)
        if not _KEEPS_PRESS.search(new):
            new = re.sub(r"\s+(Press|Verlag)$", "", new)
        if new and new != pub:
            text = enrich.set_field(text, "publisher", new)
            note("publisher-short", "publisher", pub, new)

    f = _fields(text)
    # [doc] a corporate author takes double braces. Only where it is
    # unambiguous: the author is the publisher (chicago:manual).
    au = f.get("author", "")
    if au and "," not in au and " and " not in au and not au.startswith("{") \
            and au.strip().lower() == f.get("publisher", "").strip().lower():
        text = enrich.set_field(text, "author", "{" + au + "}")
        note("corporate-author", "author", au, "{" + au + "}")

    f = _fields(text)
    # [house] Unicode letters, not TeX accent macros; -- and ---, not Unicode
    # dashes (page ranges are bib_normalize's); single spaces.
    for fld, val in f.items():
        if PROTECTED.match(fld) or fld == "pages":
            continue
        new = _unaccent_macros(val)
        new = new.replace("—", "---").replace("–", "--")
        new = re.sub(r"[ \t\r\n]{2,}", " ", new)
        if new != val:
            text = enrich.set_field(text, fld, new)
            note("typography", fld, val[:40], new[:40])

    # [house] Hyphenation is langid's legacy alias: dropped when it repeats
    # Langid, renamed Langid when there is none.
    f = _fields(text)
    hy = f.get("hyphenation")
    if hy:
        if f.get("langid", "").strip().lower() == hy.strip().lower():
            text = enrich.remove_field(text, "hyphenation")
            note("hyphenation", "hyphenation", hy, "(removed; repeats langid)")
        elif not f.get("langid"):
            text = _rename(text, "hyphenation", "langid")
            note("hyphenation", "hyphenation", "hyphenation", "langid")

    return text, applied


def date_problems(entry_text):
    """[str]: Date values that are not ISO 8601 (YYYY, YYYY-MM, YYYY-MM-DD, or
    a range of these joined by /). Reported, never changed [doc]."""
    v = _fields(entry_text).get("date", "")
    iso = r"\d{4}(-\d{2}(-\d{2})?)?"
    if v and not re.fullmatch(rf"{iso}(/({iso})?)?", v.strip()):
        return [f"date {v!r} is not ISO 8601"]
    return []
