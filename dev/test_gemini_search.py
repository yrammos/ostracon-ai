#!/usr/bin/env python3
"""Self-tests for src/gemini_search.py with a stubbed HTTP layer.

The 429 reply is recorded from the API (2026-11-04, a project without billing);
the 200 reply is constructed from the documented generateContent response shape
(candidates[].content.parts, candidates[].groundingMetadata), not recorded.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import gemini_search  # noqa: E402


class Reply:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


OK = {"candidates": [{"content": {"parts": [{"text": "A chapter in "}, {"text": "an edited volume."}]},
                      "groundingMetadata": {
                          "webSearchQueries": ["\"Music at the Limits\" De Groot"],
                          "groundingChunks": [{"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x",
                                                       "title": "brill.com"}}]}}]}
QUOTA = {"error": {"code": 429, "message": "You exceeded your current quota, please check your plan and billing details.",
                   "status": "RESOURCE_EXHAUSTED"}}


def test_answer_sources_and_queries():
    with mock.patch.object(gemini_search.requests, "post", return_value=Reply(200, OK)) as post:
        out = gemini_search.ask_google("q", "KEY")
    assert out["answer"] == "A chapter in an edited volume.", out
    assert out["sources"] == [{"title": "brill.com",
                               "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x"}]
    assert out["queries"] == ["\"Music at the Limits\" De Groot"]
    assert post.call_args.kwargs["json"]["tools"] == [{"google_search": {}}]
    assert post.call_args.kwargs["headers"] == {"x-goog-api-key": "KEY"}


def test_quota_error_is_an_error():
    with mock.patch.object(gemini_search.requests, "post", return_value=Reply(429, QUOTA)):
        out = gemini_search.ask_google("q", "KEY")
    assert out == {"error": "HTTP 429: You exceeded your current quota, please check your plan and billing details."}, out


def test_no_key_makes_no_request():
    with mock.patch.object(gemini_search.requests, "post") as post:
        assert gemini_search.ask_google("q", None) == {"error": "no Gemini API key configured"}
    post.assert_not_called()


def _typed(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "groundingMetadata": {}}],
            "usageMetadata": {"promptTokenCount": 10}}


def test_decide_type_searches_with_the_bare_query_then_chooses():
    reply = '<result>{"type": "@InCollection", "found": true, "reason": "Brill edited volume"}</result>'
    with mock.patch.object(gemini_search.requests, "post",
                           side_effect=[Reply(200, OK), Reply(200, _typed(reply))]) as post:
        out = gemini_search.decide_type('"Music at the Limits" AND De Groot', "TYPES", "KEY")
    assert (out["type"], out["found"], out["reason"]) == ("incollection", True, "Brill edited volume"), out
    assert out["answer"] == "A chapter in an edited volume." and out["queries"], out
    first, second = (c.kwargs["json"] for c in post.call_args_list)
    assert first["contents"] == [{"parts": [{"text": '"Music at the Limits" AND De Groot'}]}]
    assert first["tools"] == [{"google_search": {}}] and "tools" not in second
    assert "TYPES" in second["contents"][-1]["parts"][0]["text"]


def test_decide_type_retries_high_demand_then_answers():
    busy = Reply(503, {"error": {"message": "This model is currently experiencing high demand."}})
    ok = Reply(200, _typed('<result>{"type": null, "found": false, "reason": "not found"}</result>'))
    waits = []
    with mock.patch.object(gemini_search.requests, "post", side_effect=[busy, Reply(200, OK), ok]):
        out = gemini_search.decide_type("q", "T", "KEY", sleep=waits.append)
    assert waits == [10] and out["type"] is None and out["found"] is False, out


def test_decide_type_unparsable_is_an_error():
    with mock.patch.object(gemini_search.requests, "post",
                           side_effect=[Reply(200, OK), Reply(200, _typed("It is a chapter."))]):
        out = gemini_search.decide_type("q", "T", "KEY")
    assert out["error"] == "unparsable answer", out


def test_type_query_uses_title_and_first_surname():
    assert gemini_search.type_query("Music at the Limits", "De Groot, Rokus and Said, Edward") == \
        '"Music at the Limits" AND De Groot'
    assert gemini_search.type_query("Untitled", "") == '"Untitled"'
    assert gemini_search.type_query("Introduction", "Adorno", "Aesthetic Theory") == \
        '"Introduction" AND "Aesthetic Theory" AND Adorno'


def test_query_names_the_item_cited():
    q = lambda fields, t: gemini_search.query_from_fields(fields, t)[1]
    assert q({"Author": "Abrams, M.~H.", "Lista": "Realism and Naturalism",
              "Title": "A Glossary of Literary Terms"}, "inreference") == \
        '"Realism and Naturalism" AND "A Glossary of Literary Terms" AND Abrams'
    assert q({"Author": "Adorno, Theodor~W.", "Type": "introduction to",
              "Title": "Philosophy of New Music"}, "suppbook") == \
        'introduction AND "Philosophy of New Music" AND Adorno'
    assert q({"Author": "Carlson, Mark", "Journal": "Music Theory Online",
              "Title": r"\bibstring{reviewof} \mkbibemph{Deepening Musical Performance}, \bibstring{by} Alexandra Pierce"},
             "review").startswith("review AND ")
    assert q({"Author": "De Groot, Rokus", "Title": "Music at the Limits",
              "Booktitle": "How the West Was Won"}, "incollection") == \
        '"Music at the Limits" AND "How the West Was Won" AND De Groot'


def test_type_fields_follow_the_manual_and_the_house():
    import type_fields
    names = lambda t: [f for f, _ in type_fields.fields_for(t)]
    if not type_fields._manual():
        return                                     # TeX documentation not installed
    inc = names("incollection")
    assert {"booktitle", "editor", "publisher", "location", "pages", "date"} <= set(inc), inc
    assert not {"isbn", "keywords", "note", "year", "langid", "url"} & set(inc), inc
    ref = names("inreference")
    assert "lista" in ref and "booktitle" not in ref, ref
    assert "url" in names("online") and "booktitle" not in names("suppbook")


def test_gemini_outranks_a_scholar_value_only():
    entry = "@incollection{X,\n  Title = {T},\n  Publisher = {Routledge},\n  Location = {London},\n}"
    qs = [{"id": "field_publisher", "use": "field publisher", "auto": True},
          {"id": "field_location", "use": "field location", "auto": True}]
    out, rep = gemini_search.apply_answers(entry, qs, {"field_publisher": "Brill", "field_location": "Leiden"},
                                           "incollection", outranks={"publisher"})
    assert "Publisher = {Brill}" in out and "Location = {London}" in out, out
    assert rep["replaced"] == ["publisher"] and rep["differs"], rep


def test_listed_types_are_the_21_in_the_prompt():
    import biblio_agent
    types = gemini_search.listed_types(biblio_agent.TYPE_DEFINITIONS)
    assert len(types) == 21 and {"article", "suppcollection", "misc"} <= types, sorted(types)


def test_the_bank_loads_and_selects_by_type():
    bank = gemini_search.load_bank(os.path.join(os.path.dirname(__file__), "..", "prompt-context",
                                                "gemini-questions.yaml"))
    assert [q["id"] for q in gemini_search.select(bank, "inbook")] == \
        ["chapter_number", "other_chapters_same_author"]
    assert gemini_search.select(bank, "thesis") == []
    assert gemini_search.select([{"id": "a", "when": "any", "ask": "?", "use": "check", "expect": {}}],
                                "thesis")[0]["id"] == "a"


def test_answers_fill_confirm_or_go_to_review_and_never_overwrite():
    qs = [{"id": "ch", "when": "any", "ask": "?", "use": "field chapter"},
          {"id": "pg", "when": "any", "ask": "?", "use": "field pages"},
          {"id": "vol", "when": "any", "ask": "?", "use": "field volume"},
          {"id": "same", "when": "any", "ask": "?", "use": "check", "expect": {"inbook": "yes", "incollection": "no"}},
          {"id": "none", "when": "any", "ask": "?", "use": "field note"}]
    entry = "@inbook{X,\n  Title = {T},\n  Pages = {80--112},\n  Volume = {2},\n}"
    out, rep = gemini_search.apply_answers(entry, qs, {"ch": 4, "pg": "80--112", "vol": "3",
                                                       "same": "No", "none": None}, "inbook")
    assert rep["added"] == ["chapter"] and "Chapter = {4}" in out, (rep, out)
    assert rep["confirmed"] == ["pages"] and "Volume = {2}" in out, (rep, out)
    assert rep["checks"] == {"same": False} and len(rep["conflicts"]) == 2, rep


def test_ask_questions_offers_check_options_and_drops_unknown_ids():
    q = [{"id": "same", "when": "any", "ask": "Same author?", "use": "check", "expect": {"inbook": "yes", "incollection": "no"}}]
    reply = '<result>{"same": "yes", "bogus": 1}</result>'
    with mock.patch.object(gemini_search.requests, "post", return_value=Reply(200, _typed(reply))) as post:
        out = gemini_search.ask_questions([{"role": "user", "parts": [{"text": "q"}]}], q, "KEY")
    assert out["answers"] == {"same": "yes"}, out
    sent = post.call_args.kwargs["json"]
    assert "Options: no; yes." in sent["contents"][-1]["parts"][0]["text"] and sent["tools"] == [{"google_search": {}}]



def test_ask_questions_records_its_searches():
    # Constructed reply in the API's documented shape (groundingMetadata.webSearchQueries).
    q = [{"id": "pub", "when": "any", "ask": "Publisher?", "use": "field publisher"}]
    data = _typed('<result>{"pub": "Clarendon"}</result>')
    data["candidates"][0]["groundingMetadata"] = {"webSearchQueries": ["a publisher", "b publisher"]}
    with mock.patch.object(gemini_search.requests, "post", return_value=Reply(200, data)):
        out = gemini_search.ask_questions([], q, "KEY")
    assert out["queries"] == ["a publisher", "b publisher"], out

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {t.__name__}: {e!r}")
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} gemini_search self-tests passed.")
    sys.exit(1 if failed else 0)
