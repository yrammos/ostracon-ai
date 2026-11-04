"""The evidence step: fill an entry's missing fields from pages a search cites.

Gemini's grounded answer (step 3) cites the pages it read. This step reads
the best of them and takes from each only values it can quote: the page is
evidence, as the source is; Gemini's own wording is not (extraction-rules.md,
the evidence rule).

Three gates, each enforced in code:
1. Domain: aggregators, social media and piracy sites are never read.
2. Identity: a page counts only if it names this work (most of the title's
   words) and its author, editor or year. A page about another work with a
   similar title (the "How the West Was Won" problem) is skipped whole.
3. Quote: a value is accepted only with a verbatim quote found on the page
   that contains the value's words (`supported_by`).
Values only fill missing fields; nothing is overwritten.
"""
from __future__ import annotations

import json
import re
import unicodedata

import enrich

# Never read: they host copies or chatter, not records of the work.
EXCLUDED = ("researchgate.net", "scribd.com", "dokumen.pub", "dokumen.tips", "vdoc.pub",
            "epdf.pub", "pdfcoffee.com", "wikipedia.org", "youtube.com", "goodreads.com",
            "reddit.com", "scispace.com", "academia.edu", "amazon.", "ebay.", "abebooks.",
            "facebook.com", "x.com", "twitter.com", "linkedin.com", "pinterest.", "quizlet.com",
            "coursehero.com", "studocu.com", "medium.com", "blogspot.", "wordpress.com",
            "semanticscholar.org", "vertexaisearch.cloud.google.com")

# The fields a record of the work can supply, by type: the container and
# imprint a chapter PDF rarely prints, the issue data an offprint may lack.
WANTED = {
    "incollection": ["booktitle", "editor", "publisher", "location", "date", "pages"],
    "inproceedings": ["booktitle", "editor", "publisher", "location", "date", "pages"],
    "inbook": ["booktitle", "publisher", "location", "date", "pages"],
    "inreference": ["editor", "publisher", "location", "date"],
    "book": ["publisher", "location", "date"],
    "collection": ["editor", "publisher", "location", "date"],
    "reference": ["editor", "publisher", "location", "date"],
    "proceedings": ["editor", "publisher", "location", "date"],
    "thesis": ["institution", "date"],
    "article": ["journaltitle", "volume", "number", "pages", "date"],
    "review": ["journaltitle", "volume", "number", "pages", "date"],
}
_ALIASES = {"journaltitle": ("journaltitle", "journal"), "location": ("location", "address"),
            "date": ("date", "year"), "booktitle": ("booktitle", "maintitle")}

_STOP = {"the", "a", "an", "of", "and", "in", "on", "to", "for", "de", "la", "le", "les",
         "der", "die", "das", "und", "des", "du", "et", "zur", "zum", "im", "von", "il", "di"}

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]


def words(text):
    """Words of a value or a page: case, accents and LaTeX markup aside."""
    t = unicodedata.normalize("NFKD", enrich.strip_latex(text or "").replace("~", " ").lower())
    return set(re.findall(r"\w+", "".join(c for c in t if not unicodedata.combining(c))))


def supported_by(value, known):
    """Whether `value`'s words all occur in `known` (a set of words), in any
    order. An initial is supported by a word it begins; an ISO date by its
    year, month (name or abbreviation) and day."""
    m = re.fullmatch(r"\s*(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?\s*", value or "")
    if m:
        year, month, day = m.groups()
        ok = year in known
        if month and 1 <= int(month) <= 12:
            name = _MONTHS[int(month) - 1]
            ok = ok and (name in known or name[:3] in known or str(int(month)) in known or month in known)
        if day:
            ok = ok and (day in known or str(int(day)) in known)
        return ok
    vw = words(value)
    if not vw:
        return False
    initials = {w[0] for w in known}
    return all(w in known or (len(w) == 1 and w in initials) for w in vw)


def excluded(domain):
    d = (domain or "").lower()
    return any(x in d for x in EXCLUDED)


def present(fields, name):
    return any((fields.get(a) or "").strip() for a in _ALIASES.get(name, (name,)))


def wanted(entry_type, fields):
    """The fields this step looks for: the type's list plus whatever
    enrich.missing_fields() reports, less what the entry already has."""
    t = (entry_type or "").lower()
    required, desired = enrich.missing_fields(t, fields)
    out = []
    for f in WANTED.get(t, []) + required + desired:
        if f not in out and f != "chapter" and not present(fields, f):
            out.append(f)
    return out


def identity(fields, page_text):
    """(ok, why): whether the page names this work."""
    pw = words(page_text)
    title = " ".join(fields.get(k, "") for k in ("title", "subtitle"))
    tw = {w for w in words(title) if w not in _STOP and len(w) > 1}
    if not tw:
        return False, "no title to match"
    share = len(tw & pw) / len(tw)
    if share < 0.7:
        return False, f"title words {share:.0%}"
    names = set()
    for k in ("author", "editor"):
        for person in re.split(r"\s+and\s+", enrich.strip_latex(fields.get(k, ""))):
            surname = person.split(",")[0].strip()
            names |= {w for w in words(surname) if len(w) > 2}
    year = re.search(r"\d{4}", fields.get("date", "") or fields.get("year", "") or "")
    if names & pw or (year and year.group(0) in pw):
        return True, f"title words {share:.0%}"
    return False, "no author, editor or year"


PROMPT = """Below is a web page, and a bibliographic entry for a work it may describe.

<entry>
{entry}
</entry>

<page url="{url}">
{page}
</page>

If, and only if, the page describes this same work (or, for a chapter, the book that
contains it), give the values it states for these fields: {fields}.
For each, quote the page verbatim: the shortest passage that contains the value.
Formats: names "Last, First and Last, First"; dates YYYY or YYYY-MM-DD; pages
"first--last"; location is the place of publication.
Leave out a field the page does not state. Never supply a value from memory.

Reply with exactly this and nothing else:
<result>{{"same_work": true or false, "fields": {{"<field>": {{"value": "...", "quote": "..."}}}}}}</result>"""


_PUNCT = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                        "\u2013": "-", "\u2014": "-", "\u00a0": " "})


def _norm_ws(s):
    return re.sub(r"\s+", " ", (s or "").translate(_PUNCT)).strip().lower()


def parse_reply(text):
    m = re.search(r"<result>\s*(\{.*\})\s*</result>", text or "", re.S)
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def accept(reply, page_text, want):
    """{field: value} the page supports, and {field: why} for the rejected."""
    ok, rejected = {}, {}
    if not isinstance(reply, dict) or not reply.get("same_work"):
        return ok, {"*": "not the same work"}
    page = _norm_ws(enrich.strip_latex(page_text))
    for f, item in (reply.get("fields") or {}).items():
        f = f.lower()
        if f not in want or not isinstance(item, dict):
            continue
        value, quote = str(item.get("value") or "").strip(), str(item.get("quote") or "")
        if not value:
            continue
        if not quote or _norm_ws(enrich.strip_latex(quote)) not in page:
            rejected[f] = "quote not on the page"
        elif not supported_by(value, words(quote)):
            rejected[f] = "value not in its quote"
        else:
            ok[f] = value
    return ok, rejected
