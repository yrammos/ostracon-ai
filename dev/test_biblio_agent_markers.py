#!/usr/bin/env python3
"""
Regression test for what BiblioAgent.save_entry() writes - specifically the
"% AMBER: ..." comment web_source.py's content-plausibility follow-up relies
on to survive into the saved .bib text for a .webloc source (never fileable,
so it never reaches BibDesk's own color - see save_entry), and the review
color reaching BibDesk on the other branch.

These tests were written against the marker protocol: save_entry() took a
string with up to four "%" markers prepended, and recovered the state by
matching each positionally with an anchored re.match. That protocol is gone
(issue #18) - state now arrives as an ExtractionResult, and the comments are
rendered at the point of writing rather than parsed out of the text. What the
tests assert is unchanged, because they always asserted on what reaches the
saved file and on the kwargs BibDesk is called with, not on the markers. Only
how each fixture is built has changed: an ExtractionResult, not a prefixed
string.

The fault they were written for is worth restating, since it is what the
refactor removes. The "% Source: ..." regex placed its \\b word-boundary
assertion right after the literal colon (`Source:\\b`), which can never match.
Because that marker was outermost and re.match anchors at position 0, every
matcher below it then ran against a string still starting "% Source:" and
failed too - so needs_color was False for every entry and every source type,
and BibDesk's amber coloring was inert from 2026-07-30 until it was found. See
issue #17 for the affected window.

The later tests cover a second path into the saved file: a model response that
is not one bare entry (#32). Commentary quoting `@Suppbook` used to be taken as
the start of the entry and saved, past both of save_entry()'s guards.

No API call, no network: uses config.yaml as-is (BiblioAgent's __init__
constructs an Anthropic client but never calls it here), redirected to a
temp main_bib_file with autofile_bibdesk off, so nothing touches BibDesk.

    python3 dev/test_biblio_agent_markers.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import biblio_agent  # noqa: E402


def _agent(tmp_bib_path, autofile=False):
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    agent.config["main_bib_file"] = str(tmp_bib_path)
    agent.config["autofile_bibdesk"] = autofile
    return agent


def _capture_bibdesk(agent):
    """Replace _save_via_bibdesk with a recorder - no osascript, no BibDesk,
    nothing launched. Returns the dict the call's kwargs land in."""
    calls = {}

    def _fake(entry, bib_path, needs_color=False, auto_file=True):
        calls.update(entry=entry, bib_path=bib_path,
                     needs_color=needs_color, auto_file=auto_file)

    agent._save_via_bibdesk = _fake
    return calls


@contextmanager
def _patched(module, **attrs):
    """Set module attributes for the duration, restoring them afterwards."""
    saved = {name: getattr(module, name) for name in attrs}
    for name, value in attrs.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


def test_source_and_amber_comments_survive_needs_color_flag_is_discarded():
    result = biblio_agent.ExtractionResult(
        entry=(
            "@Online{MarkerTest2026,\n"
            "  Title = {A Study of Musical Form},\n"
            "  Urldate = {2026-08-29},\n"
            "}\n"
        ),
        source_label="webpage (https://example.org/x)",
        needs_color=True,
        amber_reason="no Author/Doi/PublicationDate - only Urldate available to date it",
    )
    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        agent = _agent(bib_path)
        err = io.StringIO()
        with redirect_stderr(err):
            ok = agent.save_entry(result, "smoketest.webloc")
        saved = bib_path.read_text(encoding="utf-8")

    assert ok is True
    # NEEDS_COLOR_FLAG is internal bookkeeping only - must not reach the file.
    assert "NEEDS_COLOR_FLAG" not in saved, saved
    # Both comments that ARE meant to persist must actually be there, in the
    # order comment_lines() renders them (Source outermost, AMBER next).
    assert saved.index("% Source:") < saved.index("% AMBER:") < saved.index("@Online"), saved
    assert "% AMBER: no Author/Doi/PublicationDate" in saved, saved
    assert "@Online{MarkerTest2026," in saved, saved
    # needs_color (from the AMBER marker) can't reach BibDesk for a
    # non-fileable source - the warning explaining why must still fire.
    assert "color flag needs" in err.getvalue(), err.getvalue()
    return True


def test_entry_without_amber_marker_is_unaffected():
    # A plain PDF-sourced entry (no AMBER marker at all) must still save
    # correctly and keep its Source comment - the fix must not have broken
    # the common case while fixing the broken one.
    result = biblio_agent.ExtractionResult(
        entry=(
            "@Article{PlainTest2026,\n"
            "  Title = {An Ordinary Article},\n"
            "  Author = {Doe, Jane},\n"
            "}\n"
        ),
        source_label="PDF (paper.pdf)",
    )
    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        agent = _agent(bib_path)
        err = io.StringIO()
        with redirect_stderr(err):
            ok = agent.save_entry(result, "plaintest.pdf")
        saved = bib_path.read_text(encoding="utf-8")

    assert ok is True
    assert "% Source: PDF (paper.pdf)" in saved, saved
    assert "AMBER" not in saved, saved
    assert "@Article{PlainTest2026," in saved, saved
    return True


def test_webloc_reaches_bibdesk_and_is_colored_without_auto_file():
    # The whole point of the routing fix: under autofile_bibdesk the importer
    # discards every % comment, so the color is the ONLY surviving carrier of
    # the amber flag - and a .webloc entry used to be excluded from the import
    # path entirely, so it got neither. It must now be imported and colored,
    # with only `auto file` withheld (there is no document to file).
    result = biblio_agent.ExtractionResult(
        entry="@Online{WeblocTest2026,\n  Title = {A Study of Musical Form},\n}\n",
        source_label="webpage (https://example.org/x)",
        needs_color=True,
        amber_reason="no Author/Doi/PublicationDate - only Urldate available to date it",
    )
    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        agent = _agent(bib_path, autofile=True)
        calls = _capture_bibdesk(agent)
        err = io.StringIO()
        with redirect_stderr(err):
            ok = agent.save_entry(result, "smoketest.webloc")

    assert ok is True
    assert calls, "a .webloc entry never reached _save_via_bibdesk"
    assert calls["needs_color"] is True, calls
    assert calls["auto_file"] is False, calls
    # No document, so no file bookmark should have been attached either.
    assert "bdsk-file-1" not in calls["entry"], calls["entry"]
    return True


def test_pdf_still_auto_files():
    # The other side of the same split: a PDF has a document, so auto_file
    # stays on. Guards against "fix the .webloc case, silently stop filing
    # every PDF."
    result = biblio_agent.ExtractionResult(
        entry="@Article{PdfTest2026,\n  Title = {An Ordinary Article},\n  Author = {Doe, Jane},\n}\n",
        source_label="PDF (paper.pdf)",
        needs_color=True,
    )
    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        agent = _agent(bib_path, autofile=True)
        calls = _capture_bibdesk(agent)
        # add_bdsk_bookmark would try to bookmark a file that isn't there;
        # the bookmark itself isn't what this test is about.
        agent.add_bdsk_bookmark = lambda entry, path: entry
        err = io.StringIO()
        with redirect_stderr(err):
            ok = agent.save_entry(result, "paper.pdf")

    assert ok is True
    assert calls["auto_file"] is True, calls
    assert calls["needs_color"] is True, calls
    return True


def test_no_color_flag_means_no_color():
    # needs_color must not become sticky: an entry carrying no
    # NEEDS_COLOR_FLAG has to reach BibDesk uncolored.
    result = biblio_agent.ExtractionResult(
        entry="@Online{CleanTest2026,\n  Title = {A Study of Musical Form},\n}\n",
        source_label="webpage (https://example.org/x)",
    )
    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        agent = _agent(bib_path, autofile=True)
        calls = _capture_bibdesk(agent)
        err = io.StringIO()
        with redirect_stderr(err):
            ok = agent.save_entry(result, "clean.webloc")

    assert ok is True
    assert calls["needs_color"] is False, calls
    return True


def test_field_sources_reaches_the_saved_text():
    # enrich_entry()'s per-field provenance was the one marker with no
    # coverage at all, and it is the marker that sat innermost - so under the
    # old positional protocol it was the first to be lost whenever anything
    # above it failed to match, and nothing would have said so.
    result = biblio_agent.ExtractionResult(
        entry="@Article{SourcesTest2026,\n  Title = {An Ordinary Article},\n  Author = {Doe, Jane},\n}\n",
        source_label="PDF (paper.pdf)",
        field_sources="PDF: title, author; CrossRef: volume, pages",
    )
    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        agent = _agent(bib_path)
        err = io.StringIO()
        with redirect_stderr(err):
            ok = agent.save_entry(result, "paper.pdf")
        saved = bib_path.read_text(encoding="utf-8")

    assert ok is True
    assert "% Sources -- PDF: title, author; CrossRef: volume, pages" in saved, saved
    # Innermost of the three: after Source, immediately before the entry.
    assert saved.index("% Source:") < saved.index("% Sources --") < saved.index("@Article"), saved
    return True


def _incomplete_line(entry_text):
    """The `% INCOMPLETE` line save_entry() writes for this entry, or None."""
    result = biblio_agent.ExtractionResult(entry=entry_text)
    with tempfile.TemporaryDirectory() as td:
        agent = _quiet_agent(td)
        with redirect_stderr(io.StringIO()):
            assert agent.save_entry(result, "x.webloc") is True
        saved = (Path(td) / "staging.bib").read_text(encoding="utf-8")
    lines = [ln for ln in saved.splitlines() if ln.startswith("% INCOMPLETE")]
    return lines[0] if lines else None


def test_chapter_without_its_container_is_reported_incomplete():
    # #30: a chapter entry with no Booktitle was saved as complete. The prompt
    # now tells the model to omit Booktitle rather than invent it, so the gap
    # has to be reported downstream or it is invisible.
    line = _incomplete_line("@Incollection{C,\n  Title = {A Chapter},\n  Pages = {1-20},\n}")
    assert line is not None and "booktitle" in line, line
    line = _incomplete_line("@Inproceedings{P,\n  Title = {A Paper},\n  Pages = {1-20},\n}")
    assert line == "% INCOMPLETE: missing booktitle", line
    return True


def test_container_named_otherwise_is_not_reported():
    # @SuppBook's Title IS the book (notes-test.bib: polakow:afterw,
    # prose:intro), and a chapter in an untitled volume of a multi-volume
    # work names its container in Maintitle. Neither lacks anything.
    assert _incomplete_line(
        "@SuppBook{S,\n  Title = {The Book},\n  Afterword = {yes},\n  Pages = {200-210},\n}") is None
    line = _incomplete_line(
        "@Inbook{F,\n  Title = {Negation},\n  Maintitle = {Standard Edition},\n"
        "  Volume = {19},\n  Chapter = {3},\n  Pages = {235-242},\n}")
    assert line is None, line
    return True


def _lookups(entry_type, fields, pdf_text, doi_record=None):
    """The lookups gather_enrichment() makes, in order, with every one stubbed:
    the DOI lookup answers `doi_record`, the rest find nothing."""
    import enrich
    calls = []
    def rec(name, result):
        return lambda *a, **kw: calls.append(name) or result
    with _patched(enrich, crossref_by_doi=rec("doi", doi_record),
                  crossref_by_biblio=rec("search", None), scrapingdog_search=rec("scholar", [])):
        enrich.gather_enrichment(pdf_text, fields.get("title", ""), entry_type, fields,
                                 scrapingdog_api_key="k")
    return calls


def test_lookups_start_only_for_what_they_can_fill():
    # #52: a lookup is made for the missing fields some source can supply,
    # and each source only for those it can.
    import enrich
    # No source supplies Chapter, so a chapter missing only that starts nothing,
    # though % INCOMPLETE's own list is unchanged.
    fields = {"title": "A Chapter", "booktitle": "B", "pages": "1-20", "publisher": "P"}
    assert enrich.missing_fields("incollection", fields) == ([], ["chapter"])
    assert enrich.fillable_gaps("incollection", fields, "doi 10.1000/xyz") == []
    assert _lookups("incollection", fields, "doi 10.1000/xyz") == []

    # Booktitle comes only from a DOI match: without a DOI nothing can fill it...
    fields = {"title": "A Chapter", "chapter": "3", "pages": "1-20", "publisher": "P"}
    assert enrich.missing_fields("incollection", fields) == (["booktitle"], [])
    assert enrich.fillable_gaps("incollection", fields, "no identifier") == []
    assert _lookups("incollection", fields, "no identifier") == []
    # ...with one it is looked up by DOI alone: no title search when the DOI
    # misses, since a search may not supply it, and never the paid Scholar.
    assert enrich.fillable_gaps("incollection", fields, "doi 10.1000/xyz") == ["booktitle"]
    assert _lookups("incollection", fields, "doi 10.1000/xyz") == ["doi"]

    # An article missing volume and pages is looked up as before: DOI, then a
    # title search when the DOI misses, then Scholar.
    fields = {"title": "An Article", "journaltitle": "J", "number": "2"}
    assert _lookups("article", fields, "doi 10.1000/xyz") == ["doi", "search", "scholar"]

    # A book-like entry's missing Publisher starts a lookup of its own, and
    # stays out of % INCOMPLETE.
    fields = {"title": "A Chapter", "booktitle": "B", "chapter": "3", "pages": "1-20"}
    assert enrich.missing_fields("inbook", fields) == ([], ["publisher"])
    assert _lookups("inbook", fields, "no identifier") == ["search"]
    return True


def test_scholar_supplies_only_what_it_may():
    # Scholar's Cite fields carry a publisher for articles too; Chicago
    # articles take none, the same rule CrossRef's publisher already obeyed.
    found = _gather_with("article", {"title": "T", "journaltitle": "J"},
                         scholar={"volume": "3", "pages": "1-9", "publisher": "PLoS", "title": "X"})
    assert found == {"volume": "3", "pages": "1-9"}, found
    return True


def _gather_with(entry_type, fields, *, doi_record=None, search_record=None, scholar=None):
    """gather_enrichment() with every lookup stubbed. doi_record answers a DOI
    lookup, search_record a title search, scholar the Cite fields."""
    import enrich
    with _patched(enrich,
                  crossref_by_doi=lambda doi, mailto=None: doi_record,
                  crossref_by_biblio=lambda *a, **kw: search_record,
                  scrapingdog_search=lambda q, key: [{"id": "x"}] if scholar else [],
                  scrapingdog_cite_fields=lambda rid, key: scholar or {}):
        pdf_text = "doi 10.1000/xyz" if doi_record else "no identifier here"
        return enrich.gather_enrichment(pdf_text, fields.get("title", ""), entry_type, fields,
                                        scrapingdog_api_key="k")[0]


CHAPTER_RECORD = {"container-title": ["The Oxford Handbook of Neo-Riemannian Music Theories"],
                  "page": "579-581"}


def test_crossref_container_is_a_booktitle_for_a_chapter():
    # #42: container-title reached chapter entries as Journaltitle.
    found = _gather_with("incollection", {"title": "Glossary"}, doi_record=CHAPTER_RECORD)
    assert found.get("booktitle") == "The Oxford Handbook of Neo-Riemannian Music Theories", found
    assert "journaltitle" not in found, found

    split = dict(CHAPTER_RECORD, **{"container-title": ["Pianist, Scholar, Connoisseur: Essays"]})
    found = _gather_with("inbook", {"title": "T"}, doi_record=split)
    assert (found.get("booktitle"), found.get("booksubtitle")) == ("Pianist, Scholar, Connoisseur", "Essays"), found

    # An article's container is still its journal.
    found = _gather_with("article", {"title": "T"}, doi_record={"container-title": ["Music Analysis"]})
    assert found.get("journaltitle") == "Music Analysis" and "booktitle" not in found, found
    return True


def test_container_not_filled_without_an_identifying_record():
    # A title search can land on the wrong work, and Scholar has no DOI at
    # all: neither may supply a chapter's container, under either name. The
    # rest of what they found still comes through.
    found = _gather_with("incollection", {"title": "Glossary"}, search_record=CHAPTER_RECORD)
    assert "booktitle" not in found and "journaltitle" not in found, found
    assert found.get("pages") == "579-581", found

    found = _gather_with("inproceedings", {"title": "Paper"},
                         scholar={"journaltitle": "Proceedings of X", "pages": "1-9"})
    assert "journaltitle" not in found and "booktitle" not in found, found

    # An entry that already names its container keeps it, Maintitle included.
    for named in ({"booktitle": "Own Book"}, {"maintitle": "Standard Edition", "volume": "19"}):
        found = _gather_with("inbook", dict(named, title="T"), doi_record=CHAPTER_RECORD)
        assert "booktitle" not in found and "booksubtitle" not in found, (named, found)
    return True


def test_comment_lines_order_and_omission():
    # The rendering order is the contract the two assertions above depend on,
    # and it is worth asserting directly rather than only through the file.
    # needs_color must never appear: it is a colour, not a comment, and
    # writing it was what the old NEEDS_COLOR_FLAG marker had to undo.
    full = biblio_agent.ExtractionResult(
        entry="@Book{X,}", source_label="PDF (a.pdf)", needs_color=True,
        amber_reason="thin source", field_sources="CrossRef: date",
    )
    assert full.comment_lines() == [
        "% Source: PDF (a.pdf)",
        "% AMBER: thin source",
        "% Sources -- CrossRef: date",
    ], full.comment_lines()

    # Absent state contributes no line at all - not an empty one.
    assert biblio_agent.ExtractionResult(entry="@Book{X,}").comment_lines() == []
    assert biblio_agent.ExtractionResult(
        entry="@Book{X,}", needs_color=True).comment_lines() == []

    # A failed result reports itself as failed and carries the reason.
    failed = biblio_agent.ExtractionResult(error="Error: File not found: x.pdf")
    assert failed.failed is True
    assert biblio_agent.ExtractionResult(entry="@Book{X,}").failed is False
    return True


def test_extract_bibtex_to_save_entry_round_trip():
    """The join the old protocol had no coverage of at all.

    Every test above builds its own ExtractionResult, so all of them would
    keep passing if extract_bibtex() stopped populating one - which is the
    exact shape of the fault this refactor is for. This one runs the real
    extract_bibtex(), with only the network stubbed, and feeds what it
    returns straight to save_entry().

    Stubbed: the Anthropic call, and a `.fake` source extractor registered in
    EXTRACTORS so no PDF or webpage is needed. Not stubbed: prompt building,
    forbidden-field stripping, the amber fold-in, the result construction,
    and all of save_entry.
    """
    import extract_pages

    class _Msg:
        content = [type("B", (), {"type": "text",
                                  "text": "@Online{RoundTrip2026,\n  Title = {A Study of Musical Form},\n}"})()]

    class _Messages:
        def create(self, **kwargs):
            return _Msg()

    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        source = Path(td) / "thin.fake"
        source.write_text("placeholder", encoding="utf-8")

        agent = _agent(bib_path)
        # No enrichment: that path makes its own network calls, and the join
        # under test is extract_bibtex -> save_entry, not CrossRef.
        agent.config["enrich_missing_fields"] = False
        agent.client = type("C", (), {"messages": _Messages()})()

        biblio_agent.EXTRACTORS[".fake"] = lambda path, **kw: extract_pages.SourceContent(
            text="some body text", label="webpage", url="https://example.org/x",
            amber=True, amber_reason="short body",
        )
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                result = agent.extract_bibtex(source)
                ok = agent.save_entry(result, source)
        finally:
            del biblio_agent.EXTRACTORS[".fake"]

        saved = bib_path.read_text(encoding="utf-8")

    # extract_bibtex actually populated the state, rather than the test
    # asserting a shape it invented itself.
    assert result.failed is False, result
    assert result.source_label == "webpage (https://example.org/x)", result
    assert result.amber_reason == "short body", result
    assert result.needs_color is True, result
    # ...and every bit of it that should reach the file did.
    assert ok is True
    assert "% Source: webpage (https://example.org/x)" in saved, saved
    assert "% AMBER: short body" in saved, saved
    assert "NEEDS_COLOR_FLAG" not in saved, saved
    assert "@Online{RoundTrip2026," in saved, saved
    return True


# ── Responses that are not one bare entry (#32) ─────────────────────────────
#
# Two fixtures, and their provenance differs:
#
# GOLLIN_RESPONSE - the prose is quoted in issue #32 from Gollin2011a's output in
# the 2026-08-29 integration run. The issue elides the entry's fields; they are
# filled in here from the values the run record gives for that output (Title
# "Glossary", Date 2017, Pages 581). Reconstructed, not recorded.
#
# RIMSKY_SAVED - recorded: dev/eval/last-run/Rimsky1952.bib from the 2026-08-29
# baseline run, verbatim. It is what the OLD clean_bibtex() saved, so it has
# already lost the prose before "@Book" and the closing fence after the entry;
# what remains is the same fault a second time, independently of #32 - a
# line-initial "@Book (standalone monograph)" the first-'@' anchor took as the
# start of the entry.

GOLLIN_RESPONSE = """Looking at this source, the text is a glossary from a larger volume - this is a `@Suppbook` entry. The only fields I can populate are the generic section type and the page number (581 is visible, but the full page range of the glossary is unknown).

Given the constraints - glossary with no title of its own, no author, no parent book information visible - the most honest entry I can produce is a minimal `@Suppbook` with the fields that are actually available:

```bibtex
@suppbook{Glossary2017,
\tDate = {2017},
\tPages = {581},
\tTitle = {Glossary},
}
```"""

RIMSKY_SAVED = """@Book (standalone monograph), in Russian, but I cannot reliably extract Author, Title, Publisher, Location, or Date from the degraded OCR of what appears to be a table of contents page.

```bibtex
@book{Unknown,
\tLangid = {russian},
\tNote = {\\foreignlanguage{russian}{Книга по акустике и физике музыкальных инструментов; библиографические данные не установлены по имеющемуся тексту}},
\tdate-added = {2026-08-29 10:58:13 +0200},
\tdate-modified = {2026-08-29 10:58:13 +0200},
}"""

# No entry at all, but everything the old guards looked for: a line-initial
# '@', a backtick-quoted '@Type', and (having no braces) perfect brace balance.
PROSE_ONLY = """@Suppbook would be the right type here, but the source gives nothing to put in it.
No parent volume, author or date is visible, so I have not produced a `@Suppbook` entry."""


def _quiet_agent(tmp_dir, **config):
    """An agent writing both bib files into tmp_dir, with notifications off
    and verbose on, so _log warnings reach the captured stderr."""
    agent = _agent(Path(tmp_dir) / "staging.bib")
    agent.config["failed_bib_file"] = str(Path(tmp_dir) / "failed.bib")
    agent.config["interface"] = dict(agent.config.get("interface") or {}, notifications=False)
    agent.config["verbose"] = True
    agent.config.update(config)
    return agent


def _stub_client(agent, text):
    """Every messages.create() call returns `text`; the calls are recorded."""
    calls = []

    class _Msg:
        content = [type("B", (), {"type": "text", "text": text})()]

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _Msg()

    agent.client = type("C", (), {"messages": _Messages()})()
    return calls


def test_isolate_entry_bare_and_fenced_leave_nothing_over():
    # The common case must not trip the commentary warning: fences are
    # packaging, not commentary.
    import enrich
    bare = "@Article{X,\n  Title = {A {Nested} Title},\n}"
    for text in (bare, f"```bibtex\n{bare}\n```", f"```\n{bare}\n```\n", f"\n\n{bare}\n"):
        entry, extra = enrich.isolate_entry(text)
        assert entry == bare, (text, entry)
        assert extra == "", (text, extra)
    return True


def test_isolate_entry_skips_prose_that_quotes_an_entry_type():
    import enrich
    entry, extra = enrich.isolate_entry(GOLLIN_RESPONSE)
    assert entry.startswith("@suppbook{Glossary2017,"), entry
    assert entry.endswith("}") and "`" not in entry, entry
    assert "most honest entry" in extra, extra

    entry, extra = enrich.isolate_entry(RIMSKY_SAVED)
    assert entry.startswith("@book{Unknown,"), entry
    assert "standalone monograph" not in entry, entry
    assert "standalone monograph" in extra, extra
    return True


def test_isolate_entry_finds_nothing_in_prose_or_an_unclosed_entry():
    # And no fallback to the first '@' - that fallback IS the bug.
    import enrich
    assert enrich.isolate_entry(PROSE_ONLY)[0] is None
    assert enrich.isolate_entry("@Article{X,\n  Title = {Unclosed},\n")[0] is None
    return True


def test_escaped_braces_are_literal():
    # #43: an escaped closing brace truncated the entry, and the truncated
    # text still balanced. Constructed fixtures - the library holds none.
    import enrich
    entry = "@Article{X,\n  Title = {A \\} B},\n  Note = {C \\{ D},\n  Pages = {1-2},\n}"
    isolated, extra = enrich.isolate_entry(entry)
    assert isolated == entry and extra == "", isolated

    fields = enrich.parse_bibtex_fields(entry)
    assert fields["title"] == "A \\} B" and fields["note"] == "C \\{ D", fields
    assert fields["pages"] == "1-2", fields

    assert enrich.set_field(entry, "Title", "New").count("Title = {New},") == 1
    removed = enrich.remove_field(entry, "Title")
    assert "Title" not in removed and "Note = {C \\{ D}," in removed, removed

    assert enrich.brace_problem(entry) == ""
    # A doubled backslash escapes itself, so the brace after it counts.
    assert enrich.brace_problem("{a\\\\}") == ""
    assert enrich.brace_problem("{a\\}") == "unclosed braces (depth=1)"
    return True


def test_save_entry_saves_only_the_entry_from_a_commented_response():
    # Against the old code this saved the prose too: the text began with '@'
    # and the prose contributed no braces, so both guards passed.
    for response, opener, prose in (
        (GOLLIN_RESPONSE, "@suppbook{Glossary2017,", "most honest entry"),
        (RIMSKY_SAVED, "@book{Unknown,", "standalone monograph"),
    ):
        with tempfile.TemporaryDirectory() as td:
            agent = _quiet_agent(td)
            with redirect_stderr(io.StringIO()):
                ok = agent.save_entry(biblio_agent.ExtractionResult(entry=response), "x.webloc")
            saved = (Path(td) / "staging.bib").read_text(encoding="utf-8")
        assert ok is True
        assert opener in saved, saved
        assert prose not in saved, saved
        assert "```" not in saved, saved
    return True


def test_save_entry_rejects_a_response_with_no_entry():
    cases = (
        (PROSE_ONLY, "response is not a BibTeX entry"),
        ("@Article{X,\n  Title = {Unclosed},\n", "unclosed braces (depth=1)"),
    )
    for response, reason in cases:
        with tempfile.TemporaryDirectory() as td:
            agent = _quiet_agent(td)
            with redirect_stderr(io.StringIO()):
                ok = agent.save_entry(biblio_agent.ExtractionResult(entry=response), "x.webloc")
            staging = Path(td) / "staging.bib"
            failed = (Path(td) / "failed.bib").read_text(encoding="utf-8")
            assert ok is False
            assert not staging.exists() or staging.read_text(encoding="utf-8") == "", staging.read_text()
            assert f"% Error: {reason}" in failed, failed
            # The failed file keeps the whole response, so it can be read.
            assert response.strip() in failed, failed
    return True


def _extract_with(agent, td, response):
    """Run the real extract_bibtex() on a `.fake` source, the API stubbed."""
    import extract_pages
    source = Path(td) / "src.fake"
    source.write_text("placeholder", encoding="utf-8")
    calls = _stub_client(agent, response)
    biblio_agent.EXTRACTORS[".fake"] = lambda path, **kw: extract_pages.SourceContent(
        text="some body text", label="PDF", url=None)
    try:
        err = io.StringIO()
        with redirect_stderr(err):
            result = agent.extract_bibtex(source)
    finally:
        del biblio_agent.EXTRACTORS[".fake"]
    return result, err.getvalue(), calls


def test_extract_bibtex_isolates_the_entry_and_says_so():
    # Salvaged, but not silently - the issue's own terms.
    with tempfile.TemporaryDirectory() as td:
        agent = _quiet_agent(td, enrich_missing_fields=False)
        result, err, _ = _extract_with(agent, td, GOLLIN_RESPONSE)
    assert result.failed is False, result
    assert result.entry.startswith("@suppbook{Glossary2017,"), result.entry
    assert "most honest entry" not in result.entry, result.entry
    assert "characters outside the entry" in err, err

    with tempfile.TemporaryDirectory() as td:
        agent = _quiet_agent(td, enrich_missing_fields=False)
        _, err, _ = _extract_with(agent, td, "```bibtex\n@Book{Y,\n  Title = {Z},\n}\n```")
    assert "outside the entry" not in err, err
    return True


def test_extract_bibtex_does_not_enrich_prose():
    # Enrichment on a response with no entry would spend CrossRef and API
    # calls merging into prose. It must be skipped, and the prose left for
    # save_entry() to reject.
    with tempfile.TemporaryDirectory() as td:
        agent = _quiet_agent(td, enrich_missing_fields=True)

        def _must_not_run(*a, **kw):
            raise AssertionError("enrichment ran on a response with no entry")
        agent.enrich_entry = _must_not_run
        agent.verify_and_flag_recollection = _must_not_run
        result, err, _ = _extract_with(agent, td, PROSE_ONLY)
    assert result.failed is False, result
    assert result.entry == PROSE_ONLY, result.entry
    assert "no complete BibLaTeX entry" in err, err
    return True


def test_reconcile_keeps_the_entry_when_the_merge_reply_is_prose():
    # The merge paths gated only on a brace-balance check, which prose passes.
    # Against the old code this returned the prose as the reconciled entry.
    import extract_pages
    entry = "@Article{R,\n  Author = {Doe, J.},\n  Title = {T},\n}"
    candidates = [{"field": "author", "claimed": "Doe, J.",
                   "verified": "Doe, Jane", "source": "CrossRef"}]
    content = extract_pages.SourceContent(text="body", label="PDF", url=None)
    with tempfile.TemporaryDirectory() as td:
        agent = _quiet_agent(td)
        _stub_client(agent, "I left the `@Article` entry as it was; nothing needed merging.")
        with redirect_stderr(io.StringIO()):
            out = agent.reconcile_fields(entry, content, candidates)
    assert out == entry, out
    return True


def test_extract_bibtex_moves_nodate_from_date_to_year():
    # biber discards \bibstring{nodate} from Date as an invalid date, so the
    # entry prints no "n.d." at all. It must leave extract_bibtex() in Year -
    # and before the Url rule runs, which would read the Date as a real date.
    response = ("@Unpublished{Kane,\n  Author = {Kane, Brian},\n  Title = {T},\n"
                "  Date = {\\bibstring{nodate}},\n  Url = {https://example.org/k},\n"
                "  Urldate = {2026-09-19},\n}")
    with tempfile.TemporaryDirectory() as td:
        agent = _quiet_agent(td, enrich_missing_fields=False)
        result, err, _ = _extract_with(agent, td, response)
    assert "Year = {\\bibstring{nodate}}" in result.entry, result.entry
    assert "Date = " not in result.entry, result.entry
    assert "Url = {https://example.org/k}" in result.entry, result.entry
    assert "from Date to Year" in err, err

    # A real Date, and a Year already present, are left alone.
    import enrich
    for entry in ("@Book{A,\n  Date = {1952},\n}",
                  "@Book{B,\n  Date = {\\bibstring{nodate}},\n  Year = {1900},\n}"):
        assert enrich.move_nodate_to_year(entry) == (entry, False), entry
    return True


TESTS = [
    test_source_and_amber_comments_survive_needs_color_flag_is_discarded,
    test_entry_without_amber_marker_is_unaffected,
    test_webloc_reaches_bibdesk_and_is_colored_without_auto_file,
    test_pdf_still_auto_files,
    test_no_color_flag_means_no_color,
    test_field_sources_reaches_the_saved_text,
    test_chapter_without_its_container_is_reported_incomplete,
    test_container_named_otherwise_is_not_reported,
    test_lookups_start_only_for_what_they_can_fill,
    test_scholar_supplies_only_what_it_may,
    test_crossref_container_is_a_booktitle_for_a_chapter,
    test_container_not_filled_without_an_identifying_record,
    test_comment_lines_order_and_omission,
    test_extract_bibtex_to_save_entry_round_trip,
    test_isolate_entry_bare_and_fenced_leave_nothing_over,
    test_isolate_entry_skips_prose_that_quotes_an_entry_type,
    test_isolate_entry_finds_nothing_in_prose_or_an_unclosed_entry,
    test_escaped_braces_are_literal,
    test_save_entry_saves_only_the_entry_from_a_commented_response,
    test_save_entry_rejects_a_response_with_no_entry,
    test_extract_bibtex_isolates_the_entry_and_says_so,
    test_extract_bibtex_does_not_enrich_prose,
    test_reconcile_keeps_the_entry_when_the_merge_reply_is_prose,
    test_extract_bibtex_moves_nodate_from_date_to_year,
]


def main():
    failures = []
    for test in TESTS:
        try:
            assert test() is True
            print(f"  ✓ {test.__name__}")
        except Exception as e:
            failures.append((test.__name__, e))
            print(f"  ✗ {test.__name__}: {e}")

    print()
    if failures:
        print(f"{len(failures)}/{len(TESTS)} test(s) failed.")
        return 1
    print(f"All {len(TESTS)} save_entry marker self-tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
