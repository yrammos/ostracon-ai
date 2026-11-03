#!/usr/bin/env python3
"""Self-tests for src/page_reader.py.

Network and Chrome are stubbed. The HTML fixtures are constructed, not recorded:
they follow the citation_* convention and the status codes observed in the
2026-11-03 fetch probe (403 from publisher platforms, 200 bot-check pages).
The real fetch path is exercised by the probe, not here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import page_reader  # noqa: E402

RECORD = ("<html><head><meta name='citation_title' content='The Musical Sublime'>"
          "<meta name='citation_doi' content='10.1234/abc.5'>"
          "<script type='application/ld+json'>{}</script></head><body><nav>Menu</nav>"
          "<p>" + "Intro text. " * 40 + "Contents: 1. Monelle, The Musical Sublime, 23-45. "
          + "More text. " * 800 + "</p></body></html>")
CHALLENGE = "<html><body><h1>Just a moment...</h1>Checking your browser.</body></html>"


class Resp:
    def __init__(self, status, text="", ctype="text/html"):
        self.status_code, self.text = status, text
        self.content = text.encode()
        self.headers = {"content-type": ctype}


def _stub(http=None, chrome=None, crossref=None):
    calls = {"http": 0, "chrome": 0, "crossref": 0}

    def get(url, headers=None, timeout=None):
        calls["http"] += 1
        return http

    def run_chrome(url, timeout):
        calls["chrome"] += 1
        return chrome or {"status": "error", "note": "stub"}

    def by_doi(doi, timeout=None):
        calls["crossref"] += 1
        return crossref

    page_reader._cache.clear()
    page_reader.requests.get = get
    page_reader._chrome = run_chrome
    page_reader.enrich.crossref_by_doi = by_doi
    return calls


def test_plain_fetch_reads_text_metadata_and_crossref():
    calls = _stub(http=Resp(200, RECORD),
                  crossref={"type": "proceedings-article", "title": ["The Musical Sublime"],
                            "container-title": ["Before and After Music"],
                            "editor": [{"family": "Navickaitė-Martinelli", "given": "Lina"}],
                            "issued": {"date-parts": [[2010]]}})
    out = page_reader.read_page("https://example.org/book")
    assert out["status"] == "ok" and out["via"] == "http" and calls["chrome"] == 0, out
    assert out["metadata"].get("Title") == "The Musical Sublime" or any(
        "Musical Sublime" in str(v) for v in out["metadata"].values()), out["metadata"]
    assert "Menu" in out["text"], "whole visible text kept, menus included"
    cr = out["crossref"]
    assert cr["type"] == "proceedings-article" and cr["issued"] == "2010", cr
    assert cr["editor"] == "Navickaitė-Martinelli, Lina", cr
    return True


def test_paging_and_find():
    _stub(http=Resp(200, RECORD))
    first = page_reader.read_page("https://example.org/book", window=1000)
    assert first["offset"] == 0 and first["next_offset"] == 1000 and first["total"] > 5000
    hit = page_reader.read_page("https://example.org/book", find="contents:", window=1000)
    assert "Contents: 1. Monelle" in hit["text"], hit["text"][:300]
    miss = page_reader.read_page("https://example.org/book", find="no such phrase")
    assert "not found" in miss["note"] and miss["offset"] == 0
    return True


def test_blocked_fetch_falls_back_to_chrome():
    calls = _stub(http=Resp(403), chrome={"status": "ok", "metadata": {}, "text": "Rendered. " * 50})
    out = page_reader.read_page("https://publisher.example/x")
    assert out["status"] == "ok" and out["via"] == "chrome" and out["http_status"] == 403, out
    assert calls["chrome"] == 1
    return True


def test_challenge_page_counts_as_blocked():
    calls = _stub(http=Resp(200, CHALLENGE))
    out = page_reader.read_page("https://publisher.example/y")
    assert out["status"] == "blocked" and calls["chrome"] == 1, out
    assert out["note"].startswith("Chrome: error"), out["note"]
    return True


def test_doi_from_url_reaches_crossref_even_when_blocked():
    calls = _stub(http=Resp(403), crossref={"type": "book-chapter", "title": ["T"]})
    out = page_reader.read_page("https://doi.org/10.1080/01411896.2012.680878")
    assert out["status"] == "blocked" and calls["crossref"] == 1, out
    assert out["crossref"]["doi"] == "10.1080/01411896.2012.680878", out["crossref"]
    assert out["crossref"]["type"] == "book-chapter"
    return True


def test_doi_in_body_text_is_ignored():
    # A page cites other works; only the page's own URL and metadata name its DOI.
    html = "<html><body><p>" + "See 10.5555/other.1 for more. " * 30 + "</p></body></html>"
    calls = _stub(http=Resp(200, html))
    out = page_reader.read_page("https://example.org/no-doi")
    assert calls["crossref"] == 0 and out["crossref"] is None, out
    return True


def test_not_found_is_reported_as_such():
    calls = _stub(http=Resp(404))
    out = page_reader.read_page("https://example.org/gone")
    assert out["status"] == "not-found" and calls["chrome"] == 0, out
    return True


TESTS = [
    test_plain_fetch_reads_text_metadata_and_crossref,
    test_paging_and_find,
    test_blocked_fetch_falls_back_to_chrome,
    test_challenge_page_counts_as_blocked,
    test_doi_from_url_reaches_crossref_even_when_blocked,
    test_doi_in_body_text_is_ignored,
    test_not_found_is_reported_as_such,
]


def main():
    originals = (page_reader.requests.get, page_reader._chrome, page_reader.enrich.crossref_by_doi)
    failures = 0
    try:
        for test in TESTS:
            try:
                assert test() is True
                print(f"  ✓ {test.__name__}")
            except Exception as e:
                failures += 1
                print(f"  ✗ {test.__name__}: {e!r}")
    finally:
        page_reader.requests.get, page_reader._chrome, page_reader.enrich.crossref_by_doi = originals
    print()
    print(f"{failures}/{len(TESTS)} test(s) failed." if failures
          else f"All {len(TESTS)} page_reader self-tests passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
