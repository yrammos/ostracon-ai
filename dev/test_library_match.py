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


def test_excluded_keys_are_never_matched():
    _, r = lm.container_fill(NEW, lm.load(_lib()), exclude={"Alpha2010", "Beta2010", "Editor2010"})
    assert r["siblings"] == [] and r["added"] == []


def test_exemplars_are_cleaned_and_container_first():
    lib = lm.load(_lib())
    ex = lm.exemplars(lib, entry_type="article", container="Essays on Listening", n=4)
    assert len(ex) == 4 and "Alpha2010" in ex[0] and "Delta2001" in ex[3], ex
    assert not any("date-added" in e or "bdsk-file" in e for e in ex), ex
    assert lm.exemplars(None, entry_type="article") == []


def test_no_library_changes_nothing():
    assert lm.load(None) is None and lm.load("/nonexistent/lib.bib") is None
    assert lm.container_fill(NEW, None)[0] == NEW and lm.duplicates(NEW, None) == []


def test_pipeline_fills_from_the_library_before_gemini():
    """extract_bibtex(): the library's Editor fills the gap Gemini would
    otherwise fill; the library outranks a Scholar-only Location; a Scholar
    Publisher the library confirms is not replaced by Gemini's."""
    import biblio_agent
    import extract_pages
    import gemini_search
    entry = NEW.replace("\tPages", "\tLocation = {Evanston},\n"
                        "\tPublisher = {University of Chicago Press},\n\tPages")
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    agent.config.update(enrich_missing_fields=False, validate_entries=False, render_check=False,
                        gemini_type_check=True, gemini_api_key="KEY", require_langid=False,
                        house_cleanup=False, library_bib_file=str(_lib()), library_exemplars=True)

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
                                  lambda self, e, c: (e, False)):
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
