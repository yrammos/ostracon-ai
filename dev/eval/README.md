# Evaluation harness

Measures whether an entry is right, rather than just what a run costs
(`NOTES.md` already covers the latter). See
[Issue #6](https://github.com/yrammos/biblatex-chicago-claude/issues/6).

```bash
python3 dev/eval/run.py                       # score dev/eval/sample/ against dev/eval/expected.bib
python3 dev/eval/run.py --model claude-opus-5  # score with a different model
python3 dev/eval/run.py --json out.json        # also persist per-field expected/produced values
python3 dev/eval/run.py --rescore              # re-score dev/eval/last-run/, no API call
python3 dev/eval/run.py --only Foo2019,Bar2020 # extract/score only these citekeys
python3 dev/eval/test_eval.py                  # exercise the harness itself, no sample/API needed
```

`--only` restricts a live run to the named citekeys - for a targeted
diagnostic (does fixing X also fix this one entry?) without paying to
re-extract the other 50-odd.

`--json` exists because the console report only carries aggregate counts - the
actual string behind a `different` or `spurious` verdict is otherwise gone the
moment the process exits, and this makes live API calls, so a run is not worth
repeating just to see a value that scrolled past.

Every live run also saves each produced entry to `last-run/<citekey>.bib` as
it's produced, whether or not it scored well - a failed extraction is saved
too, as a `% extraction failed: ...` comment, and its cause is printed to
stderr rather than left sitting unseen in the return value (run 1 reported
three blank `(no entry produced)` failures with the actual cause - a missing
tesseract language pack - never surfaced anywhere). `--rescore` reads that
directory back and scores against it instead of running the pipeline again:
re-scoring and re-extracting are different operations, and only the second
costs money or needs `biblio_agent`/`config.yaml`/`anthropic` at all. Useful
after a `scorer.py` or `expected.bib` change that shouldn't need fresh
extractions to check.

A live run also sets `interactive_ocr: false` for the duration, so a scanned
sample source can't stall an unattended run on the OCR-language dropdown -
each source's own `Langid` in `expected.bib` stands in as the hint (see
`ocr_language_hint()`), falling back to `default_ocr_language` where absent
or unmapped.

## How it fits together

- [`select_sample.py`](select_sample.py) - builds `sample/` and
  `expected.bib` together from `biblio.bib`, a stripped copy of the
  library placed by hand and gitignored because it quotes the library (see
  `.gitignore`). Stratifies by entry type and feature coverage, resolves
  each candidate's attachment via `populate_sample.py`'s
  `resolve_attachment()`, and copies only entries with a live source
  predating the pipeline - so every `expected.bib` entry is ground truth
  written before extraction existed, not a catalogue lookup.
  `dev/eval/biblio.bib` must exist before running it.
- [`sample/`](sample/) - real sources plus `manifest.json`, the list
  connecting each source file to a citekey. See `sample/README.md` for its
  format. Empty until populated by `select_sample.py`.
- [`expected.bib`](expected.bib) - one ground-truth entry per citekey in
  the manifest, copied verbatim from `biblio.bib` by `select_sample.py`.
  Empty until populated that way.
- [`run.py`](run.py) - for each manifest item with both a source file and a
  matching `expected.bib` entry: runs the real extraction pipeline on the
  source, parses its output, and scores it against the expected entry.
- [`scorer.py`](scorer.py) - the actual field-by-field comparison: for every
  field in the expected entry, whether the pipeline's output has it and
  matches (`exact`), has it but disagrees (`different`), or lacks it
  (`missing`); plus any field the output has that the expected entry
  doesn't (`spurious`). Entry-type correctness is reported separately from
  the field tally, since a wrong type invalidates everything else the entry
  got right.
- [`test_eval.py`](test_eval.py) - runs `scorer.py` and `run.py`'s
  orchestration (`evaluate()`) against a synthetic fixture pair built at
  test time, with the extraction step stubbed out. Makes no API call, reads
  no real sample, and needs no `config.yaml` - so it can run (and does run,
  as part of reviewing any change here) with nothing populated yet.

Re-scoring is meant to be cheap enough to run after any `CLAUDE.md` edit -
that's the entire point of having this harness rather than an impression.

## Reading a report

```
Entry type: 8/10 correct (80%)
  Foo2019: expected 'incollection', got 'article'

Field-level results:

Field    Exact  Different  Missing  Spurious
-------  -----  ---------  -------  --------
author   9      0          1        0
title    10     0          0        0
```

A wrong entry type is called out by name, since everything else scored for
that entry is downstream of a wrong assumption about what kind of source it
is. Field counts run across every scored entry regardless of its type
verdict - "ninety per cent correct" hides whether the tenth is a page range
or an author, which is exactly what this table is for.

## What this harness cannot measure

It scores fields against ground truth. **Confidence is not a field.** An entry
can score fully correct while carrying a review flag it should not, or missing
one it should, and nothing here will notice either way.

That is not hypothetical: BibDesk's amber colouring was inert for every entry
and every source type from 2026-07-30 until 2026-08-29, and no run of this
harness was capable of reporting it (Issue
[#17](https://github.com/yrammos/biblatex-chicago-claude/issues/17)). A change
to the flagging mechanism therefore needs its own tests -
`dev/test_biblio_agent_markers.py` and `dev/test_extract_pages.py` - and, where
BibDesk itself is involved, an import done by hand. A green report here is
silent on all of it.

Two manifest keys mark sample entries no extraction can score at all, for two
different reasons - `container_source` (the file is the whole volume, the entry
is one chapter of it) and `insufficient_source` (the right file, yielding almost
no text). Neither is consumed by the scorer yet: they are read by whoever reads
a report. See [`sample/README.md`](sample/README.md).

**Nor can it tell a field read from the source apart from one the model
recalled.** Both score `exact`. The harness compares strings; it has no view of
where a value came from, so a correct guess and a correct reading are worth the
same to it.

Gollin2011a is the worked example. Its source is three pages of glossary text,
and of the six values in the ground-truth entry, **five appear nowhere in the
PDF** - not `Oxford Handbook`, not `Neo-Riemannian`, not `Oxford University
Press`, not `Oxford`, not `2011`, not `579`. Only the page-3 running header
(`GLOSSARY 581`) is actually in the file. The 2026-08-29 baseline scored this
entry as correct; it was correct by recollection.

So **a fall in `exact` is not necessarily a regression.** A change that stops
the model supplying values the source does not contain will score as a loss on
every entry whose ground truth was only ever reachable by supplying them. This
bears directly on how the `exact 249 -> 240` in
[`baselines/2026-08-29-integration.md`](baselines/2026-08-29-integration.md) is
read: part of that -9 is the pipeline declining to guess, and the table cannot
say which part. Read `spurious` and `missing` alongside it, and read the entries
themselves before calling any of it a regression.

The grounding audit (`verify_and_flag_recollection()`) is the mechanism that
does know the difference, and its verdict reaches BibDesk as a colour rather
than the report - which is the previous point again, from the other side.

## Corrections

`expected.bib` is edited by the maintainer only. It is the one file here
whose plausible-looking error is invisible, because everything else in this
harness is measured against it.

`expected.bib` entries are copied verbatim from `biblio.bib` by
`select_sample.py` - they're ground truth by provenance (written before the
pipeline existed), not by construction, so a hand error in the source
library still reaches `expected.bib` unchanged. Found and fixed by hand,
checked against the physical source, never automatically:

| Date | Citekey | Field | Change | Reason |
|---|---|---|---|---|
| 2026-08-29 | Drabkin1983 | Title | `Riemman`→`Riemann`, `Kunth`→`Kurth` | Misspelled names; both spelled correctly elsewhere in this same sample and in the pipeline's own output for this entry. |
| 2026-08-29 | Dunsby2020 | Title | `piano`→`Piano`, `Op. 27`→`Op.~27` | Chicago title case (`Piano` is a noun, not a function word) and the house `~` non-breaking space before an opus number - CLAUDE.md rules `expected.bib` itself hadn't been swept for. |
| 2026-08-29 | Menke2004 | Author | `Johaness`→`Johannes` | Misspelled given name, found via the `author` field's run-1/run-2 comparison in `dev/eval/baselines/2026-08-29.md`; correctly spelled in the pipeline's own output for this entry. |
| 2026-08-29 | Cavell1969a | Pages | `180-2012`→`180-201` | Typo: the source runs to 197 pages, so the range was impossible on its face. Reported as #20. |
| 2026-08-29 | Arndt2014 | Title | `Robert P. Morgan`→`Robert~P. Morgan` | Internal inconsistency: the same file wrote `Lee~A. Rothfarb`, so no output could score exact on both. Settled on the unspaced-tie form throughout. Reported as #24. |

Jeong2017's `Score-Informed`/`score-informed` disagreement is deliberately
*not* here: that one is the pipeline's error (a hyphenated-compound
title-case slip CLAUDE.md names explicitly), not a ground-truth defect.

## Model note

CLAUDE.md's own operator note on reference works applies here as everywhere
else: for a sample skewed toward Grove/Stanford Encyclopedia/Wikipedia-style
entries, run with `--model claude-opus-5`.
