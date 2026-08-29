# dev/eval/sample/

The evaluation harness's labelled sample: real sources drawn from actual
intake, each scored against a hand-verified entry in
[`../expected.bib`](../expected.bib). Empty until populated by hand - see
[Issue #6](https://github.com/yrammos/biblatex-chicago-claude/issues/6) for
why this can't be done any other way.

## What goes here

- The source files themselves (`.pdf` / `.webloc`), copied in as-is. They are
  never committed - see the `dev/eval/sample/` entries in the repository
  root's `.gitignore` - since they are typically copyrighted material, not
  this project's own.
- `manifest.json`, which **is** committed: the list connecting each source
  file to its citekey in `expected.bib`, plus a content hash so a source
  silently replaced or corrupted after verification is caught rather than
  scored against a description of a different file.

## manifest.json format

A JSON array of objects:

```json
[
  {
    "citekey": "Smith2020",
    "source": "Smith2020.pdf",
    "sha256": "<sha256 of the source file, for provenance and drift detection>",
    "note": "incollection with an origtitle - awkward-end example"
  }
]
```

- `citekey` (required) - must match an entry in `expected.bib` exactly.
- `source` (required) - filename relative to this directory.
- `sha256` (optional but recommended) - `shasum -a 256 <file>`. If present,
  `dev/eval/run.py` warns (does not fail) when the file on disk no longer
  matches it.
- `note` (optional) - why this source is in the sample; useful once the
  sample is large enough that the stratification isn't obvious from the
  file list alone.
- `insufficient_source` (optional) - a sentence saying that the attached file,
  though the right one, yields too little text to build the entry from, and
  the figures. Present on one source so far. See below.

## `insufficient_source`: the right file, and nothing in it

`Motte2004` yields **232 words** across every page, after OCR, where comparable
sources in this sample yield 1,000-3,100. The pages carry no bibliographic data
at all - a person reading them would fail too - and the expected entry was
written from the physical volume. No extraction can score it right.

The pipeline nonetheless produced a correctly typed entry from it in the
2026-08-29 baseline, indistinguishable in confidence from one built off a full
title page. That is the failure this marks, and it is the same shape as a
bot-challenge page answering HTTP 200: the process succeeded and the content
did not.

Distinct from `container_source`, which is the *wrong granularity* rather than
too little text - there the file is a whole volume and the entry is one chapter
of it. Both are entries no extraction can score, for different reasons, and the
names are kept apart so a report can say which.

Nothing consumes either key yet. `dev/eval/select_sample.py` carries them, and
any other key it did not itself generate, across a regeneration of the manifest.

## Composition

Per Issue #6: 40-60 sources, stratified by entry type and deliberately
weighted toward the awkward end - `@incollection`, `@review`, `@letter`,
translations with an `origtitle`, reference-work articles, non-English
titles. A sample that only ever exercises `@article` in English measures
nothing that CLAUDE.md's harder rules are actually for.
