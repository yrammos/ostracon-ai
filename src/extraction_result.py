#!/usr/bin/env python3
"""
What extract_bibtex() hands to save_entry(): the entry text, and the review
state that belongs to it.

Before this existed the two functions communicated through `%` comment markers
prepended to the entry text, which save_entry() recovered by matching each one
positionally with an anchored re.match and slicing it off. Three properties of
that arrangement all bit within a month of each other:

- Order was load-bearing and unchecked. Adding a marker meant prepending it in
  one function and inserting its matcher at exactly the right point in the
  other, in two places that named each other only in comments.
- A failed match was silent, and cascaded. `% Source:` arrived with an
  extraction regex that could never match (`Source:\\b` - no word boundary can
  exist between ':' and a space), and because re.match anchors at 0 and that
  marker was outermost, every matcher below it then ran against a string still
  starting `% Source:` and failed too. BibDesk's amber colouring stopped
  working for every entry and every source type, from 2026-07-30 until it was
  found. Nothing anywhere reported a problem. See issues #17 and #18.
- The carrier was lossy, and differed by branch. Under autofile_bibdesk the
  importer re-serializes each entry and discards every `%` comment, so
  comment-carried state survived only on the plain-text-append branch and
  colour-carried state only on the BibDesk branch.

The markers remain as an OUTPUT format - they are how provenance reaches the
saved .bib text, and they are worth having there. They are no longer an
interchange format between two functions.

This module deliberately imports nothing beyond the standard library, so the
evaluation harness can construct a result without pulling in the Anthropic
client or pypdf. A stub that returns the shape the caller expects rather than
the shape the system emits is how the last round of testing missed a live
fault; sharing the real class is what stops that here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ExtractionResult:
    """One source's extraction: the entry, plus what is known about it.

    `error` set means extraction did not produce an entry, and `entry` is
    meaningless. Every other field describes an entry that exists.
    """

    entry: str = ''

    # Extraction failed. Carries the human-readable reason, already prefixed
    # "Error: " so it reads the same wherever it is printed.
    error: Optional[str] = None

    # Which kind of source and which locator produced this - "PDF (paper.pdf)",
    # "webpage (https://...)". Written to the saved text as "% Source: ...".
    source_label: Optional[str] = None

    # A field could not be confirmed or refuted against CrossRef/Scholar, or
    # the source was thin. Becomes BibDesk's review colour; never written to
    # the text, because the colour is where it belongs.
    needs_color: bool = False

    # web_source.py's plausibility check found the source genuine but thin or
    # sparse. Written as "% AMBER: <reason>", which is the only carrier that
    # survives on the plain-text-append branch, where no colour is possible.
    amber_reason: Optional[str] = None

    # Per-field provenance from enrichment - "CrossRef: volume, pages".
    # Written as "% Sources -- ...".
    field_sources: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.error is not None

    def comment_lines(self) -> list:
        """The `%` comments this result contributes, outermost first.

        `needs_color` is absent by design: it is state about the entry, not a
        fact about it, and it reaches the reader as a colour. An INCOMPLETE
        comment is not here either - that is determined at save time, from the
        finished entry, not by extraction.
        """
        lines = []
        if self.source_label:
            lines.append(f"% Source: {self.source_label}")
        if self.amber_reason:
            lines.append(f"% AMBER: {self.amber_reason}")
        if self.field_sources:
            lines.append(f"% Sources -- {self.field_sources}")
        return lines
