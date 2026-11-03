#!/usr/bin/env python3
"""Validate BibLaTeX entries against biblatex-chicago's data model with biber.

A throwaway document loads biblatex-chicago (notes) and \\nocite{*}s the
entries, so biber checks them against the data model a real document uses,
not biblatex's default. Read-only: nothing here edits an entry.

./install publishes this module at
~/.local/share/ostracon-ai/bib_validate.py for other tools to import. Its
interface, validate(bib_text) and ValidatorUnavailable, is kept stable for
them; rules specific to one library belong in the importing tool, layered on
validate(), not here.

biblatex-chicago prints more than biblatex's data model declares, so biber
reports some documented usages as errors. classify() drops those, each with
its source. Add a suppression only with a source of the same kind: a
package example, a passage of the manual, or a test compile.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TEX_DIRS = ("/Library/TeX/texbin", "/opt/homebrew/bin", "/usr/local/bin")

# BibDesk, DEVONthink and house bookkeeping fields: unknown to biber by design.
BOOKKEEPING = re.compile(
    r"^(bdsk-(file|url)-\d+|bdsk-color|local-url(-\d+)?|remote-url(-\d+)?|url-\d+|"
    r"devonthink(-?\d+)?|date-added|date-modified|rating|read|annote|reference|"
    r"keywords|lastchecked|color)$")

DOC = r"""\documentclass{article}
\usepackage[notes,backend=biber]{biblatex-chicago}
\addbibresource{v.bib}
\begin{document}
\nocite{*}
\end{document}
"""

WARN = re.compile(r"WARN - (.*)$")
ENTRY = re.compile(r"^Datamodel: (\w+) entry '([^']+)' \([^)]*\): (.*)$")
UNKNOWN_FIELD = re.compile(r"^Field '([^']+)' invalid in data model")
FIELD_FOR_TYPE = re.compile(r"^Invalid field '([^']+)' for entrytype '([^']+)'")
LEGACY_YEAR = re.compile(r"^legacy year field '(.*)' in entry '([^']+)'")


class ValidatorUnavailable(RuntimeError):
    """TeX or biber is missing, or the throwaway compile failed: no verdict."""


def tool(name):
    """Absolute path to a TeX tool, looking on PATH and in TEX_DIRS, or None."""
    found = shutil.which(name)
    if found:
        return found
    for d in TEX_DIRS:
        path = os.path.join(d, name)
        if os.access(path, os.X_OK):
            return path
    return None


def available():
    return all(tool(t) for t in ("pdflatex", "biber", "kpsewhich"))


def typed_entrytypes():
    """Entry types biblatex gives a field list, or None if blx-dm.def is missing."""
    kpsewhich = tool("kpsewhich")
    if not kpsewhich:
        return None
    dm = subprocess.run([kpsewhich, "blx-dm.def"], capture_output=True,
                        text=True).stdout.strip()
    if not dm:
        return None
    text = Path(dm).read_text(encoding="utf-8", errors="replace")
    types = set()
    for group in re.findall(r"\\DeclareDatamodelEntryfields\[([^\]]+)\]", text):
        types.update(t.strip() for t in group.split(","))
    return types


def run_biber(bib_text):
    """biber's --validate-datamodel log for bib_text. Raises
    ValidatorUnavailable rather than answer when the run did not happen:
    an empty log from a failed compile would read as a clean entry."""
    pdflatex, biber = tool("pdflatex"), tool("biber")
    if not (pdflatex and biber):
        raise ValidatorUnavailable("pdflatex or biber not found")
    with tempfile.TemporaryDirectory(prefix="bib-validate-") as tmp:
        work = Path(tmp)
        (work / "v.bib").write_text(bib_text, encoding="utf-8")
        (work / "v.tex").write_text(DOC, encoding="utf-8")
        latex = subprocess.run([pdflatex, "-interaction=nonstopmode", "-halt-on-error", "v.tex"],
                               cwd=work, capture_output=True, text=True, errors="replace")
        if not (work / "v.bcf").exists():
            raise ValidatorUnavailable("LaTeX run failed:\n" + latex.stdout[-2000:])
        subprocess.run([biber, "--validate-datamodel", "v"],
                       cwd=work, capture_output=True, text=True, errors="replace")
        blg = work / "v.blg"
        if not blg.exists():
            raise ValidatorUnavailable("biber produced no log")
        return blg.read_text(encoding="utf-8", errors="replace")


def entry_facts(bib_text):
    """Per-key facts from the .bib that classify() needs. Field names are
    matched case-insensitively: ostracon writes `Author`, the library `author`."""
    dated, blogs, ranges, nodate, subtype = set(), set(), set(), set(), {}
    edited, evented, key = set(), set(), None
    for line in bib_text.splitlines():
        m = re.match(r"\s*@\w+\{([^,\s]+),", line)
        if m:
            key = m.group(1)
            continue
        if not key:
            continue
        if re.match(r"\s*urldate\s*=", line, re.I):
            dated.add(key)
        elif re.match(r"\s*location\s*=\s*\{blog\}", line, re.I):
            blogs.add(key)
        elif re.match(r"\s*volume\s*=\s*\{\d+--\d+\}", line, re.I):
            ranges.add(key)
        elif re.match(r"\s*(orig)?year\s*=\s*\{\\bibstring\{nodate\}\}", line, re.I):
            nodate.add(key)
        elif st := re.match(r"\s*entrysubtype\s*=\s*\{([^}]*)\}", line, re.I):
            subtype[key] = st.group(1).strip().lower()
        elif re.match(r"\s*eventdate\s*=", line, re.I):
            evented.add(key)
        if re.match(r"\s*(editor|namea|nameb|namec|translator)\s*=", line, re.I):
            edited.add(key)
    return {"dated_by_access": dated, "blogs": blogs, "volume_ranges": ranges,
            "undated": nodate, "subtype": subtype, "edited": edited, "evented": evented}


def classify(log, typed, facts):
    """Yield (key, message) for every warning worth a reader's attention."""
    for line in log.splitlines():
        m = WARN.search(line)
        if not m:
            continue
        msg = m.group(1)
        e = ENTRY.match(msg)
        if e:
            etype, key, detail = e.groups()
            if not _documented(etype, key, detail, typed, facts):
                yield key, f"@{etype}: {detail.replace(' - ignoring field', '').replace(' - ignoring', '')}"
            continue
        y = LEGACY_YEAR.match(msg)
        if y and y.group(1) == "\\bibstring{nodate}":
            # Prints "n.d." (test compile, 2026-11-02; manual under `year`).
            continue
        if y:
            yield y.group(2), f"year '{y.group(1)}' is not a number"
            continue
        yield "-", msg


def _documented(etype, key, detail, typed, facts):
    """True if biber's complaint is a usage biblatex-chicago documents."""
    u = UNKNOWN_FIELD.match(detail)
    if u and BOOKKEEPING.match(u.group(1).lower()):
        return True
    f = FIELD_FOR_TYPE.match(detail)
    # A type with no field list in blx-dm.def (review, music, artwork, ...):
    # biber calls every specific field invalid, though biblatex-chicago prints them.
    if f and typed is not None and f.group(2) not in typed:
        return True

    def starts(prefix):
        return detail.startswith(prefix)

    edited = key in facts["edited"]
    rules = [
        # Access date in place of a date. notes-test.bib: black:infectious,
        # evanston:library, grove:sibelius, wikiped:bibtex, loc:city, genbank:db.
        etype in ("online", "inreference", "article", "review", "video", "dataset")
        and key in facts["dated_by_access"]
        and starts("Missing mandatory field - one of 'date, year'"),
        # \bibstring{nodate} as year or origyear prints "n.d."
        # (test compile, 2026-11-02; ross:thesis in notes-test.bib).
        key in facts["undated"]
        and re.match(r"Invalid value of field '(orig)?year' must be datatype", detail),
        # Combined volumes, e.g. 37--38: real data, not a malformed integer.
        key in facts["volume_ranges"]
        and starts("Invalid value of field 'volume' must be datatype 'integer'"),
        # The article driver prints introduction (byeditor+others -> byothers
        # -> withintroduction).
        etype == "article" and starts("Invalid field 'introduction'"),
        # An obscure journal's place, or "blog". biblatex-chicago.tex,
        # "location"; CMOS 14.182, 14.208.
        etype in ("article", "review") and starts("Invalid field 'location'"),
        # Book-like entry headed by an editor, translator or name[abc].
        # biblatex-chicago.tex, "author".
        etype in ("book", "collection", "mvbook", "inbook") and edited
        and starts("Missing mandatory field 'author'"),
        # The work goes in title, the article in lista.
        # biblatex-chicago.tex, "inreference".
        etype == "inreference"
        and re.match(r"Missing mandatory field '(booktitle|editor|author)'", detail),
        # biblatex-chicago.tex, "unpublished".
        etype == "unpublished"
        and re.match(r"Invalid field '(editor|translator|compiler)'", detail),
        # Printed as a place qualifier after the title (test compile, 2026-11-02).
        etype == "periodical" and starts("Invalid field 'location'"),
        # The book in title, "preface to" in type. biblatex-chicago.tex,
        # "suppbook"; notes-test.bib prose:intro.
        etype in ("suppbook", "suppcollection")
        and (starts("Missing mandatory field 'booktitle'") or starts("Invalid field 'type'")),
        # Unsigned magazine piece: the journal stands in. biblatex-chicago.tex,
        # "journaltitle"; notes-test.bib lakeforester:pushcarts.
        etype in ("article", "review") and facts["subtype"].get(key) == "magazine"
        and starts("Missing mandatory field 'author'"),
        # A colloquy, headed "Editor, ed." (test compile, 2026-11-02).
        etype == "article" and edited and starts("Missing mandatory field 'author'"),
        # A talk dated by its eventdate. notes-test.bib nass:address.
        etype == "unpublished" and key in facts["evented"]
        and starts("Missing mandatory field - one of 'date, year'"),
        # A whole issue whose editor is unknown: the manual says an unknown
        # author or editor "may simply be omitted" and that "Anonymous" is
        # "generally to be avoided" (biblatex-chicago.tex, "author"; CMOS
        # 14.79). The issue title then leads (compile-proved 2026-11-03).
        etype == "periodical" and starts("Missing mandatory field 'editor'"),
        # The holding collection. biblatex-chicago.tex, "misc".
        etype == "misc" and key in facts["subtype"]
        and starts("Invalid field 'institution'"),
    ]
    return any(rules)


def validate(bib_text):
    """[(citekey, message)] for every undocumented biber warning in bib_text,
    sorted. Raises ValidatorUnavailable when no verdict could be reached."""
    log = run_biber(bib_text)
    return sorted(set(classify(log, typed_entrytypes(), entry_facts(bib_text))))
