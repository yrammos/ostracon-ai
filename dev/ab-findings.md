# A/B: condensed field reference vs. manual §4.2 in the extraction prompt

**Question.** Does replacing `biblatex-chicago-notes-ref.md` (tier 2, ~9k tokens,
hand-condensed) with `biblatex-chicago-fields.md` (tier 1, ~45k tokens,
mechanically extracted from §4.2 of `biblatex-chicago.tex`) improve extraction?

Both arms are identical but for that one file. Both on `claude-opus-5`.

**Verdict: two null results. Do not ship §4.2 into the prompt.**

## Run 1 — 20 general sources

|                    | A (condensed) | B (§4.2) |
|--------------------|---------------|----------|
| entry type correct | 17/20         | 16/20    |
| value accuracy     | 73.2%         | 75.4%    |

Null, and — established only afterwards — uninformative. The ground-truth
entries for those 20 sources contained just **four** instances of a
semantics-bearing field between them (`introduction` ×1, `lista` ×2,
`editortype` ×1), and both arms scored 1/4. The run measured `location`, `date`
and `author` reading: a different faculty from field semantics, and not the one
this experiment was about. B lost `Alekseev1969`, a `@SuppBook`.

## Run 2 — enriched for rare types, and cut short

Selected for `@SuppBook`/`@SuppCollection`/`@Reference`/`@Inreference`, with 5
sources carrying `introduction` — the presence-flag field whose mishandling
began this investigation.

**Incomplete.** A finished all 20; B stopped after 7 when the API credit balance
was exhausted (HTTP 400, `invalid_request_error`).

The 7 surviving pairs are **all `@SuppBook`/`@SuppCollection`** — the manifest is
ordered by type group and B died inside the first one. The 13 lost pairs hold
every `@Reference` and `@Inreference` in the sample, including all three `lista`
cases (`Marston2001`, `Boorman2001`, `Boorman2001a`) — precisely the family the
operator note in `CLAUDE.md` calls the hardest sources here. So run 2 tested
**one type family out of five**, not a representative fifth of the sample.

|                                       | A     | B     |
|---------------------------------------|-------|-------|
| entry type correct                    | 4/7   | 5/7   |
| value accuracy                        | 56.9% | 58.6% |
| supplementary matter: any markup       | 4/5   | 5/5   |
| supplementary matter: right kind named | 2/5   | 2/5   |

## The two-encoding problem, and a scoring trap

biblatex-chicago accepts **two** encodings for supplementary matter. Tier 1,
`prose:intro`'s annote: "Instead of the mechanism using a defined introduction
field, here I use the alternative of putting the type of supplemental material in
the type field, with the appropriate preposition." The worked examples pair each
encoding with an authorship case:

- `polakow:afterw` — `afterword = {yes}`, **no** `bookauthor`: the book's own
  author supplies the afterword.
- `prose:intro` — `type = {introduction to}`, `bookauthor = {Wallraff, Barbara}`:
  someone else's book.

B reached for the `type` form twice, A for a flag. A first scoring pass credited
these asymmetrically — it checked the flag's *value* but accepted `type` on mere
*presence* — so `foreword = {X}` against a truth of `introduction` scored 0 while
`type = {preface to}` against the same truth scored 1. Same error class, opposite
scores, and it manufactured a 4/5-vs-2/5 gap that does not exist. Judged alike,
both arms name the right kind of supplementary matter on **2 of 5**.

Neither of B's `type` outputs carries `bookauthor`, so B chose the encoding the
tier-1 examples assign to the *other* authorship case. The one genuine B win is
`Cobley1996`, where A produced `@Incollection` under a different title
altogether ("The Play of D…" for "The Communication Theory Reader") and B got the
entry type and the markup both sensible. That is one source.

## The case I called decisive was not a failure at all

`Roudiez1984` — both arms emitted `@Book` with
`introduction = {Roudiez, Leon~S.}`, which looked like exactly the error that
started this investigation: the presence flag mistaken for a name field. Manual
§4.1, under `suppbook`, says otherwise:

> if the focus of the reference is the main text of the book, but you want to
> mention the name of the writer of an introduction or afterword for
> bibliographical completeness, then the normal biblatex rules apply, and you
> can just put their name in the appropriate field of a book entry, that is, in
> the foreword, afterword, or introduction field.

Both encodings are correct. The discriminator is what the citation is *about*,
which is a judgement about the source, not a fact about the field. Both arms
produced valid biblatex-chicago and chose the other focus.

## After that, no clean semantics failure survives

Re-examined against §4.1, the seven `@SuppBook`/`@SuppCollection` pairs are:

| | |
|---|---|
| clean passes, both arms | `Gollin2011a`, `Adorno1973a`, `Hampe` |
| legitimate difference of focus | `Roudiez1984` |
| ground truth defective | `Cobley1996` — a `customc` entry carrying `Booktitle`, since retyped `@Incollection` |
| ground truth defective | `Youens1997` — `@SuppBook` for liner notes to a CD, with no flag and no `Type` |
| ground truth defective, **and** a source-misread | `Alekseev1969` — the source heading is ПРЕДИСЛОВИЕ, so the curated `introduction = {X}` was wrong and B's `type = {preface to}` right; but both arms also dropped the author outright |

So the enriched run produced **zero** instances of either arm mishandling field
semantics. The experiment's central case does not survive contact with the
manual, and **three** of the seven "failures" were defects in the oracle rather
than in the extractions — all three now repaired in `biblio.bib`.

`Alekseev1969` is the sharpest of the three, because the oracle was wrong on the
very axis the experiment was measuring. The flag mechanism covers a closed set —
§4.1: "these three just-mentioned types of material, and only these three types"
— implemented as exactly three fields mapped to `introductionto`, `forewordto`
and `afterwordto`. A preface is in none of them, and `Type` is the functionality
§4.1 added for "any sort of supplemental material whatever", with "preface to"
as its own worked example. B produced precisely that and was scored down for it.
The arms still failed the entry on authorship, so it is not a clean B win — but
the field-semantics half of the mark was awarded backwards.

## Recommendation

Keep the corrected `biblatex-chicago-notes-ref.md` as the prompt's field
reference. Keep `biblatex-chicago-fields.md` in the repo as a consultation
source, not a prompt input.

Two runs, ~45k extra tokens per extraction, no demonstrated benefit on any
symmetric measure, and — after §4.1 was consulted — not one surviving instance
of either arm getting field semantics wrong. There was no failure left for the
extra context to have prevented.

The sharper lesson is about the oracle, not the arms. Three of the seven curated
entries were themselves wrong, and the scoring charged both configs for
reproducing the manual's shape instead of the library's — in `Alekseev1969`'s
case penalising B for the encoding §4.1 prescribes. Any future run over rare
types should audit its ground truth against tier 1 *before* scoring, or it will
keep measuring the corpus rather than the model. On a sample deliberately
enriched for the hardest types, three defects in seven is not a tolerable error
rate for an oracle.

Run 2's remaining 13 pairs would extend the test to the reference-work family
rather than settle this one; they are staged and cost only config B to finish,
so re-run when credits allow.

## Harness defect to fix first

`run_ab.sh` records `rc=0` for all 40 extractions because `src/biblio_agent.py`
exits 0 even when the API call fails; only the zero-length `.bib` files revealed
the outage. Before the next batch, either propagate the child's failure or assert
non-empty output:

```sh
timeout 420 "$PY" src/biblio_agent.py "$pdf" --config "$cfg" … > "$out.bib" 2> "$out.err"
rc=$?
[ -s "$out.bib" ] || rc=99          # empty output is a failure whatever the exit code
echo "$c $(basename "$out") rc=$rc" >> "$D/progress.log"
```
