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

## Corrections

`expected.bib` entries are copied verbatim from `biblio.bib` by
`select_sample.py` - they're ground truth by provenance (written before the
pipeline existed), not by construction, so a hand error in the source
library still reaches `expected.bib` unchanged. Found and fixed by hand,
checked against the physical source, never automatically:

| Date | Citekey | Field | Change | Reason |
|---|---|---|---|---|
| 2026-08-29 | Drabkin1983 | Title | `Riemman`→`Riemann`, `Kunth`→`Kurth` | Misspelled names; both spelled correctly elsewhere in this same sample and in the pipeline's own output for this entry. |
| 2026-08-29 | Dunsby2020 | Title | `piano`→`Piano`, `Op. 27`→`Op.~27` | Chicago title case (`Piano` is a noun, not a function word) and the house `~` non-breaking space before an opus number - CLAUDE.md rules `expected.bib` itself hadn't been swept for. |

Jeong2017's `Score-Informed`/`score-informed` disagreement is deliberately
*not* here: that one is the pipeline's error (a hyphenated-compound
title-case slip CLAUDE.md names explicitly), not a ground-truth defect.

## Model note

CLAUDE.md's own operator note on reference works applies here as everywhere
else: for a sample skewed toward Grove/Stanford Encyclopedia/Wikipedia-style
entries, run with `--model claude-opus-5`.
