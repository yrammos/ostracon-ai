#!/usr/bin/env python3
"""
Self-test for web_source.py's fallback/plausibility redesign (#11), and its
amber-vs-fail follow-up: only a URL-correspondence mismatch (the wrong
document) is a hard failure; a genuine source that's merely thin or sparse
(no Author/Doi/PublicationDate beyond Urldate, or a short body) still
produces a SourceContent, marked amber for a human glance rather than
trusted blind or discarded.

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
from urllib.parse import urlsplit

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


def _wrong_canonical_but_complete_html():
    """Complete identifying metadata (Author, PublicationDate) and a long
    body - deliberately no Doi, since a Doi embedded in BOOKMARK_URL's own
    path would itself be a correspondence signal and could rescue a wrong
    canonical, defeating the point. Complete by every OTHER measure except
    the one that matters: its canonical link names a different page. The
    hard-failure case must fire regardless of how complete the rest looks."""
    return (
        "<html><head><title>A Study of Musical Form</title>"
        '<meta name="citation_author" content="Doe, Jane">'
        '<meta name="citation_publication_date" content="2020/01/01">'
        '<link rel="canonical" href="https://academic.oup.com/login"></head>'
        "<body><p>" + _words(150) + "</p></body></html>"
    )


# The reported Silverchair case, recorded from the real failure: the
# publisher serves the work at /article-abstract/ (what the bookmark holds,
# with a ?redirectedFrom parameter) and declares /article/ as canonical.
# Everything identifying is shared - host, journal, volume, issue, page,
# article id 218886 and title slug; only the path *form* differs.
UCPRESS_CANONICAL = ("https://online.ucpress.edu/ncm/article/50/1/54/218886/"
                     "Fatigued-Voices-and-Vocal-Health-in-fine-secolo")
# Same host and journal, different work: different article id AND slug.
UCPRESS_OTHER_ARTICLE = ("https://online.ucpress.edu/ncm/article/50/1/70/218999/"
                         "A-Completely-Different-Article-Title")


# Three real articles from ONE issue of ONE journal - 19th-Century Music
# 50/1 - recorded verbatim from the publisher. The adversarial set: nothing
# discriminates them except the article id and the title slug.
#
#   - the ids are CONSECUTIVE and six digits (218886/218887/218888), so they
#     differ by a single character - the sharpest available test of the id
#     rule, and the case a substring or prefix comparison would fail;
#   - volume, issue and page (/50/1/54/, /50/1/4/, /50/1/24/) are shared or
#     near-shared and all fall below the five-digit floor, which is exactly
#     what stops them counting as identifiers;
#   - two of the three slugs both contain `Chopin-s`, so a matcher comparing
#     substrings rather than whole segments would marry them.
#
# All three carry the same ?redirectedFrom=fulltext and the same
# /article-abstract/ path form, so the query string and the path form are
# constant across the set and cannot be doing any of the discriminating.
SAME_ISSUE_ARTICLES = (
    "https://online.ucpress.edu/ncm/article-abstract/50/1/54/218886/"
    "Fatigued-Voices-and-Vocal-Health-in-fine-secolo?redirectedFrom=fulltext",
    "https://online.ucpress.edu/ncm/article-abstract/50/1/4/218887/"
    "Rehearing-Recursive-Cycles-in-Chopin-s-Preludes?redirectedFrom=fulltext",
    "https://online.ucpress.edu/ncm/article-abstract/50/1/24/218888/"
    "Object-Lesson-The-Personified-Voice-of-Chopin-s?redirectedFrom=fulltext",
)


def _canonical_form(url):
    """The /article/ address the publisher declares canonical for a
    /article-abstract/ one - the transformation this whole loosening exists
    to tolerate."""
    return url.replace("/article-abstract/", "/article/").split("?")[0]


def _ucpress_html(canonical):
    """A real-shaped Silverchair article page, parameterised by the canonical
    link so the same fixture serves the positive and negative cases."""
    return (
        "<html><head>"
        "<title>Fatigued Voices and Vocal Health in fine secolo Italy</title>"
        '<meta name="citation_title" content="Fatigued Voices and Vocal Health '
        'in fine secolo Italy">'
        '<meta name="citation_author" content="Rothstein, Edward">'
        '<meta name="citation_publication_date" content="2026/07/01">'
        f'<link rel="canonical" href="{canonical}">'
        "</head><body><p>" + _words(200, prefix="prose") + "</p></body></html>"
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


def _no_browser_tab(summary="Safari: no matching tab (3 tab(s) examined)"):
    return _patched(web_source, "browser_tab_dom",
                     lambda url, log=None: (None, None, summary))


def _browser_returns(html, app="Safari"):
    return _patched(web_source, "browser_tab_dom",
                     lambda url, log=None: (html, app, f"{app}: match"))


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


# ── browser_tab_dom, against Safari's REAL tab listing ───────────────────
#
# These are the only tests that exercise browser_tab_dom itself rather than
# stubbing it. osascript is never invoked and no browser is touched: a fake
# subprocess.run answers each AppleScript with recorded output.
#
# SAFARI_TAB_LISTING is a verbatim capture from a running Safari (26 tabs
# across 2 windows), with unrelated URLs replaced by stand-ins through a
# stable mapping, so everything structural survives:
#
#   - TWO windows, 14 tabs and 12 tabs, indices restarting per window;
#   - 11 URLs open in BOTH windows, so a matcher that assumed tab URLs are
#     unique, or that only window 1 exists, is caught;
#   - the target at window 1, tab 12 - NOT tab 1, and not in the last window;
#   - a non-http `favorites://` entry (Safari's Start Page) as the final tab.
#
# The previous version of these tests invented the listing format from the
# AppleScript's apparent intent rather than recording what it actually
# emits. It therefore encoded tab-separated fields that the real script
# never produced, and passed while the enumeration was completely broken -
# `tab` inside a `tell application "Safari"` block binds to Safari's `tab`
# class, not the tab character, so every line came out as
# "1tab1tabhttps://..." and every one was silently discarded. Recording the
# real output is the whole point; do not hand-write this fixture.

SAFARI_TAB_LISTING = (
    "1\t1\thttps://example1.invalid/page1\n"
    "1\t2\thttps://example2.invalid/page2\n"
    "1\t3\thttps://example3.invalid/page3\n"
    "1\t4\thttps://example4.invalid/page4\n"
    "1\t5\thttps://example5.invalid/page5\n"
    "1\t6\thttps://example6.invalid/page6\n"
    "1\t7\thttps://example7.invalid/page7\n"
    "1\t8\thttps://example8.invalid/page8\n"
    "1\t9\thttps://example9.invalid/page9\n"
    "1\t10\thttps://example10.invalid/page10\n"
    "1\t11\thttps://example11.invalid/page11\n"
    "1\t12\thttps://online.ucpress.edu/ncm/article-abstract/50/1/54/218886/"
    "Fatigued-Voices-and-Vocal-Health-in-fine-secolo?redirectedFrom=fulltext\n"
    "1\t13\thttps://example12.invalid/page12\n"
    "1\t14\thttps://example13.invalid/page13\n"
    "2\t1\thttps://example1.invalid/page1\n"
    "2\t2\thttps://example2.invalid/page2\n"
    "2\t3\thttps://example3.invalid/page3\n"
    "2\t4\thttps://example4.invalid/page4\n"
    "2\t5\thttps://example5.invalid/page5\n"
    "2\t6\thttps://example6.invalid/page6\n"
    "2\t7\thttps://example7.invalid/page7\n"
    "2\t8\thttps://example8.invalid/page8\n"
    "2\t9\thttps://example9.invalid/page9\n"
    "2\t10\thttps://example10.invalid/page10\n"
    "2\t11\thttps://example11.invalid/page11\n"
    "2\t12\tfavorites://\n"
)

UCPRESS_URL = ("https://online.ucpress.edu/ncm/article-abstract/50/1/54/218886/"
               "Fatigued-Voices-and-Vocal-Health-in-fine-secolo?redirectedFrom=fulltext")

# What the pre-fix script actually emitted, recorded from the same Safari:
# `tab` bound to Safari's tab class and stringified to the literal word.
BROKEN_TAB_LISTING = (
    "1tab1tabhttps://example1.invalid/page1\n"
    f"1tab12tab{UCPRESS_URL}\n"
)


class _Osa:
    """Canned osascript results, keyed by what the script is asking."""

    def __init__(self, running=True, listing=SAFARI_TAB_LISTING,
                 refuse_events=False, refuse_js=False, capture=""):
        self.running = running
        self.listing = listing
        self.refuse_events = refuse_events
        self.refuse_js = refuse_js
        self.capture = capture
        self.scripts = []

    class _R:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    # Wording taken from a real macOS -1743; only the numeric code is matched on.
    _REFUSAL = ("execution error: Not authorized to send Apple events to "
                "Safari. (-1743)")

    def __call__(self, argv, capture_output=None, text=None, timeout=None):
        script = argv[-1]
        self.scripts.append(script)
        if self.refuse_events:
            return self._R(1, "", self._REFUSAL)
        if "is running" in script:
            return self._R(0, "true" if self.running else "false")
        if "repeat with w in windows" in script:
            # Chrome is not running in these fixtures; only Safari answers.
            if 'application "Safari"' not in script:
                return self._R(0, "")
            return self._R(0, self.listing)
        if self.refuse_js:
            return self._R(1, "", self._REFUSAL)
        return self._R(0, self.capture)


def _osa(**kw):
    return _patched(web_source.subprocess, "run", _Osa(**kw))


def _probe(url=UCPRESS_URL):
    lines = []
    html, app, summary = web_source.browser_tab_dom(url, log=lines.append)
    return html, app, summary, "\n".join(lines)


def test_applescript_does_not_use_tab_inside_the_tell_block():
    # The root cause, asserted directly on the generated script rather than
    # only through its output: `tab` and `linefeed` must be bound BEFORE
    # `tell application`, where no app terminology can shadow them.
    scripts = []

    def spy(script, timeout=15):
        scripts.append(script)
        return web_source.OSA_OK, "true" if "is running" in script else ""

    with _patched(web_source, "_osascript", spy):
        web_source._list_tabs("Safari")

    listing = [s for s in scripts if "repeat with w in windows" in s][0]
    before, _, after = listing.partition("tell application")
    assert "set d to tab" in before, listing
    assert " & tab & " not in after, "bare `tab` is shadowed by Safari's tab class"
    return True


def test_browser_probe_finds_the_target_in_the_real_listing():
    # The regression test for the terminology collision: against Safari's
    # actual output the target must be found - at window 1 tab 12, past ten
    # non-matching tabs, with a second window holding 11 of the same URLs.
    with _osa(capture="<html>ok</html>"):
        html, app, summary, log = _probe()
    assert html == "<html>ok</html>", html
    assert app == "Safari"
    assert "matched window 1 tab 12" in log, log
    return True


def test_broken_listing_is_reported_as_a_parse_error_not_as_no_tabs():
    # The pre-fix output must never again present itself as an empty browser.
    # This is the exact string that produced "no tabs open" and "0 tab(s)
    # examined" against a Safari with 26 tabs open.
    with _osa(listing=BROKEN_TAB_LISTING):
        html, app, summary, log = _probe()
    assert html is None
    assert "could not parse" in summary, summary
    assert "no tabs open" not in log, log
    assert "no matching tab" not in summary, summary
    return True


def test_non_http_scheme_does_not_break_the_listing():
    # favorites:// is the last tab of the last window in the real capture.
    # A parser that assumed every entry is an http URL would drop it, or
    # worse, abandon the rest - so assert the full count survives it.
    with _osa(capture="<html>ok</html>"):
        tabs, status, detail = web_source._list_tabs("Safari")
    assert status == web_source.OSA_OK, (status, detail)
    assert len(tabs) == 26, len(tabs)
    assert (2, 12, "favorites://") in tabs, tabs[-3:]
    return True


def test_duplicate_windows_do_not_confuse_the_index():
    # 11 URLs are open in both windows. The reported (window, tab) pair must
    # be the one actually matched, not the last seen or a window-1 default.
    dup = "https://example11.invalid/page11"          # window 1 tab 11, window 2 tab 11
    with _osa(capture="<html>ok</html>"):
        lines = []
        html, app, summary = web_source.browser_tab_dom(dup, log=lines.append)
    assert html == "<html>ok</html>"
    # First match in enumeration order - window 1, not window 2.
    assert "matched window 1 tab 11" in "\n".join(lines), lines
    return True


def _listing(*rows):
    """A tab listing in Safari's real format from (window, tab, url) rows."""
    return "".join(f"{w}\t{t}\t{u}\n" for w, t, u in rows)


def test_tab_selection_falls_back_to_identity_when_no_tab_matches_exactly():
    # The path-form mismatch that no query-parameter rule reaches: the
    # bookmark holds /article-abstract/, the open tab holds /article/.
    listing = _listing(
        (1, 1, "https://example1.invalid/page1"),
        (1, 2, UCPRESS_CANONICAL),
    )
    with _osa(listing=listing, capture="<html>ok</html>"):
        html, app, summary, log = _probe(UCPRESS_URL)
    assert html == "<html>ok</html>", html
    assert "identity" in summary, summary
    assert "matched window 1 tab 2 (identity match:" in log, log
    # The tab's own URL is named, since it is not the address asked for.
    assert UCPRESS_CANONICAL in log, log
    return True


def test_tab_selection_prefers_an_exact_match_over_an_identity_match():
    # Same article open in two forms. The one actually asked for wins, even
    # though the identity match comes first in enumeration order.
    listing = _listing(
        (1, 1, UCPRESS_CANONICAL),   # identity match, earlier
        (1, 2, UCPRESS_URL),         # exact match, later
    )
    with _osa(listing=listing, capture="<html>ok</html>"):
        html, app, summary, log = _probe(UCPRESS_URL)
    assert html == "<html>ok</html>"
    assert "exact" in summary, summary
    assert "matched window 1 tab 2 (exact match)" in log, log
    return True


def test_exact_match_wins_across_windows_not_just_within_one():
    # The exact match sits in the SECOND window, behind an identity match in
    # the first - preference must not degrade into "earliest tab wins".
    listing = _listing(
        (1, 1, UCPRESS_CANONICAL),
        (2, 1, UCPRESS_URL),
    )
    with _osa(listing=listing, capture="<html>ok</html>"):
        html, app, summary, log = _probe(UCPRESS_URL)
    assert "matched window 2 tab 1 (exact match)" in log, log
    return True


def test_identity_fallback_takes_the_earliest_window_and_tab():
    # Three identity matches, none exact. Selection must be deterministic and
    # take the first in window-then-tab order.
    listing = _listing(
        (1, 1, "https://example1.invalid/page1"),
        (1, 3, "https://online.ucpress.edu/ncm/article-pdf/50/1/54/218886/"
               "Fatigued-Voices-and-Vocal-Health-in-fine-secolo.pdf"),
        (2, 1, UCPRESS_CANONICAL),
        (2, 2, "https://online.ucpress.edu/ncm/advance-article-abstract/50/1/54/"
               "218886/Fatigued-Voices-and-Vocal-Health-in-fine-secolo"),
    )
    with _osa(listing=listing, capture="<html>ok</html>"):
        html, app, summary, log = _probe(UCPRESS_URL)
    assert "matched window 1 tab 3 (identity match:" in log, log
    return True


def test_tab_selection_refuses_a_different_article_on_the_same_host():
    # Tab selection has no backstop - a wrong tab means the wrong document is
    # captured outright - so the negative matters more here than anywhere.
    listing = _listing(
        (1, 1, UCPRESS_OTHER_ARTICLE),
        (1, 2, "https://online.ucpress.edu/ncm/issue/50/1"),
    )
    with _osa(listing=listing, capture="<html>should never be captured</html>"):
        html, app, summary, log = _probe(UCPRESS_URL)
    assert html is None, html
    assert "no matching tab" in summary, summary
    return True


def test_browser_probe_reports_apple_events_refusal():
    # State 1. Previously indistinguishable from "no tab" - the conflation
    # that made a single failure take three rounds to diagnose.
    with _osa(refuse_events=True):
        html, app, summary, log = _probe()
    assert html is None and app is None
    assert "Apple Events refused" in summary, summary
    assert "-1743" in summary, summary
    assert "Apple Events refused" in log, log
    return True


def test_browser_probe_reports_not_running():
    # State 2.
    with _osa(running=False):
        html, app, summary, log = _probe()
    assert html is None
    assert "not running" in summary, summary
    assert "Apple Events refused" not in summary, summary
    return True


def test_browser_probe_names_the_tabs_it_saw_on_no_match():
    # State 3, and the diagnostic that makes a matching bug visible: without
    # the tab URLs there is no way to tell "the tab wasn't open" from "the
    # tab was open and matching is wrong." Asked for a URL on the same host
    # as the target, so the same-host section is exercised too.
    other = "https://online.ucpress.edu/ncm/article/99/9/9/999999/Some-Other-Article"
    with _osa():
        lines = []
        html, app, summary = web_source.browser_tab_dom(other, log=lines.append)
    log = "\n".join(lines)
    assert html is None
    assert "no matching tab" in summary, summary
    assert "26 tab(s) examined" in summary, summary
    # The real target is on the same host, so it is named in full.
    assert "Fatigued-Voices" in log, log
    assert "online.ucpress.edu" in log, log
    # Off-host tabs are named too, under their own heading, and capped.
    assert "example1.invalid" in log, log
    return True


def test_browser_probe_distinguishes_js_refusal_from_missing_tab():
    # A tab WAS found; only the separate "Allow JavaScript from Apple Events"
    # switch refused. Reported as its own state, not as "no matching tab".
    with _osa(refuse_js=True):
        html, app, summary, log = _probe()
    assert html is None
    assert "JavaScript from Apple Events refused" in summary, summary
    assert "no matching tab" not in summary, summary
    assert "matched window 1 tab 12" in log, log
    return True


# ── tests ────────────────────────────────────────────────────────────────

def test_browser_challenge_markup_produces_amber_entry():
    # #11 item 4, case 1, updated for the amber follow-up: a Cloudflare
    # interstitial has no correspondence-contradicting signal of its own
    # (no canonical/og:url/Doi), so it's no longer a hard failure - it's the
    # sparsest possible source (no identifying field, a two-word body), and
    # comes back amber on both counts rather than discarded.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_challenge_html()), _crossref_raises():
        result, log = _run()
    assert not isinstance(result, str), result
    assert result.amber is True
    assert "Author/Doi/PublicationDate" in result.amber_reason, result.amber_reason
    assert "words" in result.amber_reason, result.amber_reason
    assert "Source: browser tab (Safari)" in log and "[amber:" in log, log
    return True


def test_browser_thin_dom_produces_amber_entry():
    # #11 item 4, case 2, updated: identifying metadata is present (Title,
    # Author), so only the length guard fires - a tab still loading, not an
    # interstitial. Required new case: "Body under MIN_BODY_WORDS, content
    # corresponding -> amber, not failure."
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_thin_html()), _crossref_raises():
        result, log = _run()
    assert not isinstance(result, str), result
    assert result.amber is True
    assert result.amber_reason == f"body text under {web_source.MIN_BODY_WORDS} words"
    assert "[amber:" in log, log
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
    assert result.amber is False and result.amber_reason is None
    assert "Source: browser tab (Safari)" in log and "[amber:" not in log, log
    return True


def test_title_and_urldate_only_produces_amber():
    # #11 item 4, case 4 (the hole the original redesign was for), now
    # folded into the amber split, and the first of the follow-up's three
    # required new cases verbatim: "Title + Urldate only, content
    # corresponding -> amber entry produced, not a failure." A 200 consent
    # wall with a substantial body and no correspondence-contradicting
    # signal is exactly this shape - no Author/Doi/PublicationDate, only
    # Urldate (implied by having a URL at all) left to date it by. Resolved
    # straight from the fetch: the browser path is never even tried.
    with _fake_webloc(), _fetch_returns(FakeResponse(text=_consent_wall_html())), \
         _crossref_raises():
        with _patched(web_source, "browser_tab_dom",
                      lambda url, log=None: (_ for _ in ()).throw(
                          AssertionError("browser_tab_dom() should not have been called"))):
            result, log = _run()
    assert not isinstance(result, str), result
    assert result.amber is True
    assert result.amber_reason == "no Author/Doi/PublicationDate - only Urldate available to date it"
    assert "Source: fetch for" in log and "[amber:" in log, log
    return True


def test_thin_body_at_fetch_produces_amber():
    # Required new case, at the plain-fetch level rather than via a browser
    # tab: full identifying metadata (Title, Author), but a body under
    # MIN_BODY_WORDS - amber for length alone, nothing else.
    html = (
        "<html><head><title>A Study of Musical Form</title>"
        '<meta name="citation_author" content="Doe, Jane"></head>'
        "<body><p>Loading…</p></body></html>"
    )
    with _fake_webloc(), _fetch_returns(FakeResponse(text=html)), _crossref_raises():
        with _patched(web_source, "browser_tab_dom",
                      lambda url, log=None: (_ for _ in ()).throw(
                          AssertionError("browser_tab_dom() should not have been called"))):
            result, log = _run()
    assert not isinstance(result, str), result
    assert result.amber is True
    assert result.amber_reason == f"body text under {web_source.MIN_BODY_WORDS} words"
    return True


def test_canonical_differing_only_in_path_form_is_accepted():
    # The reported case. The publisher's canonical link is its own statement
    # of where this work lives; treating /article/ vs /article-abstract/ as
    # a different document rejected a correctly captured 114KB article page
    # and would fire on every Silverchair platform.
    with _fake_webloc(UCPRESS_URL), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_ucpress_html(UCPRESS_CANONICAL)), _crossref_raises():
        result, log = _run()
    assert not isinstance(result, str), result
    assert result.metadata.get("Title") == ("Fatigued Voices and Vocal Health "
                                             "in fine secolo Italy")
    assert "Source: browser tab (Safari)" in log, log
    return True


def test_same_host_different_article_id_is_still_rejected():
    # The negative the loosening must not cost: a tab on the same host
    # showing an unrelated article. Host agreement alone is not identity -
    # a false match here files a confident wrong entry, which is worse than
    # any failure.
    with _fake_webloc(UCPRESS_URL), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_ucpress_html(UCPRESS_OTHER_ARTICLE)), _no_crossref():
        result, log = _run()
    assert isinstance(result, str) and result.startswith("Error:"), result
    assert "browser tab" in log and "rejected" in log, log
    return True


def test_url_identity_accepts_path_forms_and_refuses_other_works():
    # _url_matches directly, over the shapes a publisher actually serves.
    same = [
        UCPRESS_CANONICAL,
        "https://online.ucpress.edu/ncm/article-pdf/50/1/54/218886/"
        "Fatigued-Voices-and-Vocal-Health-in-fine-secolo.pdf",
        "https://online.ucpress.edu/ncm/advance-article-abstract/50/1/54/218886/"
        "Fatigued-Voices-and-Vocal-Health-in-fine-secolo",
    ]
    for other in same:
        assert web_source._url_matches(UCPRESS_URL, other), other

    different = [
        UCPRESS_OTHER_ARTICLE,                                  # another article
        "https://online.ucpress.edu/ncm/issue/50/1",            # the issue, not the work
        "https://online.ucpress.edu/login",                     # a login wall
        # Same path entirely, different host - never the same work.
        "https://academic.oup.com/ncm/article/50/1/54/218886/"
        "Fatigued-Voices-and-Vocal-Health-in-fine-secolo",
        # A shared four-digit year must not read as a shared article id,
        # which is why the id pattern requires five digits.
        "https://x.org/j/2020/article/A-Long-Enough-Slug-Two",
    ]
    for other in different:
        assert not web_source._url_matches(UCPRESS_URL, other), other
    assert not web_source._url_matches(
        "https://x.org/j/2020/article/A-Long-Enough-Slug-One",
        "https://x.org/j/2020/article/A-Long-Enough-Slug-Two")

    # A segment built only of structural words identifies nothing, however
    # long: two different works both carry `advance-article-abstract`.
    assert not web_source._url_matches(
        "https://x.org/j/advance-article-abstract/1/2",
        "https://x.org/j/advance-article-abstract/9/9")
    return True


def test_same_issue_articles_never_match_one_another():
    # Every ordered pair, both directions, at the correspondence site.
    # Consecutive six-digit ids and two slugs sharing `Chopin-s` - if the id
    # rule ever softens to a prefix, or slug comparison to a substring, this
    # is what catches it.
    for i, one in enumerate(SAME_ISSUE_ARTICLES):
        for j, other in enumerate(SAME_ISSUE_ARTICLES):
            if i == j:
                continue
            assert not web_source._url_matches(one, other), (one, other)
            # Nor against a sibling's canonical form: the path-form
            # loosening must not become a way in.
            assert not web_source._url_matches(one, _canonical_form(other)), (one, other)
    return True


def test_each_same_issue_article_still_matches_its_own_canonical():
    # The positive half, so the test above cannot pass by a matcher that
    # simply refuses everything.
    for url in SAME_ISSUE_ARTICLES:
        assert web_source._url_matches(url, _canonical_form(url)), url
    return True


def test_tab_selection_ignores_sibling_articles_from_the_same_issue():
    # The stricter site, with the same adversarial set. Two siblings open,
    # the wanted article not open at all: nothing may be captured, because
    # here a wrong tab means the wrong document is read outright.
    wanted, sibling_a, sibling_b = SAME_ISSUE_ARTICLES
    listing = _listing(
        (1, 1, _canonical_form(sibling_a)),
        (1, 2, _canonical_form(sibling_b)),
    )
    with _osa(listing=listing, capture="<html>should never be captured</html>"):
        html, app, summary, log = _probe(wanted)
    assert html is None, html
    assert "no matching tab" in summary, summary
    return True


def test_tab_selection_picks_the_wanted_article_from_among_its_siblings():
    # Same three tabs, the wanted article now open in its canonical form.
    # It must be the one taken - not the first sibling encountered.
    wanted, sibling_a, sibling_b = SAME_ISSUE_ARTICLES
    listing = _listing(
        (1, 1, _canonical_form(sibling_a)),
        (1, 2, _canonical_form(sibling_b)),
        (1, 3, _canonical_form(wanted)),
    )
    with _osa(listing=listing, capture="<html>ok</html>"):
        html, app, summary, log = _probe(wanted)
    assert html == "<html>ok</html>", html
    assert "matched window 1 tab 3 (identity match:" in log, log
    assert "218886" in log, log
    return True


# Platform shapes beyond Silverchair. PROVENANCE, because it differs from
# SAME_ISSUE_ARTICLES above and the difference matters: those three are
# recorded verbatim from the publisher. THESE ARE NOT RECORDINGS. They are
# each platform's documented path grammar with the opaque identifiers filled
# in by hand - no URL here was fetched, and no live request was made anywhere
# in building this file. The grammar is what is under test (which segment
# carries the work's identity, and which is a container shared by every work
# on the platform), and that is stable; the digits are not evidence of
# anything. Replace any of these with a real URL when one is to hand.
#
# Two of these caught real false matches that Silverchair-only fixtures could
# never have surfaced:
#
#   Cambridge Core  /journals/<journal-slug>/article/abs/<title>/<hex id>
#     the journal slug is shared by every article in the journal;
#   Grove Music     /view/10.1093/gmo/<dictionary id>/<entry slug>
#     the dictionary id is shared by every entry, AND the truncated DOI
#     ladder shares the 10.1093/gmo stem.
#
# The rule assumed the long hyphenated segment was always a title. On
# Silverchair it is (the journal is a short code, `ncm`); on Cambridge and
# Grove it is the container. That is precisely the accent a single-publisher
# fixture set builds in.

_CAMBRIDGE = ("https://www.cambridge.org/core/journals/twentieth-century-music"
              "/article/abs/sounding-the-archive/3C8B1F2A9D4E5061728394A5B6C7D8E9")
_GROVE = ("https://www.oxfordmusiconline.com/grovemusic/view/10.1093/gmo/"
          "9781561592630.001.0001/omo-9781561592630-e-0000040055")
_TANDF = "https://www.tandfonline.com/doi/full/10.1080/07494467.2020.1717875"

# (label, one, other) - different works that must never match.
DIFFERENT_WORKS = (
    ("Cambridge: two articles in one journal", _CAMBRIDGE,
     _CAMBRIDGE.replace("sounding-the-archive/3C8B1F2A9D4E5061728394A5B6C7D8E9",
                        "listening-otherwise/9F8E7D6C5B4A3021FEDCBA0987654321")),
    ("Grove: two entries in one dictionary", _GROVE, _GROVE.replace("40055", "40056")),
    ("SEP: two entries", "https://plato.stanford.edu/entries/qualia/",
     "https://plato.stanford.edu/entries/emotion/"),
    ("SEP: entry vs a dated archive of it", "https://plato.stanford.edu/entries/qualia/",
     "https://plato.stanford.edu/archives/spr2021/entries/qualia/"),
    ("Wikipedia: two articles", "https://en.wikipedia.org/wiki/Sonata_form",
     "https://en.wikipedia.org/wiki/Rondo"),
    ("manufacturer: two products",
     "https://www.neumann.com/en-en/products/microphones/u-87-ai",
     "https://www.neumann.com/en-en/products/microphones/tlm-103"),
    ("JSTOR: two stable ids", "https://www.jstor.org/stable/40285017",
     "https://www.jstor.org/stable/40285018"),
    ("Project MUSE: two articles", "https://muse.jhu.edu/article/745211",
     "https://muse.jhu.edu/article/745212"),
    ("arXiv: two preprints", "https://arxiv.org/abs/2401.01234",
     "https://arxiv.org/abs/2401.01235"),
    ("repository handle: two items", "https://dspace.mit.edu/handle/1721.1/12345",
     "https://dspace.mit.edu/handle/1721.1/12346"),
    ("ScienceDirect: two PIIs",
     "https://www.sciencedirect.com/science/article/pii/S0304422X20300310",
     "https://www.sciencedirect.com/science/article/pii/S0304422X20300311"),
    ("Taylor & Francis: two DOIs", _TANDF, _TANDF.replace("1717875", "1717876")),
    ("Silverchair: two DOIs",
     "https://academic.oup.com/jaac/doi/10.1093/jaac/kpag034/8725072",
     "https://academic.oup.com/jaac/doi/10.1093/jaac/kpag099/8725099"),
)

# (label, one, other) - one work, served two ways; these must match.
SAME_WORK_DIFFERENT_FORM = (
    ("Cambridge: /article/abs/ vs /article/", _CAMBRIDGE,
     _CAMBRIDGE.replace("/article/abs/", "/article/")),
    ("Grove: /view/ vs /abstract/", _GROVE, _GROVE.replace("/view/", "/abstract/")),
    ("Taylor & Francis: /full/ vs /abs/", _TANDF, _TANDF.replace("/full/", "/abs/")),
    ("Silverchair: DOI with and without the trailing article id",
     "https://academic.oup.com/jaac/doi/10.1093/jaac/kpag034/8725072",
     "https://academic.oup.com/jaac/doi/10.1093/jaac/kpag034"),
)


def test_different_works_never_match_across_platforms():
    for label, one, other in DIFFERENT_WORKS:
        assert not web_source._url_matches(one, other), label
        assert not web_source._url_matches(other, one), label + " (reversed)"
    return True


def test_one_work_served_two_ways_matches_across_platforms():
    for label, one, other in SAME_WORK_DIFFERENT_FORM:
        assert web_source._url_matches(one, other), label
        assert web_source._url_matches(other, one), label + " (reversed)"
    return True


def test_shapes_without_an_identifier_fall_back_to_exact_matching():
    # An acceptable outcome, and worth pinning: where a URL carries no
    # identifying segment at all, only an exact match can succeed. Silently
    # matching the wrong thing is the unacceptable outcome; declining to
    # match a legitimate variant is not.
    for url in ("https://plato.stanford.edu/entries/qualia/",
                "https://en.wikipedia.org/wiki/Sonata_form",
                "https://example.ac.uk/~smith/papers.html"):
        ids, slugs = web_source._identifying_segments(urlsplit(url).path)
        assert not ids and not slugs, (url, ids, slugs)
        assert web_source._url_matches(url, url), url
        assert web_source._url_matches(url, url.rstrip('/') + '/'), url
    return True


def test_browser_wrong_canonical_falls_through():
    # #11 item 4, case 5. Still a hard failure under the amber split: this
    # is the one condition that stays a failure regardless.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_wrong_canonical_html()), _no_crossref():
        result, log = _run()
    assert isinstance(result, str) and result.startswith("Error:"), result
    assert "browser tab" in log and "rejected" in log, log
    return True


def test_correspondence_mismatch_hard_fails_regardless_of_metadata_completeness():
    # Required new case, verbatim: "Content not corresponding to the
    # requested URL -> hard failure, no entry, regardless of how complete
    # its metadata looks." Every identifying field present (Author, Doi,
    # PublicationDate) and a long body - only the canonical link is wrong,
    # and that alone must still fail it.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _browser_returns(_wrong_canonical_but_complete_html()), _no_crossref():
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
    assert result.amber is False and result.amber_reason is None
    assert "Source: CrossRef for" in log and "[amber:" not in log, log
    return True


def test_no_tab_no_doi_failure_names_the_browser_outcome():
    # #11 item 4, case 6 (the plain-failure branch), challenge classified:
    # no tab, no CrossRef hit. The error text deliberately NO LONGER matches
    # the pre-#11 wording byte for byte: keeping it identical is what made a
    # real failure indistinguishable from the old code path for three
    # rounds. It must now name what the browser probe actually found.
    with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
         _no_browser_tab("Safari: no matching tab (3 tab(s) examined)"), _no_crossref():
        result, log = _run()
    assert result.startswith(
        f"Error: {BOOKMARK_URL} is behind a bot challenge (HTTP 403) "
        f"and its URL carries no DOI to fall back on"), result
    assert "browser tab: Safari: no matching tab (3 tab(s) examined)" in result, result
    assert "Source: failure for" in log, log
    return True


def test_failure_text_distinguishes_refusal_from_absent_tab():
    # The three states must be legible in the RETURNED text, not only in the
    # log - the windowed run shows the returned error and little else.
    for summary, expected in [
        ("Safari: Apple Events refused (-1743)", "Apple Events refused"),
        ("Safari: not running; Google Chrome: not running", "not running"),
        ("Safari: no matching tab (12 tab(s) examined)", "no matching tab"),
    ]:
        with _fake_webloc(), _fetch_returns(CLOUDFLARE_403()), \
             _no_browser_tab(summary), _no_crossref():
            result, _ = _run()
        assert expected in result, (summary, result)
    return True


def test_non_challenge_failure_skips_crossref():
    # A plain 404 never satisfies is_bot_challenge, so crossref_fallback()
    # must never even be called for it - the original gate, preserved.
    with _fake_webloc(), _fetch_returns(PLAIN_404()), \
         _no_browser_tab(), _crossref_raises():
        result, log = _run()
    assert result.startswith(f"Error: Could not fetch {BOOKMARK_URL}: HTTP 404"), result
    assert "browser tab:" in result, result
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
    assert result.amber is False
    assert "Source: browser tab (Chrome) for" in log and "[amber:" not in log, log
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
    assert result.amber is False
    assert "Source: fetch" in log and "[amber:" not in log, log
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
                      lambda u, log=None: (_ for _ in ()).throw(
                          AssertionError("browser_tab_dom() should not have been called"))):
            result, log = _run(url=url)
    assert not isinstance(result, str), result
    assert result.amber is False
    assert "Source: fetch for" in log and "[amber:" not in log, log
    return True


def test_ordinary_fetch_success_does_not_touch_fallbacks():
    # Sanity/regression: the common case still returns straight from the
    # fetch, without ever invoking either fallback.
    response = FakeResponse(text=_article_html(), url=BOOKMARK_URL)
    with _fake_webloc(), _fetch_returns(response), _crossref_raises():
        with _patched(web_source, "browser_tab_dom",
                      lambda url, log=None: (_ for _ in ()).throw(
                          AssertionError("browser_tab_dom() should not have been called"))):
            result, log = _run()
    assert not isinstance(result, str), result
    assert result.label == "webpage"
    assert result.amber is False
    assert "Source: fetch for" in log and "[amber:" not in log, log
    return True


TESTS = [
    test_applescript_does_not_use_tab_inside_the_tell_block,
    test_browser_probe_finds_the_target_in_the_real_listing,
    test_broken_listing_is_reported_as_a_parse_error_not_as_no_tabs,
    test_non_http_scheme_does_not_break_the_listing,
    test_duplicate_windows_do_not_confuse_the_index,
    test_tab_selection_falls_back_to_identity_when_no_tab_matches_exactly,
    test_tab_selection_prefers_an_exact_match_over_an_identity_match,
    test_exact_match_wins_across_windows_not_just_within_one,
    test_identity_fallback_takes_the_earliest_window_and_tab,
    test_tab_selection_refuses_a_different_article_on_the_same_host,
    test_browser_probe_reports_apple_events_refusal,
    test_browser_probe_reports_not_running,
    test_browser_probe_names_the_tabs_it_saw_on_no_match,
    test_browser_probe_distinguishes_js_refusal_from_missing_tab,
    test_browser_challenge_markup_produces_amber_entry,
    test_browser_thin_dom_produces_amber_entry,
    test_browser_genuine_article_succeeds,
    test_title_and_urldate_only_produces_amber,
    test_thin_body_at_fetch_produces_amber,
    test_canonical_differing_only_in_path_form_is_accepted,
    test_same_host_different_article_id_is_still_rejected,
    test_url_identity_accepts_path_forms_and_refuses_other_works,
    test_same_issue_articles_never_match_one_another,
    test_each_same_issue_article_still_matches_its_own_canonical,
    test_tab_selection_ignores_sibling_articles_from_the_same_issue,
    test_tab_selection_picks_the_wanted_article_from_among_its_siblings,
    test_different_works_never_match_across_platforms,
    test_one_work_served_two_ways_matches_across_platforms,
    test_shapes_without_an_identifier_fall_back_to_exact_matching,
    test_browser_wrong_canonical_falls_through,
    test_correspondence_mismatch_hard_fails_regardless_of_metadata_completeness,
    test_browser_wrong_doi_falls_through,
    test_no_tab_falls_back_to_crossref,
    test_no_tab_no_doi_failure_names_the_browser_outcome,
    test_failure_text_distinguishes_refusal_from_absent_tab,
    test_non_challenge_failure_skips_crossref,
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
