#!/usr/bin/env python3
"""
Self-test for extract_pages.py's thin-yield check: the amber flag a PDF gets
when extraction completes having produced almost nothing.

The failure this guards against is not a crash. It is Motte2004 - 232 words
across every page of the source, where comparable sources in the evaluation
sample gave 1,000-3,100 - producing a correctly typed, entirely confident
entry with no indication that the text it was built from carried no
bibliographic data at all. OCR completing is not OCR succeeding, and a
process that exits 0 having read nothing is indistinguishable downstream from
one that read a title page. See issue #16.

No PDF is needed and no OCR runs: extract_all_text() is replaced with canned
page text, which is the input the check actually consumes. That keeps the
word counts exact rather than approximately whatever a fixture happened to
contain, and it means these tests exercise the arithmetic and the threshold
rather than pypdf.

    python3 dev/test_extract_pages.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import extract_pages  # noqa: E402


class _Canned:
    """Run extract_pdf()/extract_content() over canned page text, with no file
    and no OCR, restoring the module afterwards.

    min_words_threshold=0 keeps extract_content() off the OCR path entirely -
    the question here is what happens to a yield that has already settled,
    however it got there.
    """

    def __init__(self, page_texts):
        self.page_texts = page_texts

    def __enter__(self):
        self._all_text = extract_pages.extract_all_text
        self._metadata = extract_pages.extract_pdf_metadata
        extract_pages.extract_all_text = lambda path: (self.page_texts, len(self.page_texts))
        extract_pages.extract_pdf_metadata = lambda path: {}

        def run(**opts):
            return extract_pages.extract_pdf(
                Path("canned.pdf"), quiet=True, min_words_threshold=0, **opts)
        return run

    def __exit__(self, *exc):
        extract_pages.extract_all_text = self._all_text
        extract_pages.extract_pdf_metadata = self._metadata
        return False


def _words(n):
    """One page holding exactly n words."""
    return " ".join(f"w{i}" for i in range(n))


def test_thin_yield_is_flagged_amber():
    with _Canned([_words(120), _words(112)]) as run:   # 232, Motte2004's figure
        content = run(thin_yield_words=400)
    assert content.amber is True, content
    assert "232 words" in content.amber_reason, content.amber_reason
    assert "400" in content.amber_reason, content.amber_reason
    return True


def test_ordinary_yield_is_not_flagged():
    with _Canned([_words(900), _words(900)]) as run:
        content = run(thin_yield_words=400)
    assert content.amber is False, content
    assert content.amber_reason is None, content
    return True


def test_threshold_boundary_is_exclusive():
    # Exactly at the threshold is not thin; one word under is. Stated because
    # an off-by-one here is invisible - it changes which entries get a flag,
    # and nothing downstream would disagree with either answer.
    with _Canned([_words(400)]) as run:
        assert run(thin_yield_words=400).amber is False
    with _Canned([_words(399)]) as run:
        assert run(thin_yield_words=400).amber is True
    return True


def test_zero_threshold_disables_the_check():
    with _Canned([_words(3)]) as run:
        content = run(thin_yield_words=0)
    assert content.amber is False, content
    return True


def test_default_threshold_sits_above_ocr_threshold():
    # A floor at or below the OCR threshold could never fire: anything under
    # that has already been through OCR by the time extract_pdf() looks.
    assert extract_pages.DEFAULT_THIN_YIELD_WORDS > 100
    with _Canned([_words(232)]) as run:
        assert run().amber is True          # default applies with no argument
    return True


def test_extract_content_returns_the_yield_not_the_excerpt_length():
    # The count has to be the whole PDF's, not the returned excerpt's - the
    # excerpt is capped at min_first_words + last_words, so deriving the yield
    # from it would report the cap for every long source.
    # Two pages, so the excerpt is genuinely an excerpt: page 1 clears
    # min_first_words and is taken whole, the rest is not.
    with _Canned([_words(500), _words(4500)]):
        text, total = extract_pages.extract_content(
            Path("canned.pdf"), quiet=True, min_words_threshold=0)
    assert total == 5000, total
    # The excerpt the model actually sees begins with 500 of those 5,000 - so
    # the count is demonstrably the PDF's, not the excerpt's. (The returned
    # string is not simply shorter than the source: it also carries a
    # headers/footers section that repeats page text, which is exactly why
    # counting the returned string would be the wrong way to get this number.)
    assert "--- BEGINNING (500 words) ---" in text, text[:80]
    return True


def test_extract_content_reports_zero_words_on_error():
    real = extract_pages.extract_all_text
    extract_pages.extract_all_text = lambda path: ("Error: File not found: x.pdf", 0)
    try:
        text, total = extract_pages.extract_content(Path("missing.pdf"), quiet=True)
    finally:
        extract_pages.extract_all_text = real
    assert text.startswith("Error:"), text
    assert total == 0, total
    # And extract_pdf passes the error straight through rather than wrapping
    # an errored extraction in a SourceContent that looks merely thin.
    extract_pages.extract_all_text = lambda path: ("Error: File not found: x.pdf", 0)
    try:
        result = extract_pages.extract_pdf(Path("missing.pdf"), quiet=True)
    finally:
        extract_pages.extract_all_text = real
    assert isinstance(result, str) and result.startswith("Error:"), result
    return True


TESTS = [
    test_thin_yield_is_flagged_amber,
    test_ordinary_yield_is_not_flagged,
    test_threshold_boundary_is_exclusive,
    test_zero_threshold_disables_the_check,
    test_default_threshold_sits_above_ocr_threshold,
    test_extract_content_returns_the_yield_not_the_excerpt_length,
    test_extract_content_reports_zero_words_on_error,
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
    print(f"All {len(TESTS)} extract_pages self-tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
