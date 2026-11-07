"""A new entry matched against the maintainer's library (library_bib_file).

Three uses, all deterministic and offline:

- duplicates(): the same work already in the library (DOI, or title + first
  author's surname + year). Logged, never coloured.
- container_fill(): a part whose container is already in the library - as
  another part of the same type with the same container title, or as the
  container's own entry of its paired type (PAIRS: incollection/collection,
  inproceedings/proceedings, inbook/book, inreference/reference) - inherits
  the container's fields in the library's form: gaps filled, a value from a
  search or unconfirmed recollection replaced, a value from the source kept.
  Inheritance runs both ways: a new container inherits from its parts, and a
  supplement (suppbook, suppcollection) and its book from each other, whichever
  is new (SUPP_PAIRS, REVERSE_PAIRS, CONTAINER_TYPES). An article takes only
  its journal's spelling. Gated: same container title,
  same year (an online reference work: both online), and no disagreement on
  volume, editor, maintitle or edition; a field the parts disagree on, or most
  of them lack, is left alone.
- exemplars(): up to three library entries, for the extraction prompt (opt-in:
  library_exemplars): the item's container's other items, then the decided
  type's most fully populated entries, one per author. Only fields biblatex or
  biblatex-chicago defines are shown.

The library is read once per process and re-read when the file changes.
`exclude` keys are never matched: the eval harness passes its sample's
citekeys, which are library entries themselves.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

import bib_audit
from house_style import _key

# A chapter's container fields, as a sibling chapter writes them.
SIBLING_FIELDS = ('booktitle', 'booksubtitle', 'booktitleaddon', 'editor', 'editora',
                  'editorb', 'editortype', 'editoratype', 'editorbtype', 'publisher',
                  'location', 'series', 'number', 'volume', 'volumes', 'maintitle',
                  'mainsubtitle', 'maintitleaddon', 'edition')
# The book's own entry: its field -> the chapter's field.
PARENT_MAP = {'title': 'booktitle', 'subtitle': 'booksubtitle', 'titleaddon': 'booktitleaddon',
              'editor': 'editor', 'editora': 'editora', 'editorb': 'editorb',
              'editortype': 'editortype', 'editoratype': 'editoratype',
              'editorbtype': 'editorbtype', 'publisher': 'publisher', 'location': 'location',
              'series': 'series', 'number': 'number', 'volume': 'volume', 'volumes': 'volumes',
              'maintitle': 'maintitle', 'mainsubtitle': 'mainsubtitle',
              'maintitleaddon': 'maintitleaddon', 'edition': 'edition'}
# Each part type and the container types whose own entry it may inherit from.
PAIRS = {'incollection': {'collection', 'mvcollection'},
         'inproceedings': {'proceedings', 'mvproceedings'},
         'inbook': {'book', 'mvbook'},
         'bookinbook': {'book', 'mvbook'},
         'inreference': {'reference', 'mvreference'}}
# A supplement (an introduction, preface, glossary or liner notes) keeps its
# book in Title, as biblatex-chicago has it, and the book's author in
# Bookauthor. It inherits the book's own fields from the book's entry, and the
# inheritance runs both ways (maintainer's ruling, 2026-11-05): a supplement
# is sometimes entered months before its book, so a new book inherits the same
# fields from its supplements already in the library.
SUPP_PAIRS = {'suppbook': {'book', 'mvbook', 'collection', 'mvcollection'},
              'suppcollection': {'collection', 'mvcollection'}}
REVERSE_PAIRS = {'book': {'suppbook'}, 'mvbook': {'suppbook'},
                 'collection': {'suppbook', 'suppcollection'},
                 'mvcollection': {'suppbook', 'suppcollection'}}
# The book's fields as a supplement writes them. Editor goes only to and from a
# suppcollection: a suppbook's Editor is its own (a performer, say).
BOOK_FIELDS = ('subtitle', 'translator', 'publisher', 'location', 'series', 'number',
               'volumes', 'maintitle', 'mainsubtitle', 'maintitleaddon', 'edition',
               'origlanguage')
EDITOR_FIELDS = ('editor', 'editortype', 'editora', 'editoratype', 'editorb', 'editorbtype')
ROLE_NAMES = {'editor', 'editora', 'editorb'}
CHILD_TYPES = set(PAIRS)
SUPP_TYPES = set(SUPP_PAIRS)
# Inheritance runs both ways for every part-whole pair (maintainer's ruling,
# 2026-11-05): a new container inherits from its parts already in the library,
# the part's fields under the container's names.
CONTAINER_TYPES = set().union(*PAIRS.values()) | set(REVERSE_PAIRS)
CHILD_MAP = {v: k for k, v in PARENT_MAP.items() if k != 'title'}
# An entry in a reference work keeps the work in Title (biblatex-chicago,
# with its own heading in Lista), so it inherits the work's fields under their
# own names; its Volume is the article's, not the work's.
REFERENCE_FIELDS = ('editor', 'editora', 'editorb', 'editortype', 'editoratype',
                    'editorbtype', 'publisher', 'location', 'series', 'number',
                    'volumes', 'maintitle', 'mainsubtitle', 'maintitleaddon', 'edition')
# Never shown to the extraction: BibDesk bookkeeping, paths, private notes.
PRIVATE = re.compile(r'^(bdsk-|date-added$|date-modified$|annote$|keywords$|file$|abstract$|'
                     r'read$|rating$|devonthink$)')
_CACHE: dict = {}
_BRACKET = re.compile(r'\s*\([^)]*\)\s*$')


def _ckey(value):
    """A container title's key for finding related entries: _key() without
    bracketed segments, so 'Proceedings of the 20th ... Retrieval (ISMIR)
    Conference' and '... Retrieval Conference' meet (#121). Measured on the
    whole library (2026-11-07): 5 more chapters matched, 4 more fields right,
    6 more that differ from the entry, each a disagreement between library
    entries of the same proceedings. A 90%-similarity match added one chapter
    more and was not adopted. Journals too: a bracketed qualifier there
    ('... Pedagogy (Resources)') belongs in Journaltitleaddon."""
    return _key(re.sub(r'\s*\([^)]*\)', ' ', value or ''))
_FIELDS_MD = Path(__file__).resolve().parent.parent / 'prompt-context' / 'biblatex-chicago-fields.md'
_KNOWN: set = set()


def known_fields():
    """Field names biblatex or biblatex-chicago defines: the biblatex manual's
    data fields, the headings of the biblatex-chicago field reference, and
    Journal (house ruling). Anything else in a library entry - a reference
    manager's or a user's own bookkeeping - is never shown to the model."""
    if not _KNOWN:
        import type_fields
        _KNOWN.update(type_fields._tables()[1])
        try:
            text = _FIELDS_MD.read_text(encoding='utf-8')
        except OSError:
            text = ''
        _KNOWN.update(m.group(1).lower() for m in re.finditer(r'^### ([A-Za-z]+)\s*$', text, re.M))
        _KNOWN.add('journal')
    return _KNOWN


def _shown(key):
    return key in known_fields() and not PRIVATE.match(key)


class Lib:
    def __init__(self, entries):
        self.entries = entries                        # [(key, type, {field: value})]
        self.types = {k: t for k, t, _ in entries}
        self.by_container = collections.defaultdict(list)
        self.by_title = collections.defaultdict(list)
        self.by_journal = collections.defaultdict(list)
        self.by_doi = collections.defaultdict(list)
        for i, (_k, t, f) in enumerate(entries):
            if f.get('booktitle'):
                self.by_container[_ckey(f['booktitle'])].append(i)
            if f.get('title'):
                self.by_title[_ckey(f['title'])].append(i)
            j = f.get('journal') or f.get('journaltitle')
            if j:
                self.by_journal[_ckey(j)].append(i)
            if f.get('doi'):
                self.by_doi[f['doi'].strip().lower()].append(i)


def load(path):
    """The library's Lib, or None when no library is configured or readable."""
    if not path:
        return None
    p = Path(path).expanduser()
    try:
        stamp = p.stat().st_mtime
    except OSError:
        return None
    cached = _CACHE.get(str(p))
    if cached and cached[0] == stamp:
        return cached[1]
    text = p.read_text(encoding='utf-8', errors='replace')
    entries, _ = bib_audit.scan(text)
    lib = Lib([(e.citekey, e.etype, {f.key: f.value.strip() for f in e.fields if f.value.strip()})
               for e in entries])
    lib.text = text
    lib.spans = {e.citekey: e.span for e in entries}
    _CACHE[str(p)] = (stamp, lib)
    return lib


def _year(fields):
    m = re.search(r'\b(\d{4})\b', fields.get('date') or fields.get('year') or '')
    return m.group(1) if m else None


def _surnames(names):
    """Surnames of a BibTeX name list, as identity keys."""
    out = []
    for n in re.split(r'\s+and\s+', bib_audit.unwrap(names or '')):
        n = n.strip()
        if not n:
            continue
        last = n.split(',')[0] if ',' in n else n.split()[-1]
        out.append(_key(last))
    return out


def _fields(entry_text):
    import enrich
    return {k.lower(): v.strip() for k, v in enrich.parse_bibtex_fields(entry_text).items()
            if v.strip()}


def _title_key(f):
    """The work's identity by title: for an entry in a reference work, its
    heading (Lista) too; for a supplement, its kind (Type) too, since both
    keep the containing work in Title."""
    return _key(' '.join(f.get(x, '') for x in ('title', 'subtitle', 'lista', 'type')))


def duplicates(entry_text, lib, exclude=()):
    """Citekeys of library entries that are this work."""
    if lib is None:
        return []
    f = _fields(entry_text)
    hits = []
    # A chapter often carries its book's DOI, so a shared DOI makes a
    # duplicate only where the titles agree too.
    doi = f.get('doi', '').lower()
    if doi:
        hits += [lib.entries[i][0] for i in lib.by_doi.get(doi, [])
                 if (lib.entries[i][1] in SUPP_TYPES) == (_etype(entry_text) in SUPP_TYPES)
                 and (not _title_key(f) or _title_key(lib.entries[i][2]) == _title_key(f))]
    t, y = f.get('title'), _year(f) or (f.get('urldate') or '')[:4] or None
    first = (_surnames(f.get('author') or f.get('editor')) or [None])[0]
    if t and y and first:
        tk = _title_key(f)
        for i in lib.by_title.get(_ckey(t), []):
            k, _ty, g = lib.entries[i]
            if ((_ty in SUPP_TYPES) == (_etype(entry_text) in SUPP_TYPES)
                    and _title_key(g) == tk and (_year(g) or (g.get('urldate') or '')[:4]) == y
                    and first in _surnames(g.get('author') or g.get('editor'))):
                hits.append(k)
    seen = set(exclude)
    return [k for k in hits if not (k in seen or seen.add(k))]


def _agree(f, g, field):
    """Neither has the field, only one has it, or both say the same."""
    a, b = f.get(field), g.get(field)
    return not a or not b or _key(a) == _key(b)


def _online(f):
    return (f.get('entrysubtype') or '').strip().lower() == 'online'


def container(entry_text):
    """(etype, the title that links it to related library entries), else
    (etype, None): a part's Booktitle; Title for an entry in a reference work,
    a supplement, or a book that supplements may belong to."""
    f = _fields(entry_text)
    etype = _etype(entry_text)
    if etype in CHILD_TYPES:
        return etype, f.get('title' if etype == 'inreference' else 'booktitle')
    if etype in SUPP_TYPES or etype in CONTAINER_TYPES:
        return etype, f.get('title')
    return etype, None


def inherited_fields(etype):
    """The fields an entry of this type may inherit from the library."""
    if etype == 'inreference':
        return REFERENCE_FIELDS
    if etype in CHILD_TYPES:
        return SIBLING_FIELDS
    if etype == 'suppbook':
        return BOOK_FIELDS + ('bookauthor',)
    if etype == 'suppcollection':
        return BOOK_FIELDS + EDITOR_FIELDS
    if etype in ('reference', 'mvreference'):
        return REFERENCE_FIELDS
    if etype in CONTAINER_TYPES:
        names = tuple(x for x in CHILD_MAP.values() if x not in BOOK_FIELDS + EDITOR_FIELDS)
        return BOOK_FIELDS + EDITOR_FIELDS + names + (('author',) if etype in ('book', 'mvbook') else ())
    return ()


def _book_side(etype, g_type, g):
    """{field: value} a supplement or book takes from a related entry g: the
    book's fields under the supplement's names, or the reverse."""
    out = {x: g[x] for x in BOOK_FIELDS if x in g}
    pair_has_editor = 'suppcollection' in (etype, g_type)
    if pair_has_editor:
        out.update({x: g[x] for x in EDITOR_FIELDS if x in g})
    if etype in SUPP_TYPES and g_type in SUPP_TYPES and 'bookauthor' in g:
        out['bookauthor'] = g['bookauthor']           # a sibling supplement
    elif etype in SUPP_TYPES and g_type in ('book', 'mvbook') and 'author' in g:
        out['bookauthor'] = g['author']               # the book's own entry
    elif etype in ('book', 'mvbook') and 'bookauthor' in g:
        out['author'] = g['bookauthor']               # the reverse
    return out


def _book_author(t, g):
    """The book's author as an entry of type t states it: a book's Author; a
    supplement's Bookauthor; an inbook chapter's Bookauthor, else its Author."""
    if t in ('book', 'mvbook'):
        return g.get('author')
    if t in SUPP_TYPES:
        return g.get('bookauthor')
    if t in ('inbook', 'bookinbook'):
        return g.get('bookauthor') or g.get('author')
    return None


def _same_author(etype, f, g_type, g):
    """Whether two related entries agree on the book's author, where both
    state it; a generic title (Collected Essays) recurs across authors."""
    mine, theirs = _book_author(etype, f), _book_author(g_type, g)
    if not mine or not theirs:
        return True
    return bool(set(_surnames(mine)) & set(_surnames(theirs)))


def _part_candidates(f, etype, lib, skip):
    """[(key, mapped, g)]: a new container's parts already in the library
    (chapters, papers, reference entries), their container fields under the
    container's own names."""
    parts = {p for p, cs in PAIRS.items() if etype in cs}
    name = f.get('title')
    found, seen = [], set()
    for key in {_ckey(name), _ckey(name + ' ' + f.get('subtitle', ''))}:
        pool = (lib.by_title.get(key, []) if 'inreference' in parts else []) + lib.by_container.get(key, [])
        for i in pool:
            k, t, g = lib.entries[i]
            if t not in parts or k in skip or k in seen:
                continue
            if t != 'inreference' and _ckey(g.get('booktitle', '')) != key and \
                    _ckey(g.get('booktitle', '') + ' ' + g.get('booksubtitle', '')) != key:
                continue
            seen.add(k)
            mapped = ({x: g[x] for x in REFERENCE_FIELDS if x in g} if t == 'inreference'
                      else {CHILD_MAP[x]: g[x] for x in CHILD_MAP if x in g})
            # A part's Volume is its own volume of a multivolume work; the
            # library's book entries often stand for the whole set. Not
            # inherited (measured 2026-11-05: 4 of 22 wrong fills).
            mapped.pop('volume', None)
            found.append((k, mapped, g))
    return found


def _supplement_candidates(f, etype, lib, skip):
    """[(key, mapped, g)]: a supplement's siblings (same type, same Title) and
    its book's entry; or a book's supplements already in the library."""
    name = f.get('title')
    keys = {_ckey(name), _ckey(name + ' ' + f.get('subtitle', ''))}
    related = SUPP_PAIRS[etype] | {etype} if etype in SUPP_TYPES else REVERSE_PAIRS.get(etype, set())
    if not related:
        return []
    found, seen = [], set()
    for key in keys:
        for i in lib.by_title.get(key, []):
            k, t, g = lib.entries[i]
            if t in related and k not in skip and k not in seen:
                seen.add(k)
                found.append((k, _book_side(etype, t, g), g))
    return found


def siblings(entry_text, lib, exclude=(), year_gate=True):
    """[(citekey, {field: value})]: the library's other parts of the same
    container, of the same type, and the container's own entry, of its paired
    type (PAIRS), that pass the gates. An online reference work, which carries
    no publication year, is gated on being online instead."""
    if lib is None:
        return []
    f = _fields(entry_text)
    etype, name = container(entry_text)
    if not name:
        return []
    if etype in SUPP_TYPES or etype in CONTAINER_TYPES:
        online = etype in ('reference', 'mvreference') and _online(f)
        if year_gate and not online and not _year(f):
            return []
        skip = set(exclude) | set(duplicates(entry_text, lib))
        found = _supplement_candidates(f, etype, lib, skip)
        if etype in CONTAINER_TYPES:
            found += _part_candidates(f, etype, lib, skip)
        g_type_of = lib.types
        out = []
        for k, mapped, g in found:
            if etype in ('reference', 'mvreference') and _online(g) != online:
                continue
            # Volume is never inherited in these directions, but it still
            # tells two volumes of one set apart (not for a set's own entry).
            if not etype.startswith('mv') and not _agree(f, g, 'volume'):
                continue
            # Same author: a generic title (Collected Essays) recurs across
            # authors. A book's Author is a supplement's or chapter's
            # Bookauthor, or the chapter's own Author when it has none.
            if not _same_author(etype, f, g_type_of[k], g):
                continue
            if year_gate and not online and _year(g) != _year(f):
                continue
            if not all(_agree(f, mapped, x) for x in
                       ('volume', 'edition', 'editor', 'maintitle', 'bookauthor', 'author')):
                continue
            out.append((k, mapped))
        return out
    reference = etype == 'inreference'
    dated = year_gate and not (reference and _online(f))
    if dated and not _year(f):
        return []
    skip = set(exclude) | set(duplicates(entry_text, lib))
    ck = _ckey(name)
    keys = {ck, _ckey(name + ' ' + f.get('subtitle' if reference else 'booksubtitle', ''))}
    found = []
    if reference:
        for i in lib.by_title.get(ck, []):
            k, t, g = lib.entries[i]
            if t == etype and k not in skip:
                found.append((k, {x: g[x] for x in REFERENCE_FIELDS if x in g}, g))
    else:
        for i in lib.by_container.get(ck, []):
            k, t, g = lib.entries[i]
            if t == etype and k not in skip:
                found.append((k, {x: g[x] for x in SIBLING_FIELDS if x in g}, g))
    seen = {k for k, _, _ in found}
    for key in keys:
        for i in lib.by_title.get(key, []):
            k, t, g = lib.entries[i]
            if t in PAIRS[etype] and k not in skip and k not in seen:
                seen.add(k)
                mapped = ({x: g[x] for x in REFERENCE_FIELDS if x in g} if reference
                          else {PARENT_MAP[x]: g[x] for x in PARENT_MAP if x in g})
                found.append((k, mapped, g))
    gates = ('editor', 'maintitle', 'edition') if reference else ('volume', 'editor', 'maintitle', 'edition')
    g_type_of = lib.types if etype in ('inbook', 'bookinbook') else {}
    out = []
    for k, mapped, g in found:
        if reference and _online(f) != _online(g):
            continue
        if g_type_of and not _same_author(etype, f, g_type_of[k], g):
            continue
        if dated and _year(g) != _year(f):
            continue
        if not all(_agree(f, mapped, x) for x in gates):
            continue
        out.append((k, mapped))
    return out


def _etype(entry_text):
    m = re.search(r'@(\w+)\s*\{', entry_text)
    return m.group(1).lower() if m else ''


def container_fill(entry_text, lib, allowed=None, replaceable=(), exclude=(), year_gate=True):
    """(entry_text, report). allowed: the fields the entry's type takes (None:
    any); replaceable: fields whose value came from a search, which the
    library outranks. report = {'siblings', 'added', 'replaced', 'snapped',
    'confirmed', 'differs', 'disagree', 'minority'}; a field in added,
    replaced, snapped or confirmed now holds the library's value."""
    import enrich
    report = {'siblings': [], 'added': [], 'replaced': [], 'snapped': [], 'confirmed': [],
              'differs': [], 'disagree': [], 'minority': []}
    if lib is None:
        return entry_text, report
    f = _fields(entry_text)
    etype = _etype(entry_text)
    if etype in ('article', 'review', 'suppperiodical'):
        return _journal_snap(entry_text, f, lib, exclude, report)
    sibs = siblings(entry_text, lib, exclude, year_gate)
    if not sibs:
        return entry_text, report
    report['siblings'] = [k for k, _ in sibs]
    values = collections.defaultdict(collections.Counter)
    for _k, m in sibs:
        for x, v in m.items():
            values[x][v] += 1
    # Each name list goes with its role (Editor with Editortype): both from
    # the library, or neither. A role the type list omits rides on its name.
    def unit(x):
        return x[:-4] if x.endswith('type') and x[:-4] in ROLE_NAMES else x
    usable = {}
    for x, counter in values.items():
        if allowed is not None and unit(x) not in allowed:
            continue
        # A field most siblings lack is one sibling's own (a series only the
        # book's entry records, an editor written in error): not the book's.
        if 2 * sum(counter.values()) <= len(sibs):
            report['minority'].append(x)
        elif len({_key(v) for v in counter}) > 1:
            report['disagree'].append(x)
        else:
            usable[x] = counter.most_common(1)[0][0]
    blocked = {unit(x) for x in report['minority'] + report['disagree']}
    for x, house in usable.items():
        if unit(x) in blocked:
            continue
        if unit(x) != x and not f.get(unit(x)) and unit(x) not in usable:
            continue
        have = f.get(x)
        if not have:
            new = enrich.add_field(entry_text, x, house)
            if new != entry_text:
                entry_text = new
                report['added'].append(x)
        elif have == house:
            report['confirmed'].append(x)
        elif _key(have) == _key(house):
            entry_text = enrich.set_field(entry_text, x, house)
            report['snapped'].append(x)
        elif x in replaceable:
            entry_text = enrich.set_field(entry_text, x, house)
            report['replaced'].append(x)
        else:
            report['differs'].append(f"{x}: entry has {have!r}; library has {house!r}")
    return entry_text, report


def _journal_snap(entry_text, f, lib, exclude, report):
    import enrich
    name = 'journal' if 'journal' in f else 'journaltitle' if 'journaltitle' in f else None
    if not name:
        return entry_text, report
    skip = set(exclude)
    forms = collections.Counter()
    for i in lib.by_journal.get(_ckey(f[name]), []):
        k, _t, g = lib.entries[i]
        if k not in skip:
            # The name without a bracketed qualifier: that belongs in
            # Journaltitleaddon (extraction-rules), so it is neither taken
            # from the library nor removed from the entry (#121).
            forms[_BRACKET.sub('', g.get('journal') or g.get('journaltitle')).strip()] += 1
    if not forms:
        return entry_text, report
    tail = _BRACKET.search(f[name])
    house = forms.most_common(1)[0][0] + (' ' + tail.group(0).strip() if tail else '')
    if house != f[name]:
        entry_text = enrich.set_field(entry_text, name, house)
        report['snapped'].append(name)
    else:
        report['confirmed'].append(name)
    report['siblings'] = [f'{sum(forms.values())} in {house}']
    return entry_text, report


def _clean(entry_text):
    """An entry with BibDesk bookkeeping and private fields removed."""
    entries, _ = bib_audit.scan(entry_text)
    if not entries:
        return entry_text
    e = entries[0]
    keep = [f for f in e.fields if _shown(f.key)]
    body = ',\n'.join(f"\t{f.name} = {{{f.value.strip()}}}" for f in keep)
    return f"@{e.etype}{{{e.citekey},\n{body}\n}}"


def exemplars(lib, entry_type=None, container=None, exclude=(), n=3):
    """Up to n library entries, cleaned, as house-form examples: the
    container's own chapters or articles first, then the decided type's
    entries with the most populated fields (only fields biblatex or
    biblatex-chicago defines count), newest first on a tie, one per first
    author so that the examples differ."""
    if lib is None:
        return []
    skip = set(exclude)
    picked = []
    if container:
        ck = _ckey(container)
        refs = [i for i in lib.by_title.get(ck, []) if lib.entries[i][1] == 'inreference']
        for i in lib.by_container.get(ck, []) + lib.by_journal.get(ck, []) + refs:
            if lib.entries[i][0] not in skip and i not in picked:
                picked.append(i)
    if entry_type and len(picked) < n:
        typed = [i for i, (k, t, _f) in enumerate(lib.entries)
                 if t == entry_type and k not in skip and i not in picked]
        typed.sort(key=lambda i: lib.entries[i][2].get('date-modified', ''), reverse=True)
        typed.sort(key=lambda i: sum(1 for x in lib.entries[i][2] if _shown(x)), reverse=True)
        authors = set()
        for i in typed:
            if len(picked) >= n:
                break
            k, _t, f = lib.entries[i]
            names = _surnames(f.get('author') or f.get('editor') or '')
            who = names[0] if names else re.sub(r'[^A-Za-z]+$', '', re.sub(r'\d.*', '', k))
            if who in authors:
                continue
            authors.add(who)
            picked.append(i)
    out = []
    for i in picked[:n]:
        a, b = lib.spans[lib.entries[i][0]]
        out.append(_clean(lib.text[a:b]))
    return out
