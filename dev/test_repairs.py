#!/usr/bin/env python3
"""Self-tests for the cleanup spec's repairs: src/repairs.py (mechanical, no
model), BiblioAgent._repair_entry (one Claude call, stubbed) and
BiblioAgent._render_check (bib_render stubbed). Entries are constructed."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import biblio_agent  # noqa: E402
import bib_render  # noqa: E402
import extract_pages  # noqa: E402
import repairs  # noqa: E402
from bib_validate import ValidatorUnavailable  # noqa: E402


def _e(etype, **fields):
    body = "".join(f"\t{k.capitalize()} = {{{v}}},\n" for k, v in fields.items())
    return f"@{etype}{{X,\n{body}}}"


def _fix(text, **kw):
    return repairs.repair(text, **kw)


def test_journaltitle_becomes_journal():
    out, applied = _fix(_e("article", journaltitle="Music Analysis", title="T"))
    assert "Journal = {Music Analysis}" in out and "Journaltitle" not in out, out
    out, _ = _fix(_e("article", journal="J", journaltitle="J"))
    assert out.count("Journal") == 1 and "Journaltitle" not in out, out


def test_bibtex_address_and_year_become_location_and_date():
    out, applied = _fix(_e("book", address="Oxford", year="2001", title="T"))
    assert "Location = {Oxford}" in out and "Date = {2001}" in out, out
    assert "Address" not in out and "Year" not in out and len(applied) == 2, applied
    out, _ = _fix(_e("book", location="Oxford", address="Oxford", date="2001-05", year="2001"))
    assert "Address" not in out and "Year" not in out, out
    # A differing duplicate is kept, and Year's nodate bibstring is the house form.
    out, _ = _fix(_e("book", location="Oxford", address="New York"))
    assert "Address = {New York}" in out, out
    out, _ = _fix(_e("book", year="\\bibstring{nodate}"))
    assert "Year = {" in out and "Date" not in out, out


def test_a_first_edition_is_dropped_and_later_ones_kept():
    out, applied = _fix(_e("book", edition="1", title="T"))
    assert "Edition" not in out and any(a.startswith("edition-first") for a in applied), (out, applied)
    assert "Edition = {2}" in _fix(_e("book", edition="2", title="T"))[0]


def test_volume_roman_split_and_combined():
    assert "Volume = {23}" in _fix(_e("article", volume="XXIII"))[0]
    out = _fix(_e("article", volume="29(2)"))[0]
    assert "Volume = {29}" in out and "Number = {2}" in out, out
    out = _fix(_e("article", volume="29/2"))[0]
    assert "Volume = {29}" in out and "Number = {2}" in out, out
    assert "Volume = {37--38}" in _fix(_e("article", volume="37/38"))[0]
    # A number already present: no split, nothing lost.
    out = _fix(_e("article", volume="29/2", number="4"))[0]
    assert "Volume = {29/2}" in out and "Number = {4}" in out, out


def test_edition_becomes_an_integer():
    assert "Edition = {2}" in _fix(_e("book", edition="2nd ed."))[0]
    assert "Edition = {3}" in _fix(_e("book", edition="Third edition"))[0]
    assert "Edition = {revised}" in _fix(_e("book", edition="revised"))[0]
    assert _fix(_e("book", edition="2"))[1] == []


def test_article_takes_no_publisher_or_volumes():
    out = _fix(_e("article", publisher="Informa UK Limited", volumes="3", title="T"))[0]
    assert "Publisher" not in out and "Volumes" not in out, out
    assert "Publisher" in _fix(_e("book", publisher="Brill"))[0]


def test_unpublished_fields_that_never_print_move_into_note():
    out = _fix(_e("unpublished", title="T", edition="2", institution="Santa Monica College"))[0]
    assert "Edition" not in out and "Institution" not in out, out
    assert "Note = {2nd ed., Santa Monica College}" in out, out


def test_aggregator_url_and_orphan_urldate_go():
    out = _fix(_e("online", url="https://www.researchgate.net/x", urldate="2020-01-01"))[0]
    assert "Url" not in out and "Urldate" not in out, out
    out = _fix(_e("online", url="https://plato.stanford.edu/x", urldate="2020-01-01"))[0]
    assert "Url = {https://plato.stanford.edu/x}" in out and "Urldate" in out, out


def test_first_city_and_publisher_short():
    assert "Location = {London}" in _fix(_e("book", location="London and New York"))[0]
    assert "Publisher = {Logos}" in _fix(_e("book", publisher="Logos Verlag"))[0]
    assert "Publisher = {Norton}" in _fix(_e("book", publisher="The Norton Company"))[0]
    for keep in ("Oxford University Press", "MIT Press", "Academic Press", "SUNY Press", "Psychology Press"):
        assert f"Publisher = {{{keep}}}" in _fix(_e("book", publisher=keep))[0], keep
    # The library's own form wins: it keeps UMI Research Press.
    out = _fix(_e("book", publisher="UMI Research Press"),
               is_house=lambda f, v: v == "UMI Research Press")[0]
    assert "Publisher = {UMI Research Press}" in out, out


def test_corporate_author_braced_only_when_it_is_the_publisher():
    out = _fix(_e("book", author="Honeywell", publisher="Honeywell"))[0]
    assert "Author = {{Honeywell}}" in out, out
    assert "Author = {Honeywell}" in _fix(_e("book", author="Honeywell", publisher="Brill"))[0]


def test_typography_and_hyphenation():
    out = _fix(_e("book", title="Th\\'{e}orie   du \\c{c}a — now", hyphenation="french",
                  langid="french", pages="12–14"))[0]
    assert "Title = {Théorie du ça --- now}" in out, out
    assert "Hyphenation" not in out and "Pages = {12–14}" in out, out
    out = _fix(_e("book", hyphenation="german"))[0]
    assert "Langid = {german}" in out, out


def test_template_needs_no_repair():
    text = (ROOT / "prompt-context" / "biblio-template.bib").read_text(encoding="utf-8")
    import bib_audit
    entries, _ = bib_audit.scan(text)
    for e in entries:
        _, applied = repairs.repair(text[e.span[0]:e.span[1]])
        assert applied == [], (e.citekey, applied)


def test_dates_not_iso_are_reported():
    assert repairs.date_problems(_e("book", date="1999?"))
    assert not repairs.date_problems(_e("book", date="2008-10-21/2008-10-25"))


# ---- the repair call and the render check ----------------------------------

def _reply(text):
    return type("M", (), {"content": [type("B", (), {"type": "text", "text": text})()],
                          "stop_reason": "end_turn", "usage": None})()


def _agent(replies):
    agent = biblio_agent.BiblioAgent.__new__(biblio_agent.BiblioAgent)
    agent.config = {"verbose": False, "max_tokens": 4000}
    agent.usage, agent._local, agent._progress_callback = [], threading.local(), None
    calls = []
    queue = list(replies)
    agent.client = type("C", (), {"messages": type("Ms", (), {
        "create": lambda self, **kw: calls.append(kw) or _reply(queue.pop(0))})()})()
    return agent, calls


SOURCE = extract_pages.SourceContent(text="Jane Doe. A Study. Paper read at Leeds, 2001.", label="PDF")
BROKEN = "@unpublished{X,\n\tAuthor = {Doe, Jane},\n\tTitle = {A Study},\n\tdate-added = {2026-11-05},\n}"


def test_repair_keeps_a_decided_type_and_reverts_invented_values():
    agent, calls = _agent(["@unpublished{X,\n\tAuthor = {Doe, Jane},\n\tTitle = {A Study},\n"
                           "\tNote = {paper read at Leeds},\n\tPublisher = {Penguin},\n\tDate = {2001},\n}"])
    out, changed = agent._repair_entry(BROKEN, ["@unpublished: missing date"], SOURCE, "m", retype=False)
    assert changed and len(calls) == 1
    assert "Note = {paper read at Leeds}" in out and "Date = {2001}" in out, out
    assert "Penguin" not in out, out                       # not in the source: reverted
    assert "date-added = {2026-11-05}" in out, out         # bookkeeping restored
    prompt = calls[0]["messages"][0]["content"]
    assert "decided upstream" in prompt and "missing date" in prompt


def test_repair_that_retypes_a_decided_type_is_refused():
    agent, _ = _agent(["@misc{X,\n\tTitle = {A Study},\n}"])
    out, changed = agent._repair_entry(BROKEN, ["w"], SOURCE, "m", retype=False)
    assert out == BROKEN and not changed


def test_render_check_colours_real_failures_only():
    agent, _ = _agent([])
    with mock.patch.object(bib_render, "render", side_effect=ValidatorUnavailable("no TeX")):
        assert agent._render_check(BROKEN, SOURCE, "m") == (BROKEN, False)
    with mock.patch.object(bib_render, "render", side_effect=bib_render.RenderFailed("LaTeX failed")):
        assert agent._render_check(BROKEN, SOURCE, "m")[1] is True
    # A field that never prints: moved into Note, and coloured even so.
    entry = BROKEN.replace("\tdate-added", "\tEdition = {2},\n\tdate-added")
    with mock.patch.object(bib_render, "render", return_value="Doe, Jane. A Study."):
        out, colour = agent._render_check(entry, SOURCE, "m")
    assert colour is True and "Edition" not in out and "Note = {2}" in out, out


def test_render_check_repairs_nested_quotes_once_then_colours():
    agent, calls = _agent([BROKEN])
    with mock.patch.object(bib_render, "render", return_value="Doe, Jane. “‘A Study.”’"):
        out, colour = agent._render_check(BROKEN, SOURCE, "m")
    assert colour is True and len(calls) == 1, (colour, calls)


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
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} repair self-tests passed.")
    sys.exit(1 if failed else 0)
