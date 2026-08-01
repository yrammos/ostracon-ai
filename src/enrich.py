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


def join_subtitle(main, sub):
    """Recombine a split title for comparison against external records.

    Entries store colon-separated titles across Title/Subtitle (biblatex
    supplies the colon at render time), but CrossRef and Google Scholar both
    return the whole title as one string. Comparing a bare main title against
    a full external title would otherwise read as a mismatch - and worse, as a
    *completion* ("Recent Schenker" is a substring of "Recent Schenker: The
    Poetic Power of..."), which would let auto-reconciliation overwrite Title
    with the recombined form and silently undo the split.
    """
    main = (main or '').strip()
    sub = (sub or '').strip()
    if main and sub:
        return f"{main}: {sub}"
    return main or sub


def work_level_title(entry_type, fields):
    """
    The title to use when verifying the whole *work* this entry belongs to,
    as opposed to a possibly too-generic chapter/article title - e.g. a
    chapter titled "Introduction" or "Manifesto" is useless to search on, but
    the book it's part of is usually far more distinctive.

    Subtitles are rejoined so external searches see the full title the
    indexes actually hold, and LaTeX markup is stripped: the value goes
    straight into a CrossRef/Scholar query and into the title-similarity gate
    that vets the results, and neither has any use for it. A German book
    searched as "\\foreignlanguage{ngerman}{Die Romanzen Robert Schumanns}"
    missed in CrossRef and only matched in Scholar on the strength of the
    author; the similarity gate meanwhile scored the wrapper's own letters as
    part of the title.
    """
    entry_type = (entry_type or '').lower()
    if entry_type in ('incollection', 'inbook', 'inproceedings'):
        booktitle = join_subtitle(fields.get('booktitle'), fields.get('booksubtitle'))
        if booktitle:
            return strip_latex(booktitle)
    return strip_latex(join_subtitle(fields.get('title'), fields.get('subtitle')))


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

    # Match the surrounding entry's indentation and field-name casing rather
    # than hardcoding two spaces and lowercase - an appended field otherwise
    # stands out against Claude's tab-indented, Capitalised output. Judge the
    # style from the FIRST field: the last one is typically date-modified,
    # which is lowercase by BibDesk convention and would misreport the entry.
    indent = re.match(r'\s*', lines[-2]).group(0) or '\t'
    for line in lines[1:-1]:
        m = re.match(r'\s*([A-Za-z][A-Za-z-]*)\s*=', line)
        if m and '-' not in m.group(1):        # skip date-added / bdsk-* etc.
            if m.group(1)[:1].isupper():
                field_name = field_name[:1].upper() + field_name[1:]
            break

    lines.insert(-1, f'{indent}{field_name} = {{{value}}},')
    return '\n'.join(lines)


def remove_field(entry_text, field_name):
    """
    Remove a top-level field and its trailing comma from a BibTeX entry,
    handling nested braces in its value (see set_field()). Returns the entry
    unchanged if the field isn't found. Fixes up the new last field's
    trailing comma if the removed field was the last one before the closing
    brace.
    """
    m = re.search(rf'(?im)^([ \t]*{re.escape(field_name)}\s*=\s*)', entry_text)
    if not m:
        return entry_text

    value_start = m.end()
    if value_start >= len(entry_text) or entry_text[value_start] != '{':
        return entry_text

    depth = 0
    i = value_start
    while i < len(entry_text):
        if entry_text[i] == '{':
            depth += 1
        elif entry_text[i] == '}':
            depth -= 1
            if depth == 0:
                break
        i += 1
    end = i + 1
    if end < len(entry_text) and entry_text[end] == ',':
        end += 1
    newline_pos = entry_text.find('\n', end)
    end = newline_pos + 1 if newline_pos != -1 else len(entry_text)

    line_start = entry_text.rfind('\n', 0, m.start()) + 1
    lines = (entry_text[:line_start] + entry_text[end:]).split('\n')

    # If the removed field was the last one before the closing brace, the
    # new last field may now have a dangling trailing comma - strip it.
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].strip() == '}':
            for prev in range(idx - 1, -1, -1):
                if lines[prev].strip():
                    if lines[prev].rstrip().endswith(','):
                        lines[prev] = lines[prev].rstrip()[:-1]
                    break
            break

    return '\n'.join(lines)


FORBIDDEN_FIELDS_ALWAYS = {'issn', 'isbn', 'keywords', 'reference', 'devonthink'}


def strip_forbidden_fields(entry_text):
    """
    Remove fields CLAUDE.md forbids from a raw BibLaTeX entry, structurally -
    the initial extraction prompt already asks Claude not to include these,
    but doesn't reliably follow through (e.g. a PDF that's actually a
    printout of an online-only page can get typed as something other than
    @Online, or a PDF whose own body text states a URL can still get a Url
    field despite the instruction against it).

    Url/Urldate are kept only when either is true - regardless of whether
    the entry was sourced from a PDF or a webloc:
    - the entry is typed @Online, the one type with no other locator to
      fall back on; or
    - the entry has no Date, in which case Urldate is the only dating
      evidence available and Url its necessary companion.
    Otherwise both are stripped.

    Returns (entry_text, stripped_field_names).
    """
    entry_type = get_entry_type(entry_text)
    fields = parse_bibtex_fields(entry_text)

    to_strip = [f for f in FORBIDDEN_FIELDS_ALWAYS if fields.get(f)]
    # An online reference work is the third case that must keep its locator.
    # The package is explicit that these "need not only a url but also,
    # always, a urldate (instead of a date), as these sources are in constant
    # flux" - so a dated @Inreference/@Reference carrying entrysubtype=online
    # would otherwise be stripped of exactly the fields it requires.
    online_reference = (entry_type in ('inreference', 'reference')
                        and fields.get('entrysubtype', '').strip().lower() == 'online')
    keep_url = entry_type == 'online' or online_reference or not fields.get('date')
    if not keep_url:
        to_strip += [f for f in ('url', 'urldate') if fields.get(f)]

    for field in to_strip:
        entry_text = remove_field(entry_text, field)

    return entry_text, to_strip


# Macros whose FIRST brace group is a parameter rather than content. The
# generic rule below unwraps every group it sees, which on \foreignlanguage
# leaves the language name welded to the front of the title - "Die Romanzen
# Robert Schumanns" searched as "ngermanDie Romanzen Robert Schumanns". These
# have to be handled before it runs.
_PARAM_FIRST_MACROS = ('foreignlanguage', 'hyphenation')


def _matching_brace(text, start):
    """Index of the `}` closing the `{` at `start`, or None if unbalanced."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
    return None


def _drop_first_arg(text, macro):
    r"""Rewrite \macro{param}{content} to just `content`, brace-aware.

    Brace-aware rather than a regex because the content group nests:
    \foreignlanguage{french}{\mkbibquote{Le Sacre}} is routine here.
    """
    token = '\\' + macro
    out, i = [], 0
    while True:
        j = text.find(token, i)
        if j == -1:
            out.append(text[i:])
            return ''.join(out)
        out.append(text[i:j])
        k = j + len(token)
        while k < len(text) and text[k].isspace():
            k += 1
        end1 = _matching_brace(text, k) if k < len(text) and text[k] == '{' else None
        if end1 is None:                      # not the shape we expected
            out.append(text[j:k or len(text)])
            i = max(k, j + len(token))
            continue
        m = end1 + 1
        while m < len(text) and text[m].isspace():
            m += 1
        if m >= len(text) or text[m] != '{':
            # Only the parameter group is present. Drop it rather than falling
            # back to treating it as content: for these macros group one is a
            # language name, never text, and emitting it is the exact noise
            # this function exists to remove.
            i = end1 + 1
            continue
        end2 = _matching_brace(text, m)
        if end2 is None:                      # unbalanced - keep what follows
            out.append(text[m + 1:])
            return ''.join(out)
        out.append(text[m + 1:end2])
        i = end2 + 1


def strip_latex(text):
    """Strip LaTeX markup from a field value for use in external search queries."""
    if not text:
        return text
    prev = None
    while prev != text:
        prev = text
        for macro in _PARAM_FIRST_MACROS:
            text = _drop_first_arg(text, macro)
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
    surname and (roughly) publication year when those are known.

    Callers are expected to pass plain text, but strip defensively here too:
    every value in this module's reach comes from a BibLaTeX field, and one
    unstripped path (work_level_title) already shipped once. strip_latex is
    idempotent, so a caller that has stripped pays nothing."""
    if not title:
        return None
    title = strip_latex(title)
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
    # Stripped defensively for the same reason as crossref_by_biblio().
    query = strip_latex(query)
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
            # CrossRef reports a publisher for journal articles too, but Chicago
            # doesn't carry one outside book-like types - merging it produced a
            # spurious `Publisher = {Public Library of Science (PLoS)}` on an
            # @article. Same rule as container_fields_missing().
            if k == 'publisher' and (entry_type or '').lower() not in _BOOKLIKE:
                continue
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


def _split_name_list(value):
    """Split a bibtex name list ('Last, First and Last2, First2') into
    {lastname_lower: firstname} pairs. Entries without a comma are skipped."""
    names = {}
    for part in value.split(' and '):
        part = part.strip()
        if ',' not in part:
            continue
        last, first = part.split(',', 1)
        names[last.strip().lower()] = first.strip()
    return names


def _is_name_completion(claimed, verified):
    """True only if `verified` adds detail to `claimed` - a missing
    co-author, or a fuller/spelled-out first name for a surname both share -
    never a surname claimed doesn't share with verified. That distinction is
    exactly what a bad fuzzy match (typically from Google Scholar) fails:
    it substitutes an unrelated person's name rather than completing one
    that's genuinely there."""
    claimed_names = _split_name_list(claimed)
    verified_names = _split_name_list(verified)
    if not claimed_names:
        return False
    for last, first in claimed_names.items():
        if last not in verified_names:
            return False
        v_first = verified_names[last]
        c_first = first.rstrip('.').strip()
        if c_first and not v_first.lower().startswith(c_first.lower()):
            return False
    return True


def _is_completion(field, claimed, verified):
    """Deterministic (non-LLM) check that `verified` merely completes
    `claimed` - adds detail - rather than contradicting it. This is the only
    case auto-reconciliation is allowed to apply (see verify_recollection());
    a genuine contradiction is always left for manual review instead, no
    matter how confident an external match looks."""
    if field in _LIST_FIELDS:
        return _is_name_completion(claimed, verified)
    claimed_norm = strip_latex(claimed).strip().lower()
    verified_norm = strip_latex(verified).strip().lower()
    return bool(claimed_norm) and claimed_norm in verified_norm


# Container-level fields worth filling from a work-level match even when they
# weren't flagged, as long as the entry doesn't already have them - e.g. the
# Editor of an edited collection is a distinct field from the chapter's own
# Author, and outside of this check nothing else in the pipeline attempts to
# source it at all. 'date' is included because a chapter excerpt often has no
# reliable date anywhere in its own text/metadata, but the containing book's
# publication date is exactly the kind of fact a work-level lookup can supply.
#
# 'series' and 'location' are deliberately excluded, even though they're also
# container-level facts: neither CrossRef nor Google Scholar's Cite API
# reliably supplies them (confirmed empirically - CrossRef's `publisher-location`
# is unpopulated even for well-cataloged book DOIs), so including them here
# would only trigger a lookup that can never actually fill them.
_CONTAINER_FIELDS = {'editor', 'publisher', 'date'}

# ...but only where the field belongs to that entry type. Filling these blindly
# produced real errors: a journal article picked up `publisher` (Chicago
# articles don't carry one) and `editor` (CrossRef reports the handling editor
# who accepted the paper, not a bibliographic editor). Both are wrong on an
# @article and neither would be obvious in a finished bibliography. 'date' is
# universal; the other two apply only to book-like types.
_BOOKLIKE = {
    'book', 'mvbook', 'inbook', 'bookinbook', 'suppbook',
    'collection', 'mvcollection', 'incollection', 'suppcollection',
    'proceedings', 'mvproceedings', 'inproceedings',
    'reference', 'mvreference', 'inreference',
}


def container_fields_missing(fields, entry_type=None):
    """Which _CONTAINER_FIELDS this entry lacks and could meaningfully take."""
    applicable = set(_CONTAINER_FIELDS)
    if (entry_type or '').lower() not in _BOOKLIKE:
        applicable -= {'editor', 'publisher'}
    return [f for f in applicable if not fields.get(f)]


def verify_recollection(work_title, flagged_fields, entry_fields, year=None,
                         crossref_email=None, scrapingdog_api_key=None, log=None):
    """
    Attempt to confirm or refute entry fields Claude flagged as sourced from
    its own background knowledge of the work rather than the PDF text/metadata,
    and separately fill in a few container-level fields (Editor, Publisher,
    Date) from the same lookup when the entry doesn't already have them -
    useful for e.g. an edited collection's Editor, which is otherwise never
    sourced anywhere else in this pipeline.

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
      fields whose claimed value differs from a confirmed external record,
      pre-vetted (via _is_completion(), see _maybe_reconcile() above) as safe
      to auto-apply: the source must be CrossRef (DOI-keyed, high trust), and
      the verified value must provably just complete the claimed one (add a
      missing co-author, spell out an initial) rather than contradict it.
      Anything that doesn't clear that bar - a genuine contradiction, or any
      Google Scholar-sourced conflict regardless of how it looks - is routed
      into `unconfirmed`/`unresolved` instead, never silently applied. This
      is a deterministic, code-level gate rather than an LLM judgment call:
      two real cases (both Scholar-sourced) had the LLM-judgment version of
      this decision get it wrong, overwriting a correct author with an
      unrelated person's name from a bad fuzzy match.
    - additions: {field: value} for _CONTAINER_FIELDS the entry is missing
      entirely, filled from the same matched record. Unlike overrides, there
      is nothing to reconcile against here, so these are applied directly.
    - unresolved: True if at least one flagged/comparable field has no
      verified value to compare against at all, or was rejected by the
      reconciliation gate above - either because no matching record was
      found via either source, a record WAS found but doesn't cover that
      field, or its value didn't pass the completion/source check. The
      caller should treat it as "unverified, review manually" rather than
      "confirmed".
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

    def _maybe_reconcile(field, claimed, verified_value):
        # Auto-reconciliation is only ever allowed for a CrossRef-sourced
        # (DOI-keyed, high-trust) value that provably just completes the
        # claimed one (see _is_completion()) - a genuine contradiction, or
        # anything from Google Scholar's fuzzy title/author search, is left
        # unresolved for manual review instead of risking a silent, wrong
        # override. Two real cases (a Scholar mismatch on an author name)
        # showed the LLM-judgment version of this check isn't reliable
        # enough on its own.
        if source == 'CrossRef' and _is_completion(field, claimed, verified_value):
            reconcile_candidates.append({
                'field': field, 'claimed': claimed, 'verified': verified_value, 'source': source,
            })
        else:
            unconfirmed.append(field)

    for field in relevant:
        claimed = entry_fields.get(field, '')
        if field == 'title':
            # Compare the recombined Title+Subtitle against the external
            # record's single full-title string - see join_subtitle().
            claimed = join_subtitle(claimed, entry_fields.get('subtitle'))
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
                _maybe_reconcile(field, claimed, verified_value)
        else:
            verified_value = verified.get(field)
            if not verified_value:
                unconfirmed.append(field)
                continue
            if not _exact_match(claimed, verified_value):
                _maybe_reconcile(field, claimed, verified_value)

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
