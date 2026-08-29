#!/usr/bin/env python3
"""
Self-test for web_source.py's fallback/plausibility redesign (#11).

No network, no browser, no API call: requests.get(), browser_tab_dom() and
crossref_fallback() are all monkeypatched to canned values. The failure this
exists to cover - a live Cloudflare challenge, or a half-loaded tab - can't
be staged by hand (a logged-in browser passes every challenge; a private
window doesn't reproduce a half-loaded DOM either), so this is the only place
it's actually checked.

    python3 dev/test_web_source.py
"""

from __future__ import annotations

import io
import sys
from contextlib import contextmanager, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import web_source  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────

# Silverchair-shaped, with the DOI embedded in the path (see
# doi_candidates()'s own docstring) - needed so a stubbed CrossRef record for
# this DOI passes the URL-correspondence check the same way a real one would.
BOOKMARK_URL = "https://academic.oup.com/jaac/doi/10.1093/jaac/kpag034/8725072"


def _words(n, prefix="word"):
    return " ".join(f"{prefix}{i}" for i in range(n))


def _article_html(canonical=BOOKMARK_URL, doi="10.1093/jaac/kpag034"):
    """A genuine article page: Title, Author, a matching canonical link, and
    well over MIN_BODY_WORDS of body text."""
    return (
        "<html><head>"
        "<title>A Study of Musical Form</title>"
        '<meta name="citation_title" content="A Study of Musical Form">'
        '<meta name="citation_author" content="Doe, Jane">'
        f'<meta name="citation_doi" content="{doi}">'
        f'<link rel="canonical" href="{canonical}">'
        "</head><body><p>" + _words(150) + "</p></body></html>"
    )


def _challenge_html():
    """Built from the same markers is_bot_challenge() itself looks for, so
    this fixture can't drift out of step with the detector it stands in for."""
    marker = web_source._CHALLENGE_BODY_MARKERS[0]
    return f"<html><head><title>{marker}</title></head><body>{marker}</body></html>"


def _thin_html():
    """Identifying metadata is present (Title, Author) but the body is a
    handful of words - a tab still loading, not an interstitial."""
    return (
        "<html><head><title>A Study of Musical Form</title>"
        '<meta name="citation_author" content="Doe, Jane"></head>'
        "<body><p>Loading…</p></body></html>"
    )


def _consent_wall_html():
    """Title only, nothing identifying a work - what a login/consent wall
    typically carries, and what a plain 200 response gives no other way to
    catch."""
    return (
        "<html><head><title>Please accept cookies to continue</title></head>"
        "<body><p>" + _words(150, prefix="cookie") + "</p></body></html>"
    )


def _wrong_doi_html():
    """Otherwise-plausible content with no canonical/og:url at all, whose
    embedded Doi names a different work - the one signal the DOI gate exists
    to still catch, at a URL that does carry a DOI of its own."""
    return (
        "<html><head><title>A Study of Musical Form</title>"
        '<meta name="citation_author" content="Doe, Jane">'
        '<meta name="citation_doi" content="10.9999/not-the-same-doi"></head>'
        "<body><p>" + _words(150) + "</p></body></html>"
    )


def _wrong_canonical_html():
    """Otherwise-plausible content whose canonical link names a different
    page entirely - no og:url or Doi present to rescue it."""
    return (
        "<html><head><title>A Study of Musical Form</title>"
        '<meta name="citation_author" content="Doe, Jane">'
        '<link rel="canonical" href="https://academic.oup.com/login"></head>'
        "<body><p>" + _words(150) + "</p></body></html>"
    )


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None, url=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}
        self.url = url if url is not None else BOOKMARK_URL

    @property
    def ok(self):
        return self.status_code < 400


CLOUDFLARE_403 = lambda: FakeResponse(  # noqa: E731
    status_code=403, headers={"cf-mitigated": "challenge"}, text="cf challenge body"
)
PLAIN_404 = lambda: FakeResponse(status_code=404, text="not found")  # noqa: E731


@contextmanager
def _patched(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


def _fake_webloc(tmp_path_url=BOOKMARK_URL):
    """resolve_webloc() is not under test here - patched to hand back a URL
    directly so no actual .webloc file has to exist on disk."""
    return _patched(web_source, "resolve_webloc", lambda path: tmp_path_url)


def _no_browser_tab():
    return _patched(web_source, "browser_tab_dom", lambda url: (None, None))


def _browser_returns(html, app="Safari"):
    return _patched(web_source, "browser_tab_dom", lambda url: (html, app))


def _no_crossref():
    return _patched(web_source, "crossref_fallback", lambda url, crossref_email=None: None)


def _crossref_raises():
    def _boom(url, crossref_email=None):
        raise AssertionError("crossref_fallback() should not have been called")
    return _patched(web_source, "crossref_fallback", _boom)


def _crossref_returns_article():
    message = {
        "title": ["A Study of Musical Form"],
        "author": [{"family": "Doe", "given": "Jane"}],
        "DOI": "10.1093/jaac/kpag034",
        "published-print": {"date-parts": [[2020, 1]]},
    }
    content = web_source._crossref_content(message, BOOKMARK_URL)
    return _patched(web_source, "crossref_fallback", lambda url, crossref_email=None: content)


def _fetch_returns(response):
    return _patched(web_source.requests, "get",
                     lambda url, headers=None, timeout=None: response)


def _run(url=BOOKMARK_URL):
    """extract_webloc() with a dummy path - resolve_webloc() is patched by
    every test via _fake_webloc(), so the path itself is never opened, only
    checked for existence."""
    with _patched(Path, "exists", lambda self: True):
        err = io.StringIO()
        with redirect_stderr(err):
            result = web_source.extract_webloc("dummy.webloc")
        return result, err.getvalue()


# ── tests ────────────────────────────────────────────────────────────────

def test_browser_challenge_markup_falls_through():
    # #11 item 4, case 1: browser_tab_dom() hands back an actual Cloudflare
    # interstitial - no entry, and the rejection is logged against the
    # browser path.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_challenge_html()), _no_crossref():
        result, log = _run()
    assert isinstance(result, str) and result.startswith("Error:"), result
    assert "browser tab" in log and "rejected" in log, log
    return True


def test_browser_thin_dom_falls_through():
    # #11 item 4, case 2: identifying metadata is present, but the body is
    # a handful of words - a tab still loading, not an interstitial. Length
    # guard fires specifically (metadata check alone would pass this one).
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_thin_html()), _no_crossref():
        result, log = _run()
    assert isinstance(result, str) and result.startswith("Error:"), result
    assert "browser tab" in log and "rejected" in log, log
    assert "words" in log, log
    return True


def test_browser_genuine_article_succeeds():
    # #11 item 4, case 3: without this one, the first two pass trivially
    # even if the capture path is broken outright.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_article_html(), app="Safari"):
        result, log = _run()
    assert not isinstance(result, str), result
    assert result.metadata.get("Title") == "A Study of Musical Form"
    assert result.metadata.get("Author") == "Doe, Jane"
    assert "Safari" in result.label
    assert "Source: browser tab (Safari)" in log, log
    return True


def test_200_interstitial_rejected():
    # #11 item 4, case 4: the hole the whole redesign is for - a 200
    # response is never routed through is_bot_challenge at all.
    with _fake_webloc(), _fetch_returns(FakeResponse(text=_consent_wall_html())), \
         _no_browser_tab():
        result, log = _run()
    assert isinstance(result, str) and result.startswith("Error:"), result
    assert "fetch" in log and "rejected" in log, log
    return True


def test_browser_wrong_canonical_falls_through():
    # #11 item 4, case 5.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_wrong_canonical_html()), _no_crossref():
        result, log = _run()
    assert isinstance(result, str) and result.startswith("Error:"), result
    assert "browser tab" in log and "rejected" in log, log
    return True


def test_browser_wrong_doi_falls_through():
    # The DOI gate added after the initial pass exists precisely so this
    # case is still caught: BOOKMARK_URL carries a DOI of its own
    # (doi_candidates() finds one), so a page declaring a *different* DOI -
    # with no canonical/og:url to override it - is a genuine contradiction,
    # not merely unverifiable. A captured tab that navigated to a different
    # article entirely would look like this.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_wrong_doi_html()), _no_crossref():
        result, log = _run()
    assert isinstance(result, str) and result.startswith("Error:"), result
    assert "browser tab" in log and "rejected" in log, log
    return True


def test_no_tab_falls_back_to_crossref():
    # #11 item 4, case 6 (the DOI-fallback branch): unchanged from #8/#10.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _no_browser_tab(), _crossref_returns_article():
        result, log = _run()
    assert not isinstance(result, str), result
    assert result.label == "CrossRef record"
    assert "Source: CrossRef for" in log, log
    return True


def test_no_tab_no_doi_clean_failure_unchanged_text():
    # #11 item 4, case 6 (the plain-failure branch), challenge classified:
    # no tab, no CrossRef hit - the pre-#11 error text, unchanged.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _no_browser_tab(), _no_crossref():
        result, log = _run()
    assert result == (f"Error: {BOOKMARK_URL} is behind a bot challenge (HTTP 403) "
                       f"and its URL carries no DOI to fall back on")
    assert "Source: failure for" in log, log
    return True


def test_non_challenge_failure_skips_crossref_unchanged_text():
    # A plain 404 never satisfies is_bot_challenge, so crossref_fallback()
    # must never even be called for it - the original gate, preserved.
    with _fake_webloc(), _fetch_returns(PLAIN_404()), \
         _no_browser_tab(), _crossref_raises():
        result, log = _run()
    assert result == f"Error: Could not fetch {BOOKMARK_URL}: HTTP 404"
    assert "Source: failure for" in log, log
    return True


def test_browser_tried_on_non_challenge_status():
    # #11 item 1: browser_tab_dom() must be tried on ANY non-2xx, not only
    # a classified challenge - a plain 404 with a matching tab open still
    # succeeds via the browser, which the pre-#11 gate would never reach.
    with _fake_webloc(), _fetch_returns(PLAIN_404()), \
         _browser_returns(_article_html(), app="Chrome"), _crossref_raises():
        result, log = _run()
    assert not isinstance(result, str), result
    assert "Chrome" in result.label
    assert "Source: browser tab (Chrome) for" in log, log
    return True


def test_fetch_canonical_matches_redirected_url_not_bookmark():
    # A doi.org bookmark resolves to the publisher's own URL; the page's
    # canonical link then names that resolved URL, not the redirector. The
    # correspondence check must accept a signal matching EITHER candidate.
    doi_url = "https://doi.org/10.1093/jaac/kpag034"
    resolved_url = BOOKMARK_URL
    response = FakeResponse(text=_article_html(canonical=resolved_url), url=resolved_url)
    with _fake_webloc(doi_url), _fetch_returns(response):
        result, log = _run(url=doi_url)
    assert not isinstance(result, str), result
    assert "Source: fetch" in log, log
    return True


def test_doi_meta_ignored_when_url_has_no_doi():
    # Most publisher URLs (Oxford Academic's own /article/80/1/1/1234567
    # among them) carry no DOI in the path at all, even though the page's
    # own citation_doi meta tag almost always exists. If that URL is the
    # only candidate and the page has no canonical/og:url, the embedded DOI
    # must not become the sole signal and reject an ordinary article for a
    # "mismatch" that was never checkable in the first place.
    url = "https://academic.oup.com/jaac/article/80/1/1/1234567"
    html = (
        "<title>A Study of Musical Form</title>"
        '<meta name="citation_author" content="Doe, Jane">'
        '<meta name="citation_doi" content="10.1093/jaac/kpag034">'
    )
    html = f"<html><head>{html}</head><body><p>{_words(150)}</p></body></html>"
    response = FakeResponse(text=html, url=url)
    with _fake_webloc(url), _fetch_returns(response), _crossref_raises():
        with _patched(web_source, "browser_tab_dom",
                      lambda u: (_ for _ in ()).throw(
                          AssertionError("browser_tab_dom() should not have been called"))):
            result, log = _run(url=url)
    assert not isinstance(result, str), result
    assert "Source: fetch for" in log, log
    return True


def test_ordinary_fetch_success_does_not_touch_fallbacks():
    # Sanity/regression: the common case still returns straight from the
    # fetch, without ever invoking either fallback.
    response = FakeResponse(text=_article_html(), url=BOOKMARK_URL)
    with _fake_webloc(), _fetch_returns(response), _crossref_raises():
        with _patched(web_source, "browser_tab_dom",
                      lambda url: (_ for _ in ()).throw(
                          AssertionError("browser_tab_dom() should not have been called"))):
            result, log = _run()
    assert not isinstance(result, str), result
    assert result.label == "webpage"
    assert "Source: fetch for" in log, log
    return True


TESTS = [
    test_browser_challenge_markup_falls_through,
    test_browser_thin_dom_falls_through,
    test_browser_genuine_article_succeeds,
    test_200_interstitial_rejected,
    test_browser_wrong_canonical_falls_through,
    test_browser_wrong_doi_falls_through,
    test_no_tab_falls_back_to_crossref,
    test_no_tab_no_doi_clean_failure_unchanged_text,
    test_non_challenge_failure_skips_crossref_unchanged_text,
    test_browser_tried_on_non_challenge_status,
    test_fetch_canonical_matches_redirected_url_not_bookmark,
    test_doi_meta_ignored_when_url_has_no_doi,
    test_ordinary_fetch_success_does_not_touch_fallbacks,
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
    print(f"All {len(TESTS)} web_source self-tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
