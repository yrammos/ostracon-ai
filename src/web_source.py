#!/usr/bin/env python3
"""
Extract bibliographic text/metadata from the page a .webloc file bookmarks.

Mirrors extract_pages.py's role for PDFs: produces a SourceContent (text +
first-party metadata) for a page reachable only online, with no PDF file to
read. Everything downstream of extraction treats this identically to a PDF's
SourceContent.
"""

import json
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

import enrich
from extract_pages import SourceContent, split_into_words, snap_to_sentence_end

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# Word budget for the extracted body text, matching the PDF path's combined
# beginning+end budget (450 + 150) so Claude gets a comparably-sized excerpt.
TEXT_WORD_BUDGET = 600

# citation_* meta tag names (Highwire/Google Scholar convention) worth
# surfacing as first-party metadata, analogous to embedded PDF metadata fields.
CITATION_META_FIELDS = {
    'citation_title': 'Title',
    'citation_author': 'Author',
    'citation_editor': 'Editor',
    'citation_journal_title': 'Journal',
    'citation_publisher': 'Publisher',
    'citation_doi': 'Doi',
    'citation_volume': 'Volume',
    'citation_issue': 'Number',
    'citation_firstpage': 'FirstPage',
    'citation_lastpage': 'LastPage',
    'citation_publication_date': 'PublicationDate',
    'citation_online_date': 'OnlineDate',
    'citation_language': 'Language',
}

OG_META_FIELDS = {
    'og:title': 'Title',
    'og:site_name': 'SiteName',
    'article:published_time': 'PublicationDate',
}

JSONLD_TYPES = {'Article', 'ScholarlyArticle', 'NewsArticle', 'BlogPosting', 'Book', 'CreativeWork'}


# Query parameters that identify a campaign, a referrer, or the person who
# shared the link rather than the resource itself. A Url lands in a .bib file
# that may be published or shared, so a share token there is both noise and a
# small leak - Oxford's "email this article" links carry one identifying the
# sender. Deliberately conservative: only parameters that are unambiguously
# tracking. Anything load-bearing (a page or article id) must survive, since a
# wrongly-stripped parameter yields a broken citation, which is worse than an
# untidy one.
_TRACKING_PARAMS = {
    'fbclid', 'gclid', 'dclid', 'msclkid', 'twclid', 'ttclid', 'igshid', 'yclid',
    'mc_cid', 'mc_eid', '_ga', '_gl', 'at_medium', 'at_campaign', 'ito', 'cmp',
}
_TRACKING_PREFIXES = ('utm_',)

# Some platforms put the share token in a generically-named parameter, so the
# name alone can't identify it. Silverchair (Oxford Academic, Grove Music
# Online) uses p=email<token>; `p` is far too common a name to strip outright,
# so it is matched on the value instead.
_TRACKING_VALUES = {'p': re.compile(r'^email\w+$', re.I)}


def _is_tracking(key, value):
    lower = key.lower()
    if lower in _TRACKING_PARAMS or lower.startswith(_TRACKING_PREFIXES):
        return True
    pattern = _TRACKING_VALUES.get(lower)
    return bool(pattern and pattern.match(value))


def clean_url(url):
    """Drop tracking and share-token parameters from a URL.

    Everything else - path, fragment, and any parameter that might identify
    the resource - is left untouched.
    """
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking(k, v)]
    return urlunsplit(parts._replace(query=urlencode(kept)))


# A bot wall answers a plain HTTP client with an interstitial challenge page
# instead of the article. Oxford Academic (Silverchair) fronts every page with
# Cloudflare's managed challenge, which keys on TLS fingerprint and JS
# execution - no User-Agent or header set can pass it, and the whole host
# answers 403, homepage included. Detected so the DOI fallback below can run,
# and so the error message can say "bot challenge" rather than the misleading
# "could not fetch", which reads like a dead bookmark.
_CHALLENGE_STATUSES = {403, 429, 503}
_CHALLENGE_BODY_MARKERS = ('just a moment', 'cf-browser-verification', 'challenges.cloudflare.com')


def is_bot_challenge(response):
    """True when a response is an interstitial bot challenge, not the page.

    Deliberately permissive: a false positive costs one CrossRef lookup that
    either yields a better record than the blocked page would have, or misses
    and leaves the original error intact. A false negative costs the entry.
    """
    if response.status_code not in _CHALLENGE_STATUSES:
        return False
    headers = response.headers
    if 'cf-mitigated' in headers:
        return True
    if 'cloudflare' in headers.get('server', '').lower():
        return True
    # .content rather than .text: an error payload may be large and carry no
    # declared charset, and .text would run encoding detection over the whole
    # of it before this slice ever narrowed it.
    body = response.content[:4000].decode('utf-8', 'ignore').lower()
    return any(marker in body for marker in _CHALLENGE_BODY_MARKERS)


_DOI_PATH_RE = re.compile(r'/(10\.\d{4,9}/[^?#]+)')


def doi_candidates(url, limit=3):
    """DOIs the URL's path might contain, longest first.

    Publishers embed the DOI in the path but frequently append an identifier
    of their own after it: Silverchair writes
    /doi/10.1093/jaac/kpag034/8725072, where the trailing number is an article
    id, not part of the DOI. Because a DOI suffix may itself contain slashes,
    no pattern can tell where one ends - so offer progressively shorter
    candidates and let CrossRef adjudicate. `10.1093/jaac/kpag034/8725072`
    misses; `10.1093/jaac/kpag034` hits.
    """
    match = _DOI_PATH_RE.search(urlsplit(url).path)
    if not match:
        return []
    doi = match.group(1).rstrip('/')
    candidates = []
    while doi.count('/') >= 1 and len(candidates) < limit:
        candidates.append(doi)
        doi = doi.rsplit('/', 1)[0]
    return candidates


def _strip_jats(text):
    """CrossRef abstracts arrive as JATS XML; keep the prose, drop the tags."""
    return ' '.join(re.sub(r'<[^>]+>', ' ', text or '').split())


def _full_date(message):
    """Full publication date as YYYY-MM-DD (or as much as CrossRef gives).

    Deliberately not enrich.crossref_date(), which yields the year alone.
    Here the model needs the whole date, since CLAUDE.md's year-only rule has
    exceptions (@Unpublished, magazine articles) it can only apply if it can
    see the month and day.
    """
    for key in ('published-print', 'published-online', 'issued', 'published'):
        parts = (message.get(key) or {}).get('date-parts') or []
        if parts and parts[0] and parts[0][0]:
            return '-'.join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts[0]))
    return None


def _crossref_content(message, url):
    """Turn a CrossRef work object into the same SourceContent shape the page
    itself would have produced, so nothing downstream can tell the difference."""
    title = ' '.join(message.get('title') or [])
    subtitle = ' '.join(message.get('subtitle') or [])
    authors = enrich.crossref_authors(message)
    editors = enrich.crossref_editors(message)
    container = (message.get('container-title') or [None])[0]
    date = _full_date(message)
    pages = message.get('page')

    metadata = {}
    for label, value in (
        ('Title', ': '.join(p for p in (title, subtitle) if p)),
        ('Author', '; '.join(authors)),
        ('Editor', '; '.join(editors)),
        ('Journal', container),
        ('Publisher', message.get('publisher')),
        ('Doi', message.get('DOI')),
        ('Volume', message.get('volume')),
        ('Number', message.get('issue')),
        ('Pages', pages),
        ('PublicationDate', date),
        ('Language', message.get('language')),
        ('Type', message.get('type')),
    ):
        if value:
            metadata[label] = value

    # Body text, so the model reads the work in prose as it would a webpage,
    # rather than inferring everything from the metadata block alone.
    lines = [f"{label}: {value}" for label, value in metadata.items()]
    abstract = _strip_jats(message.get('abstract'))
    if abstract:
        # Held to the same budget as a fetched page, so a long abstract can't
        # quietly cost more than the webpage path it stands in for.
        spare = TEXT_WORD_BUDGET - len(split_into_words('\n'.join(lines)))
        if spare > 0:
            lines.append(abstract if len(split_into_words(abstract)) <= spare
                         else snap_to_sentence_end(abstract, spare, from_end=False))

    return SourceContent(
        text='\n'.join(lines),
        metadata=metadata,
        label="CrossRef record",
        url=clean_url(url),
    )


def crossref_fallback(url, crossref_email=None):
    """A SourceContent built from CrossRef, for a URL whose page is walled off.

    The DOI is already in the URL's own path, so the record can be fetched
    from a registry that welcomes clients rather than from a publisher that
    does not. Returns None when the URL carries no DOI or none resolves.
    """
    for doi in doi_candidates(url):
        message = enrich.crossref_by_doi(doi, mailto=crossref_email)
        if message:
            return _crossref_content(message, url)
    return None


# ── Browser DOM fallback (Safari / Chrome) ──────────────────────────────────
#
# A Cloudflare challenge keys on TLS fingerprint and JS execution, which no
# plain HTTP client can satisfy. A browser the operator already has open
# holds the session cookie and a genuine fingerprint, so when the bookmarked
# URL happens to still be open in a tab, the DOM is taken from there instead
# of fetching the page a second time. Requires a one-time setting in each
# browser (Safari: Develop > Allow JavaScript from Apple Events; Chrome:
# View > Developer > Allow JavaScript from Apple Events) that cannot be
# detected in advance, so a missing permission is indistinguishable here from
# a missing tab - both simply fall through to the existing failure path.

_BROWSER_APPS = ("Safari", "Google Chrome")
_JS_OUTER_HTML = "document.documentElement.outerHTML"


def _osascript(script, timeout=15):
    """Run an AppleScript snippet, returning its stdout (stripped), or None
    on any failure: osascript missing, the script errored (no permission, no
    such window/tab), or it ran past timeout."""
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _browser_is_running(app_name):
    """True if app_name is already running - checked so a closed browser is
    never launched just to look for a tab that, by definition, isn't open."""
    return _osascript(f'application "{app_name}" is running') == 'true'


def _list_tabs(app_name):
    """(window_index, tab_index, url) for every open tab of app_name, both
    indices 1-based as AppleScript addresses them. Empty if the app isn't
    running or has no windows."""
    if not _browser_is_running(app_name):
        return []
    script = f'''
    tell application "{app_name}"
        if (count of windows) is 0 then return ""
        set out to ""
        set wIdx to 0
        repeat with w in windows
            set wIdx to wIdx + 1
            set tIdx to 0
            repeat with t in tabs of w
                set tIdx to tIdx + 1
                set out to out & wIdx & tab & tIdx & tab & (URL of t) & linefeed
            end repeat
        end repeat
        return out
    end tell
    '''
    output = _osascript(script)
    if not output:
        return []
    tabs = []
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        try:
            tabs.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return tabs


def _find_matching_tab(app_name, target_url):
    """(window_index, tab_index) of the first open tab of app_name whose URL
    matches target_url once tracking parameters are stripped from both and a
    trailing slash is ignored, or None if no tab matches."""
    target = clean_url(target_url).rstrip('/')
    for win_idx, tab_idx, tab_url in _list_tabs(app_name):
        if clean_url(tab_url).rstrip('/') == target:
            return win_idx, tab_idx
    return None


def _capture_tab_dom(app_name, window_index, tab_index, timeout=20):
    """The live DOM of one already-identified open tab, via each browser's
    own JS-execution verb (they differ). None if capture fails - almost
    always because the one-time Apple Events permission was never granted."""
    if app_name == "Safari":
        script = (
            f'tell application "Safari" to do JavaScript "{_JS_OUTER_HTML}" '
            f'in tab {tab_index} of window {window_index}'
        )
    else:  # Google Chrome
        script = (
            f'tell application "Google Chrome" to execute tab {tab_index} '
            f'of window {window_index} javascript "{_JS_OUTER_HTML}"'
        )
    return _osascript(script, timeout=timeout)


def browser_tab_dom(url):
    """The rendered DOM of `url` and the browser it came from, if the page
    happens to be open in a Safari or Chrome tab right now - (None, None)
    otherwise (neither browser running, no matching tab, or the Apple Events
    JS permission was never granted in the browser that does have it open).

    Tried in place of a second HTTP fetch, never as a replacement for one:
    this never launches a browser that isn't already running, and never
    fetches or navigates a tab - only reads whatever page is already there.
    """
    for app_name in _BROWSER_APPS:
        match = _find_matching_tab(app_name, url)
        if not match:
            continue
        html = _capture_tab_dom(app_name, *match)
        if html:
            return html, app_name
    return None, None


def resolve_webloc(path):
    """Parse a .webloc plist and return its bookmarked URL, or None."""
    with open(path, 'rb') as f:
        data = plistlib.load(f)
    return data.get('URL')


def _meta_fields(soup, field_map, attr):
    """Collect <meta {attr}="key" content="..."> tags per field_map, joining
    repeated tags (e.g. multiple citation_author) with '; '."""
    found = {}
    for key, label in field_map.items():
        values = [
            tag.get('content', '').strip()
            for tag in soup.find_all('meta', attrs={attr: key})
            if tag.get('content', '').strip()
        ]
        if values:
            found.setdefault(label, '; '.join(dict.fromkeys(values)))
    return found


def _jsonld_fields(soup):
    """Pull a few common fields out of the first recognized JSON-LD block."""
    for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
        try:
            data = json.loads(script.string or '')
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            if entry.get('@type') not in JSONLD_TYPES:
                continue
            fields = {}
            name = entry.get('headline') or entry.get('name')
            if name:
                fields.setdefault('Title', str(name))
            author = entry.get('author')
            if isinstance(author, dict):
                author = author.get('name')
            elif isinstance(author, list):
                author = '; '.join(a.get('name', str(a)) if isinstance(a, dict) else str(a) for a in author)
            if author:
                fields.setdefault('Author', str(author))
            date = entry.get('datePublished')
            if date:
                fields.setdefault('PublicationDate', str(date))
            publisher = entry.get('publisher')
            if isinstance(publisher, dict):
                publisher = publisher.get('name')
            if publisher:
                fields.setdefault('Publisher', str(publisher))
            if fields:
                return fields
    return {}


def _page_metadata(soup):
    """First-party metadata from the page itself, analogous to
    extract_pdf_metadata()'s embedded PDF metadata - often more complete here
    since publishers deliberately embed these for indexing."""
    metadata = {}
    metadata.update(_jsonld_fields(soup))
    metadata.update(_meta_fields(soup, OG_META_FIELDS, 'property'))
    metadata.update(_meta_fields(soup, CITATION_META_FIELDS, 'name'))  # citation_* wins on overlap
    if not metadata.get('Title') and soup.title and soup.title.string:
        metadata['Title'] = soup.title.string.strip()
    return metadata


def _page_text(soup):
    """Visible body text, stripped of chrome, capped to TEXT_WORD_BUDGET words."""
    # <noscript> matters here beyond the obvious: JS-rendered (Vue/Nuxt etc.)
    # publisher sites often ship a full no-JS fallback site map inside it -
    # e.g. Cambridge Core's book pages embed their entire subject/partner/
    # services navigation tree (thousands of words) in a <noscript> block
    # that appears before the real page content in document order. A real
    # browser never renders it, but BeautifulSoup's get_text() doesn't know
    # that, so left unstripped it can consume the whole TEXT_WORD_BUDGET
    # before extraction ever reaches the actual title/author/abstract text.
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form', 'noscript']):
        tag.decompose()
    text = soup.get_text(separator=' ', strip=True)
    text = ' '.join(text.split())
    if len(split_into_words(text)) <= TEXT_WORD_BUDGET:
        return text
    return snap_to_sentence_end(text, TEXT_WORD_BUDGET, from_end=False)


def _content_from_html(html, source_url, label):
    """Parse raw page HTML into a SourceContent - the same construction
    whether the HTML came from a live HTTP fetch or a captured browser tab,
    so the plausibility check below runs identically over both. Returns the
    soup alongside the content, since the caller needs it again to check the
    page's own canonical link / og:url."""
    soup = BeautifulSoup(html, 'html.parser')
    # Metadata before text: _page_text() decomposes every <script> tag
    # (deliberately, to keep chrome out of the body text), which destroys
    # the JSON-LD block _page_metadata() reads from.
    metadata = _page_metadata(soup)
    text = _page_text(soup)
    return SourceContent(text=text, metadata=metadata, label=label,
                          url=clean_url(source_url)), soup


# ── Content plausibility ─────────────────────────────────────────────────
#
# Classifying the *failure* (is_bot_challenge, above) only ever catches a
# gatekeeper that answers with an error status. DataDome, Kasada, consent
# gates and login walls commonly answer 200 with an interstitial instead -
# there is no error to classify, so the only thing that tells it apart from
# the article is what actually came back. Applied identically to every path
# that can produce a SourceContent (fetch, browser tab, CrossRef), so none
# of the three can hand the model a confident wrong entry.

_IDENTIFYING_FIELDS = ('Author', 'Doi', 'PublicationDate')

# Provisional floor pending #16's own thin-yield threshold; borrowed from
# extract_pages.py's OCR-retry threshold (also 100 words), the closest
# existing precedent in this codebase for "too little text to trust." HTML-
# derived content only (fetch, browser tab) - a CrossRef record isn't a DOM
# that can still be loading, so it has no length to check; its guard is the
# identifying-metadata check below instead (see _content_plausible).
MIN_BODY_WORDS = 100


def _url_matches(a, b):
    return clean_url(a).rstrip('/') == clean_url(b).rstrip('/')


def _url_correspondence(candidate_urls, soup, metadata):
    """(ok, reason) - whether at least one of the page's own self-identifying
    signals (canonical link, og:url, embedded Doi) points back at one of the
    URLs this content might legitimately be found at.

    Every present signal is checked against every candidate URL, rather than
    stopping at the first signal found or requiring all of them to agree:
    `candidate_urls` normally holds both the bookmarked .webloc URL and, for
    a live fetch, the URL requests.get() actually landed on after following
    redirects - a doi.org bookmark resolves to the publisher's own URL, whose
    canonical link then names the publisher URL rather than the redirector,
    while its embedded DOI still matches the original bookmark. Requiring
    the first signal present to match would reject that page over a mismatch
    that was never a contradiction.

    No signal present at all is neither confirmed nor contradicted, so it
    passes: plenty of legitimate pages carry none of the three, and this
    check exists to catch a genuine *contradiction* (a login wall's
    canonical link pointing at the login page, a captured tab that has
    navigated elsewhere) - a page too thin to carry any of these tags is
    already caught by the identifying-metadata and length checks.
    """
    signals = []
    if soup is not None:
        canonical = soup.find('link', rel='canonical')
        href = canonical.get('href') if canonical else None
        if href and href.startswith('http'):
            signals.append(('canonical link', href))
        og_url = soup.find('meta', attrs={'property': 'og:url'})
        content = og_url.get('content') if og_url else None
        if content and content.startswith('http'):
            signals.append(('og:url', content))
    doi = metadata.get('Doi')
    if doi:
        signals.append(('embedded Doi', doi))

    if not signals:
        return True, None

    for label, value in signals:
        for candidate in candidate_urls:
            if label == 'embedded Doi':
                if value.lower() in candidate.lower():
                    return True, None
            elif _url_matches(candidate, value):
                return True, None
    described = '; '.join(f"{label} = {value}" for label, value in signals)
    return False, f"none of its self-identifying signals match the requested URL ({described})"


def _content_plausible(content, candidate_urls, soup=None):
    """(is_plausible, reason) - whether `content` genuinely describes the
    bookmarked work, applied the same way regardless of which path produced
    it. `candidate_urls` is every URL the content may legitimately declare
    itself as (see _url_correspondence); `soup` is the parsed page for the
    fetch/browser-tab paths, or None for CrossRef, which has no page to read
    a canonical link or og:url from.
    """
    metadata = content.metadata
    if not metadata.get('Title'):
        return False, "no Title in the extracted metadata"
    if not any(metadata.get(f) for f in _IDENTIFYING_FIELDS):
        return False, "Title but no Author/Doi/PublicationDate - looks like an interstitial"
    ok, reason = _url_correspondence(candidate_urls, soup, metadata)
    if not ok:
        return False, reason
    if soup is not None and len(split_into_words(content.text)) < MIN_BODY_WORDS:
        return False, f"body text under {MIN_BODY_WORDS} words - looks like a page still loading"
    return True, None


def _log_source(chosen, url):
    """State which path produced the text for `url` - printed unconditionally
    (matching extract_pages.py's OCR-retry warnings) so this is visible in
    normal output with nothing to instrument."""
    print(f"   Source: {chosen} for {url}", file=sys.stderr)


def _log_rejected(path_label, reason, next_step):
    print(f"   ⚠️  {path_label} rejected ({reason}); trying {next_step}", file=sys.stderr)


def extract_webloc(webloc_path, timeout=20, crossref_email=None):
    """
    Extract a .webloc bookmark's target page as a SourceContent.

    Three paths are tried in order - an ordinary fetch, a browser tab already
    open on the bookmarked URL, and the CrossRef record for a DOI in the
    URL's own path - and every one of them is checked for plausibility
    before being trusted (see _content_plausible): the metadata must
    identify a work, and the content must correspond to the requested URL.
    A path that fails the check is treated exactly like a path that produced
    nothing at all, and the next one is tried.

    The browser tab is tried on any failure of the fetch (a non-2xx status,
    or a 200 that failed the plausibility check) - a tab rendering the page
    is proof of life no HTTP response can provide, whatever its status code.
    CrossRef stays gated on a classified bot challenge (is_bot_challenge):
    unlike the browser path it has no page to check against, only a DOI
    mined from the URL, so trying it on every failure would risk filing a
    record for a URL that's simply dead (a plain 404) - the original
    rationale for that gate, unchanged here.

    Returns:
        SourceContent on success, or a string starting with "Error:" on failure.
    """
    webloc_path = Path(webloc_path)

    if not webloc_path.exists():
        return f"Error: File not found: {webloc_path}"

    try:
        url = resolve_webloc(webloc_path)
    except Exception as e:
        return f"Error: Could not parse .webloc file: {e}"

    if not url:
        return f"Error: .webloc file has no URL: {webloc_path}"

    try:
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=timeout)
    except requests.RequestException as e:
        return f"Error: Could not fetch {url}: {e}"

    challenge = is_bot_challenge(response)
    rejected = []  # (path label, reason), in the order tried

    if response.ok:
        content, soup = _content_from_html(response.text, response.url, "webpage")
        ok, reason = _content_plausible(content, {url, response.url}, soup)
        if ok:
            _log_source("fetch", url)
            return content
        rejected.append(("fetch", reason))
        _log_rejected("fetch", reason, "a browser tab")

    # Tried on ANY failure - non-2xx status or a 200 that didn't pass the
    # plausibility check - not only a classified bot challenge: a browser
    # already holding the session and a genuine TLS fingerprint beats
    # whatever the plain HTTP client got back, and DataDome/Kasada/consent-
    # wall interstitials never trip is_bot_challenge at all.
    html, browser_app = browser_tab_dom(url)
    if html:
        label = f"browser tab ({browser_app})"
        content, soup = _content_from_html(html, url, f"webpage (via {browser_app} tab)")
        ok, reason = _content_plausible(content, {url}, soup)
        if ok:
            _log_source(label, url)
            return content
        rejected.append((label, reason))
        _log_rejected(label, reason, "CrossRef" if challenge else "reporting failure")

    if challenge:
        fallback = crossref_fallback(url, crossref_email)
        if fallback:
            ok, reason = _content_plausible(fallback, {url}, soup=None)
            if ok:
                _log_source("CrossRef", url)
                return fallback
            rejected.append(("CrossRef", reason))
            _log_rejected("CrossRef", reason, "reporting failure")

    _log_source("failure", url)
    if rejected:
        detail = '; '.join(f"{path}: {reason}" for path, reason in rejected)
        return f"Error: {url} yielded no plausible source ({detail})"
    if challenge:
        return (f"Error: {url} is behind a bot challenge (HTTP "
                f"{response.status_code}) and its URL carries no DOI to fall back on")
    return f"Error: Could not fetch {url}: HTTP {response.status_code}"
