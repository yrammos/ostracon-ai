#!/usr/bin/env python3
"""Self-tests for src/house_style.py: the library's normalizer rules applied
to one new entry, and values snapped to the library's spelling.

Fixtures are constructed (a three-entry library), not recorded from the
maintainer's data. Not stubbed: bib_normalize's rules and bib_audit's scanner.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import house_style  # noqa: E402

LIBRARY = r"""@thesis{A,
	author = {A, B},
	title = {T},
	type = {PhD diss.},
	location = {Cambridge, MA},
	institution = {Harvard University}}

@book{B,
	author = {C, D},
	title = {U},
	location = {Vienna},
	publisher = {Oxford University Press}}

@book{C,
	author = {E, F},
	title = {V},
	location = {Vienna},
	publisher = {Oxford University Press}}
"""


def _library():
    td = tempfile.mkdtemp()
    p = Path(td) / "lib.bib"
    p.write_text(LIBRARY, encoding="utf-8")
    return p


def test_normalize_applies_the_library_rules_in_the_entrys_own_style():
    entry = "@book{X,\n\tTitle = {Music: A Very Short Introduction},\n\tPages = {12–34},\n\tIsbn = {123},\n}"
    out, rules = house_style.normalize(entry)
    assert "Title = {Music}" in out and "Subtitle = {A Very Short Introduction}" in out, out
    assert "Pages = {12-34}" in out and "Isbn" not in out, out
    assert {"split-title", "range-punctuation"} <= set(rules), rules


def test_drop_fields_removes_the_users_list_only():
    entry = "@book{X,\n  Title = {T},\n  Pagetotal = {311},\n  Month = {3},\n  Edition = {2},\n}"
    out, dropped = house_style.drop_fields(entry, ["pagetotal", "month", "issue"])
    assert dropped == ["pagetotal", "month"] and "Edition = {2}" in out and "Pagetotal" not in out, out
    assert house_style.drop_fields(entry, None) == (entry, [])


def test_normalize_leaves_a_clean_entry_alone():
    entry = "@book{X,\n\tTitle = {Music},\n\tDate = {2001},\n}"
    assert house_style.normalize(entry) == (entry, [])


def test_snap_takes_the_librarys_spelling_for_an_equivalent_value():
    lib = _library()
    entry = ("@thesis{X,\n\tType = {Ph.D. diss.},\n\tLocation = {cambridge,~ma},\n"
             "\tPublisher = {oxford university press},\n}")
    out, changes = house_style.snap_to_library(entry, lib)
    assert "Type = {PhD diss.}" in out and "Location = {Cambridge, MA}" in out, out
    assert "Publisher = {Oxford University Press}" in out
    assert len(changes) == 3, changes


def test_snap_leaves_values_the_library_does_not_hold():
    lib = _library()
    entry = "@book{X,\n\tLocation = {Wien},\n\tPublisher = {Brill},\n}"
    assert house_style.snap_to_library(entry, lib) == (entry, [])


def test_snap_unwraps_a_language_wrapper_the_library_does_not_use():
    lib = _library()
    entry = "@book{X,\n\tLocation = {\\foreignlanguage{ngerman}{vienna}},\n}"
    out, _ = house_style.snap_to_library(entry, lib)
    assert "Location = {Vienna}" in out, out


def test_no_library_no_snapping():
    entry = "@book{X,\n\tLocation = {vienna},\n}"
    assert house_style.snap_to_library(entry, None) == (entry, [])
    assert house_style.snap_to_library(entry, "/nonexistent/lib.bib") == (entry, [])


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
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} house-style self-tests passed.")
    sys.exit(1 if failed else 0)
