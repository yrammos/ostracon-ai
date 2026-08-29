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

No API call, no network: uses config.yaml as-is (BiblioAgent's __init__
constructs an Anthropic client but never calls it here), redirected to a
temp main_bib_file with autofile_bibdesk off, so nothing touches BibDesk.

    python3 dev/test_biblio_agent_markers.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stderr
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


TESTS = [
    test_source_and_amber_comments_survive_needs_color_flag_is_discarded,
    test_entry_without_amber_marker_is_unaffected,
    test_webloc_reaches_bibdesk_and_is_colored_without_auto_file,
    test_pdf_still_auto_files,
    test_no_color_flag_means_no_color,
    test_field_sources_reaches_the_saved_text,
    test_comment_lines_order_and_omission,
    test_extract_bibtex_to_save_entry_round_trip,
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
