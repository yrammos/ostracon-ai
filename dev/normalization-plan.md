# Normalization plan

Bringing a legacy `.bib` file into the shape this workflow produces today.

**Scope.** Field presence, field format, field completeness, and — added after
the fact, see Tier D — whether a value belongs in the field that holds it. Entry
types are out of scope: they are never rewritten unilaterally, only reported and
changed on instruction.

**Target.** `~/Documents/Bibdesk/biblio.bib`, 17 MB, 5,745 entries.

---

## Status, 2026-08-03 — normalization is complete

Every defect class found in this work now reports **0**. What the audit still
counts is either a standing decision or absent data, and that is the baseline to
read future runs against:

| bucket | n | why it is not a defect |
|---|---|---|
| `keywords-present` | 5,507 | maintainer's decision to keep a decade of tagging |
| `number-range` | 82 | `Number = {1--2}` is house style for a double issue |
| `no-date` | 67 | absent data; 55 carry a `Urldate`, which IS the dating evidence |
| `non-ascii-no-langid` | 43 | English titles carrying a foreign proper name |
| `undated-no-urldate` | 12 | no dating evidence of any kind; tabulated for the maintainer |
| `volume-range-REVIEW` | 11 | genuine combined volumes (*Theory and Practice* 49–50 and the like), all but one with `Pages` to prove it |
| `foreign-text-in-title` | 7 | advisory: a wrapper is evidence about the string, never the entry |
| `shorttitle-missing` | 2 | `Tremblay2016` is a standing exception |
| `doubled-text-in-title` | 1 | `Duerksen2009`'s repetition is genuine |

**A new rule firing, or a jump in one of these, is the signal.** Zero is not the
target and never was.

### Picking this up later

1. `python3 dev/bib_audit.py <file>` — compare against the table above.
2. `python3 dev/bib_normalize.py <file>` — must report 0 edits.
3. `python3 dev/bib_normalize.py prompt-context/biblio-template.bib` — the
   regression fixture, also 0.
4. Compile with the maintainer's own preamble and engine (see constraint 6).

Entry-level history is in `~/Documents/Bibdesk/biblio-changelog_{1,2}.md`;
outstanding data questions in `biblio-undated_1.md` and the
`biblio-worklist_1*` files. All are outside the repository because they quote
the library.

### Open questions the maintainer has not settled

- **1,749 of 1,834 `\foreignlanguage` wrappers (95%, in 496 entries) are
  redundant** under `autolang=other`, where the entry's `Langid` already
  encloses the record — proved by compilation, byte-identical output. Only 85
  are load-bearing: 81 on entries with no `Langid`, 4 naming a genuinely
  different language. Kept, because they document intent and keep the `.bib`
  independent of one document's package options.
- **Eight transliterated/Cyrillic duplicate pairs**, kept on instruction.
  `Senderovic1987a` has a `rating` its twin lacks, so a future merge has a
  direction.
- **Transliterated titles**: whether a Latin-transliterated Russian or Persian
  title takes a `Langid` and a wrapper is unruled. `Talai` and the transliterated
  Russian entries currently have neither, consistently.
- **12 entries with no dating evidence**, three of which (`SchumannSchenker*`)
  have no attachment, imprint or identifier and are a keep-or-delete decision.

---

## Status, 2026-08-02

| | |
|---|---|
| Tier A (mechanical) | **complete**, idempotent — `bib_normalize.py` proposes 0 edits |
| Tier B (judgement) | **complete** on everything scoped |
| Tier C (invisible) | dropped — was already clean |
| Tier D (wrong values) | **complete**; a class the plan did not anticipate |
| Compile | **0 LaTeX errors, 0 biber errors**, 445 rendered pages |
| Entries changed | ~740 of 5,745 |

Entry-level history is in `~/Documents/Bibdesk/biblio-changelog_{1,2}.md`, kept
out of the repository because it quotes the library. Outstanding items are
listed at the end of this file.

---

## The tools

Three programs, and the split between them is the design.

| | Acts by | For |
|---|---|---|
| `dev/bib_audit.py` | — (read-only) | Counting. Parses by byte span, proves round-trip equality before reporting anything, writes nothing |
| `dev/bib_normalize.py` | **rule** | Edits derived from a predicate that fires across the corpus |
| `dev/bib_apply.py` | **name** | A JSON list of "in entry X, set field Y to Z", for the residue judgement has to settle |

`bib_audit.py` also holds the **shared predicates** — `would_split`,
`keeps_shorttitle`, `shorttitle_verdict`, `url_is_earned`, `full_stop_boundary`
and the rest. They live there, below the scanner, because the dependency arrow
runs audit → normalize and cannot be reversed. For one day they did not: the
audit paraphrased the normalizer instead of importing it, the two drifted, and
three counts (1,730 / 216 / 263) were phantoms of superseded rules. **Never
reimplement a predicate in the audit; import it.**

`dev/bib_bisect.py` isolates a bad entry by writing subsets and testing them —
built for the BibDesk crash, and the technique that later found both compile
faults. When bisecting a LaTeX failure the probe must use the **same preamble**
as the failing compile, and must refuse to answer when the compile dies early:
a first attempt reported "clean" over the whole file because `pdflatex`
exhausted memory before reaching the bibliography.

### The gates

Ten must pass before anything is written:

1. output re-scans, byte-accounted
2. entry count unchanged (minus any explicitly named `dropentry`)
3. citekey order unchanged
4. `@comment` blocks byte-identical — they hold BibDesk's group hierarchy as a plist
5. no protected field altered
6. **no field swallowed by a missing comma** (`merged_fields`)
7. no doubled commas
8. length arithmetic exact — *deliberately independent of the scanner*
9. utf-8 round trip is the identity — catches CRLF translation
10. an independent parser (`bibtool`) reports no new errors

`bib_apply.py` adds an eleventh: **every entry not named in the edit list is
byte-identical.** A hand-written list should touch exactly what it names.

Gates 8–10 exist because 1–5 share a blind spot: they are built on the scanner,
so they validate its own parse and structurally cannot catch a byte moving
somewhere no rule claimed to touch.

**A gate that cannot fail is worse than no gate.** `merged_fields()` had a
brace-counting bug that made it return "all clean" unconditionally — it passed a
synthetic entry whose `bookauthor` had demonstrably been eaten. Write a negative
control for every gate.

---

## The four tiers

Sort by whether the defect reaches the rendered page.

- **Tier A — visible, mechanically decidable.** Title/subtitle splitting,
  redundant `Shorttitle`, range punctuation, forbidden fields, misplaced `Url`.
  Highest value, lowest risk.
- **Tier B — visible, requires judgement.** Title case, language identification,
  separating a series from its division. Real value, real risk of systematic
  error.
- **Tier C — invisible once compiled.** Field order, whitespace, stamp presence.
  Never a reason to run a pass.
- **Tier D — well-formed and wrong.** *The plan did not anticipate this, and it
  held most of the real damage.*

### Tier D: the class that matters most

Nothing in tiers A–C asks whether a value **belongs in the field that holds it**.
The entries parse, compile, and read as slightly odd records. Worse, Tier A's
punctuation rules had *polished* them — an elided-range expansion set a correct
en dash inside `2461--2478`, a number that never existed. **The tidier the pass,
the better the disguise.**

What was found:

| class | n | signature |
|---|---|---|
| Field rotation | 22 | a bare number in `Issuetitle` — volume → `Issuetitle`, number → `Volume`, pages → `Number` |
| Page range in a numbering field | 16 | a wide range in `Volume`/`Number` with no `Pages` |
| Doubled text | 4 | a five-word run occurring twice in one title |
| Truncated value | 8 | a parenthesis that opens and never closes |
| Build-breaking macro | 6 | `\reviewof{}` — a bibstring name written as a command |
| Name-parser fault | 6 | `\,` in a name field: biber reads its comma as a name-part separator |
| List-field fault | 6 | `\and` in `location`, where biblatex splits on the bare word `and` |
| TeX specials | 5 | unescaped `_`, `&` in printed fields |
| Ligature artefacts | 3 | U+FB03 in "Eﬃcient" — a glyph, not a character |

`Williams1976` is the specimen: `volumes = {2461--2478}` was the issue number
`2` welded to the page range `461-478`.

**The generalisation: a rule that is right about punctuation can still be wrong
about meaning.** Every Tier D rule now lives in `bib_audit.py`, so the class is
executable rather than remembered.

---

## Durable design constraints

1. **Never re-serialize.** BibDesk's on-disk form — tab indent, alphabetical
   fields with `bdsk-*` last, multi-KB base64 in `bdsk-file-*`, two `@comment`
   plists holding the group hierarchy — is not incidental. A generic parser
   would reorder and possibly drop them. The decisive argument is reviewability:
   surgical span edits give a diff a maintainer can skim; reserialization gives
   a 103k-line diff nobody can review, destroying the safety net exactly when
   the pass runs unattended.
2. **Group the dry run by rule, not by entry.** Reviewing thousands of records
   is infeasible; reviewing ~30 rule classes with samples is not. A misfiring
   rule is caught on its third instance, not its three-hundredth.
3. **Idempotence.** Running twice must equal running once.
4. **Leave untouched and report.** Under-changing is recoverable at this scale;
   over-changing is not.
5. **Compile to decide, don't reason to decide.** Where it is unclear whether a
   question is one of fact or of house style, encode both forms and compare the
   rendered output. This settled the terminal-punctuation split, the French
   colon, `\autocap` in subtitles, `version` vs `edition` on `@Online`, and
   whether `%` in a `url` needs escaping — on which reasoning alone had me
   wrong, and my "fix" made three URLs worse.
6. **Compile with the maintainer's own preamble, not a minimal one.** This is
   the rule that cost the most to learn, because a wrong harness does not fail —
   it answers confidently. Four false findings came from it, none from the data:
   - **Missing `\setotherlanguages` entries** made polyglossia raise its error
     *inside* biblatex's text macro, producing 12 of 16 "errors".
   - **`pdflatex` exhausting memory** before reaching the bibliography made a
     bisect report "clean" over a file that demonstrably failed.
   - **A missing `csquotes`** invented an `\mkbibquote` nesting "defect". I
     reverted a correct encoding to an inferior one, then proposed a brace
     workaround that would have degraded it further. With `csquotes` — which the
     maintainer loads — the original was already textbook Chicago.
   - **The wrong engine.** The preamble is `pdflatex` (`inputenc` +
     `fontenc[T1,T2A]`); run under `lualatex` the T2A Cyrillic fonts are not
     found at all — 19,651 missing characters — and the resulting internal
     errors sent a bisect to an entirely innocent entry.

   A corollary for the probe itself: it needs **three** LaTeX passes, and it must
   refuse to answer when the compile dies early. And check the harness's own
   escaping — one "clean" result came from a shell heredoc turning
   `\foreignlanguage` into `\\foreignlanguage`, so the test was testing nothing.

### Snapshots, not git

The target lives in an iCloud-synced folder where a `.git` object database is
liable to be corrupted by the sync client, and the base64 in `bdsk-file-*`
defeats textual diffing anyway. Both write tools save verified bytes to
`biblio_<date>_pre-<label>.bib` first. `snapshot_path()` never clobbers — it
appends `-2` — so distinct labels are for legibility, not safety.

**To revert, use `/bin/cp -f`, never `cp`.** `cp` is aliased to `cp -i` in this
environment's login shell; run non-interactively it declines the overwrite,
prints "not overwritten" on **stdout**, and exits 1. A revert that quietly does
nothing is the worst possible failure for that command.

---

## Risks

- **Field rules key off entry type.** Where a type is wrong, the field pass
  inherits the error and normalizes confidently in the wrong direction. Report
  mismatches; act only on instruction. 13 `@Review` entries turned out to be
  ordinary articles, and freeing them exposed work their exemption had hidden.
- **Systematic error.** A wrong rule damages every matching record at once and
  leaves the result looking tidy.
- **The corpus interrogates the house style.** A large legacy file contains
  forms the guidelines have never ruled on. Each is a decision only the
  maintainer can take, and this backlog dominates the schedule. Realised
  repeatedly: the terminal-question-mark `Shorttitle`, subtitle capitalisation,
  transliterated duplicates, `Langid` on an English review of a French book.
- **A rule that is wrong 92 times out of 92 trains its reader to ignore it.**
  `non-ascii-no-langid` fired on any accented character and so measured the
  alphabet, not the language. Precision is a feature of an audit rule, not a
  nicety.

---

## Outstanding

1. **26 entries with no dating evidence at all.** Tabulated for assessment in
   `~/Documents/Bibdesk/biblio-undated_1.md`. The audit's `no-date` count of 81
   is misleading: 55 carry a `Urldate`, which `CLAUDE.md` rules *is* the dating
   evidence, with Chicago's "n.d." the intended output.
2. **Measured, no action proposed.** 82 double issues (`Number = {1--2}` is
   house style), 11 combined volumes, `Tremblay2016`, `Duerksen2009`.
3. **Eight transliterated/Cyrillic duplicate pairs**, kept on instruction.
   `Senderovic1987a` carries a `rating` its twin lacks, so a future merge has a
   direction.
4. **Environment, not data**: a document citing this library must load
   `danish`, `norwegian` and `serbian` in `\setotherlanguages`.
