#!/usr/bin/env python3
"""Self-tests for enrich.verify_recollection(): a CrossRef record whose
identity is confirmed beyond its title replaces a recollected value; and for
verify_and_flag_recollection(): which fields end up reported as inferred.

Stubbed: crossref_by_biblio (a constructed record, not recorded), and in the
agent tests the grounding audit (a constructed verdict) and the reconcile call.
Not stubbed: the identity check, the completion check, the candidate kinds and
the derivation of the inferred list.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import enrich  # noqa: E402

RECORD = {"title": ["Music at the Limits"], "DOI": "10.1163/x",
          "author": [{"family": "De Groot", "given": "Rokus"}],
          "published-print": {"date-parts": [[2010]]}, "publisher": "Brill"}
FIELDS = {"author": "De Groot, Rokus", "title": "Music at the Limits",
          "date": "2009", "publisher": "Routledge"}


def _verify(flagged, fields=FIELDS, record=RECORD):
    with mock.patch.object(enrich, "crossref_by_biblio", return_value=record):
        return enrich.verify_recollection("Music at the Limits", flagged, fields)


def _kinds(cands):
    return {c["field"]: c["kind"] for c in cands}


def test_a_recollected_value_is_replaced_when_the_author_confirms_the_record():
    cands, _, _ = _verify(["date", "publisher"])
    assert _kinds(cands) == {"date": "replace", "publisher": "replace"}, cands
    assert {c["field"]: c["verified"] for c in cands}["publisher"] == "Brill"


def test_a_value_read_from_the_source_is_never_replaced():
    cands, _, unresolved = _verify(["date"])
    assert "publisher" not in _kinds(cands), cands


def test_without_identity_beyond_the_title_nothing_is_replaced():
    fields = dict(FIELDS, author="Smith, Jane")
    cands, _, unresolved = _verify(["date", "publisher"], fields)
    assert cands == [] and unresolved is True, cands


def test_a_flagged_author_cannot_vouch_for_the_record():
    cands, _, _ = _verify(["author", "date"])
    assert "date" not in _kinds(cands), cands


def test_a_matching_doi_confirms_identity_on_its_own():
    fields = dict(FIELDS, doi="10.1163/X")
    cands, _, _ = _verify(["author", "date"], fields)
    assert _kinds(cands).get("date") == "replace", cands


def test_a_field_the_record_states_exactly_is_reported_as_confirmed():
    confirmed = set()
    with mock.patch.object(enrich, "crossref_by_biblio", return_value=RECORD):
        enrich.verify_recollection("Music at the Limits", ["date"],
                                   dict(FIELDS, date="2010"), confirmed=confirmed)
    assert "date" in confirmed, confirmed


def _inferred(flagged, entry, record):
    import io
    from contextlib import redirect_stderr
    import biblio_agent
    import extract_pages
    agent = biblio_agent.BiblioAgent(str(ROOT / "config.yaml"))
    content = extract_pages.SourceContent(text="t", label="PDF")
    with mock.patch.object(agent, "_audit_entry_grounding", return_value=flagged), \
         mock.patch.object(enrich, "crossref_by_biblio", return_value=record), \
         mock.patch.object(agent, "reconcile_fields", lambda e, c, cands: e), \
         redirect_stderr(io.StringIO()):
        agent.verify_and_flag_recollection(entry, content)
    return agent.inferred_fields


ENTRY = ("@incollection{X,\n\tAuthor = {De Groot, Rokus},\n\tTitle = {Music at the Limits},\n"
         "\tEditor = {Otten, Willemien},\n\tDate = {2010},\n\tVolume = {7},\n}")


def test_a_confirmed_field_is_not_reported_when_another_is_not():
    # The record has no editor list, so the check is left unresolved; the
    # date it states exactly must still not be reported (it used to be).
    assert _inferred(["date", "volume"], ENTRY, RECORD) == ["volume"]


def test_an_uncheckable_field_is_reported_when_everything_else_matches():
    # Author and editor both match, nothing is unresolved: the recalled
    # volume, which no record is consulted for, must still be reported (it
    # used to pass silently).
    record = dict(RECORD, editor=[{"family": "Otten", "given": "Willemien"}])
    assert _inferred(["volume"], ENTRY, record) == ["volume"]
    assert _inferred(["date"], ENTRY, record) == []


def test_a_corrected_field_is_not_reported():
    entry = ENTRY.replace("\tVolume", "\tPublisher = {Routledge},\n\tVolume")
    assert _inferred(["publisher", "volume"], entry, RECORD) == ["volume"]


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
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} recollection self-tests passed.")
    sys.exit(1 if failed else 0)
