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


def work_level_title(entry_type, fields):
    """
    The title to use when verifying the whole *work* this entry belongs to,
    as opposed to a possibly too-generic chapter/article title - e.g. a
    chapter titled "Introduction" or "Manifesto" is useless to search on, but
    the book it's part of is usually far more distinctive.
    """
    entry_type = (entry_type or '').lower()
    if entry_type in ('incollection', 'inbook', 'inproceedings'):
        return fields.get('booktitle') or fields.get('title', '')
    return fields.get('title', '')


def set_field(entry_text, field_name, new_value):
    """
    Replace the value of an existing top-level field in a BibTeX entry,
    handling nested braces in the current value (e.g. a \\mkbibquote{...}
    inside a title). Returns the entry unchanged if the field isn't found.
    """
    m = re.search(rf'(?im)^(\s*{re.escape(field_name)}\s*=\s*)', entry_text)
    if not m:
        return entry_text
    start = m.end()
    if start >= len(entry_text) or entry_text[start] != '{':
        return entry_text
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
    return entry_text[:start] + '{' + new_value + '}' + entry_text[i + 1:]


def add_field(entry_text, field_name, value):
    """
    Insert a new field into a BibTeX entry, just before its closing brace.
    Assumes the standard one-field-per-line, closing-brace-on-its-own-line
    shape this project's entries are always formatted in.
    """
    lines = entry_text.rstrip().split('\n')
    if len(lines) < 2 or lines[-1].strip() != '}':
        return entry_text
    last_field = lines[-2].rstrip()
    if last_field and not last_field.endswith(','):
        lines[-2] = last_field + ','
    lines.insert(-1, f'  {field_name} = {{{value}}},')
    return '\n'.join(lines)


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


def _exact_match(a, b):
    """Whitespace/case/punctuation-normalized exact comparison. Deliberately
    NOT fuzzy: deciding whether two differently-formatted-but-plausibly-equal
    values (a fuller name, a more complete author list) should be merged is a
    judgment call, not a string-distance threshold - live testing showed a
    fuzzy cutoff here just relocates the same failure mode this module
    already got burned by elsewhere. Only truly identical values are treated
    as "nothing to reconcile"; anything else is left for the caller's own
    AI-based reconciliation to actually decide."""
    norm = lambda s: re.sub(r'[^a-z0-9 ]', '', (s or '').lower()).strip()
    return norm(a) == norm(b)


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


# APA is used specifically because it lists EVERY author (not just the
# first) as "Last, Initials", making the author list far more reliable to
# extract than Chicago/MLA's mixed "Last, First, and First Last" format.
_APA_AUTHOR_RE = re.compile(r'([A-Z][\w\-]+),\s*((?:[A-Z]\.\s*)+)')
_APA_YEAR_RE = re.compile(r'\((\d{4})\)')
_APA_TITLE_RE = re.compile(r'\(\d{4}\)\.\s*(.+?)\.')
# Best-effort: an APA journal-article citation typically continues
# "Journal Name, Volume(Number), Pages." after the title - captured
# opportunistically. Verified live only against a BOOK citation (which has
# no such tail); this pattern for articles is inferred, not yet confirmed.
_APA_JOURNAL_RE = re.compile(r'\.\s*([^,]+),\s*(\d+)\s*\((\d+)\)\s*,\s*([\d\-–—]+)\.')


def parse_apa_citation(text):
    """Best-effort parse of an APA-style citation string into a fields dict."""
    if not text:
        return {}
    out = {}
    authors = [f'{fam}, {init.strip()}' for fam, init in _APA_AUTHOR_RE.findall(text)]
    if authors:
        out['author'] = ' and '.join(authors)
    year_m = _APA_YEAR_RE.search(text)
    if year_m:
        out['date'] = year_m.group(1)
    title_m = _APA_TITLE_RE.search(text)
    if title_m:
        out['title'] = title_m.group(1).strip()
    journal_m = _APA_JOURNAL_RE.search(text)
    if journal_m:
        out['journaltitle'] = journal_m.group(1).strip()
        out['volume'] = journal_m.group(2)
        out['number'] = journal_m.group(3)
        out['pages'] = _normalize_pages(journal_m.group(4))
    return out


def _chicago_author_text(snippet):
    """
    Extract the raw author segment from a Chicago-style citation snippet
    (everything before the quoted title, e.g. "Chua, Daniel KL, and
    Alexander Rehding" from '...Rehding. "Alien Listening..." (2021): ...').

    Deliberately returned as raw, unparsed text rather than split into a
    name list: Chicago/MLA's author-list format is inconsistent (first
    author "Last, First", every subsequent author "First Last", joined by
    "and") - genuinely harder to parse reliably than APA's uniform pattern,
    but it's exactly the kind of "reformat this into our target convention"
    task better suited to the reconciliation LLM call (which already has the
    project's name-format guidelines) than to another bespoke regex parser
    here that would just relocate the risk this module already got burned by.
    """
    if not snippet:
        return None
    m = re.search(r'["“]', snippet)
    if not m:
        m = re.search(r'\(\d{4}\)', snippet)
    author_part = snippet[:m.start()] if m else snippet
    return author_part.rstrip('. ').strip() or None


def scrapingdog_cite_fields(result_id, api_key, timeout=20):
    """
    Fetch the Cite API entry for a Scholar result and parse it into a fields
    dict: date/title/journal-shaped fields from the APA citation (reliable,
    uniform format), author from the Chicago citation (fuller given names
    than APA's initials-only convention, and this project's own style).

    NOTE: the Cite API's own 'links' (BibTeX/EndNote/RefMan/RefWorks) point
    directly at scholar.googleusercontent.com, which - confirmed via a live
    call, not assumed - blocks direct (non-browser) requests with a
    429/CAPTCHA response even when reached through a valid ScrapingDog
    result. So this deliberately does NOT follow those links; it parses the
    `citations` array the Cite endpoint itself returns, which IS reliably
    served through ScrapingDog's proxy.
    """
    try:
        resp = requests.get(
            "https://api.scrapingdog.com/google_scholar/cite",
            params={'api_key': api_key, 'query': result_id},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
    except requests.RequestException:
        return {}

    fields = {}
    for citation in data.get('citations', []):
        label = (citation.get('title') or '').strip().lower()
        if label == 'apa':
            fields.update(parse_apa_citation(citation.get('snippet', '')))
        elif label == 'chicago':
            author_text = _chicago_author_text(citation.get('snippet', ''))
            if author_text:
                fields['author'] = author_text  # supersedes APA's initials-only version
    return fields


def _crossref_names(name_list):
    """Format a CrossRef author/editor array as 'Family, Given' strings."""
    out = []
    for a in name_list or []:
        family = (a.get('family') or '').strip()
        given = (a.get('given') or '').strip()
        if family:
            out.append(f"{family}, {given}".rstrip(', '))
    return out


def crossref_authors(message):
    """Return author names as 'Family, Given' strings from a CrossRef work object."""
    return _crossref_names((message or {}).get('author'))


def crossref_editors(message):
    """Return editor names as 'Family, Given' strings from a CrossRef work object."""
    return _crossref_names((message or {}).get('editor'))


def crossref_date(message):
    """Return the best-available publication year as a string, or None."""
    for key in ('published-print', 'published-online', 'published'):
        parts = (message or {}).get(key, {}).get('date-parts')
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return None


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

    Returns (found_fields, field_sources):
    - found_fields only contains keys not already present in `fields` -
      existing values are never overwritten.
    - field_sources maps each found field name to the service that supplied
      it ('CrossRef' or 'Google Scholar'), so callers can report provenance
      per field rather than just an aggregate list of services used.
    """
    required, desired = missing_fields(entry_type, fields)
    if not required and not desired:
        return {}, {}

    found = {}
    field_sources = {}

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
                field_sources[k] = 'CrossRef'

    # Only these - matching what crossref_fields() also supplies above - are
    # in scope here; scrapingdog_cite_fields() returns author/title/date too,
    # but this function was never responsible for those and shouldn't start
    # touching them just because the parser now happens to return them.
    _SCHOLAR_ENRICHMENT_FIELDS = {'volume', 'number', 'pages', 'publisher', 'journaltitle'}

    still_required = [f for f in required if f not in found]
    if still_required and scrapingdog_api_key and title:
        # Combine title+author (when known) into one query and trust
        # Scholar's own top result, rather than re-ranking candidates by
        # title-similarity to `title` ourselves: live testing showed that
        # heuristic can score an unrelated result HIGHER than the correct
        # one (0.65 vs 0.41 for a real case), i.e. it's actively unreliable,
        # not just imprecise - see verify_recollection() for the same fix.
        scholar_query = f"{title} {author_surname}" if author_surname else title
        results = scrapingdog_search(scholar_query, scrapingdog_api_key)
        top = results[0] if results else None
        if top and top.get('id'):
            scholar_found = scrapingdog_cite_fields(top['id'], scrapingdog_api_key)
            for k, v in scholar_found.items():
                if k in _SCHOLAR_ENRICHMENT_FIELDS and not fields.get(k) and k not in found:
                    found[k] = v
                    field_sources[k] = 'Google Scholar'

    return found, field_sources


# Fields we know how to compare against a verified external record. 'author'
# and 'editor' are compared against a list of names (any-agreement); the rest
# are simple string comparisons.
_LIST_FIELDS = {'author', 'editor'}
_COMPARABLE_FIELDS = _LIST_FIELDS | {'title', 'date', 'publisher', 'location'}

# Container-level fields worth filling from a work-level match even when they
# weren't flagged, as long as the entry doesn't already have them - e.g. the
# Editor of an edited collection is a distinct field from the chapter's own
# Author, and outside of this check nothing else in the pipeline attempts to
# source it at all. 'date' is included because a chapter excerpt often has no
# reliable date anywhere in its own text/metadata, but the containing book's
# publication date is exactly the kind of fact a work-level lookup can supply.
_CONTAINER_FIELDS = {'editor', 'series', 'publisher', 'location', 'date'}


def container_fields_missing(fields):
    """Which _CONTAINER_FIELDS the entry doesn't already have a value for."""
    return [f for f in _CONTAINER_FIELDS if not fields.get(f)]


def verify_recollection(work_title, flagged_fields, entry_fields, year=None,
                         crossref_email=None, scrapingdog_api_key=None, log=None):
    """
    Attempt to confirm or refute entry fields Claude flagged as sourced from
    its own background knowledge of the work rather than the PDF text/metadata,
    and separately fill in a few container-level fields (Editor, Series,
    Publisher, Location) from the same lookup when the entry doesn't already
    have them - useful for e.g. an edited collection's Editor, which is
    otherwise never sourced anywhere else in this pipeline.

    Tries CrossRef first, searching by `work_title` alone (deliberately NOT
    gated on the entry's claimed author - that's exactly the field that may
    be wrong). Falls back to a Google Scholar search on `work_title`,
    optionally combined with the entry's claimed author IF that author isn't
    itself one of `flagged_fields` (only a trusted, already-grounded author
    is used to sharpen the query - an unverified one would bias the search
    toward confirming the very guess we're trying to check independently).

    An earlier version of this function tried a verbatim-quote phrase search
    instead. Dropped after live testing found it unreliable: a fairly
    generic-sounding sentence from the PDF matched two different, definitely
    wrong books across two runs. It also tried re-ranking Scholar's results
    by title-similarity to `work_title`; also dropped after live testing
    showed an unrelated result could score HIGHER on that similarity metric
    than the correct one (0.65 vs 0.41) - so Scholar's own search relevance
    ranking (particularly title+author combined) is trusted directly, taking
    its top result, rather than re-ranked by a heuristic proven unreliable.

    `log`, if given, is called with short human-readable strings describing
    what was searched and what each source returned, for transparency into
    what would otherwise be a black-box "could not verify" outcome.

    Returns (reconcile_candidates, additions, unresolved):
    - reconcile_candidates: [{field, claimed, verified, source}] for flagged
      fields whose claimed value differs from a confirmed external record.
      Deliberately NOT auto-resolved here: "differs" covers both a flat
      contradiction (wrong author) and a compatible-but-incomplete claim (a
      partial author list missing a co-author), and a similarity threshold
      can't reliably tell those apart - that's a judgment call, left to the
      caller's own AI-based reconciliation step rather than decided by an
      arbitrary string-distance cutoff here.
    - additions: {field: value} for _CONTAINER_FIELDS the entry is missing
      entirely, filled from the same matched record. Unlike overrides, there
      is nothing to reconcile against here, so these are applied directly.
    - unresolved: True if at least one flagged/comparable field has no
      verified value to compare against at all - either because no matching
      record was found via either source, or because a record WAS found but
      simply doesn't cover that particular field (e.g. an edited collection's
      record may list an editor but no author). The caller should treat it
      as "unverified, review manually" rather than "confirmed".
    """
    log = log or (lambda msg: None)
    # Author/editor completeness is checked opportunistically whenever a
    # search runs at all, not only when flagged - a claimed author can be
    # genuinely grounded in the PDF's own text/metadata (so the audit won't
    # flag it) and still be incomplete (e.g. missing a co-author the PDF
    # simply never names), and we already have the verified list in hand
    # once the search runs for any reason.
    relevant = set(f for f in flagged_fields if f in _COMPARABLE_FIELDS) | _LIST_FIELDS

    verified_lists = {'author': None, 'editor': None}
    verified = {}
    source = None

    if work_title:
        log(f"Searching CrossRef for work title: \"{work_title}\"")
        message = crossref_by_biblio(work_title, author_surname=None, year=year, mailto=crossref_email)
    else:
        log("No work-level title available - skipping CrossRef")
        message = None

    if message:
        source = 'CrossRef'
        found_title = (message.get('title') or [''])[0]
        log(f"CrossRef match: \"{found_title}\"")
        verified_lists['author'] = crossref_authors(message)
        verified_lists['editor'] = crossref_editors(message)
        verified['title'] = found_title or None
        verified['date'] = crossref_date(message)
        verified['publisher'] = message.get('publisher')
    else:
        if work_title:
            log("CrossRef: no match")
        if not work_title:
            log("No work-level title available - skipping Google Scholar")
        elif not scrapingdog_api_key:
            log("No ScrapingDog API key configured - skipping Google Scholar")
        else:
            # Only include the claimed author when it's NOT itself one of the
            # flagged fields - otherwise the search would be biased toward
            # confirming the very guess it's supposed to check independently.
            scholar_query = work_title
            if 'author' not in flagged_fields and entry_fields.get('author'):
                scholar_query = f"{work_title} {strip_latex(entry_fields['author'])}"
            log(f"Searching Google Scholar for: \"{scholar_query}\"")
            results = scrapingdog_search(scholar_query, scrapingdog_api_key)
            log(f"Google Scholar returned {len(results)} result(s)")
            top = results[0] if results else None
            scholar_fields = scrapingdog_cite_fields(top['id'], scrapingdog_api_key) if top and top.get('id') else {}
            if scholar_fields:
                source = 'Google Scholar'
                log(f"Google Scholar Cite match: \"{scholar_fields.get('title', '?')}\"")
                if scholar_fields.get('author'):
                    # Kept as one raw (Chicago-style) string rather than split
                    # into individual names here - see _chicago_author_text().
                    verified_lists['author'] = [scholar_fields['author']]
                verified['title'] = scholar_fields.get('title')
                verified['date'] = scholar_fields.get('date')
                verified['publisher'] = scholar_fields.get('publisher')
                # Note: editor/series/location are not reliably present in
                # Scholar's APA citation strings - only CrossRef supplies
                # those with any regularity.
            elif top:
                log("Google Scholar: top result had no usable Cite data")

    if not any(verified_lists.values()) and not any(verified.values()):
        return [], {}, True  # neither source found any matching record for this work

    reconcile_candidates = []
    unconfirmed = []
    for field in relevant:
        claimed = entry_fields.get(field, '')
        if field in _LIST_FIELDS:
            names = verified_lists.get(field)
            if not names:
                # A record was found, but it doesn't cover this specific
                # field (e.g. an edited collection's record has an editor
                # list but no author list) - that's still "can't confirm or
                # refute", not "confirmed", so it must count toward unresolved.
                unconfirmed.append(field)
                continue
            verified_value = ' and '.join(names)
            if not _exact_match(claimed, verified_value):
                reconcile_candidates.append({
                    'field': field, 'claimed': claimed, 'verified': verified_value, 'source': source,
                })
        else:
            verified_value = verified.get(field)
            if not verified_value:
                unconfirmed.append(field)
                continue
            if not _exact_match(claimed, verified_value):
                reconcile_candidates.append({
                    'field': field, 'claimed': claimed, 'verified': verified_value, 'source': source,
                })

    additions = {}
    for field in _CONTAINER_FIELDS:
        if entry_fields.get(field):
            continue  # already present - this is a fill, not a correction
        if field in _LIST_FIELDS:
            names = verified_lists.get(field)
            if names:
                additions[field] = ' and '.join(names)
        else:
            value = verified.get(field)
            if value:
                additions[field] = value

    return reconcile_candidates, additions, bool(unconfirmed)
