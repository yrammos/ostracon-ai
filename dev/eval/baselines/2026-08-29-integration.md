# Run — 2026-08-29, integration of PRs #22/#25/#26/#27/#28/#29

Not a new baseline. `2026-08-29.md` remains the reference point; this records
one full run of the merged state of six pull requests against it, made to settle
what the individual branches could not measure.

## Provenance

Same sample and same model as the baseline: 61 sources, `claude-sonnet-4-6`, no
`--model` override. **Not the same ground truth**: `expected.bib` was corrected in
`7155812` (2026-08-29 21:04:11 +0200) between the baseline run and this one, and
this branch contains that correction. Every figure below that compares the two runs
was obtained by re-scoring both against the corrected file — see the note on the
field table. Run from the branch
`integration-2026-08-29`, which merges the six PRs in the order given in their
bodies. Produced entries written to a scratch directory, so
`dev/eval/last-run/` still holds the baseline's own output and remains what
`--rescore` reads.

No entry was written to BibDesk or to `main_bib_file`: `run_pipeline()` calls
`extract_bibtex()` and `clean_bibtex()` only, never `save_entry()`.

## Entry type: 51/61 (84%), from 50/61 (82%)

One change, in the intended direction, and it is the one the prompt could reach:

| Citekey | Baseline | This run |
|---|---|---|
| Heidegger1993 | `inbook` → `incollection` | **correct** |

Everything else on the baseline's mismatch list is unmoved, entry for entry:
Adorno2011, Ayrey2006, Cavell1969a, Hultqvistnodate, Jeong2017, Kranenburg2008,
Monelle2008, Roudiez1984, Schenker1994a, ScienceDirect0.

Four of those (Adorno2011, Cavell1969a, Roudiez1984, Schenker1994a) are the
`container_source` entries — the attached PDF is the whole volume and does not
record which fragment the entry describes. No extraction can score them.

## Field level: the trade this run actually made

**These figures are like for like.** `expected.bib` changed twice on 2026-08-29
(`4dff992`, Menke2004's author; `7155812`, Arndt2014's title and Cavell1969a's
pages), so the field table published in `2026-08-29.md` was computed against a
ground truth that no longer exists. The "Baseline" column below is therefore **not**
that table: it is the baseline run's own saved output, re-scored against
`expected.bib` as it now stands, so both columns face the same ground truth.

| Verdict | Baseline (re-scored) | This run | Delta |
|---|---|---|---|
| exact | 249 | 240 | **−9** |
| different | 117 | 117 | 0 |
| missing | 94 | 103 | **+9** |
| spurious | 83 | 67 | **−16** |

The totals happen to match `2026-08-29.md`'s published figures exactly, which is a
coincidence and not a reason to trust them: two fields moved in opposite directions.
`author` goes 33 → 34 (Menke2004, fixed after that table was computed) and `title`
36 ← 37 (Arndt2014, fixed by `7155812`). Anyone comparing a future run against the
published table rather than against a re-score will be off by those two.

Read as one sentence: **sixteen fields the pipeline used to invent, it no longer
invents; nine it used to get right, it now omits.** Nothing moved into
`different`, which is the verdict that matters most — a value that is present and
wrong.

That is the trade PR #26's Booktitle rule asks for in as many words ("a field left
empty is a gap someone can see and fill; a field filled with the wrong work reads
as evidence and will not be questioned"), and it is the trade #16 argues for
("getting these wrong confidently is a worse failure than getting them wrong
loudly"). Whether nine lost `exact` is worth sixteen fewer inventions is a
judgement about the maintainer's workflow, not a fact this harness settles. It is
the single most important thing to look at before merging #26.

The largest single component is `entrysubtype`, whose spurious count falls 7 → 1.

## Fields that moved

| Field | exact | spurious |
|---|---|---|
| entrysubtype | 1 → 1 | **7 → 1** |
| chapter | 0 → 0 | 2 → 0 |
| publisher | 13 → 13 | 4 → 2 |
| shorttitle | **0 → 2** | 2 → 1 |
| author | **33 → 34** | 2 → 1 |
| volumes | 1 → 2 | — |
| title | 37 → 34 | 0 → 0 |
| date | 32 → 30 | 4 → 3 |
| editor | 11 → 9 | 2 → 2 |
| pages | 19 → 17 | 3 → 3 |
| location | 16 → 15 | 4 → 3 |
| type | 5 → 4 | — |
| foreword, booksubtitle | 1 → 0 | — |

## Attribution of the fourteen lost `exact` verdicts

Attribution is per entry, against the baseline's saved output. It is not clean —
the baseline documents a ≈1.6% type-level variance floor and an unexplained
`author` drift between two runs of an identical pipeline — so the column below is
a reading, not a measurement.

**Plausibly caused by the omit-rather-than-invent rule (PR #26).** Fields that
became `missing`: Greenhead2016 `pages` and `publisher`, Ericson2022 `booktitle`,
Genosko1998 `lista`, Zemtsovsky2016 `booksubtitle`, Gollin2011a `location` and
`publisher`. The same rule is also responsible for most of the −16 spurious.

**Known-unstable entries, unchanged in kind from the baseline's own findings.**

- **Gollin2011a** — `title` becomes `Glossary` rather than the book's title, with
  `date` 2017 and `pages` 581. Type still correct.

  **Investigated after this file was first written; the reading above was wrong.**
  It is not the `@Suppbook` exception being diluted, and it is not the baseline's
  unattributable flip. The source is three pages of glossary text, and **five of
  the six values in the ground-truth entry appear nowhere in it** — no `Oxford
  Handbook`, no `Neo-Riemannian`, no `Oxford University Press`, no `Oxford`, no
  `2011`, no `579`. The only container-ish token in the file is the page-3 running
  header `GLOSSARY 581`, which is where `581` comes from; `2017` is the PDF's
  `CreationDate`, a scan timestamp. The model said as much in its output — "no
  parent book information visible ... the most honest entry I can produce is a
  minimal `@Suppbook`" — and chose the right type, named it, and justified it. The
  exception's first instruction survived intact.

  So this is the omit-rather-than-invent rule working as written, on an entry
  whose ground truth was only ever reachable by supplying values the source does
  not contain. **The baseline's "correct" entry was correct by recollection.**
  `dev/eval/README.md` now records this as the worked example of a limitation this
  harness has: it cannot tell a field read from the source apart from one the model
  recalled, and a fall in `exact` is therefore not necessarily a regression. Part
  of the −9 above is exactly that, and the table cannot say which part.

  The placement defect the earlier note suspected is real but was not the cause,
  and is fixed independently: the container-provenance bullet had been inserted
  between the Title rule and the `Exception:` that overrides it, displacing the
  exception's antecedent. Transposed back, verified as a pure move (same rendered
  length, same multiset of lines).

  One thing found while investigating, and worse than this entry's score: the
  model's output here was **prose with a fenced block**, and `clean_bibtex()`
  anchored on a backtick-quoted `@Suppbook` inside that prose. Both of
  `save_entry()`'s guards pass on the result. Filed as #32.
- **Monelle2008** — `title` `The Musical Sublime` → `Can It Be All So
  Simple---Remix`. The source's first page runs title, author and opening subject
  heading together with no distinction surviving OCR. Run 1 gave the wrong
  reading, the baseline the right one, this run the wrong one again.
- **Motte2004** — `editor` particle order. This is the 232-word source; its
  editor comes from enrichment, not from the page.
- **Roudiez1984** — `date` 1984 → 2024. A `container_source` entry.
- **Jeong2017** — `Score-Informed` → `Score-informed`, the hyphenated-compound
  title-case slip this file's Corrections section already names as a known
  pipeline error rather than a ground-truth one.

**Possibly caused by this session's changes, and worth checking.**

- **Heidegger1993 `editor`** — `Krell, David Farrell` → `Krell, David~Farrell`.
  PR #25 added "a forename followed by an initial still takes the non-breaking
  tie" inside the `@Review` clause; `David Farrell` is two forenames, not an
  initial, and this is a different entry type. Reads as the instruction
  generalising past its clause.
- **Byom2023 `type`** — `White Paper` → `white paper`. PR #29 repaired the three
  `\bibstring` occurrences in the `@Thesis`/`@Report` guidance, which previously
  reached the model as `\x08ibstring`. Byom2023 is the sample's `@Report`. A
  lowercase descriptive term is what a bibstring looks like, so the repair may be
  pulling the value toward that form.
- **Albright2021 `foreword`** — `Rehding, Alexander` → `yes`, with
  `introduction` newly spurious. Not attributable to any change here; recorded
  because CLAUDE.md's tier-1 note on `@SuppBook` says the style reads these
  fields' *presence* and never prints the value, which makes `yes` less obviously
  wrong than it looks.

## What the run settled that the branches could not

- **PR #27's refactor ran end to end over 61 real sources**, with enrichment,
  the grounding audit and reconciliation all live. No extraction failure, no
  unparseable entry, 61 of 61 produced. The branch itself had no live run at all.
- **PR #28's thin-yield floor fired exactly once**, on the 232-word source, with
  the reason naming both figures:

  ```
  ⚠️  Thin/sparse source (thin extraction: 232 words from the whole PDF
      (under 400) - the text may not carry the work's bibliographic data
      at all) - flagging for review
  ```

  **Zero false positives across 61 sources.** That closes the open assumption in
  that PR: the thirteen scanned sources whose post-OCR yield had never been
  measured all clear 400.
- **PR #25's `by` clause held at full scale.** All seven `@Review`-typed entries
  carry `\bibstring{by}` in `Shorttitle`, against 0 of 6 in the baseline, and
  `shorttitle` exact goes 0 → 2. On the review subset `title` exact goes 2 → 3.
- **PR #26's first rule works** (Heidegger1993). Its second is responsible for the
  omission trade above. Kranenburg2008 was deliberately not attempted and did not
  move.

## What it did not settle

Attribution between four simultaneous changes, on any entry not named above. A
per-branch run would cost four times as much and, at this sample size, still could
not separate a two-entry effect from noise.
