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


def extract_webloc(webloc_path, timeout=20, crossref_email=None):
    """
    Extract a .webloc bookmark's target page as a SourceContent.

    Where the publisher answers a bot challenge rather than the page, falls
    back to the CrossRef record for the DOI in the URL's own path.

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

    # A challenge is the one failure worth routing around: the work exists and
    # is citable, only this client is unwelcome. Every other non-2xx is left to
    # fail loudly - a 404 masked by a CrossRef hit would file an entry whose
    # Url points at a dead page, which is worse than a reported failure.
    if not response.ok:
        if not is_bot_challenge(response):
            return f"Error: Could not fetch {url}: HTTP {response.status_code}"
        fallback = crossref_fallback(url, crossref_email)
        if fallback:
            return fallback
        return (f"Error: {url} is behind a bot challenge (HTTP "
                f"{response.status_code}) and its URL carries no DOI to fall back on")

    soup = BeautifulSoup(response.text, 'html.parser')

    return SourceContent(
        text=_page_text(soup),
        metadata=_page_metadata(soup),
        label="webpage",
        # Final URL after any redirects (e.g. DOI resolvers), minus tracking.
        url=clean_url(response.url),
    )
