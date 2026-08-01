# Normalization plan (draft)

Bringing a legacy `.bib` file into the shape this workflow produces today.

**Scope.** Field presence, field format, field completeness. Entry types are
out of scope: types are assumed correct and are never rewritten, though
suspected mismatches are reported (see Risks).

**Status.** Design only. Nothing here has been implemented, and step zero
below is deliberately the only part worth building until its results are in.

---

## Step zero: the diagnostic audit

Build this first, run it, and decide the rest from what it returns.

- **Read-only.** Parses the target file, writes nothing, touches no record.
- **No model.** Every check below is structural, so step zero costs nothing
  but the writing of it and can run as often as wanted.
- **Output:** a count per rule, plus a small sample of offending entry keys
  for each, so a rule that fires unexpectedly often can be inspected before
  anything is built on it.

Checks that need no model:

| Check | Detection |
|---|---|
| Unsplit title | `Title` contains `: ` outside `\mkbibquote{}`/`\mkbibemph{}` |
| Redundant `Shorttitle` | `Shorttitle` present where `Title` carries no colon |
| Missing `Shorttitle` | Title unsplittable and over six words, no `Shorttitle` |
| Range punctuation | `--`, en/em dash in `Pages`, `Date`, any range field |
| Forbidden fields | `ISSN`, `ISBN`, `keywords`, `reference`, `devonthink` present |
| Misplaced `Url` | `Url`/`Urldate` on an entry that is neither `@Online`, nor an online reference work, nor undated |
| Subseries in `Series` | `Series` value contains a division marker or internal punctuation |
| Duplicated number | `Number`'s value also appears inside `Series` |
| Missing stamps | no `date-added` / `date-modified` |
| Brace balance | unbalanced braces anywhere in the entry |
| Field naming | field names not in the house casing |
| Language markers | non-ASCII text present without `Langid` or a language wrapper |

The last is a heuristic and belongs in the report, not in any rewrite.

**Decision gate.** The violation distribution determines whether this is an
afternoon or a project. Do not commit to the rest of the plan before seeing
it.

---

## Tiering

Sort every rule by whether the defect reaches the rendered page. This, not
the deterministic/judgment split, is what decides priority.

- **Tier A. Visible in output, mechanically decidable.** Title/subtitle
  splitting, redundant `Shorttitle`, range punctuation, forbidden fields,
  misplaced `Url`, a division left inside `Series`. Highest value, lowest
  risk. Do these first.
- **Tier B. Visible in output, requires judgment.** Title case (compounds
  especially), language identification and wrapping, distinguishing a series
  from its division. Real value, real risk of systematic error. Gate behind
  the dry run; consider emitting a worklist rather than a rewrite.
- **Tier C. Invisible once compiled.** Field order, field-name casing,
  whitespace, stamp presence. Free riders on a pass being run anyway. Never
  a reason to run one.

---

## Execution design

Four constraints, each answering a specific failure mode.

1. **Group the dry run by rule, not by entry.** Reviewing several thousand
   records is infeasible; reviewing ~30 rule classes with a sample of each is
   not. A misfiring rule is then caught on its third instance rather than its
   three-hundredth. This is the single most important decision here.
2. **Idempotence.** Running twice must equal running once. Assert it in
   tests: it is the cheapest guard against a rule that half-applies.
3. **Leave untouched and report.** Anything the transform cannot handle
   confidently stays as it is and goes into the report. Under-changing is
   recoverable at this scale; over-changing is not.
4. **Ground truth before rules.** A hand-marked sample of ~50 entries,
   corrected by hand to the intended output, becomes the fixture the rules
   must reproduce. Write it before writing the rules, not after.

Sequence: audit → ground-truth sample → Tier A rules → dry run → apply →
re-audit → repeat for Tier B. The file goes under version control first, and
the first pass changes nothing at all.

---

## Cost

Measured with `dev/estimate_cost.py`, not estimated.

Normalization is cheap because it skips almost everything expensive: no OCR,
no source document, no enrichment lookup, no grounding audit. It is reshaping
data already present, not establishing facts.

The prefix shrinks too. The two worked-example corpora cost 49,082 of the
62,974-token static prefix and teach entry-type discrimination exclusively,
which is out of scope here. Dropping them leaves the guidelines, the template
and the field reference at roughly 13,300 tokens.

| | value |
|---|---|
| Full static prefix | 62,974 tokens |
| Prefix without the type corpora | ~13,300 tokens |
| Typical entry | ~154 tokens (at the measured 3.39 chars/token) |
| Batch of 25 entries | ~3,850 tokens in, ~3,850 out |
| Per entry | ~$0.003 |
| Per full pass, several thousand entries | ~$12 (Sonnet), ~$25 (Opus) |

Several passes will be needed as rules are refined; the whole undertaking
still lands well under $100. **API cost is not the constraint.** Review time
is, which is what the rule-grouped dry run exists to compress.

For comparison, regenerating entries from their sources costs an order of
magnitude more per item, requires every source document to be in hand, and
discards whatever curation the file already holds.

---

## Risks

- **Field rules key off entry type.** Whether `Url` is permitted, and how
  `Series`/`Number` divide, both depend on the type. Where types are wrong,
  the field pass inherits the error and normalizes confidently in the wrong
  direction. Report suspected mismatches; do not act on them.
- **Systematic error.** A wrong rule damages every matching record at once
  and leaves the result looking tidy. The dry run and the ground-truth
  fixture are the mitigations; neither is optional for Tier B.
- **The target is not fully determinate.** Repeated runs of the extraction
  pipeline on one source have produced differing renderings of the same
  field. A deterministic normalizer is therefore *more* consistent than the
  generative pipeline it imitates, and "what the workflow would produce
  today" should be read as the house rules, not as any single run's output.
- **The corpus will interrogate the house style.** A large legacy file will
  contain forms the guidelines have never ruled on. Each is a decision only
  the maintainer can take. This backlog is the one genuinely unautomatable
  part and should be expected to dominate the schedule.

---

## Reuse

Build on `src/enrich.py` rather than starting fresh. Already present and
directly applicable: `set_field`, `remove_field`, `join_subtitle`,
`strip_forbidden_fields`, `strip_latex`, and the brace-balance validation.
The house rules themselves live in `CLAUDE.md`, and `prompt-context/`
carries both the presentation template and the package's own annotated
documentation, whose annotations state several of the field-placement rules
outright.
