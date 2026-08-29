#!/usr/bin/env python3
"""
Self-test for the evaluation harness (scorer.py, run.py's evaluate()), run
against a synthetic fixture pair built here at run time rather than against
dev/eval/sample/ and dev/eval/expected.bib, which are real, hand-verified
data this script has no business needing. Makes no API call, needs no
config.yaml, and needs no anthropic package installed - it never imports
biblio_agent.py, only run.py's evaluate() and load_* helpers, with the
actual extraction step replaced by a canned lookup.

    python3 dev/eval/test_eval.py
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bib_audit  # noqa: E402
import run as eval_run  # noqa: E402
from scorer import score_entry, aggregate, format_report  # noqa: E402
import populate_sample  # noqa: E402
import select_sample  # noqa: E402


def _entry(text: str) -> bib_audit.Entry:
    """Parse a single .bib entry from literal text - exercises the same
    scanner production code uses, rather than hand-building Entry objects."""
    entries, _ = bib_audit.scan(text)
    assert len(entries) == 1, f"expected exactly one entry in fixture text, got {len(entries)}"
    return entries[0]


# ── scorer.py ────────────────────────────────────────────────────────────

def test_score_entry_all_verdicts():
    expected = _entry("""
@Article{Fixture1,
  Author = {Doe, Jane},
  Title = {A Sample Article Title},
  Journaltitle = {A Journal},
  Date = {2020},
}
""")
    produced = _entry("""
@Article{Fixture1,
  Author = {Doe, Jane},
  Title = {A Sample Article Tile},
  Note = {an unexpected extra field},
}
""")
    result = score_entry(expected, produced)

    assert result.type_correct is True
    verdicts = {fs.field: fs.verdict for fs in result.fields}
    assert verdicts['author'] == 'exact'
    assert verdicts['title'] == 'different'
    assert verdicts['journaltitle'] == 'missing'
    assert verdicts['note'] == 'spurious'
    return True


def test_score_entry_normalizes_whitespace():
    expected = _entry("@Article{X, Title = {A   Title\nWith Wraps},}")
    produced = _entry("@Article{X, Title = {A Title With Wraps},}")
    result = score_entry(expected, produced)
    assert result.fields[0].verdict == 'exact', result.fields[0]
    return True


def test_score_entry_wrong_type():
    expected = _entry("@Incollection{X, Author = {Roe, John}, Booktitle = {An Edited Volume},}")
    produced = _entry("@Article{X, Author = {Roe, John},}")
    result = score_entry(expected, produced)
    assert result.type_correct is False
    assert result.type_expected == 'incollection'
    assert result.type_produced == 'article'
    verdicts = {fs.field: fs.verdict for fs in result.fields}
    assert verdicts['author'] == 'exact'
    assert verdicts['booktitle'] == 'missing'
    return True


def test_score_entry_extraction_failed():
    expected = _entry("@Article{X, Author = {Doe, Jane}, Title = {T},}")
    result = score_entry(expected, produced=None)
    assert result.type_correct is False
    assert result.type_produced is None
    assert all(fs.verdict == 'missing' for fs in result.fields)
    return True


def test_score_entry_excludes_bookkeeping_fields():
    # expected side carries a CLAUDE.md-suppressed field (keywords), BibDesk
    # bookkeeping (rating, bdsk-file-1), and a free-text note field carrying
    # BibDesk's line-wrap artifact (annote) - none of these four should
    # register as 'missing'.
    expected = _entry("""
@Book{Fixture1,
  Author = {Doe, Jane},
  Title = {A Sample Book Title},
  Keywords = {some-tag; another-tag},
  Rating = {4},
  Bdsk-File-1 = {not-real-bookmark-data},
  Annote = {a curatorial note, wrapped by BibDesk},
}
""")
    # produced side carries an isbn (CLAUDE.md forbids the pipeline from
    # populating it) and an abstract (the pipeline never produces one) - if
    # either ever slipped through, neither must register as 'spurious'.
    produced = _entry("""
@Book{Fixture1,
  Author = {Doe, Jane},
  Title = {A Sample Book Title},
  Isbn = {978-0-000-00000-0},
  Abstract = {should never come from the pipeline},
}
""")
    result = score_entry(expected, produced)
    verdicts = {fs.field: fs.verdict for fs in result.fields}

    assert 'keywords' not in verdicts
    assert 'rating' not in verdicts
    assert 'bdsk-file-1' not in verdicts
    assert 'annote' not in verdicts
    assert 'isbn' not in verdicts
    assert 'abstract' not in verdicts
    # the remaining, non-excluded fields still score normally
    assert verdicts['author'] == 'exact'
    assert verdicts['title'] == 'exact'
    return True


def test_aggregate_and_report_do_not_crash_on_empty():
    agg = aggregate([])
    assert agg['n_entries'] == 0
    assert agg['type_accuracy'] is None
    report = format_report([], warnings=["X: no matching entry in expected.bib"])
    assert "Nothing scored" in report
    assert "no matching entry" in report
    return True


# ── populate_sample.py: BibDesk line-wrap reconstruction ──────────────────

def test_unwrap_collapses_both_wrap_forms():
    # Newline+18-space form (surviving as written, e.g. in abstract/annote)
    # and the 19-space form (the same wrap, with the newline itself lost
    # upstream of dev/eval/biblio.bib - local-url's actual shape).
    assert populate_sample._unwrap("Music -\n                  Research") == "Music - Research"
    assert populate_sample._unwrap("Music -                   Research") == "Music - Research"
    # A run of ordinary spaces that isn't a wrap (wrong length) is untouched.
    assert populate_sample._unwrap("a  b") == "a  b"
    return True


def test_resolve_attachment_reconstructs_wrapped_local_url():
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "Sample File.pdf"
        target.write_bytes(b"%PDF-1.4 placeholder")
        wrapped = str(target).replace(" ", " " * 19, 1)
        assert wrapped != str(target)  # the fixture actually exercises _unwrap
        entry = _entry(f"@Article{{X, Local-Url = {{{wrapped}}},}}")
        assert populate_sample.resolve_attachment(entry) == target
    return True


# ── run.py: evaluate() and the load_* helpers, extraction stubbed ─────────

def test_evaluate_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        sample_dir = td / 'sample'
        sample_dir.mkdir()

        # Two real (placeholder) source files - content is irrelevant, since
        # extraction is stubbed below; only their existence and bytes matter,
        # for the exists()/sha256 checks evaluate() actually performs.
        (sample_dir / 'fixture1.pdf').write_bytes(b'placeholder bytes for fixture1')
        (sample_dir / 'fixture2.pdf').write_bytes(b'placeholder bytes for fixture2')
        correct_hash = hashlib.sha256((sample_dir / 'fixture1.pdf').read_bytes()).hexdigest()

        manifest = [
            {"citekey": "Fixture1", "source": "fixture1.pdf", "sha256": correct_hash},
            {"citekey": "Fixture2", "source": "fixture2.pdf", "sha256": "0" * 64},  # deliberately wrong
            {"citekey": "Fixture3", "source": "missing.pdf"},                       # file absent
            {"citekey": "NoSuchExpected", "source": "fixture1.pdf"},                # not in expected.bib
        ]

        expected_path = td / 'expected.bib'
        expected_path.write_text("""
@Article{Fixture1,
  Author = {Doe, Jane},
  Title = {A Sample Article Title},
  Journaltitle = {A Journal},
}

@Incollection{Fixture2,
  Author = {Roe, John},
  Title = {A Chapter Title},
  Booktitle = {An Edited Volume},
}

@Article{Fixture3,
  Author = {Poe, Ann},
  Title = {A Source That Has Gone Missing},
}
""", encoding='utf-8')
        expected_entries = eval_run.load_bib(expected_path)
        assert set(expected_entries) == {"Fixture1", "Fixture2", "Fixture3"}

        produced_by_source = {
            "fixture1.pdf": _entry("""
@Article{Fixture1,
  Author = {Doe, Jane},
  Title = {A Sample Article Tile},
  Note = {spurious},
}
"""),
            # Model got the type wrong (Article instead of Incollection) -
            # a realistic failure mode this harness exists to catch.
            "fixture2.pdf": _entry("@Article{Fixture2, Author = {Roe, John},}"),
        }

        def fake_run_one(citekey, source_path):
            return produced_by_source[source_path.name]

        entry_scores, warnings = eval_run.evaluate(
            manifest, sample_dir, expected_entries, run_one=fake_run_one,
        )

        assert len(entry_scores) == 2, entry_scores
        by_key = {es.citekey: es for es in entry_scores}
        assert by_key["Fixture1"].type_correct is True
        assert by_key["Fixture2"].type_correct is False

        warning_text = " | ".join(warnings)
        assert "Fixture2" in warning_text and "changed" in warning_text
        assert "Fixture3" in warning_text and "not found" in warning_text
        assert "NoSuchExpected" in warning_text and "no matching entry" in warning_text

        # The report renders without crashing and mentions the type mismatch.
        report = format_report(entry_scores, warnings)
        assert "Fixture2" in report
        assert "incollection" in report and "article" in report

        # --json persists exactly what the console table can't show: the
        # actual expected/produced strings behind a 'different' verdict.
        json_path = td / 'detail.json'
        eval_run.write_json_report(json_path, entry_scores, warnings)
        detail = json.loads(json_path.read_text(encoding='utf-8'))
        assert detail['warnings'] == warnings
        fixture1 = next(e for e in detail['entries'] if e['citekey'] == 'Fixture1')
        title_field = next(f for f in fixture1['fields'] if f['field'] == 'title')
        assert title_field['verdict'] == 'different'
        assert title_field['expected'] == 'A Sample Article Title'
        assert title_field['produced'] == 'A Sample Article Tile'

    return True


class _FakeAgent:
    """Stands in for biblio_agent.BiblioAgent's two methods run_pipeline()
    calls - enough to test the save/reload round trip without the API."""

    def __init__(self, bibtex_by_source):
        self._bibtex_by_source = bibtex_by_source

    def extract_bibtex(self, source_path):
        return self._bibtex_by_source[source_path.name]

    def clean_bibtex(self, bibtex_entry):
        return bibtex_entry


def test_run_pipeline_persists_and_reloads():
    # A run that costs real API calls must not discard its output - saved
    # unconditionally, then read back with no agent involved at all.
    agent = _FakeAgent({
        "ok.pdf": "@Article{Ok, Author = {Doe, Jane}, Title = {A Title},}",
        "fails.pdf": "Error: could not extract text",
    })
    with tempfile.TemporaryDirectory() as td:
        save_dir = Path(td)

        entry = eval_run.run_pipeline(agent, Path("ok.pdf"), citekey="Ok", save_dir=save_dir)
        assert entry is not None and entry.citekey == "Ok"
        assert (save_dir / "Ok.bib").exists()
        reloaded = eval_run.load_last_run(save_dir, "Ok")
        assert reloaded is not None and reloaded.citekey == "Ok"

        # A failed extraction is still saved (as a comment, no parseable
        # entry) rather than leaving no trace of the attempt.
        failed = eval_run.run_pipeline(agent, Path("fails.pdf"), citekey="Fails", save_dir=save_dir)
        assert failed is None
        assert (save_dir / "Fails.bib").exists()
        assert eval_run.load_last_run(save_dir, "Fails") is None

        # No save file at all for a citekey never run.
        assert eval_run.load_last_run(save_dir, "NeverRun") is None
    return True


def test_run_pipeline_prints_failure_cause():
    # Run 1 reported three blank '(no entry produced)' failures with the
    # actual cause (a missing tesseract language pack) sitting unseen in
    # extract_bibtex()'s return value - nothing printed it. Both failure
    # shapes (an "Error: ..." string, and output that parses to nothing)
    # must now surface on stderr.
    agent = _FakeAgent({
        "errors.pdf": "Error: OCR Error: missing language data for deu",
        "unparseable.pdf": "% not a bibtex entry at all",
    })
    with tempfile.TemporaryDirectory() as td:
        err = io.StringIO()
        with redirect_stderr(err):
            entry = eval_run.run_pipeline(agent, Path("errors.pdf"), citekey="Bad",
                                           save_dir=Path(td))
        assert entry is None
        assert "Bad" in err.getvalue() and "missing language data for deu" in err.getvalue()

        err2 = io.StringIO()
        with redirect_stderr(err2):
            entry2 = eval_run.run_pipeline(agent, Path("unparseable.pdf"), citekey="Empty",
                                            save_dir=Path(td))
        assert entry2 is None
        assert "Empty" in err2.getvalue() and "no parseable entry" in err2.getvalue()
    return True


def test_ocr_language_hint_prefers_langid_over_fallback():
    entry = _entry("@Book{X, Author = {Doe, Jane}, Langid = {ngerman},}")
    assert eval_run.ocr_language_hint(entry, fallback="eng") == "deu"
    # No Langid, unmapped Langid, or no expected entry at all: falls back.
    assert eval_run.ocr_language_hint(None, fallback="eng") == "eng"
    no_langid = _entry("@Book{X, Author = {Doe, Jane},}")
    assert eval_run.ocr_language_hint(no_langid, fallback="eng") == "eng"
    unmapped = _entry("@Book{X, Author = {Doe, Jane}, Langid = {klingon},}")
    assert eval_run.ocr_language_hint(unmapped, fallback="rus") == "rus"
    return True


def test_main_rescore_only_restricts_to_named_citekeys():
    # --rescore + --only together: no API, no config.yaml, and only the
    # named citekey scored even though the manifest holds two.
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        sample_dir = td / 'sample'
        sample_dir.mkdir()
        last_run_dir = td / 'last-run'
        last_run_dir.mkdir()

        (sample_dir / 'one.pdf').write_bytes(b'placeholder')
        (sample_dir / 'two.pdf').write_bytes(b'placeholder')
        (sample_dir / 'manifest.json').write_text(json.dumps([
            {"citekey": "One", "source": "one.pdf"},
            {"citekey": "Two", "source": "two.pdf"},
        ]), encoding='utf-8')

        (td / 'expected.bib').write_text("""
@Article{One, Author = {Doe, Jane}, Title = {First},}
@Article{Two, Author = {Roe, John}, Title = {Second},}
""", encoding='utf-8')

        (last_run_dir / 'One.bib').write_text(
            "@Article{One, Author = {Doe, Jane}, Title = {First},}", encoding='utf-8')
        (last_run_dir / 'Two.bib').write_text(
            "@Article{Two, Author = {Roe, John}, Title = {Not Second},}", encoding='utf-8')

        out = io.StringIO()
        with redirect_stdout(out):
            rc = eval_run.main([
                '--sample-dir', str(sample_dir),
                '--expected', str(td / 'expected.bib'),
                '--last-run-dir', str(last_run_dir),
                '--rescore', '--only', 'One',
            ])
        report = out.getvalue()
        assert rc == 0
        assert 'One' in report or '1 entr' in report  # scored exactly one entry
        assert 'Two' not in report
    return True


def test_load_manifest_missing_is_empty_not_error():
    with tempfile.TemporaryDirectory() as td:
        assert eval_run.load_manifest(Path(td)) == []
    return True


def test_load_manifest_reads_json():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / 'manifest.json').write_text(json.dumps([{"citekey": "X", "source": "x.pdf"}]))
        manifest = eval_run.load_manifest(td)
        assert manifest == [{"citekey": "X", "source": "x.pdf"}]
    return True


def test_manifest_extras_keeps_only_hand_added_keys():
    """A rerun of select_sample.py recomputes citekey/source/sha256/note and
    must carry everything else across untouched - `container_source` is a
    judgement about the source that re-selection cannot rederive, so losing it
    would be silent and permanent."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'manifest.json'
        path.write_text(json.dumps([
            {"citekey": "A", "source": "A.pdf", "sha256": "deadbeef",
             "note": "@book — plain (control)", "container_source": "whole volume"},
            {"citekey": "B", "source": "B.pdf", "note": "@article — plain (control)"},
        ]), encoding='utf-8')
        extras = select_sample.manifest_extras(path)
        # Only the hand-added key survives, and only for the item that had one.
        assert extras == {"A": {"container_source": "whole volume"}}, extras
    return True


def test_manifest_extras_survives_a_manifest_it_cannot_read():
    """Preserving annotations must never be the reason a fresh selection
    cannot be written: absent, malformed and wrong-shaped all yield {}."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        assert select_sample.manifest_extras(td / 'nope.json') == {}
        (td / 'bad.json').write_text('{not json', encoding='utf-8')
        assert select_sample.manifest_extras(td / 'bad.json') == {}
        (td / 'object.json').write_text('{"citekey": "A"}', encoding='utf-8')
        assert select_sample.manifest_extras(td / 'object.json') == {}
        (td / 'ragged.json').write_text(
            json.dumps(["a string", {"no": "citekey", "x": 1}]), encoding='utf-8')
        assert select_sample.manifest_extras(td / 'ragged.json') == {}
    return True


TESTS = [
    test_score_entry_all_verdicts,
    test_score_entry_normalizes_whitespace,
    test_score_entry_wrong_type,
    test_score_entry_extraction_failed,
    test_score_entry_excludes_bookkeeping_fields,
    test_aggregate_and_report_do_not_crash_on_empty,
    test_unwrap_collapses_both_wrap_forms,
    test_resolve_attachment_reconstructs_wrapped_local_url,
    test_evaluate_end_to_end,
    test_run_pipeline_persists_and_reloads,
    test_run_pipeline_prints_failure_cause,
    test_ocr_language_hint_prefers_langid_over_fallback,
    test_main_rescore_only_restricts_to_named_citekeys,
    test_load_manifest_missing_is_empty_not_error,
    test_load_manifest_reads_json,
    test_manifest_extras_keeps_only_hand_added_keys,
    test_manifest_extras_survives_a_manifest_it_cannot_read,
]


def main():
    failures = []
    for test in TESTS:
        try:
            assert test() is True
            print(f"  ✓ {test.__name__}")
        except Exception as e:
            failures.append((test.__name__, e))
            print(f"  ✗ {test.__name__}: {e}")

    print()
    if failures:
        print(f"{len(failures)}/{len(TESTS)} test(s) failed.")
        return 1
    print(f"All {len(TESTS)} eval harness self-tests passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
