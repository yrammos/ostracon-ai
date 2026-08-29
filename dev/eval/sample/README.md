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
- `container_source` (optional) - a sentence saying that the attached file is
  the whole containing volume rather than the fragment the entry describes,
  and what the fragment is. Present on four sources so far. See below.
- `insufficient_source` (optional) - a sentence saying that the attached file,
  though the right one, yields too little text to build the entry from, and
  the figures. Present on one source so far. See below.

## `container_source`: entries no extraction can score

Four sample entries describe a chapter, essay or introduction whose attached
PDF is a scan of the entire book:

| Citekey | PDF pages | The entry is |
|---|---|---|
| Adorno2011 | 588 | chapter 28 of *Baroque Music* |
| Roudiez1984 | 310 | a 10-page translator's introduction |
| Cavell1969a | 197 | one essay |
| Schenker1994a | 146 | one 8-page essay |

Linking a whole-volume scan to a chapter-level entry is ordinary practice in a
personal library, and the entries are correct. But the file does not record
*which* chapter is meant, so no amount of reading further into it recovers the
answer - a person handed the same PDF and nothing else would fail in the same
way. The pipeline duly describes the container: all four came back typed
`collection`, `book`, `book`, `book` in the 2026-08-29 baseline, and the
correspondence with the page-count ratio is exact.

The key marks them so that a reader of a report knows which failures are the
pipeline's. Nothing consumes it yet; teaching `run.py` and `scorer.py` to
honour it belongs with the same decision for a starved source
([#16](https://github.com/yrammos/biblatex-chicago-claude/issues/16)), which
proposes `insufficient_source` for a different cause - the file is the right
one but yields almost no text. The two are kept apart deliberately.

`dev/eval/select_sample.py` carries `container_source`, and any other key it
did not itself generate, across a regeneration of this file. It computes
`citekey`, `source`, `sha256` and `note`; everything else belongs to whoever
wrote it.

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
