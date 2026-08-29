#!/usr/bin/env python3
"""
Regression test for BiblioAgent.save_entry()'s marker extraction/reattachment
- specifically the "% AMBER: ..." comment web_source.py's content-plausibility
follow-up relies on to survive into the saved .bib text for a .webloc source
(never fileable, so it never reaches BibDesk's own color - see save_entry).

Found in passing while adding that marker: the pre-existing "% Source: ..."
regex placed its \\b word-boundary assertion right after the literal colon
(`Source:\\b`), which can never match (a non-word character can't start a
word boundary against another non-word character) - so source_comment was
always empty, and clean_bibtex()'s "strip everything before the first @"
silently discarded the "% Source: ..." comment from every saved entry. Fixed
alongside the new "% AMBER: ..." marker, which was written with the same
mistake and would otherwise have failed exactly the same way, invisibly.

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


def _agent(tmp_bib_path):
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    agent.config["main_bib_file"] = str(tmp_bib_path)
    agent.config["autofile_bibdesk"] = False
    return agent


def test_source_and_amber_comments_survive_needs_color_flag_is_discarded():
    entry_text = (
        "% Source: webpage (https://example.org/x)\n"
        "% NEEDS_COLOR_FLAG\n"
        "% AMBER: no Author/Doi/PublicationDate - only Urldate available to date it\n"
        "@Online{MarkerTest2026,\n"
        "  Title = {A Study of Musical Form},\n"
        "  Urldate = {2026-08-29},\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        agent = _agent(bib_path)
        err = io.StringIO()
        with redirect_stderr(err):
            ok = agent.save_entry(entry_text, "smoketest.webloc")
        saved = bib_path.read_text(encoding="utf-8")

    assert ok is True
    # NEEDS_COLOR_FLAG is internal bookkeeping only - must not reach the file.
    assert "NEEDS_COLOR_FLAG" not in saved, saved
    # Both comments that ARE meant to persist must actually be there, in the
    # order extract_bibtex() prepends them (Source outermost, AMBER next).
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
    entry_text = (
        "% Source: PDF (paper.pdf)\n"
        "@Article{PlainTest2026,\n"
        "  Title = {An Ordinary Article},\n"
        "  Author = {Doe, Jane},\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as td:
        bib_path = Path(td) / "staging.bib"
        agent = _agent(bib_path)
        err = io.StringIO()
        with redirect_stderr(err):
            ok = agent.save_entry(entry_text, "plaintest.pdf")
        saved = bib_path.read_text(encoding="utf-8")

    assert ok is True
    assert "% Source: PDF (paper.pdf)" in saved, saved
    assert "AMBER" not in saved, saved
    assert "@Article{PlainTest2026," in saved, saved
    return True


TESTS = [
    test_source_and_amber_comments_survive_needs_color_flag_is_discarded,
    test_entry_without_amber_marker_is_unaffected,
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
