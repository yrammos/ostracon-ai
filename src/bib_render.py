#!/usr/bin/env python3
"""Render one BibLaTeX entry and report fields it fills that never reach the
page.

The template prints the bibliography entry, the full note and the short note
for one citekey. The first of these that exists is used:

1. $OSTRACON_PREVIEW_TEMPLATE
2. ${XDG_CONFIG_HOME:-~/.config}/ostracon-ai/previewtemplate.tex, a user's own
   preamble (packages, languages, biblatex options), so that what is checked is
   what that user actually prints
3. src/tex/previewtemplate.tex, shipped here: biblatex-chicago notes, pdflatex,
   nothing user-specific

A template uses BibDesk's preview placeholders, <<File>> and <<CiteKeys>>.

A compile that dies is reported as such, never as a clean render: a harness
that fails quietly answers confidently, and wrongly.
"""
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from bib_validate import ValidatorUnavailable, tool

USER_TEMPLATE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") \
    / "ostracon-ai" / "previewtemplate.tex"
SHIPPED_TEMPLATE = Path(__file__).resolve().parent / "tex" / "previewtemplate.tex"

# Fields whose value should appear in print. Names are checked by surname,
# dates by year, numeric fields by their digits; the rest as text.
NAME_FIELDS = ("author", "editor", "translator", "bookauthor", "editora", "editorb")
TEXT_FIELDS = ("title", "subtitle", "titleaddon", "booktitle", "booksubtitle",
               "maintitle", "journal", "journaltitle", "journalsubtitle", "issuetitle",
               "publisher", "location", "institution", "organization", "series",
               "note", "eventtitle", "chapter")
NUMBER_FIELDS = ("volume", "number", "pages", "edition", "volumes", "issue")
DATE_FIELDS = ("date", "year", "origdate")


BREAK = "\x00"


class RenderFailed(RuntimeError):
    """The entry made the document fail to compile."""


def template_path():
    p = os.environ.get("OSTRACON_PREVIEW_TEMPLATE")
    if p:
        return Path(p)
    return USER_TEMPLATE if USER_TEMPLATE.is_file() else SHIPPED_TEMPLATE


def render(entry_text, citekey):
    """Plain text of the compiled preview for one entry.

    Raises ValidatorUnavailable when the harness itself is missing (no
    template, no TeX), RenderFailed when the compile dies on this entry."""
    template = template_path()
    if not template.is_file():
        raise ValidatorUnavailable(f"preview template not found: {template}")
    pdflatex, biber, pdftotext = tool("pdflatex"), tool("biber"), tool("pdftotext")
    if not (pdflatex and biber and pdftotext):
        raise ValidatorUnavailable("pdflatex, biber or pdftotext not found")

    doc = (template.read_text(encoding="utf-8")
           .replace("<<File>>", "e").replace("<<CiteKeys>>", citekey))
    with tempfile.TemporaryDirectory(prefix="bib-render-") as tmp:
        work = Path(tmp)
        (work / "e.bib").write_text(entry_text, encoding="utf-8")
        (work / "p.tex").write_text(doc, encoding="utf-8")

        def latex():
            return subprocess.run([pdflatex, "-interaction=nonstopmode", "-halt-on-error", "p.tex"],
                                  cwd=work, capture_output=True, text=True, errors="replace")

        first = latex()
        if not (work / "p.bcf").exists():
            raise RenderFailed("first LaTeX pass failed:\n" + first.stdout[-1500:])
        bib = subprocess.run([biber, "p"], cwd=work, capture_output=True,
                             text=True, errors="replace")
        if bib.returncode != 0:
            raise RenderFailed("biber failed:\n" + bib.stdout[-1500:])
        # Two more passes: some errors only surface once the .aux has settled.
        for _ in range(2):
            run = latex()
            if run.returncode != 0:
                raise RenderFailed("LaTeX failed:\n" + run.stdout[-1500:])
        out = subprocess.run([pdftotext, "-enc", "UTF-8", "p.pdf", "-"], cwd=work,
                             capture_output=True, text=True, errors="replace")
        if out.returncode != 0 or not out.stdout.strip():
            raise RenderFailed("no text in the rendered PDF")
        return out.stdout


def detex(value):
    """A field value as it would read in print, roughly: commands dropped,
    their arguments kept. A bibstring prints localized text ("review of",
    "Rezension des"), so it becomes a break: the text on either side of it
    is checked separately."""
    v = re.sub(r"\\bibstring\{[^}]*\}", BREAK, value)
    v = re.sub(r"\\foreignlanguage\{[^}]*\}", "", v)
    v = re.sub(r"\\[a-zA-Z]+\*?", " ", v)       # \mkbibquote, \autocap, \textit ...
    v = re.sub(r"\\.", "", v)                     # \&, \%, accents' backslashes
    return v.replace("~", " ").replace("{", "").replace("}", "")


def norm(text):
    """Lowercase alphanumerics separated by single spaces; ligatures and
    accents folded; line-end hyphenation joined."""
    text = re.sub(r"-\s*\n\s*", "", text)
    # Hyphens dropped, not split on: Karl-Otto may print with a hyphen
    # character pdftotext does not return as "-".
    # Letters only: a page range may print as 9-26.
    text = re.sub(r"(?<=[^\W\d_])[-\u2010\u2011\u00ad](?=[^\W\d_])", "", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", text.lower()))


def _present(needle, haystack):
    n = norm(needle)
    return not n or f" {n} " in f" {haystack} "


def dropped_fields(fields, rendered):
    """Field names whose value does not appear in the rendered text.
    `fields` maps lowercase field name to raw value."""
    page = norm(rendered)
    dropped = []
    for f, raw in fields.items():
        if not raw:
            continue
        value = detex(raw)
        if f in NAME_FIELDS:
            surnames = [p.split(",")[0] for p in re.split(r"\s+and\s+", value)]
            ok = all(_present(s, page) for s in surnames if s.strip())
        elif f in DATE_FIELDS or f in NUMBER_FIELDS:
            # Digits, not tokens: Edition {2} prints "2nd ed.".
            nums = re.findall(r"\d{4}" if f in DATE_FIELDS else r"\d+", value)
            if f in DATE_FIELDS:
                nums = list(dict.fromkeys(nums))  # 2008-10-21/2008-10-25 -> 2008
            if f in DATE_FIELDS and len(nums) > 1:
                # A range's end year may print elided: 2012/2013 -> 2012-13.
                nums = nums[:1] + [n[2:] for n in nums[1:]]
            ok = all(re.search(rf"(?<!\d){n}(?!\d)", page) for n in nums)
        elif f in TEXT_FIELDS:
            ok = all(_present(part, page) for part in value.split(BREAK))
        else:
            continue
        if not ok:
            dropped.append(f)
    return dropped
