#!/usr/bin/env python3
"""Self-tests for src/bib_render.py.

dropped_fields() runs on rendered text recorded 2026-11-03 under the
maintainer's preview template (constructed entries). The integration test
compiles, and needs pdflatex, biber, pdftotext and the template
($OSTRACON_PREVIEW_TEMPLATE or ~/.local/share/bibdesk/previewtemplate.tex);
it is skipped without them.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import bib_render  # noqa: E402
import bib_validate  # noqa: E402

UNPUBLISHED = r"""@unpublished{U,
  Author = {Doe, Jane},
  Title = {A Talk on Things},
  Edition = {2},
  Volume = {3},
  Date = {2001},
  Note = {\autocap{p}aper presented at the Annual Meeting},
}"""
UNPUBLISHED_FIELDS = {"author": "Doe, Jane", "title": "A Talk on Things", "edition": "2",
                      "volume": "3", "date": "2001",
                      "note": r"\autocap{p}aper presented at the Annual Meeting"}
# Recorded: Edition and Volume never reach the page on @unpublished.
UNPUBLISHED_RENDERED = """Bibliography:
Doe, Jane. “A Talk on Things.” Paper presented at the Annual Meeting, 2001.
Full:
Jane Doe, “A Talk on Things” (paper presented at the Annual Meeting, 2001).
Short:
Doe, “A Talk on Things.”
"""

REVIEW_FIELDS = {
    "title": r"\bibstring{reviewof} \mkbibemph{Authenticities: Philosophical Reflections "
             r"on Musical Performance}, \bibstring{by} Peter Kivy",
    "date": "2012/2013",
    "pages": "132-135",
}
# Constructed from two recorded renders: Dunsby1997's bibliography line (the
# template), with Marvin2012's year range, which prints elided as 2012–13.
REVIEW_RENDERED = ("Dunsby, Jonathan. Review of Authenticities: Philosophical Reflections on "
                   "Musical Performance, by Peter Kivy. Music & Letters 78, no. 1 (2012–13): 132–135.")


def test_unprinted_fields_are_reported():
    assert bib_render.dropped_fields(UNPUBLISHED_FIELDS, UNPUBLISHED_RENDERED) == \
        ["edition", "volume"]
    return True


def test_bibstrings_and_elided_ranges_are_not_reported():
    assert bib_render.dropped_fields(REVIEW_FIELDS, REVIEW_RENDERED) == []
    return True


def test_render_integration():
    if not (bib_render.template_path().is_file() and bib_validate.available()
            and bib_validate.tool("pdftotext")):
        print("    (skipped: template or TeX tools not found)")
        return True
    rendered = bib_render.render(UNPUBLISHED, "U")
    assert bib_render.dropped_fields(UNPUBLISHED_FIELDS, rendered) == ["edition", "volume"]
    try:
        bib_render.render("@book{B,\n  Title = {A_B},\n}", "B")
    except bib_render.RenderFailed:
        return True
    raise AssertionError("an unescaped _ compiled")


TESTS = [test_unprinted_fields_are_reported,
         test_bibstrings_and_elided_ranges_are_not_reported,
         test_render_integration]


def main():
    failures = 0
    for test in TESTS:
        try:
            assert test() is True
            print(f"  ✓ {test.__name__}")
        except Exception as e:
            failures += 1
            print(f"  ✗ {test.__name__}: {e!r}")
    print()
    print(f"{failures}/{len(TESTS)} test(s) failed." if failures
          else f"All {len(TESTS)} bib_render self-tests passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
