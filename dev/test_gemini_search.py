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
