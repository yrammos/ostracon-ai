"""The fields an entry type takes, each with a one-line definition, read from
the biblatex manual installed with TeX (biblatex.tex §2.1 and §2.2).

Gemini is asked for these fields (extract_bibtex step 3a), so that a value the
source does not show but a page it found does - a chapter's book title,
editor, publisher - can fill the gap, whatever the type.

The lists are biblatex's own (\\reqitem, \\optitem); a type the manual calls an
alias of another takes that type's lists. Two kinds of field are left out:
biblatex's special fields (sorting, cross-referencing, options), and fields
this project never takes from a search (identifiers it strips, free-text
notes). biblatex-chicago's one structural departure that matters here is
written out in CHICAGO: an entry in a reference work keeps the work in Title
and its own heading in Lista, and a supplement keeps the book in Title and its
kind in Type, so neither takes a Booktitle.
"""
from __future__ import annotations

import functools
import re
import subprocess
from pathlib import Path

# Never asked for: special fields (biblatex §2.2.3), what the project strips
# (enrich.FORBIDDEN_FIELDS_ALWAYS), free text, Langid and Language (this
# project records the language in Langid), and Year (a date goes in Date;
# Year holds only \bibstring{nodate}, which no search supplies).
SKIP = {
    "crossref", "xref", "xdata", "entryset", "execute", "ids", "options", "presort",
    "sortkey", "sortname", "sortshorthand", "sorttitle", "sortyear", "related",
    "relatedoptions", "relatedtype", "relatedstring", "keywords", "isbn", "issn",
    "isrn", "label", "indexsorttitle", "indextitle", "shorthand", "shorthandintro",
    "gender", "langid", "langidopts", "hyphenation", "library", "file", "abstract",
    "annotation", "addendum", "note", "eprint", "eprintclass", "eprinttype", "eid",
    "pubstate", "url", "urldate", "language", "year",
}
# Url and Urldate only where this project keeps them (extraction-rules.md).
ONLINE_ONLY = {"url", "urldate"}
# Types biblatex leaves without field lists, and whose lists they follow.
BASE = {"review": "article", "suppperiodical": "article"}
# biblatex-chicago's departures (biblatex-chicago.tex §4.1, §4.2: lista).
_NO_BOOKTITLE = {"booktitle", "booksubtitle", "booktitleaddon"}
CHICAGO = {
    "inreference": {"drop": _NO_BOOKTITLE, "add": ["lista"]},
    # A supplement: the book's title is Title, the supplement's kind is Type.
    "suppbook": {"drop": _NO_BOOKTITLE, "add": ["type"]},
    "suppcollection": {"drop": _NO_BOOKTITLE, "add": ["type"]},
}
LISTA = "The name of the entry in an alphabetically arranged reference work (biblatex-chicago)."


def _first_sentence(text, limit=200):
    text = re.sub(r"\s+", " ", text).strip()
    first = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text)[0] if text else ""
    return first[:limit]


def _plain(tex):
    t = tex.replace('"=', "-").replace("\\ie", "i.e.").replace("\\eg", "e.g.")
    for _ in range(4):
        t = re.sub(r"\\(?:bibfield|bibtype|texttt|emph|cmd|opt|prm|env|textsf|textbf|path|"
                   r"file|sty|acr|keyword|bibmacro)\*?\{([^{}]*)\}", r"\1", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", "", t)
    return re.sub(r"[{}~]", " ", t)


@functools.lru_cache(maxsize=1)
def _manual():
    """biblatex.tex's text, or '' when TeX's documentation is not installed."""
    try:
        root = subprocess.run(["kpsewhich", "-var-value", "TEXMFDIST"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
        return (Path(root) / "doc" / "latex" / "biblatex" / "biblatex.tex").read_text(encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        return ""


@functools.lru_cache(maxsize=1)
def _tables():
    tex = _manual()
    if not tex:
        return {}, {}
    types = {}
    body = tex[tex.index(r"\subsubsection{Regular Types}"):tex.index(r"\subsubsection{Data Fields}")]
    for m in re.finditer(r"\\typeitem\{([a-z]+)\}(.*?)(?=\\typeitem|\\subsubsection|\Z)", body, re.S):
        chunk = m.group(2)
        lists = [re.search(rf"\\{k}\{{([^}}]*)\}}", chunk) for k in ("reqitem", "optitem")]
        names = [f.strip() for g in lists if g for f in g.group(1).split(",") if f.strip()]
        alias = re.search(r"alias for \\bibtype\{(\w+)\}|alias for (\w+)", chunk)
        types[m.group(1)] = (names, (alias.group(1) or alias.group(2)) if alias else None)
    fields = {}
    body = tex[tex.index(r"\subsubsection{Data Fields}"):tex.index(r"\subsubsection{Field Aliases}")]
    for m in re.finditer(r"\\(?:fielditem|listitem|keyitem)\{([a-z]+)\}\{[^}]*\}(.*?)"
                         r"(?=\\(?:fielditem|listitem|keyitem)\{|\\subsubsection|\Z)", body, re.S):
        fields.setdefault(m.group(1), _first_sentence(_plain(m.group(2))))
    return types, fields


def fields_for(entry_type):
    """[(field, definition)] the type takes and a search may supply, in the
    manual's order; [] when the manual is unavailable or the type unknown."""
    types, defs = _tables()
    t = (entry_type or "").lower()
    names, alias = types.get(t, ([], None))
    if not names:
        names = types.get(BASE.get(t) or alias or "", ([], None))[0]
    out = []
    for group in names:
        for f in group.split("/"):          # year/date, author/editor
            if f in SKIP and not (f in ONLINE_ONLY and t == "online"):
                continue
            if f in CHICAGO.get(t, {}).get("drop", ()) or f in (x for x, _ in out):
                continue
            out.append((f, defs.get(f, "")))
    # [house] journal, not journaltitle (maintainer's ruling, 2026-11-05).
    out = [("journal", d) if f == "journaltitle" else (f, d) for f, d in out]
    for f in CHICAGO.get(t, {}).get("add", []):
        out.append((f, LISTA if f == "lista" else defs.get(f, "")))
    return out
