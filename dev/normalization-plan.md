# Normalization plan (draft)

Bringing a legacy `.bib` file into the shape this workflow produces today.

**Scope.** Field presence, field format, field completeness. Entry types are
out of scope: types are assumed correct and are never rewritten, though
suspected mismatches are reported (see Risks).

**Status.** Tier A applied to `~/Documents/Bibdesk/biblio.bib` on 2026-08-01
(6,736 edits, 2,772 of 5,746 entries). Tier B not started. Tier C was found
already clean and dropped.

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

### Results, 2026-08-01 — `dev/bib_audit.py`

Target: `~/Documents/Bibdesk/biblio.bib`, 17 MB, 5,746 entries, 21 types.
Round-trip byte equality **passes**, so every count below is trustworthy.

| Rule | Count |
|---|---|
| `keywords` present | 5,508 |
| Unsplit `Title` | 2,043 |
| `Url` on a dated non-`@Online` entry | 702 |
| Redundant `Shorttitle` | 499 |
| Non-ASCII title, no `Langid` | 269 |
| Unsplit `Booktitle` | 216 |
| `Shorttitle` ≠ pre-colon segment | 175 |
| No `Date` at all | 84 |
| Range punctuation, series division, ISBN/ISSN | 51 combined |
| Missing stamps, field-name casing, duplicated `Number` | **0** |

Tier C is already clean, so it earns nothing and can be dropped.

**One structural constraint the original draft missed.** The file must never
be re-serialized. BibDesk's on-disk form — tab indent, alphabetical fields
with `bdsk-*` forced last, the closing `}}` glued to the final field, multi-KB
base64 in `bdsk-file-*` — is not incidental, and two `@comment` blocks hold
the entire static/smart group hierarchy as an embedded plist. A generic parser
(`bibtexparser`, `pybtex`) would reorder, re-wrap and possibly drop those. The
decisive argument is reviewability, not purity: surgical span edits give a
diff a maintainer can skim, whereas reserialization gives a 103k-line diff
nobody can review — which destroys the safety net exactly when the pass runs
unattended. So: locate each field's brace-balanced extent, rewrite only that
span, treat `@comment` as opaque, and assert byte equality before trusting
anything.

### Maintainer decisions taken, 2026-08-01

- **`keywords` stays.** All 5,508. The house rule governs new entries; it is
  not licence to strip a decade of tagging. Verified first that no smart group
  keys off it — the sole smart group filters on `Date-Modified`, and static
  groups key on citekeys, which are never rewritten.
- **Tier A only** this pass, re-audit before considering Tier B.
- **`Shorttitle` belongs only to genuinely unsplittable titles.** A merely
  colonless title has no subtitle to hoist and needs none, which drops the
  "missing shorttitle" rule from 1,280 to nil.
- **`bdsk-url-*`, `rating`, `read` are protected** alongside the named fields.
- **The `Url` policy was relaxed, and `CLAUDE.md` amended to match.** The rule
  is now one canonical locator per entry: Chicago cites a DOI in preference to
  a URL, so a `Url` beside a `Doi` is redundant and goes (440 entries), while
  an entry with no DOI keeps its `Url` whatever its type (319 entries), because
  there the address is the only locator the style has to print. This is a
  better bibliographic rule than the blanket type-based one, not merely a
  gentler one. Checked first that no `url` field anywhere carries a decoded
  `x-devonthink://` URI — all 759 are http/https or malformed, and the 12,322
  DEVONthink URIs all sit correctly in `remote-url`/`bdsk-url`.

### Tier A pass — `dev/bib_normalize.py`

6,736 edits across 2,772 entries. Seven gates must pass before it will write:
output re-scans byte-accounted; entry count and citekey order unchanged;
`@comment` blocks byte-identical; no protected field altered; idempotent;
length arithmetic exact; utf-8 round trip the identity. It also refuses to
write while BibDesk is running, since BibDesk would overwrite from its
in-memory copy and a save can trigger autofile.

The last two gates exist because the first five share a blind spot: they are
all built on the scanner, so they validate the scanner's own parse and
structurally cannot catch a byte moving somewhere no rule claimed to touch.
The arithmetic gate — output length must equal input length plus the summed
edit deltas — is independent of it. The utf-8 gate closes a subtler hole:
Python's text mode translates line endings on read *and* write, so a file
with any CRLF would come back all-LF with every other gate still green. The
file is read as bytes, proved to survive decode/encode unchanged, and written
back as bytes.

### Correction: terminal punctuation *is* a splittable boundary

`CLAUDE.md` states that a title whose halves are joined by a question mark or
full stop must not be split, "biblatex would insert a colon the source
doesn't have." **That is factually wrong about the package**, and by
`CLAUDE.md`'s own precedence rule — the corpora govern on what biblatex-chicago
actually does — the package wins. `chicago-notes.cbx` defines:

```latex
\renewcommand*{\subtitlepunct}{% Follows CMS16 spec.
  \ifboolexpr{ test {\ifterm} and not test {\ifcsdef{@cmsst}} }%
    {\addspace}%          <- terminal punctuation: NO colon
    {\addcolon\addspace}} <- otherwise: ": "
```

`\ifterm` resolves to `\ifcapital`, which reads TeX's spacefactor — 3000
after `.`, `!`, `?`. So the colon is suppressed automatically. The package's
test suite says the same in prose: `batson` carries
`title = {How Social Is the Animal?}` beside
`subtitle = {The Human Capacity for Caring}`, annotated "you no longer need
to include the subtitle in the title field when the latter ends in a question
mark... This also means that you no longer necessarily need a shorttitle
field in such entries."

Consequences for the pass:

- **`?` and `!` boundaries are now split** (57 entries), with the mark kept on
  the title. Their `Shorttitle` then falls away like any other split.
- **Titles with more than one top-level `?`/`!` are reported, not split**
  (3 entries). `Aesthetics---What? Why? and Wherefore?` is one continuous
  rhetorical construction, not a title plus a subtitle; splitting at the first
  mark mis-models it. Same guard, same reasoning as multi-colon titles.
- **`.` boundaries are reported, not split** (4 entries). TeX's spacefactor
  rule reads a period after a capital as an abbreviation, and the splitter
  must do the same or it would cut "J.\,S. Bach", "Op.\,110" and
  "Pitch vs. Timbre" in half. Checked against all 164 titles in the file
  carrying an abbreviation-shaped period: **zero false positives**. Two
  guards do the work independently — the pattern demands a capital or a
  backslash after the space (so "Op. 110" cannot match), and an abbreviation
  list catches the rest. Deciding where a period ends a title rather than an
  initial is judgment; the renderer cannot make it on our behalf.

Two bugs the dry run caught, both of which would have damaged records at
scale and left the result looking tidy:

1. Span helpers walked backwards for the field's leading newline, but the
   parser already includes `\n\t` at the *front* of each field span. 2,754
   edits silently became "unparsable layout" — a **fail-safe** miss.
2. The redundancy rule would have stripped 194 earned `Shorttitle` values:
   79 of the `Kretschmer2008` shape (halves joined by `?` or `.`, so the
   title cannot be split) plus every `@Review`, whose `Shorttitle` is the
   compressed `\bibstring{reviewof}` form. A **fail-dangerous** miss, and
   the reason the rule-grouped dry run is not optional.

Left untouched and reported, never rewritten:

| Bucket | Count | Why it is judgment, not mechanics |
|---|---|---|
| `colon-inside-macro-NOT-SPLIT` | 142 | Colon sealed inside `\foreignlanguage{…}` or `\mkbibquote{…}`; not an entry-level boundary |
| `shorttitle-earned-KEPT` | 139 | Title genuinely unsplittable, so the `Shorttitle` is doing work |
| `skipped-multi-colon` | 10 | Two top-level colons; which one is the boundary is a decision |
| `series-division-REVIEW` | 7 | Splitting `Series` from its division needs the imprint page |
| `skipped-review-title` | 6 | `@Review` with a top-level colon; the title encodes the reviewed work |
| `full-stop-boundary-REVIEW` | 4 | A `.` that may end the title or may abbreviate a name |
| `url-malformed-REVIEW` | 8 | `hps://`, a ligature-mangled `hĴp://`, bare hostnames, one bare DOI |
| `multi-terminal-mark-REVIEW` | 3 | Multi-part rhetorical title, not a title plus subtitle |
| `url-sole-copy-KEPT` | 2 | Only copy of the URL — one has curly quotes, one's `http` is mangled to `hĴp` by a ligature |
| `subtitle-exists-but-parent-still-split` | 1 | `Richter2020` has a `Subtitle` *and* a colon still in `Title` — pre-existing, not caused here. Its `Shorttitle` is now dropped on the maintainer's instruction; the `Title`/`Subtitle` disagreement is left for a human. |

The macro-sealed 142 matter for honesty about scope: they are titles that
*contain* a colon and are nonetheless not split. "Title splitting is done"
would overstate coverage without them, so they are counted rather than
silently dropped.

### Applied, 2026-08-01

Written after BibDesk was quit. Post-write verification against the file on
disk, independent of the run that produced it:

| Check | Result |
|---|---|
| Round-trip byte equality | PASS |
| Entries | 5,746, unchanged |
| `@comment` blocks (BibDesk groups) | 2, byte-identical |
| `Subtitle` | 6 → 2,098 |
| `Booksubtitle` | 0 → 219 |
| `Mainsubtitle` | 0 → 6 |
| `keywords` | 5,508 → 5,508, untouched |
| Entries with both `Subtitle` and `Shorttitle` | 0 |
| Re-running the pass | proposes 0 edits — idempotent on disk |

Diff shape: 4,659 insertions, 4,419 deletions over 9,078 of 103,352 lines.
Under 5% of the file moved, which is what makes the change reviewable — the
whole reason for surgical span edits over reserialization.

**Versioning is by filename suffix, not git.** The target directory is synced
to iCloud, where a `.git` object database is liable to be corrupted by the
sync client, and the multi-KB base64 in `bdsk-file-*` defeats textual diffing
anyway. `bib_normalize.py --apply` therefore writes a verified snapshot of the
current bytes to `biblio_<date>_pre-<label>.bib` before touching anything, and
prints the single `cp` that reverts to it. `--no-snapshot` opts out.

Restore points in `~/Documents/Bibdesk/`:

| File | State |
|---|---|
| `biblio_backup.bib` | Pre-normalization, the maintainer's own backup |
| `biblio_2026-08-01_post-tier-a.bib` | Immediately after Tier A, before BibDesk re-serialized |
| `biblio_normalized_fixed.bib` | Tier A + the Cutler2008 repair — **installed as `biblio.bib`** |
| `biblio_unnormalized_fixed.bib` | Backup + the Cutler2008 repair, if the normalization is ever unwanted |
| `biblio.bib` | Live |

---

## Postscript: the BibDesk 98-field crash, 2026-08-02

Reopening the library crashed BibDesk — **and so did the untouched backup**,
which cleared the normalization of blame and redirected the search.

`bibtool` parsed both files identically, so the file was valid BibTeX and no
static check would ever find it. Bisecting instead — write a subset, open it,
watch whether the process survives (`dev/bib_bisect.py`) — found one entry in
13 rounds: `Cutler2008`, 111 fields.

**The bug is in BibDesk 1.9.12 itself.** A synthetic entry of 98 fields named
`field001`…`field098` with values `{v1}`…`{v98}` — 1,873 bytes, no bookmarks,
nothing exotic — crashes it on open. 97 fields is fine. Content is irrelevant;
the ceiling is the field count. Worth reporting upstream.

How the entry got there: the bulk pass of 2026-07-29 that stamped 5,265
entries added a `devonthink*` field per linked DEVONthink record. `Cutler2008`
travels with 25 Finale musical examples, so it gained 28 fields at once,
crossing from 83 to 111. It did not crash for three days because the fault is
in the *parser*, not the data model — BibDesk will happily hold and even write
a 111-field entry, it just cannot read one back. The first cold start since
that bulk pass was the one this work prompted.

The repair, on the maintainer's instruction: the 26 attachments (the PDF plus
25 `.mus` examples) all live in one DEVONthink group, so a single reference to
that group replaces every per-file link — 111 fields down to 20. The two
attachments *outside* the group, a Markdown bibliography note and a PNG
screenshot, are kept, because the group URI does not cover them. Verified by
opening the result in BibDesk, not merely by parsing it.

Largest entry in the library now: 62 fields.

### Two follow-up corrections, 2026-08-02

Both were mine, and both were caused by rules that were wrong rather than by
code that misfired:

1. **Date ranges take a solidus.** `CLAUDE.md` asked for a single hyphen on
   every range; biblatex parses date fields as ISO 8601-2, where `-` is the
   year-month delimiter and `/` the range separator, so `2012-13` reads as
   month 13. `Marvin2012a` (`2012--13` → now `2012/2013`) and `Jonas1989`
   (`origdate 1937--1938` → now `1937/1938`) were the only two affected. The
   normalizer now routes date-type fields through a separate rule that emits
   `/`, and `CLAUDE.md` records the exception. This is the third instance of
   the same pattern: a house rule contradicting what the package accepts, with
   the package winning under `CLAUDE.md`'s own precedence clause.
2. **French typography strands a tie.** `Migliore2022` reads
   `…{AudioSculpt}~: chronique…`, the non-breaking space French sets before a
   colon. Dropping the colon left the `~` dangling at the end of `Title`. The
   splitter now absorbs a trailing `~`, `\,`, `\ ` or whitespace along with
   the colon. Of the ten titles in the file with whitespace before a colon,
   three were split and only this one had a tie rather than a plain space.

Still outstanding, and **pre-existing rather than introduced here**: two date
fields carry the same invalid shape, `Mackay2022-23` (`date = {2022-23}`) and
`Wlodarczyk2020-22` (`date = {2020-21}`). Left untouched — whether those mean
academic years is a question for the maintainer.

---

## Re-audit, 2026-08-02 — Tier A is complete

`bib_normalize.py` proposes **0 edits, 0 entries touched** against the current
file. Tier A is done; what follows is the state it left behind.

| Rule | 2026-08-01 | now | note |
|---|---|---|---|
| unsplit `Title` | 2,043 | **8** (+6 `@Review`, likely exempt) | |
| unsplit `Booktitle` | 216 | **2** | |
| `Shorttitle` ≠ pre-colon segment | 175 | **3** | |
| range punctuation | 51 combined | **1** | plus 13 `series-carries-division` |
| non-ASCII title, no `Langid` | 269 | **193** | Tier B |
| no `Date` at all | 84 | **85** | needs sources, not rules |
| `keywords` present | 5,508 | 5,508 | decision: kept |
| `Url` misplaced | 702 | **263** | see below |
| `Shorttitle` redundant | 499 | **216** | see below |
| `Shorttitle` missing | 1,280 | **1,730** | see below |

### First task: the audit lags the decisions

Three counts above are inflated because `bib_audit.py`'s rules were never
updated when the decisions of 2026-08-01 refined them. The audit is a
*reporting* tool and was left as first written; the normalizer got the refined
logic. They now disagree, and the audit is the one that is wrong:

- **`shorttitle-missing` 1,730.** The decision was that a merely colonless
  title has no subtitle to hoist and needs no `Shorttitle`, which "drops the
  rule from 1,280 to nil." The audit still applies the old test.
- **`shorttitle-redundant` 216** against the normalizer's
  `shorttitle-earned-KEPT` **225**. The audit flags as redundant the very
  entries the normalizer correctly preserves — sealed colons, `@Review` titles.
- **`url-misplaced` 263.** The policy was relaxed to one canonical locator per
  entry: a `Url` beside a `Doi` goes, an entry without a `Doi` keeps its `Url`
  whatever its type. The audit still tests by entry type.

Reconcile these before triaging anything, or the worklist will be mostly
phantoms. Nothing here indicates a defect in the library.

### Worklist

**Tier A stragglers — per-entry judgement, ~23 items.** 8 unsplit titles and 6
`@Review`s that look exempt; 2 unsplit booktitles; 3 `Shorttitle` mismatches; 2
raw quotation marks in titles; 1 `@Online` with no `Url` (`Gotham`, which also
lacks a date and carries a stray `volume = {2}`); 1 range-punctuation case.

**Two entries are well-formed nonsense, and this is the sharpest lesson of the
pass.** `Smalley1997` carries a page range in `Volume` (`{107--126}`, with no
`Pages` and no `Number`); `Williams1976` carries one in `Volumes` — the field
meaning *number of volumes in a set* — and is typed `@Book` while having
neither publisher nor location, though it looks like a journal article. Both
were touched during the elided-range expansion, which applied the literal-field
rule correctly to values sitting in the wrong fields, and so made the
corruption look deliberate. **A rule that is right about punctuation can still
be wrong about meaning; nothing in Tier A checks whether a value belongs in the
field that holds it.** Worth a dedicated audit rule: numeric-looking ranges in
`Volume`, `Volumes` or `Number`.

**Tier B proper.** 193 non-ASCII titles with no `Langid`; 13 entries with a
division left inside `Series`; and title case across the corpus — which the
audit does not measure at all, and where compounds are the usual failure.

**141 titles whose colon is sealed inside `\foreignlanguage`.** Deliberately
not auto-split. Each needs the `Deliege2009` treatment: split by hand, both
halves separately wrapped. Mostly Russian. Arguably Tier B, since it turns on
reading the title.

**85 entries with no `Date`, 44 with neither `Date` nor `Urldate`.** Data
completion; needs the sources.

### Session decisions, 2026-08-02 (Tier B kickoff)

- **The no-date backlog is out of scope**, on the maintainer's instruction. The
  85 undated entries and the 44 with neither `Date` nor `Urldate` are not
  touched this pass and are not reported against.
- **One consolidated worklist, not five review cycles.** The read-only work —
  audit-rule fixes, title-case scoping, source lookups, and every proposal —
  runs end to end unsupervised; the maintainer reviews once, and each approved
  class is then applied in its own gated pass. Review time, not API cost, is
  the constraint on this project, and five small proposal/review cycles spend
  it five times over. Nothing writes to `biblio.bib` without sign-off, and
  BibDesk is quit only with the maintainer's assent.
- **Item 4 must precede the shorttitle rules, not follow them.** The 141
  sealed-colon titles are exactly what fills the normalizer's
  `shorttitle-earned-KEPT` bucket: they keep their `Shorttitle` only because
  the splitter declines to act on a colon inside `\foreignlanguage`. Split them
  by hand and each acquires a `Subtitle`, at which point those same
  `Shorttitle` values become redundant under the never-both rule. So the order
  is: fix the audit → do the 141 → *then* re-run the shorttitle rules. Running
  them earlier only means running them twice.
- **Title case has no measured scope yet, and none is asserted.** The audit has
  never covered it. Two probes bound the problem loosely: 191 English titles
  carry two or more lowercase non-stopwords, but the sample inspected was
  dominated by `\bibstring{reviewof}` and `\mkbibquote` artefacts, so that
  figure measures the probe's poor precision rather than the corpus; and 75
  `Capital-lowercase` hyphen compounds (`Avant-garde`, `Post-modern`,
  `Twenty-first`) each need a CMOS 8.161 judgement. `lower-lower` compounds
  mid-title and ALL-CAPS imports remain unmeasured. A scoping pass precedes any
  number.
- **Snapshot labels must not be reused.** `bib_normalize.py --apply` writes
  `biblio_<date>_pre-<label>.bib`, and `biblio_2026-08-02_pre-tier-b.bib` is
  already occupied by a hand-made pristine backup taken before this session.
  Re-running `--apply --label tier-b` today would overwrite the only fresh
  clean copy with a post-edit state. Use a distinct label per apply —
  `pre-stragglers`, `pre-langid`, `pre-colon-split`.
- **Restore points as of this session**: `biblio_backup.bib` (the maintainer's
  own, pre-normalization) and `biblio_2026-08-02_pre-tier-b.bib` (Tier A
  complete, verified md5-identical to the live file at the time of copying).
  The intermediate snapshots listed further up were deleted; those two are what
  remains.
- **Model**: `claude-opus-5` for the whole pass. The pipeline's `--model` flag
  does not enter into it — normalization never invokes `src/extract.py`, so
  this is a question about the interactive session alone. The test that decides
  it is whether a wrong answer announces itself. Where output is checkable by
  re-running something (the audit rules, the mechanical split generation) a
  cheaper model would serve; where a wrong answer looks exactly like a right
  one — a CMOS call on a compound, a boundary inside a Russian title — it buys
  confidently wrong edits to an irreplaceable file, which is the one failure
  mode the seven gates are structurally blind to. `CLAUDE.md`'s operator note
  records `claude-sonnet-5` fabricating a publisher and a date on this
  project's own comparison run. Invention, not omission.

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
re-audit → repeat for Tier B. A suffixed snapshot is taken before each apply
(see below — not git; the directory is iCloud-synced), and the first pass
changes nothing at all.

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
