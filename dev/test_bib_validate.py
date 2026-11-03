#!/usr/bin/env python3
"""Self-tests for src/bib_validate.py.

The classify() fixtures are biber log lines recorded on 2026-11-03 from a
real `biber --validate-datamodel` run (TeX Live 2026) over the constructed
entries in DEFECTIVE, plus lines built to the same grammar for the skip
rules. The integration tests need pdflatex and biber and are skipped
without them.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import bib_validate  # noqa: E402

DEFECTIVE = """@article{A,
  Author = {Doe, Jane},
  Title = {T},
  Journaltitle = {J},
  Publisher = {P},
  Date = {2001},
}
@unpublished{U,
  Author = {Doe, Jane},
  Title = {Talk},
  Edition = {2},
  Volume = {3},
  Date = {2001},
}
@article{M,
  Entrysubtype = {magazine},
  Title = {Unsigned},
  Journaltitle = {J},
  Date = {2001-02},
}
"""

RECORDED_LOG = """\
[172] Biber.pm:132> WARN - Datamodel: article entry 'A' (v.bib): Invalid field 'publisher' for entrytype 'article'
[172] Biber.pm:132> WARN - Datamodel: unpublished entry 'U' (v.bib): Invalid field 'edition' for entrytype 'unpublished'
[172] Biber.pm:132> WARN - Datamodel: unpublished entry 'U' (v.bib): Invalid field 'volume' for entrytype 'unpublished'
[172] Biber.pm:132> WARN - Datamodel: article entry 'M' (v.bib): Missing mandatory field 'author'
[172] Biber.pm:134> INFO - WARNINGS: 4
"""

# Built to the grammar above, one per skip rule that needs entry facts.
SKIPPED = """@online{O,
  Title = {Page},
  Url = {https://example.org},
  Urldate = {2026-01-01},
}
@book{B,
  Editor = {Doe, Jane},
  Title = {Collected},
  Year = {\\bibstring{nodate}},
}
@unpublished{T,
  Author = {Doe, Jane},
  Title = {Talk},
  Eventdate = {2001-05-01},
}
"""
SKIPPED_LOG = """\
[1] Biber.pm:132> WARN - Datamodel: online entry 'O' (v.bib): Missing mandatory field - one of 'date, year' must be defined
[1] Biber.pm:132> WARN - Datamodel: book entry 'B' (v.bib): Missing mandatory field 'author'
[1] Biber.pm:132> WARN - Datamodel: book entry 'B' (v.bib): Invalid value of field 'year' must be datatype 'datepart' - ignoring field
[1] Biber.pm:132> WARN - Datamodel: unpublished entry 'T' (v.bib): Missing mandatory field - one of 'date, year' must be defined
[1] Biber.pm:132> WARN - Datamodel: online entry 'O' (v.bib): Field 'date-added' invalid in data model - ignoring
"""


def test_classify_keeps_real_defects_and_drops_documented_usage():
    # Both types have field lists in blx-dm.def, so their field warnings stand.
    typed = {"article", "unpublished"}
    found = sorted(bib_validate.classify(RECORDED_LOG, typed, bib_validate.entry_facts(DEFECTIVE)))
    assert found == [
        ("A", "@article: Invalid field 'publisher' for entrytype 'article'"),
        ("U", "@unpublished: Invalid field 'edition' for entrytype 'unpublished'"),
        ("U", "@unpublished: Invalid field 'volume' for entrytype 'unpublished'"),
    ], found
    return True


def test_classify_skip_rules():
    found = list(bib_validate.classify(SKIPPED_LOG, set(), bib_validate.entry_facts(SKIPPED)))
    assert found == [], found
    # The same warnings without the facts that excuse them are kept.
    bare = "@online{O,\n}\n@book{B,\n}\n@unpublished{T,\n}\n"
    kept = list(bib_validate.classify(SKIPPED_LOG, set(), bib_validate.entry_facts(bare)))
    assert len(kept) == 4, kept  # all but the bookkeeping field
    return True


def test_validate_integration():
    if not bib_validate.available():
        print("    (skipped: pdflatex/biber not found)")
        return True
    assert bib_validate.validate(DEFECTIVE) == sorted(
        bib_validate.classify(RECORDED_LOG, bib_validate.typed_entrytypes(),
                              bib_validate.entry_facts(DEFECTIVE)))
    # The template validates clean: a warning here is a classify() gap or a
    # template defect.
    template = (ROOT / "prompt-context" / "biblio-template.bib").read_text(encoding="utf-8")
    assert bib_validate.validate(template) == [], bib_validate.validate(template)
    return True


def test_failed_compile_raises_instead_of_reporting_clean():
    if not bib_validate.available():
        print("    (skipped: pdflatex/biber not found)")
        return True
    original = bib_validate.DOC
    bib_validate.DOC = "\\documentclass{article}\\undefinedcommand\n"
    try:
        bib_validate.validate(DEFECTIVE)
    except bib_validate.ValidatorUnavailable:
        return True
    finally:
        bib_validate.DOC = original
    raise AssertionError("a failed compile returned a verdict")


TESTS = [
    test_classify_keeps_real_defects_and_drops_documented_usage,
    test_classify_skip_rules,
    test_validate_integration,
    test_failed_compile_raises_instead_of_reporting_clean,
]


def main():
    failures = 0
    for test in TESTS:
        try:
            assert test() is True
            print(f"  ✓ {test.__name__}")
        except Exception as e:
            failures += 1
            print(f"  ✗ {test.__name__}: {e}")
    print()
    print(f"{failures}/{len(TESTS)} test(s) failed." if failures
          else f"All {len(TESTS)} bib_validate self-tests passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
