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
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
        tag.decompose()
    text = soup.get_text(separator=' ', strip=True)
    text = ' '.join(text.split())
    if len(split_into_words(text)) <= TEXT_WORD_BUDGET:
        return text
    return snap_to_sentence_end(text, TEXT_WORD_BUDGET, from_end=False)


def extract_webloc(webloc_path, timeout=20):
    """
    Extract a .webloc bookmark's target page as a SourceContent.

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
        response.raise_for_status()
    except requests.RequestException as e:
        return f"Error: Could not fetch {url}: {e}"

    soup = BeautifulSoup(response.text, 'html.parser')

    return SourceContent(
        text=_page_text(soup),
        metadata=_page_metadata(soup),
        label="webpage",
        url=response.url,  # final URL after any redirects (e.g. DOI resolvers)
    )
