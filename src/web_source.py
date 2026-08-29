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
# of fetching the page a second time.
#
# Every distinguishable way this can come up empty is reported separately.
# The first version collapsed them all into None on the theory that the
# caller only needed to know whether it had a DOM; that cost three rounds of
# diagnosis on a single failure, because "Apple Events refused", "browser not
# running" and "no tab matches" are the same symptom with entirely different
# remedies. Nothing here launches a browser that isn't already running, and
# nothing navigates a tab - it only reads what is already loaded.

_BROWSER_APPS = ("Safari", "Google Chrome")
_JS_OUTER_HTML = "document.documentElement.outerHTML"

# osascript outcomes.
OSA_OK = 'ok'
OSA_REFUSED = 'refused'      # the Apple Events grant is missing or denied
OSA_ERROR = 'error'          # anything else: no such tab, osascript absent, timeout

# macOS reports a denied Apple Events grant as error -1743; the accompanying
# wording varies by OS version and locale, so the numeric code is what this
# matches on, with the English phrasings as a secondary net. Safari's separate
# "Allow JavaScript from Apple Events" switch reports -1743 as well when off.
_REFUSED_MARKERS = ('-1743', 'not authorized', 'not authorised', 'not allowed',
                    'not permitted')

# How many non-matching tab URLs to name before summarising the rest. Tabs on
# the target's own host are always named in full regardless of this cap: they
# are the ones worth eyeballing when a match was expected and didn't happen.
_TAB_LOG_LIMIT = 12


def _osascript(script, timeout=15):
    """(status, text) - status is one of OSA_*; text is the script's stdout
    when it ran, and osascript's stderr (or the exception) when it didn't."""
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return OSA_ERROR, f"osascript timed out after {timeout}s"
    except (subprocess.SubprocessError, OSError) as e:
        return OSA_ERROR, str(e)
    if result.returncode == 0:
        return OSA_OK, result.stdout.strip()
    stderr = (result.stderr or '').strip()
    lowered = stderr.lower()
    if any(marker in lowered for marker in _REFUSED_MARKERS):
        return OSA_REFUSED, stderr
    return OSA_ERROR, stderr or f"osascript exited {result.returncode}"


# What one browser had to say about the target URL.
BROWSER_MATCH = 'match'
BROWSER_NOT_RUNNING = 'not running'
BROWSER_NO_WINDOWS = 'running, no windows open'
BROWSER_NO_MATCH = 'no matching tab'
BROWSER_REFUSED = 'Apple Events refused'
BROWSER_CAPTURE_REFUSED = 'tab matched, JavaScript from Apple Events refused'
BROWSER_ERROR = 'error'


def _browser_is_running(app_name):
    """(is_running, status, detail). Checked before anything else so a closed
    browser is never launched just to look for a tab that, by definition,
    isn't open - and so a refused Apple Events grant is named as such rather
    than silently read as "not running"."""
    status, text = _osascript(f'application "{app_name}" is running')
    if status == OSA_OK:
        return text == 'true', OSA_OK, text
    return False, status, text


def _list_tabs(app_name):
    """(tabs, status, detail) - tabs is [(window_index, tab_index, url)] for
    every open tab of app_name, both indices 1-based as AppleScript addresses
    them."""
    running, status, detail = _browser_is_running(app_name)
    if status == OSA_REFUSED:
        return [], BROWSER_REFUSED, detail
    if status == OSA_ERROR:
        return [], BROWSER_ERROR, detail
    if not running:
        return [], BROWSER_NOT_RUNNING, ''

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
    status, output = _osascript(script)
    if status == OSA_REFUSED:
        return [], BROWSER_REFUSED, output
    if status == OSA_ERROR:
        return [], BROWSER_ERROR, output
    if not output:
        return [], BROWSER_NO_WINDOWS, ''

    tabs = []
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        try:
            tabs.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return tabs, OSA_OK, ''


def _describe_tabs(tabs, target_url):
    """The cleaned URLs of the tabs that did not match, as a list of log
    lines - without this there is no way to tell a matching bug from an
    absent tab.

    Returned as separate lines, never one string with newlines in it: the
    caller's log callback prefixes each message it is given, so an embedded
    newline produces a ragged block in the progress window.

    Tabs sharing the target's host are named in full however many there are;
    everything else is capped, since a browser session can carry hundreds of
    tabs that have no bearing on the question.
    """
    if not tabs:
        return ["no tabs open"]
    target_host = urlsplit(clean_url(target_url)).netloc.lower()
    same_host, other = [], []
    for _, _, tab_url in tabs:
        cleaned = clean_url(tab_url).rstrip('/')
        (same_host if urlsplit(cleaned).netloc.lower() == target_host else other).append(cleaned)

    lines = [f"{len(tabs)} tab(s) open; none matched"]
    if same_host:
        lines.append(f"on {target_host} ({len(same_host)}):")
        lines.extend(f"  {u}" for u in same_host)
    else:
        lines.append(f"none on {target_host}")
    shown = other[:_TAB_LOG_LIMIT]
    if shown:
        lines.append(f"elsewhere ({len(other)}):")
        lines.extend(f"  {u}" for u in shown)
        if len(other) > len(shown):
            lines.append(f"  ... and {len(other) - len(shown)} more")
    return lines


def _capture_tab_dom(app_name, window_index, tab_index, timeout=20):
    """(html, status, detail) for one already-identified open tab, via each
    browser's own JS-execution verb (they differ). A refusal here is the
    browser's separate "Allow JavaScript from Apple Events" switch, distinct
    from the Apple Events grant that listing tabs already cleared."""
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
    status, text = _osascript(script, timeout=timeout)
    if status == OSA_REFUSED:
        return None, BROWSER_CAPTURE_REFUSED, text
    if status == OSA_ERROR:
        return None, BROWSER_ERROR, text
    if not text:
        return None, BROWSER_ERROR, "the tab returned an empty document"
    return text, BROWSER_MATCH, ''


def browser_tab_dom(url, log=None):
    """(html, app_name, summary) - the rendered DOM of `url` and the browser
    it came from, if the page happens to be open in a Safari or Chrome tab
    right now; (None, None, summary) otherwise.

    `summary` says what each browser reported, so the caller can tell a
    refused Apple Events grant from a closed browser from a tab that simply
    isn't there. `log`, if given, receives the same information as it is
    discovered, including the cleaned URLs of the tabs that were examined and
    did not match.
    """
    log = log or (lambda msg: None)
    target = clean_url(url).rstrip('/')
    log(f"Browser capture: looking for {target}")

    outcomes = []
    for app_name in _BROWSER_APPS:
        tabs, status, detail = _list_tabs(app_name)

        if status in (BROWSER_REFUSED, BROWSER_ERROR):
            outcomes.append(f"{app_name}: {status}" + (f" ({detail})" if detail else ""))
            log(f"  {app_name}: {status}" + (f" - {detail}" if detail else ""))
            continue
        if status in (BROWSER_NOT_RUNNING, BROWSER_NO_WINDOWS):
            outcomes.append(f"{app_name}: {status}")
            log(f"  {app_name}: {status}")
            continue

        match = None
        for win_idx, tab_idx, tab_url in tabs:
            if clean_url(tab_url).rstrip('/') == target:
                match = (win_idx, tab_idx)
                break

        if not match:
            outcomes.append(f"{app_name}: {BROWSER_NO_MATCH} ({len(tabs)} tab(s) examined)")
            described = _describe_tabs(tabs, url)
            log(f"  {app_name}: {described[0]}")
            for line in described[1:]:
                log(f"    {line}")
            continue

        html, cap_status, cap_detail = _capture_tab_dom(app_name, *match)
        if html:
            log(f"  {app_name}: matched window {match[0]} tab {match[1]}; "
                f"captured {len(html)} characters")
            return html, app_name, f"{app_name}: {BROWSER_MATCH}"
        outcomes.append(f"{app_name}: {cap_status}" + (f" ({cap_detail})" if cap_detail else ""))
        log(f"  {app_name}: matched window {match[0]} tab {match[1]}, but {cap_status}"
            + (f" - {cap_detail}" if cap_detail else ""))

    return None, None, '; '.join(outcomes)


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
# that can produce a SourceContent (fetch, browser tab, CrossRef).
#
# Only one thing here is a hard failure: the content doesn't correspond to
# the URL requested - that's the wrong document, not a sparse one, and no
# amount of metadata completeness excuses it. Everything else this checks
# (missing identifying fields, a thin body) is genuine-but-sparse rather
# than wrong, and is flagged amber (SourceContent.amber, folded into
# biblio_agent.py's existing "needs_color" mechanism) instead of discarded -
# this library carries plenty of real @Online sources (a personal page, a
# manufacturer's spec sheet) with a Title and nothing else, for which
# Urldate is the normal, correct dating evidence per CLAUDE.md, not a
# consolation prize.

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
    # A contradiction only if some candidate URL actually advertises a DOI in
    # its own path - most publisher URLs don't (an Oxford Academic
    # /article/80/1/1/1234567 carries none), so the page's own citation_doi
    # meta tag would otherwise be the only signal available and reject a
    # perfectly ordinary article on every such URL. Absent that, the DOI is
    # unverifiable rather than contradicted, which is the same reasoning as
    # "no signal present" below.
    if doi and any(doi_candidates(c) for c in candidate_urls):
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
    """(ok, reason, amber, amber_reason).

    `ok=False` only for a URL-correspondence mismatch - the one case this
    treats as the wrong document rather than a sparse one. Every other
    outcome is `ok=True`: `amber=True` marks content worth a human glance
    (no Title, or identifying fields thin enough that only Urldate is left
    to date it, or - for HTML-derived content only - a body under
    MIN_BODY_WORDS) without discarding it. `candidate_urls` is every URL the
    content may legitimately declare itself as (see _url_correspondence);
    `soup` is the parsed page for the fetch/browser-tab paths, or None for
    CrossRef, which has no page to read a canonical link or og:url from (and
    whose own metadata block is exempt from the length check - see
    MIN_BODY_WORDS).
    """
    metadata = content.metadata

    ok, reason = _url_correspondence(candidate_urls, soup, metadata)
    if not ok:
        return False, reason, False, None

    amber_bits = []
    if not metadata.get('Title'):
        amber_bits.append("no Title in the extracted metadata")
    if not any(metadata.get(f) for f in _IDENTIFYING_FIELDS):
        # Not a hard fail: a Title with no Author/Doi/PublicationDate still
        # has Urldate to date it by (this project's own convention for an
        # entry with no stated Date - CLAUDE.md), which is a normal, valid
        # @Online source, not a defective one. Flagged amber rather than
        # passed clean, though, since it's the sparsest shape that's still
        # usable at all.
        amber_bits.append("no Author/Doi/PublicationDate - only Urldate available to date it")
    if soup is not None and len(split_into_words(content.text)) < MIN_BODY_WORDS:
        amber_bits.append(f"body text under {MIN_BODY_WORDS} words")

    if amber_bits:
        return True, None, True, '; '.join(amber_bits)
    return True, None, False, None


def _stderr_log(msg):
    """Default sink when no caller-supplied log is given - direct module use,
    tests, and the CLI without the progress window."""
    print(f"   {msg}", file=sys.stderr)


def extract_webloc(webloc_path, timeout=20, crossref_email=None, log=None):
    """
    Extract a .webloc bookmark's target page as a SourceContent.

    Three paths are tried in order - an ordinary fetch, a browser tab already
    open on the bookmarked URL, and the CrossRef record for a DOI in the
    URL's own path - and every one of them is checked for plausibility
    before being trusted (see _content_plausible). Only a URL-correspondence
    mismatch is treated as a failed path (nothing produced, next path
    tried); missing identifying metadata or a thin body still return the
    content, marked amber (SourceContent.amber/amber_reason) for a human to
    glance at rather than trust blind.

    The browser tab is tried on any failure of the fetch (a non-2xx status,
    or a 200 that failed the plausibility check) - a tab rendering the page
    is proof of life no HTTP response can provide, whatever its status code.
    CrossRef stays gated on a classified bot challenge (is_bot_challenge):
    unlike the browser path it has no page to check against, only a DOI
    mined from the URL, so trying it on every failure would risk filing a
    record for a URL that's simply dead (a plain 404) - the original
    rationale for that gate, unchanged here.

    `log`, if given, is called with short human-readable strings naming which
    path produced the text and why each rejected path was rejected - the same
    shape enrich.verify_recollection(log=...) uses, so biblio_agent can route
    it through _log() and it reaches the progress window as well as stderr.
    Defaults to stderr alone, which is invisible in the windowed run.

    Returns:
        SourceContent on success, or a string starting with "Error:" on failure.
    """
    log = log or _stderr_log

    def log_source(chosen, amber_reason=None):
        suffix = f" [amber: {amber_reason}]" if amber_reason else ""
        log(f"Source: {chosen} for {url}{suffix}")

    def log_rejected(path_label, reason, next_step):
        log(f"⚠️  {path_label} rejected ({reason}); trying {next_step}")

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
        ok, reason, amber, amber_reason = _content_plausible(content, {url, response.url}, soup)
        if ok:
            content.amber, content.amber_reason = amber, amber_reason
            log_source("fetch", amber_reason)
            return content
        rejected.append(("fetch", reason))
        log_rejected("fetch", reason, "a browser tab")

    # Tried on ANY failure - non-2xx status or a 200 that didn't pass the
    # plausibility check - not only a classified bot challenge: a browser
    # already holding the session and a genuine TLS fingerprint beats
    # whatever the plain HTTP client got back, and DataDome/Kasada/consent-
    # wall interstitials never trip is_bot_challenge at all.
    html, browser_app, browser_summary = browser_tab_dom(url, log=log)
    if html:
        label = f"browser tab ({browser_app})"
        content, soup = _content_from_html(html, url, f"webpage (via {browser_app} tab)")
        ok, reason, amber, amber_reason = _content_plausible(content, {url}, soup)
        if ok:
            content.amber, content.amber_reason = amber, amber_reason
            log_source(label, amber_reason)
            return content
        # Distinct from "no tab was found": a tab WAS captured and its
        # content examined, and it is the content that was refused.
        rejected.append((label, reason))
        log_rejected(label, reason, "CrossRef" if challenge else "reporting failure")

    if challenge:
        fallback = crossref_fallback(url, crossref_email)
        if fallback:
            ok, reason, amber, amber_reason = _content_plausible(fallback, {url}, soup=None)
            if ok:
                fallback.amber, fallback.amber_reason = amber, amber_reason
                log_source("CrossRef", amber_reason)
                return fallback
            rejected.append(("CrossRef", reason))
            log_rejected("CrossRef", reason, "reporting failure")

    log_source("failure")

    # The browser summary goes into the error text whether or not a tab was
    # found, and says which of the distinguishable outcomes occurred. Without
    # it the returned string cannot tell "no browser was consulted" from "the
    # grant was refused" from "no tab matched" - the conflation that made a
    # single failure take three rounds to diagnose.
    browser_note = f"; browser tab: {browser_summary}" if browser_summary else ""

    if rejected:
        detail = '; '.join(f"{path}: {reason}" for path, reason in rejected)
        return f"Error: {url} yielded no plausible source ({detail}){browser_note}"
    if challenge:
        return (f"Error: {url} is behind a bot challenge (HTTP "
                f"{response.status_code}) and its URL carries no DOI to fall back on"
                f"{browser_note}")
    return f"Error: Could not fetch {url}: HTTP {response.status_code}{browser_note}"
