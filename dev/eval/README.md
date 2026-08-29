# Evaluation harness

Measures whether an entry is right, rather than just what a run costs
(`NOTES.md` already covers the latter). See
[Issue #6](https://github.com/yrammos/biblatex-chicago-claude/issues/6).

```bash
python3 dev/eval/run.py                       # score dev/eval/sample/ against dev/eval/expected.bib
python3 dev/eval/run.py --model claude-opus-5  # score with a different model
python3 dev/eval/test_eval.py                  # exercise the harness itself, no sample/API needed
```

## How it fits together

- [`sample/`](sample/) - real sources plus `manifest.json`, the list
  connecting each source file to a citekey. See `sample/README.md` for its
  format. Empty until populated by hand.
- [`expected.bib`](expected.bib) - one hand-verified entry per citekey in
  the manifest, checked against the physical source rather than a
  catalogue. Empty until populated by hand.
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

## Model note

CLAUDE.md's own operator note on reference works applies here as everywhere
else: for a sample skewed toward Grove/Stanford Encyclopedia/Wikipedia-style
entries, run with `--model claude-opus-5`.
