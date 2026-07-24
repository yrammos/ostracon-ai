#!/usr/bin/env python3
"""
External bibliographic enrichment for entries with fields missing from the
PDF itself (volume, issue/number, page range, chapter number).

Two sources, queried in order of trust:
1. CrossRef (free, no key) - by DOI when one is found in the PDF text or
   entry, else a validated title/author search.
2. ScrapingDog's Google Scholar + Cite APIs (paid, requires an API key) -
   a title search followed by a Cite lookup for a BibTeX-formatted result.

Only fills fields that are missing; never overwrites what the PDF/Claude
already produced. A low-confidence match is discarded rather than merged.
"""
import re
import difflib
import requests

DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>{}]+')

# Fields whose absence marks an entry incomplete.
REQUIRED_FIELDS = {
    'article': ['volume', 'pages'],
    'review': ['volume', 'pages'],
    'inproceedings': ['pages'],
    'periodical': ['volume'],
}

# Fields worth attempting to fill, but whose absence doesn't block completeness -
# not every article/review has an issue number, not every proceedings paper has
# a session/track number, and not every book chapter is numbered or paginated
# in a way CrossRef/Scholar can reliably look up.
DESIRED_FIELDS = {
    'article': ['number'],
    'review': ['number'],
    'periodical': ['number'],
    'inproceedings': ['number'],
    'incollection': ['chapter', 'pages'],
    'inbook': ['chapter', 'pages'],
}

_FIELD_RE = re.compile(r'(?m)^\s*([A-Za-z]+)\s*=\s*')


def get_entry_type(entry_text):
    m = re.search(r'@(\w+)\{', entry_text)
    return m.group(1).lower() if m else None


def parse_bibtex_fields(entry_text):
    """Parse `field = {value}` pairs, handling nested braces (e.g. a
    \\mkbibquote{...} inside a title)."""
    fields = {}
    for m in _FIELD_RE.finditer(entry_text):
        name = m.group(1).lower()
        start = m.end()
        if start >= len(entry_text):
            continue
        if entry_text[start] == '{':
            depth = 0
            i = start
            while i < len(entry_text):
                if entry_text[i] == '{':
                    depth += 1
                elif entry_text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            fields[name] = entry_text[start + 1:i]
        else:
            end = entry_text.find(',', start)
            end = end if end != -1 else len(entry_text)
            fields[name] = entry_text[start:end].strip()
    return fields


def missing_fields(entry_type, fields):
    """Returns (missing_required, missing_desired) field-name lists."""
    entry_type = (entry_type or '').lower()
    required = [f for f in REQUIRED_FIELDS.get(entry_type, []) if not fields.get(f)]
    desired = [f for f in DESIRED_FIELDS.get(entry_type, []) if not fields.get(f)]
    return required, desired


def strip_latex(text):
    """Strip LaTeX markup from a field value for use in external search queries."""
    if not text:
        return text
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r'\\[A-Za-z]+\{([^{}]*)\}', r'\1', text)
    return text.replace('{', '').replace('}', '').strip()


def extract_doi(text):
    m = DOI_RE.search(text or '')
    if not m:
        return None
    return m.group(0).rstrip('.,;)')


def _normalize_pages(value):
    if not value:
        return value
    return re.sub(r'\s*[-–—]{1,2}\s*', '-', value).strip()


def _title_similarity(a, b):
    norm = lambda s: re.sub(r'[^a-z0-9 ]', '', (s or '').lower())
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def crossref_by_doi(doi, mailto=None, timeout=15):
    if not doi:
        return None
    try:
        params = {'mailto': mailto} if mailto else {}
        resp = requests.get(f"https://api.crossref.org/works/{doi}", params=params, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.json().get('message')
    except requests.RequestException:
        return None


def _author_matches(item, author_surname):
    if not author_surname:
        return True
    surname = author_surname.lower()
    for a in item.get('author') or []:
        family = (a.get('family') or '').lower()
        if family and (surname in family or family in surname):
            return True
    return False


def _year_matches(item, year):
    if not year:
        return True
    try:
        year = int(str(year)[:4])
    except ValueError:
        return True
    for key in ('published-print', 'published-online', 'published'):
        parts = (item.get(key) or {}).get('date-parts')
        if parts and parts[0] and parts[0][0]:
            return abs(int(parts[0][0]) - year) <= 1
    return True  # no date info to check against - don't block on it


def crossref_by_biblio(title, author_surname=None, year=None, mailto=None, timeout=15, min_similarity=0.72):
    """Fuzzy title search - unlike the DOI lookup this isn't a guaranteed
    match, so a candidate is also required to share the entry's author
    surname and (roughly) publication year when those are known."""
    if not title:
        return None
    try:
        params = {'query.bibliographic': title, 'rows': 5}
        if mailto:
            params['mailto'] = mailto
        resp = requests.get("https://api.crossref.org/works", params=params, timeout=timeout)
        if resp.status_code != 200:
            return None
        items = resp.json().get('message', {}).get('items', [])
    except requests.RequestException:
        return None

    best, best_score = None, 0.0
    for item in items:
        candidate_titles = item.get('title') or []
        if not candidate_titles:
            continue
        if not _author_matches(item, author_surname) or not _year_matches(item, year):
            continue
        score = _title_similarity(title, candidate_titles[0])
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= min_similarity else None


def crossref_fields(message):
    if not message:
        return {}
    out = {}
    if message.get('volume'):
        out['volume'] = message['volume']
    if message.get('issue'):
        out['number'] = message['issue']
    if message.get('page'):
        out['pages'] = _normalize_pages(message['page'])
    if message.get('publisher'):
        out['publisher'] = message['publisher']
    container = message.get('container-title') or []
    if container:
        out['journaltitle'] = container[0]
    return out


def scrapingdog_search(query, api_key, timeout=20):
    try:
        resp = requests.get(
            "https://api.scrapingdog.com/google_scholar",
            params={'api_key': api_key, 'query': query},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        return resp.json().get('scholar_results', [])
    except requests.RequestException:
        return []


def scrapingdog_best_match(results, title, min_similarity=0.75):
    best, best_score = None, 0.0
    for r in results:
        score = _title_similarity(title, r.get('title', ''))
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= min_similarity else None


def scrapingdog_cite_bibtex(result_id, api_key, timeout=20):
    """Fetch the Cite API entry for a Scholar result and return raw BibTeX text.

    NOTE: the exact key names below (`links`, url-shaped values, a 'bibtex'
    label somewhere in the entry) are inferred from ScrapingDog's public docs
    rather than a live call, since no API key is configured. Parsing is
    deliberately defensive - if the schema doesn't match, this returns None
    (silently skipped) rather than raising or on unrelated data.
    """
    try:
        resp = requests.get(
            "https://api.scrapingdog.com/google_scholar/cite",
            params={'api_key': api_key, 'query': result_id},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except requests.RequestException:
        return None

    bibtex_url = None
    for link in data.get('links', []):
        if not isinstance(link, dict):
            continue
        label = ' '.join(str(v) for v in link.values() if isinstance(v, str)).lower()
        if 'bibtex' not in label:
            continue
        for v in link.values():
            if isinstance(v, str) and v.startswith('http'):
                bibtex_url = v
                break
        if bibtex_url:
            break

    if not bibtex_url:
        return None
    try:
        bib_resp = requests.get(bibtex_url, timeout=timeout)
        if bib_resp.status_code == 200:
            return bib_resp.text
    except requests.RequestException:
        pass
    return None


def scrapingdog_fields(bibtex_text):
    if not bibtex_text:
        return {}
    fields = parse_bibtex_fields(bibtex_text)
    out = {}
    if fields.get('volume'):
        out['volume'] = fields['volume']
    if fields.get('number'):
        out['number'] = fields['number']
    if fields.get('pages'):
        out['pages'] = _normalize_pages(fields['pages'])
    if fields.get('publisher'):
        out['publisher'] = fields['publisher']
    if fields.get('journal'):
        out['journaltitle'] = fields['journal']
    return out


def gather_enrichment(pdf_text, title, entry_type, fields, crossref_email=None, scrapingdog_api_key=None):
    """
    Look up supplementary bibliographic data for an entry with missing
    required/desired fields.

    CrossRef is tried first (DOI lookup, else a title+author+year search);
    any fields it returns win. ScrapingDog's Google Scholar is only queried
    for whatever required fields CrossRef didn't fill, and only used to fill
    those specific gaps - it never overrides a CrossRef value. This keeps the
    free, structured source authoritative and treats the paid source purely
    as a fallback for what's still missing.

    Returns (found_fields, source_labels). found_fields only contains keys
    not already present in `fields` - existing values are never overwritten.
    """
    required, desired = missing_fields(entry_type, fields)
    if not required and not desired:
        return {}, []

    found = {}
    sources = []

    author_surname = None
    raw_author = strip_latex(fields.get('author', ''))
    if raw_author:
        author_surname = raw_author.split(',')[0].strip()
    year = fields.get('date') or fields.get('year')

    doi = fields.get('doi') or extract_doi(pdf_text)
    message = crossref_by_doi(doi, mailto=crossref_email) if doi else None
    if not message and title:
        message = crossref_by_biblio(title, author_surname=author_surname, year=year, mailto=crossref_email)
    if message:
        for k, v in crossref_fields(message).items():
            if not fields.get(k) and k not in found:
                found[k] = v
        if found:
            sources.append('CrossRef')

    still_required = [f for f in required if f not in found]
    if still_required and scrapingdog_api_key and title:
        results = scrapingdog_search(title, scrapingdog_api_key)
        match = scrapingdog_best_match(results, title)
        if match and match.get('id'):
            bibtex_text = scrapingdog_cite_bibtex(match['id'], scrapingdog_api_key)
            scholar_found = scrapingdog_fields(bibtex_text)
            added = False
            for k, v in scholar_found.items():
                if not fields.get(k) and k not in found:
                    found[k] = v
                    added = True
            if added:
                sources.append('Google Scholar')

    return found, sources
