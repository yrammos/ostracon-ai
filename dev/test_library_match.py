#!/usr/bin/env python3
"""Self-tests for src/library_match.py and its place in extract_bibtex().

Fixtures are constructed (an invented library of five entries), not recorded
from the maintainer's data. Stubbed in the pipeline test: the Anthropic client
and Gemini's answers (constructed), and a `.fake` source extractor. Not
stubbed: bib_audit's scanner, the matching, the fill, apply_answers().
"""
from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import library_match as lm  # noqa: E402

LIBRARY = r"""@incollection{Alpha2010,
	author = {Alpha, Ann},
	booktitle = {Essays on Listening},
	booksubtitle = {Theory and Practice},
	date = {2010},
	date-added = {2024-01-01 00:00:00 +0000},
	editor = {Editor, Eve},
	location = {Chicago},
	pages = {1-20},
	publisher = {University of Chicago Press},
	title = {On Hearing}}

@collection{Editor2010,
	date = {2010},
	editor = {Editor, Eve},
	location = {Chicago},
	publisher = {University of Chicago Press},
	series = {Studies in Sound},
	subtitle = {Theory and Practice},
	title = {Essays on Listening}}

@incollection{Beta2010,
	author = {Beta, Bo},
	booktitle = {Essays on Listening},
	booksubtitle = {Theory and Practice},
	date = {2010},
	editor = {Editor, Eve},
	location = {Chicago},
	publisher = {University of Chicago Press},
	title = {On Echoes}}

@incollection{Gamma1995,
	author = {Gamma, Gil},
	booktitle = {Essays on Listening},
	date = {1995},
	editor = {Other, Otto},
	location = {London},
	publisher = {Faber},
	title = {An Earlier Book of the Same Name}}

@article{Delta2001,
	author = {Delta, Dan},
	date = {2001},
	doi = {10.1000/xyz},
	journal = {Journal of Music Theory},
	bdsk-file-1 = {YnBsaXN0},
	title = {Old Article}}
"""

NEW = """@incollection{New2010,
	Author = {New, Nora},
	Title = {On Silence},
	Booktitle = {essays on listening},
	Date = {2010},
	Pages = {21-40}
}"""


def _lib():
    p = Path(tempfile.mkdtemp()) / "lib.bib"
    p.write_text(LIBRARY, encoding="utf-8")
    return p


def test_a_chapter_takes_its_books_fields_in_the_librarys_form():
    out, r = lm.container_fill(NEW, lm.load(_lib()))
    assert sorted(r["siblings"]) == ["Alpha2010", "Beta2010", "Editor2010"], r
    assert "Booktitle = {Essays on Listening}" in out and r["snapped"] == ["booktitle"], out
    for f in ("Booksubtitle = {Theory and Practice}", "Editor = {Editor, Eve}",
              "Location = {Chicago}", "Publisher = {University of Chicago Press}"):
        assert f in out, (f, out)
    # The series only the book's own entry records: one of three, not filled.
    assert "Series" not in out and "series" in r["minority"], r
    assert "Faber" not in out and "Gamma1995" not in r["siblings"]   # another year


def test_a_value_from_the_source_stands_and_one_from_a_search_gives_way():
    entry = NEW.replace("\tPages", "\tPublisher = {Chicago UP},\n\tLocation = {Evanston},\n\tPages")
    out, r = lm.container_fill(entry, lm.load(_lib()), replaceable={"location"})
    assert "Publisher = {Chicago UP}" in out and r["differs"], r
    assert "Location = {Chicago}" in out and r["replaced"] == ["location"], r


def test_gates_volume_editor_and_type_fields():
    lib = lm.load(_lib())
    other_editor = NEW.replace("\tPages", "\tEditor = {Someone, Else},\n\tPages")
    assert lm.container_fill(other_editor, lib)[1]["siblings"] == []
    no_year = NEW.replace("\tDate = {2010},\n", "")
    assert lm.container_fill(no_year, lib)[1]["siblings"] == []
    out, r = lm.container_fill(NEW, lib, allowed={"booktitle", "editor"})
    assert "Publisher" not in out and "Editor = {Editor, Eve}" in out, out


def test_a_name_list_comes_with_its_role_or_not_at_all():
    lib_text = LIBRARY.replace("\teditor = {Editor, Eve},\n\tlocation = {Chicago},\n\tpages",
                               "\teditor = {Editor, Eve},\n\teditortype = {compiler},\n\tlocation = {Chicago},\n\tpages")
    lib_text = lib_text.replace("\teditor = {Editor, Eve},\n\tlocation = {Chicago},\n\tpublisher = {University of Chicago Press},\n\ttitle = {On Echoes}",
                                "\teditor = {Editor, Eve},\n\teditortype = {compiler},\n\tlocation = {Chicago},\n\tpublisher = {University of Chicago Press},\n\ttitle = {On Echoes}")
    p = Path(tempfile.mkdtemp()) / "lib.bib"
    p.write_text(lib_text, encoding="utf-8")
    # The type list takes Editor but not Editortype: the role rides on the name.
    out, r = lm.container_fill(NEW, lm.load(p), allowed={"booktitle", "editor"})
    assert "Editor = {Editor, Eve}" in out and "Editortype = {compiler}" in out, out
    # Only one of three siblings gives the role: neither name nor role is filled.
    lib_text = LIBRARY.replace("\teditor = {Editor, Eve},\n\tlocation = {Chicago},\n\tpages",
                               "\teditor = {Editor, Eve},\n\teditortype = {compiler},\n\tlocation = {Chicago},\n\tpages")
    p.write_text(lib_text, encoding="utf-8")
    out, r = lm.container_fill(NEW, lm.load(p))
    assert "Editor" not in out and "editortype" in r["minority"], (out, r)


def test_a_value_the_library_confirms_is_reported():
    entry = NEW.replace("\tPages", "\tPublisher = {University of Chicago Press},\n\tPages")
    _, r = lm.container_fill(entry, lm.load(_lib()))
    assert "publisher" in r["confirmed"], r


def test_siblings_that_disagree_fill_nothing_for_that_field():
    lib_text = LIBRARY.replace("location = {Chicago},\n\tpages = {1-20}", "location = {Berlin},\n\tpages = {1-20}")
    p = Path(tempfile.mkdtemp()) / "lib.bib"
    p.write_text(lib_text, encoding="utf-8")
    out, r = lm.container_fill(NEW, lm.load(p))
    assert "Location" not in out and "location" in r["disagree"], r


def test_an_article_takes_only_its_journals_spelling():
    art = ("@article{N,\n\tTitle = {T},\n\tJournal = {journal of music theory},\n"
           "\tDate = {2020},\n\tVolume = {3}\n}")
    out, r = lm.container_fill(art, lm.load(_lib()))
    assert "Journal = {Journal of Music Theory}" in out and r["snapped"] == ["journal"], out
    assert out.count("=") == art.count("="), out


def test_duplicates_by_doi_and_by_title_author_year():
    lib = lm.load(_lib())
    assert lm.duplicates("@article{N,\n\tDoi = {10.1000/XYZ}\n}", lib) == ["Delta2001"]
    same = "@incollection{N,\n\tAuthor = {Alpha, A.},\n\tTitle = {On hearing},\n\tDate = {2010}\n}"
    assert lm.duplicates(same, lib) == ["Alpha2010"]
    assert lm.duplicates(same, lib, exclude={"Alpha2010"}) == []
    # A duplicate is not a sibling: its fields would just copy the old entry.
    _, r = lm.container_fill(same.replace("}\n}", "},\n\tBooktitle = {Essays on Listening}\n}"), lib)
    assert "Alpha2010" not in r["siblings"], r


REFERENCE_LIB = r"""@inreference{Ah2001,
	author = {Ah, Al},
	entrysubtype = {online},
	lista = {Cadence},
	location = {New York},
	publisher = {Oxford University Press},
	title = {Music Online},
	urldate = {2001}}

@inreference{Be2001,
	author = {Be, Bea},
	entrysubtype = {online},
	lista = {Mode},
	location = {New York},
	publisher = {Oxford University Press},
	title = {Music Online},
	urldate = {2001}}

@inreference{Ce1980,
	author = {Ce, Cy},
	date = {1980},
	lista = {Mode},
	location = {London},
	publisher = {Macmillan},
	title = {Dictionary of Music}}

@reference{Dict2001,
	date = {2001},
	edition = {2},
	editor = {Sadie, Stanley},
	location = {London},
	publisher = {Macmillan},
	title = {Dictionary of Music}}

@proceedings{Conf2010,
	date = {2010},
	editor = {Chair, Cat},
	publisher = {Conference Press},
	title = {Essays on Listening}}
"""


def _lib_with(extra):
    p = Path(tempfile.mkdtemp()) / "lib.bib"
    p.write_text(LIBRARY + "\n" + extra, encoding="utf-8")
    return lm.load(p)


def test_an_online_reference_entry_inherits_from_other_online_entries():
    lib = _lib_with(REFERENCE_LIB)
    new = ("@inreference{N,\n\tAuthor = {Ne, Ned},\n\tTitle = {Music Online},\n"
           "\tLista = {Tonality},\n\tEntrysubtype = {online},\n\tUrldate = {2024-01-01}\n}")
    out, r = lm.container_fill(new, lib)
    assert sorted(r["siblings"]) == ["Ah2001", "Be2001"], r
    assert "Publisher = {Oxford University Press}" in out and "Location = {New York}" in out, out
    # A print entry of the same title is another work: no year, not online.
    print_entry = new.replace("\tEntrysubtype = {online},\n", "")
    assert lm.container_fill(print_entry, lib)[1]["siblings"] == []


def test_a_print_reference_entry_inherits_from_its_works_entry_of_that_year():
    lib = _lib_with(REFERENCE_LIB)
    new = ("@inreference{N,\n\tAuthor = {Ne, Ned},\n\tTitle = {Dictionary of Music},\n"
           "\tLista = {Tonality},\n\tDate = {2001}\n}")
    out, r = lm.container_fill(new, lib)
    assert r["siblings"] == ["Dict2001"], r      # Ce1980 is the first edition
    assert "Editor = {Sadie, Stanley}" in out and "Edition = {2}" in out, out
    assert "Macmillan" in out


def test_a_part_inherits_only_from_its_paired_container_type():
    # A @proceedings of the same title and year is not a collection's own entry.
    lib = _lib_with(REFERENCE_LIB)
    _, r = lm.container_fill(NEW, lib)
    assert "Conf2010" not in r["siblings"] and "Editor2010" in r["siblings"], r
    paper = NEW.replace("@incollection", "@inproceedings")
    _, r = lm.container_fill(paper, lib)
    assert r["siblings"] == ["Conf2010"], r


def test_reference_entries_and_shared_dois_are_not_mistaken_for_duplicates():
    lib = _lib_with(REFERENCE_LIB)
    # Same work, author and year, another heading: not a duplicate.
    other = ("@inreference{N,\n\tAuthor = {Ah, Al},\n\tTitle = {Music Online},\n"
             "\tLista = {Rhythm},\n\tUrldate = {2001}\n}")
    assert lm.duplicates(other, lib) == []
    assert lm.duplicates(other.replace("{Rhythm}", "{Cadence}"), lib) == ["Ah2001"]
    # A chapter carrying its book's DOI is not the book.
    lib2 = _lib_with("@collection{Book,\n\ttitle = {A Book},\n\tdoi = {10.1/b},\n\tdate = {2019}}\n")
    chapter = "@incollection{C,\n\tTitle = {A Chapter},\n\tDoi = {10.1/b}\n}"
    assert lm.duplicates(chapter, lib2) == []


SUPP_LIB = r"""@book{Kristeva1984,
	author = {Kristeva, Julia},
	date = {1984},
	location = {New York},
	publisher = {Columbia University Press},
	title = {Revolution in Poetic Language},
	translator = {Waller, Margaret}}

@suppbook{Intro1990,
	author = {Writer, Wanda},
	bookauthor = {Author, Abe},
	date = {1990},
	location = {Chicago},
	publisher = {University of Chicago Press},
	title = {A Book Entered Late},
	type = {preface to}}

@collection{Handbook2011,
	date = {2011},
	editor = {Gollin, Edward and Rehding, Alexander},
	location = {Oxford},
	publisher = {Oxford University Press},
	title = {The Oxford Handbook}}
"""


def test_a_supplement_inherits_its_books_fields():
    lib = _lib_with(SUPP_LIB)
    new = ("@suppbook{N,\n\tAuthor = {Roudiez, Leon},\n\tTitle = {Revolution in Poetic Language},\n"
           "\tType = {introduction to},\n\tDate = {1984}\n}")
    out, r = lm.container_fill(new, lib)
    assert r["siblings"] == ["Kristeva1984"], r
    for x in ("Bookauthor = {Kristeva, Julia}", "Translator = {Waller, Margaret}",
              "Publisher = {Columbia University Press}", "Location = {New York}"):
        assert x in out, (x, out)
    assert lm.duplicates(new, lib) == []          # a supplement is not its book
    assert lm.container_fill(new.replace("{1984}", "{1999}"), lib)[1]["siblings"] == []


def test_a_suppbook_in_a_collection_takes_no_editor_and_a_suppcollection_does():
    lib = _lib_with(SUPP_LIB)
    glossary = ("@suppbook{N,\n\tAuthor = {Gollin, Edward},\n\tTitle = {The Oxford Handbook},\n"
                "\tType = {glossary in},\n\tDate = {2011}\n}")
    out, _ = lm.container_fill(glossary, lib)
    assert "Publisher = {Oxford University Press}" in out and "Editor" not in out, out
    out, _ = lm.container_fill(glossary.replace("@suppbook", "@suppcollection"), lib)
    assert "Editor = {Gollin, Edward and Rehding, Alexander}" in out, out


def test_a_book_inherits_from_its_supplement_entered_first():
    lib = _lib_with(SUPP_LIB)
    book = "@book{N,\n\tTitle = {A Book Entered Late},\n\tDate = {1990}\n}"
    out, r = lm.container_fill(book, lib)
    assert r["siblings"] == ["Intro1990"], r
    assert "Author = {Author, Abe}" in out and "Publisher = {University of Chicago Press}" in out, out
    assert "Writer" not in out and "preface" not in out, out
    # Another author: not this book.
    other = book.replace("\tDate", "\tAuthor = {Else, Eli},\n\tDate")
    assert lm.container_fill(other, lib)[1]["siblings"] == []


def test_a_container_inherits_from_its_parts_entered_first():
    # LIBRARY holds two chapters of Essays on Listening (and its own entry,
    # excluded here); a new entry for the book inherits from the chapters.
    lib = lm.load(_lib())
    book = "@collection{N,\n\tTitle = {Essays on Listening},\n\tDate = {2010}\n}"
    out, r = lm.container_fill(book, lib, exclude={"Editor2010"})
    assert sorted(r["siblings"]) == ["Alpha2010", "Beta2010"], r
    for x in ("Subtitle = {Theory and Practice}", "Editor = {Editor, Eve}",
              "Publisher = {University of Chicago Press}", "Location = {Chicago}"):
        assert x in out, (x, out)
    assert "Gamma" not in str(r) and "Faber" not in out      # the 1995 book
    # A @book of that title is not the chapters' container (incollection pairs with collection).
    assert lm.container_fill(book.replace("@collection", "@book"), lib,
                             exclude={"Editor2010"})[1]["siblings"] == []


def test_a_container_does_not_inherit_a_parts_volume():
    lib_text = LIBRARY.replace("\tpages = {1-20},", "\tpages = {1-20},\n\tvolume = {2},")
    p = Path(tempfile.mkdtemp()) / "lib.bib"
    p.write_text(lib_text.replace("title = {On Echoes}}", "title = {On Echoes},\n\tvolume = {2}}"),
                 encoding="utf-8")
    book = "@collection{N,\n\tTitle = {Essays on Listening},\n\tDate = {2010}\n}"
    out, _ = lm.container_fill(book, lm.load(p), exclude={"Editor2010"})
    assert "Volume" not in out and "Publisher" in out, out


def test_volumes_of_one_set_and_books_by_other_authors_stay_apart():
    lib_text = LIBRARY.replace("\tpages = {1-20},", "\tpages = {1-20},\n\tvolume = {2},")
    lib_text = lib_text.replace("title = {On Echoes}}", "title = {On Echoes},\n\tvolume = {2}}")
    p = Path(tempfile.mkdtemp()) / "lib.bib"
    p.write_text(lib_text, encoding="utf-8")
    vol1 = "@collection{N,\n\tTitle = {Essays on Listening},\n\tDate = {2010},\n\tVolume = {1}\n}"
    assert lm.container_fill(vol1, lm.load(p), exclude={"Editor2010"})[1]["siblings"] == []
    # A book of essays by one author, chapters by another of the same title.
    lib = _lib_with("@inbook{Ch,\n\tauthor = {Adorno, Theodor},\n\tbooktitle = {Collected Essays},\n"
                    "\tdate = {1990},\n\tpublisher = {Suhrkamp},\n\ttitle = {Essay}}\n")
    book = "@book{N,\n\tAuthor = {Benjamin, Walter},\n\tTitle = {Collected Essays},\n\tDate = {1990}\n}"
    assert lm.container_fill(book, lib)[1]["siblings"] == []
    assert lm.container_fill(book.replace("Benjamin, Walter", "Adorno, Theodor"), lib)[1]["siblings"] == ["Ch"]
    chapter = ("@inbook{M,\n\tAuthor = {Benjamin, Walter},\n\tTitle = {Other},\n"
               "\tBooktitle = {Collected Essays},\n\tDate = {1990}\n}")
    assert lm.container_fill(chapter, lib)[1]["siblings"] == []


def test_a_reference_work_inherits_from_its_entries():
    lib = _lib_with(REFERENCE_LIB)
    work = ("@reference{N,\n\tTitle = {Music Online},\n\tEntrysubtype = {online},\n"
            "\tUrldate = {2024-01-01}\n}")
    out, r = lm.container_fill(work, lib)
    assert sorted(r["siblings"]) == ["Ah2001", "Be2001"], r
    assert "Publisher = {Oxford University Press}" in out, out


def test_excluded_keys_are_never_matched():
    _, r = lm.container_fill(NEW, lm.load(_lib()), exclude={"Alpha2010", "Beta2010", "Editor2010"})
    assert r["siblings"] == [] and r["added"] == []


def test_exemplars_are_cleaned_and_container_first():
    lib = lm.load(_lib())
    ex = lm.exemplars(lib, entry_type="article", container="Essays on Listening", n=4)
    assert len(ex) == 4 and "Alpha2010" in ex[0] and "Delta2001" in ex[3], ex
    assert not any("date-added" in e or "bdsk-file" in e for e in ex), ex
    assert lm.exemplars(None, entry_type="article") == []


RICH_BOOKS = r"""@book{Rich2015,
	author = {Rich, Ruth},
	date = {2015},
	edition = {2},
	location = {Oxford},
	publisher = {Clarendon},
	series = {Studies},
	title = {Full Form},
	translator = {Trans, Tom}}

@book{Rich2015a,
	author = {Rich, Ruth},
	date = {2015},
	edition = {2},
	location = {Oxford},
	publisher = {Clarendon},
	series = {Studies},
	title = {Full Form, Volume Two},
	translator = {Trans, Tom}}

@book{Kept2001,
	author = {Kept, Kim},
	date = {2001},
	devonthink2 = {x-devonthink-item://AAA},
	devonthink3 = {x-devonthink-item://BBB},
	local-url = {/private/path/one.pdf},
	local-url-2 = {/private/path/two.pdf},
	local-url-3 = {/private/path/three.pdf},
	location = {Paris},
	publisher = {Minuit},
	title = {Few Fields}}

@book{Mid2010,
	author = {Mid, Max},
	date = {2010},
	location = {Berlin},
	publisher = {Suhrkamp},
	series = {Taschenbuch},
	title = {Some Fields}}
"""


def test_exemplars_prefer_full_entries_one_per_author_without_bookkeeping():
    lib = _lib_with(RICH_BOOKS)
    ex = lm.exemplars(lib, entry_type="book", n=3)
    keys = [e.split("{", 1)[1].split(",", 1)[0] for e in ex]
    # Rich2015a ties with Rich2015 but repeats its author; Kept2001's
    # bookkeeping fields do not count, so Mid2010 outranks it.
    assert keys[:2] == ["Rich2015", "Mid2010"] and "Rich2015a" not in keys, keys
    assert not any("local-url" in e or "devonthink" in e for e in ex), ex
    assert keys[2] == "Kept2001" and "publisher = {Minuit}" in ex[2], ex


def test_exemplar_fields_are_only_those_biblatex_defines():
    assert {"lista", "entrysubtype", "userd", "journal", "title"} <= lm.known_fields()
    assert not {"local-url", "devonthink2", "lastchecked"} & lm.known_fields()


ISMIR = r"""@inproceedings{Gotham2019,
	author = {Gotham, Mark},
	booktitle = {Proceedings of the 20th International Society for Music Information Retrieval (ISMIR) Conference},
	date = {2019},
	location = {Delft},
	publisher = {International Society for Music Information Retrieval},
	title = {A},
}

@inproceedings{Shi2019,
	author = {Shi, Z.},
	booktitle = {Proceedings of the 20th International Society for Music Information Retrieval (ISMIR) Conference},
	date = {2019},
	location = {Delft},
	publisher = {International Society for Music Information Retrieval},
	title = {B},
}
"""


def test_a_bracketed_acronym_does_not_hide_the_siblings():
    new = ("@inproceedings{J,\n  Author = {Jeong, Dasaem},\n  Title = {VirtuosoNet},\n"
           "  Booktitle = {Proceedings of the 20th International Society for Music Information Retrieval Conference},\n"
           "  Date = {2019},\n}")
    out, r = lm.container_fill(new, _lib_with(ISMIR))
    assert sorted(r["siblings"]) == ["Gotham2019", "Shi2019"], r
    assert "Location = {Delft}" in out and "Publisher = {International Society" in out, out


def test_no_library_changes_nothing():
    assert lm.load(None) is None and lm.load("/nonexistent/lib.bib") is None
    assert lm.container_fill(NEW, None)[0] == NEW and lm.duplicates(NEW, None) == []


def test_pipeline_fills_from_the_library_before_gemini():
    """extract_bibtex(): the library's Editor fills the gap Gemini would
    otherwise fill; the library outranks a Scholar-only Location and a
    recollected Booksubtitle; a Scholar
    Publisher the library confirms is not replaced by Gemini's."""
    import biblio_agent
    import extract_pages
    import gemini_search
    entry = NEW.replace("\tPages", "\tLocation = {Evanston},\n\tBooksubtitle = {Remembered Wrongly},\n"
                        "\tPublisher = {University of Chicago Press},\n\tPages")
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    agent.config.update(enrich_missing_fields=False, validate_entries=False, render_check=False,
                        gemini_type_check=True, gemini_api_key="KEY", require_langid=False,
                        house_cleanup=False, library_bib_file=str(_lib()), library_exemplars=True,
                        type_search="gemini")

    class _Messages:
        calls = []
        def create(self, **kw):
            self.calls.append(kw)
            text = ('{"title": "On Silence", "authors": ["New, Nora"], "container": '
                    '"Essays on Listening", "year": "2010"}') if len(self.calls) == 1 else entry
            return type("M", (), {"content": [type("B", (), {"type": "text", "text": text})()],
                                  "stop_reason": "end_turn", "usage": None})()
    agent.client = type("C", (), {"messages": _Messages()})()
    decided = {"type": "incollection", "found": True, "reason": "r", "history": [], "usage": []}
    answers = {"field_editor": "Wrong, Will", "field_location": "Paris",
               "field_publisher": "Gemini House"}
    orig_enrich = biblio_agent.BiblioAgent.enrich_entry

    def fake_enrich(self, e, c):      # Scholar supplied the Location
        self._scholar_fields().update({"location", "publisher"})
        return e, None

    def fake_verify(self, e, c):      # Claude recollected the Booksubtitle
        self.inferred_fields = ["booksubtitle"]
        return e, False
    biblio_agent.EXTRACTORS[".fake"] = lambda path, **kw: extract_pages.SourceContent(
        text="Nora New, On Silence", label="PDF")
    err = io.StringIO()
    try:
        with tempfile.TemporaryDirectory() as td, redirect_stderr(err), \
                mock.patch.object(gemini_search, "decide_type", return_value=decided), \
                mock.patch.object(gemini_search, "ask_questions",
                                  return_value={"answers": answers, "usage": []}), \
                mock.patch.object(biblio_agent.BiblioAgent, "enrich_entry", fake_enrich), \
                mock.patch.object(biblio_agent.BiblioAgent, "verify_and_flag_recollection",
                                  fake_verify):
            agent.config["enrich_missing_fields"] = True
            src = Path(td) / "x.fake"
            src.write_text("x")
            result = agent.extract_bibtex(src)
    finally:
        del biblio_agent.EXTRACTORS[".fake"]
        assert biblio_agent.BiblioAgent.enrich_entry is orig_enrich
    e = result.entry
    assert "Editor = {Editor, Eve}" in e and "Wrong, Will" not in e, e
    assert "Location = {Chicago}" in e and "Paris" not in e, e
    # Scholar's Publisher, confirmed by the library, is no longer Gemini's to replace.
    assert "Publisher = {University of Chicago Press}" in e and "Gemini House" not in e, e
    # A recollected value gives way to the library's, and is no longer inferred.
    assert "Booksubtitle = {Theory and Practice}" in e and "Remembered" not in e, e
    assert "booksubtitle" not in result.inferred_fields, result.inferred_fields
    assert "Library (" in (result.field_sources or ""), result.field_sources
    prompt = agent.client.messages.calls[1]["messages"][0]["content"][-1]["text"]
    assert "house form" in prompt and "@incollection{Alpha2010" in prompt, prompt[-600:]
    shown = prompt[prompt.index("house form"):]
    assert "date-added" not in shown and "Gamma1995" in shown, shown


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"  ✗ {t.__name__}: {e!r}")
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} library-match self-tests passed.")
    sys.exit(1 if failed else 0)
