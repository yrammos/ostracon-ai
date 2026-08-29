# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This repository contains academic publications - PDFs, and `.webloc` bookmarks to online-only publications with no PDF. The task is to extract bibliographic information from each source in the `./pdf-in` folder and store it in a BibLaTeX file using the **biblatex-chicago** standard (notes and bibliography variant, not author-date).

## Bibliographic Extraction Guidelines

- Look for PDFs and `.webloc` files in the `./pdf-in` folder.
- **Read only** the beginning (max of first page or ~450 words) and last ~150 words of each PDF for bibliographic data.
- For a `.webloc` file, fetch the page it bookmarks and extract from that instead - the same recognition/formatting rules apply, source text and metadata just come from the fetched webpage (main body text and `citation_*`/`og:*`/JSON-LD metadata) rather than from PDF pages and embedded file metadata.
- IMPORTANT: `biblio-template.bib` shows this project's **house style** — field ordering, title case, name format, `Shorttitle`/`Subtitle` handling, which fields to omit. Follow it on all such questions. It is **not** a list of permitted entry types: it covers only 20 of the roughly 40 types biblatex-chicago offers, and its coverage reflects what has come up so far, not what is allowed.
- Select whichever entry type genuinely fits the source, drawing on the **full** biblatex-chicago repertoire — including types absent from `biblio-template.bib`, such as `@Letter`, `@CustomC` (archival material), `@Audio`, `@Artwork`, `@Manual`, `@Booklet`, `@Bookinbook`, `@Dataset`, `@Standard`, `@Performance`, `@Patent`, `@Image`, and the multi-volume `@Mv*` types. `notes-test.bib` has worked examples for most of these; `biblatex-chicago-notes-ref.md` lists every type, including the few (`@Jurisdiction`, `@Legal`, `@Legislation`, `@MvProceedings`, `@MvReference`, `@SuppPeriodical`) that no example file covers. Never force a source into a template type when a better-fitting one exists.
- Two worked-example corpora carry the entry-type reasoning, and both are loaded into the extraction prompt automatically via `example_files` in `config.yaml`. Use them for **classification** — working out what kind of thing the source is and which of the ~40 types therefore fits — and not merely as a tie-breaker between two candidates you have already narrowed down to:
  - `cms-notes-intro-guide.md` — organised *by kind of source*, which is what makes it a classification aid. Its taxonomy sections map source kinds onto entry types, naming a worked example for each in `[brackets]`; its "Online materials" section governs the access-mode question directly ("an online edition of a printed book still calls for a `@Book` entry"); and its `entrysubtype` section covers the cases where the type alone is insufficient (magazine, newspaper, classical, letter).
  - `notes-test.bib` — the biblatex-chicago package's annotated test suite, covering 32 of the 40 types. Nearly every entry carries an `annote` stating what the source *is* and why that type and field set follow from it. Consult it both to recognise a type and to settle boundaries between confusable ones. `@Inproceedings` vs. `@Incollection`, `@Proceedings` vs. `@Collection`, `@Thesis` vs. `@Report` vs. `@Unpublished`, `@Reference` vs. `@Inreference`, and `@Online` vs. a print-equivalent type are the boundaries that have caused trouble here so far — they are examples of the reasoning to apply, not the only distinctions that matter.
- Classification precedes discrimination: identify the kind of source first, from the corpora's taxonomy, and only then resolve between whichever confusable types remain.
- **Precedence is split by question, not by file — and there are three tiers, not two.** The distinction that matters is between what the *package* does and what this *project* has chosen; the trap is a third category, a convenience index that looks authoritative and is not.

  | Tier | Files | Authoritative on | Status |
  |---|---|---|---|
  | 1. The package | `notes-test.bib` (verbatim upstream, annotated), `cms-notes-intro-guide.md`, and where those run out the `.bbx`/`.cbx` sources and the full manual via `texdoc biblatex-chicago` | Which entry types exist, what each means, which fields it takes, **what those fields actually do**, and the use of `entrysubtype`, `relatedtype`, `\bibstring` and the rest of the machinery | Ground truth |
  | 2. The local index | `biblatex-chicago-notes-ref.md` | Nothing on its own. It is a *condensed, hand-derived* summary — the only local list of all 40 types, and the fastest way to find a field — but it is a secondary source and **has been wrong** | Use to locate; verify against tier 1 before relying on it for behaviour |
  | 3. This project | `CLAUDE.md`, `biblio-template.bib` | **Presentation choices this project has made** where the package permits more than one: which fields to omit (`ISSN`, `ISBN`, `keywords`), when `Url`/`Urldate` may appear, title case by language, `LastName, FirstName~Initials` name format, `Title`/`Subtitle` splitting, `Shorttitle` use, `\foreignlanguage`/`\mkbibquote` wrapping, range separators, the `date-added`/`date-modified` stamps | Governs style, never capability |

  **Which tier decides a given question.** Do not classify by topic — the same field can raise both kinds of question. Classify by what would settle it:

  1. **Questions of fact about the package** — does this field or type exist, what does the style do with this value, what reaches the printed page. Tier 1 decides. These have one correct answer, independent of anything this project prefers, and a test compilation settles them conclusively.
  2. **Questions of choice among forms the package treats as equivalent** — where two or more encodings are all accepted and all produce the same printed result, and the only remaining question is which this project writes. Tier 3 decides.

  **Tier 2 decides neither kind, ever.** `biblatex-chicago-notes-ref.md` is a *condensation of §§4.1–4.2 of `biblatex-chicago.pdf`* — the manual's entry-type and field chapters, compressed by hand into 340 lines. That makes it a finding aid, not an authority: everything in it is a paraphrase of something tier 1 states in full, and a paraphrase can lose the exception. Its number marks where it sits in the *lookup path* — reach for it first, because it is by far the fastest way to find a field or type — and is not a rank of authority between tiers 1 and 3. Two rules follow, and neither is optional:

  - **Use it to locate, never to conclude.** Any claim it makes about what a field *does* is provisional. Confirm it against tier 1 — or by compiling — before acting on it. It is treacherous precisely because it is phrased as plain fact and reads exactly like tier 1.
  - **When it turns out to be wrong, correct it there.** Not in `CLAUDE.md`, and not by working around it at the call site. A package fact recorded as a house rule is duplication that drifts out of step the next time the package is re-vendored.

  **The operational test, when it is unclear which kind you face: encode the alternatives, compile, and compare the rendered output.** Tier 2 is never one of the alternatives — it describes forms, it is not one.
  - Different output → question of fact → **tier 1 decides**, and the form that renders correctly wins regardless of what tier 3 says.
  - Identical output → question of choice → **tier 3 decides**, and consistency with the existing corpus is the tie-breaker.

  Three consequences, each of which has already been got wrong here:

  - **Tier 3 cannot overrule tier 1.** A rule in `CLAUDE.md` that prescribes an encoding which renders incorrectly is not a house style; it is a defect, and it must be corrected here rather than worked around. Four such defects have been found and fixed: hyphens for date ranges, the prohibition on splitting at terminal punctuation, whole-field `\foreignlanguage` on names, and `Url` retention by entry type alone.
  - **Tier 1 does not settle questions of choice.** Where the package accepts several forms, its own files exercise only one of them, and that is not a ruling. `notes-test.bib` writes literal-field ranges with a single hyphen; both forms compile, so the choice is tier 3's and this project writes `--`.
  - **Silence is not prohibition.** A type or field appearing in no tier-2 or tier-3 file is available whenever tier 1 defines it. `biblio-template.bib` covers 13 entry types and `notes-test.bib` 32, against roughly 40 the package defines; neither is a list of what is permitted. Absence is a gap in local coverage, never a restriction.

  **This is not hypothetical.** `biblatex-chicago-notes-ref.md` described `foreword`/`introduction`/`afterword` as "author of a foreword" and so on — true of biblatex in general, false for `@SuppBook`/`@SuppCollection`, where the style reads the field's presence and never prints its value. Acting on it destroyed a working entry: deleting what looked like a stray placeholder cost the entry its label and the full stop after the author. Tier 1 had the answer all along, in `polakow:afterw` and `prose:intro`.

  **Beware also the source that is authoritative for a different question.** biblatex's own `blx-dm.def` gives each field a datatype, and for these fields says `datatype=name` — correct about typing, misleading about behaviour, because biblatex-chicago overrides what it does with the value. The data model tells you a field's *type*; the style's test suite tells you what the *style* does with it.
- Known local departures from the corpora, all stylistic: they populate `url` on printed works, use `keywords` and `annote`, and use the legacy `school`/`address` aliases (this project uses `institution` and `location`). Never copy an example's bibliographic data into an entry; they illustrate form, not content.
- Every new entry must include `date-added` and `date-modified` fields, both set to the current date, time, and timezone, in the format `date-added = {2026-03-22 14:30:00 +0200}`. The extraction prompt supplies the current timestamp — use exactly that value. Never infer one from the publication or guess: a plausible-looking wrong timestamp is worse than an obviously missing one, because nothing downstream will catch it.
- Do not populate the following fields:
  - ISSN
  - ISBN
  - keywords
  - reference
  - devonthink
  - URL - **exception:** populate `Url` and `Urldate` when the entry is typed `@Online` (the one type with no other locator to fall back on), when it is an online reference work (`@Inreference`/`@Reference` with `Entrysubtype = {online}`, which the package requires to carry both), when the entry has no `Date` (in which case `Urldate` is the only dating evidence available and `Url` its necessary companion), or **when the entry carries no `Doi`**. The governing principle is that an entry should offer exactly one canonical locator: Chicago cites a DOI in preference to a URL, so a `Url` sitting beside a `Doi` is redundant and should be omitted, whereas on an entry with no DOI the URL is the only locator the style has to print and therefore stays - whatever the entry type. This is a retention rule, not an instruction to invent an access date for a PDF that carries no URL of its own - and it applies by the entry's own fields and type, not by the source it came from: a `.webloc`-sourced page need not be typed `@Online` if the work it bookmarks is properly a book, article, etc. (see item 1 above), and conversely a PDF that turns out to be a printout of an online-only source is still typed `@Online` and keeps its `Url`/`Urldate`. If `Urldate` is populated with only an access date (no revision/last-modified date given by the source), Chicago prints "n.d." as the headline date - this is expected. If the source itself states a revision or last-modified date, put that date in `Urldate` instead and add `Userd = {last modified}` (or similar) so it prints as a qualified date rather than a bare access date.
- **Range separators depend on how biblatex types the field, and there are three cases.** Getting this wrong is invisible in the `.bib` and wrong on the page, so check the field's datatype before assuming.
  - **`Pages` — a single hyphen.** `Pages` is the only field biblatex types as a range, and range fields have every dash normalised to an en dash, so `67-97`, `67--97` and `67–97` render identically. The source form therefore cannot reach the page, which makes this a tier-3 choice and the house form is the single hyphen — matching `notes-test.bib` (27 of 27) and `biblio-template.bib`. **Do not "upgrade" `Pages` to `--`**: several thousand values would change and no output would. (Tier 1: manual §2.2.1 under Range fields, and `\bibrangedash` in §3.12.4.)
  - **`Date`, `Origdate`, `Eventdate`, `Urldate` — a solidus.** These are parsed as ISO 8601-2, where `/` separates the endpoints and `-` separates year from month. So `2012-13` does not read as "2012 to 13"; it reads as month 13 of 2012, which is invalid. Write `Date = {2012/2013}`, both years in full — `2012/13` is not valid either. Single dates are unaffected, so `Date = {2020-02}` remains a perfectly good February 2020. (Manual, Table 3, which gives `1988/1992`.)
  - **Every other field — `--`.** `Volume` and `Volumes` are `datatype=integer`; `Number`, `Title`, `Subtitle`, `Issuetitle`, `Titleaddon` and the rest are `datatype=literal`, which the manual describes as "printed as is". Nothing normalizes them, so the separator you type is the separator that prints. Chicago sets a numeric range with an en dash, and in LaTeX that is `--`. Write `Number = {1--2}`, `Volume = {49--50}`, `Title = {… 1900--1950}`.
    - Confirmed by compilation, not only by reading the manual: under `[notes]{biblatex-chicago}`, `pages = {67-97}` and `pages = {67--97}` both render `67–97`, whereas `title = {…, 1900-1950}` renders a hyphen-minus and `title = {…, 1900--1950}` an en dash. The two field classes really do behave differently.
    - `notes-test.bib` does **not** follow this — all 11 of its literal-field ranges use a single hyphen (`title = {The Pattern of Crime in England, 1660-1800}`, `number = {23-24}`), which therefore renders as a hyphen. That is not a tier-1 ruling against the `--` form: both forms compile, so by the test below this is a tier-3 choice, and the test suite takes no view on typography.
    - Only convert a hyphen to `--` where the pair genuinely ascends. `Op.15-2` is an opus number, `3-2 Cohn Cycle` is a name, and an elided range such as `107-26` needs expanding to `107--126` by hand rather than mechanically.
    - **Never touch hyphens in any of these — they belong to identifiers, filenames, URIs or timestamps, and a "range" found inside one is always spurious:** `Doi`, `Url`, `Local-Url`/`Local-Url-N`, `Remote-Url`/`Remote-Url-N`, `Devonthink`/`Devonthink-N`, `Bdsk-File-N`, `Bdsk-Url-N`, `Date-Added`, `Date-Modified`. This is a superset of the protected-field list and holds even where a value looks exactly like a range: `10.1215/00222909-1650433` is a DOI suffix, and `2026-07-29 19:52:49 +0300` is a timestamp.
  - **Never normalize dashes in `Doi` or `Url`.** They are `verbatim` and `uri` fields respectively; their hyphens are part of the identifier, not punctuation.
- In the date field only the four-digit year, unless the item is:
  - @unpublished
  - @article with `entrysubtype={magazine}`.
- If the main language of the publication is English, the following fields should appear in title case (according to Chicago Manual of Style sense of "title case"):
  - Title
  - Journaltitle
  - Booktitle
  - Issuetitle
  - Publisher
- Hyphenated and other compound words are where title case most often goes wrong — capitalising only the first element is the usual error. Give compounds particular attention and apply the Chicago Manual's treatment of them rather than a mechanical first-word-only rule.
- If the language of the publication is Russian or French, use title case.
- If the language of the publication is German, use German title case.
- Do not use all caps, unless it is a single, individual word in the title.
- All proper-name fields should be in "LastName, FirstName\~Initials" format, e.g. "Smith, John~A."
- If any individual words or subphrases within the title appear in quotation marks, enclose them in \mkbibquote{} instead of using quotation marks. For example, if the title of the book is: `From 'Here' to 'There'` encode that as `From \mkbibquote{Here} to \mkbibquote{There}`
  - **One exception, and it is a genuine package limitation rather than a preference: do not use `\mkbibquote` where the quoted phrase *ends* a field that the style itself sets in quotation marks** — the `Title` or `Subtitle` of an `@Article`, `@Incollection` and their kin. There the nesting inverts: `Subtitle = {… the Making of \mkbibquote{Happy Birthday}}` renders `… the Making of ‘Happy Birthday.”’`, closing the outer double quote *inside* the inner single one. Compile-proved against the alternative, which is right: raw curly quotes give `‘Happy Birthday’.”` Only the terminal position in a quoted field is affected — `\mkbibquote` mid-field nests correctly (`Investigating the \mkbibquote{Flow} Experience` → `Investigating the ‘Flow’ Experience`), and so does a field the style does not quote, such as `Note`.
- For foreign-language text, use `\foreignlanguage{<language name>}{<text>}`. See examples in the template file.
- **In a name field, wrap each name *component* separately — never the field as a whole.** This applies to `Author`, `Editor`, `Editora`/`Editorb`/`Editorc`, `Translator`, `Bookauthor`, `Introduction`, `Foreword`, `Afterword`, `Commentator`, `Annotator`, `Holder` and the `Name*` fields.

  ```
  Author = {\foreignlanguage{russian}{Акопян}, \foreignlanguage{russian}{Л.~О.}}        ✓
  Author = {\foreignlanguage{russian}{Акопян, Л.~О.}}                                   ✗
  Author = {Акопян, Л.~О.}                                                        legal, but see below
  ```

  Wrapping the whole field buries the separators biber needs — the comma between surname and forenames, and ` and ` between people — inside the macro's argument, so the value becomes one opaque string. Wrapping per component leaves those separators at brace depth zero, so the name parses normally *and* every component still carries its language. Two things break with the whole-field form, neither visible in the `.bib`:
  - **The first/last inversion is lost.** Chicago prints a name "First Last" in every position except the leading one, and above all in the note, which is this style's primary citation form. Compiled proof: `Author = {\foreignlanguage{russian}{Акопян, Л.~О.}}` gives the note `Акопян, Л. О., Эвфония и парафония (…)`, whereas the unwrapped `Author = {Акопян, Л.~О.}` gives the correct `Л. О. Акопян, Эвфония и парафония (…)`. The bibliography looks identical either way — "Last, First" is right *there* — which is exactly why this hides.
  - **A name list collapses into a single person.** `{Ivanov, Ivan~I. and Petrov, Petr~P.}` wrapped renders as the one name `Ivanov, Ivan I. and Petrov, Petr P.`; unwrapped it renders as `Ivan I. Ivanov and Petr P. Petrov`, two people with Chicago's conjunction. This is a loss of bibliographic data, not merely of typography.
  - **Do not simply strip the wrapper instead: `Langid` does not compensate.** biblatex applies a language environment per entry only when the `autolang` package option asks it to, and `autolang` defaults to `none` — under which `Langid` is typographically inert. Verified by compiling a French title with no space before its question mark: wrapped, French spacing is inserted; bare with `Langid = {french}`, nothing happens. `autolang=hyphen` behaves the same here (it loads hyphenation patterns only). The loss is not theoretical even for names: with `autolang=none` a bare Cyrillic surname is offered English hyphenation patterns, which never match, so a long name will not break at all and overflows the measure — `Константинопольский` runs off the line bare, and hyphenates to `Константино-польский` when wrapped per component. Record `Langid` as well, for sorting and for tools that read it; the two are complementary, not alternatives.
  - **Never write `\,` inside a name field — use `~`.** biber reads the comma in `\,` as the surname/forename separator, so `Author = {Harrison, Peter~M.\,C.}` is split three ways and written to the `.bbl` as `suffix={Peter\bibnamedelima M.\}` — a trailing backslash that escapes the closing brace, so the group never closes. One such entry broke the bibliography's list environment and orphaned 2,099 of 5,741 entries as `Lonely \item`. `~` is equally non-breaking and is the house form already: `Smith, John~A.`, `Harrison, Peter~M.~C.` Two neighbouring faults from the same family, both compile-proved: **`\and` is LaTeX's `\author` separator and is undefined in a bibliography field** — a list field such as `Location` is split on the bare word `and`, so write `Durham and London`; and **`\reviewof{…}` is not a command** — `reviewof` exists only as a bibstring and a `relatedtype`, so the form is `\bibstring{reviewof} \mkbibemph{…}`.
  - **Non-name fields** — `Title`, `Subtitle`, `Booktitle`, `Publisher`, `Location`, `Organization`, `Series`, `Note` — take a single wrapper around the whole value, since there is no name parser to obstruct. A wrapper is evidence about the *string*, never about the *entry*: `Langid` records the language of the work being catalogued, so an English review of a French book, or an Anglophone journal with a German series name, correctly carries a `\foreignlanguage` wrapper and **no** `Langid`.
  - **A TeX special left unescaped in a printed field halts the build.** `_` and `^` demand math mode; `&` is the alignment tab and opens a table cell; `%` comments out the rest of the line. Write `\_`, `\^{}`, `\&`. This does **not** apply to `Url`, `Doi` or `Eprint`, which are verbatim fields: there the raw characters are correct and escaping them corrupts the identifier — `\%20` renders as `%5C%20`, putting the backslash inside the address.
  - **This project compiles with `autolang=other`, so `Langid` is load-bearing.** An earlier version of this file claimed `autolang=none` was correct here and warned against `other`; that was written without sight of the maintainer's actual preamble, which sets `autolang=other` deliberately and pairs it with `\DeclareLanguageMapping{french}{cms-french}` and `{ngerman}{cms-german}`. The localisation is the point, not a side effect. What the option selects (manual §3.1.2) is which babel environment biblatex wraps each entry in: `none` uses none, so `Langid` is typographically inert; `hyphen` gives hyphenation patterns only; **`other` gives hyphenation, every extra babel/biblatex definition for the language, and translation of key terms**. Compiled on one entry, changing only the option: `edited by Marie Dupont, 9–26. Paris: Vrin` becomes `« … ». In …, sous la direction de Marie Dupont, 9-26. Paris : Vrin`. Four things move — guillemets, the key term, French colon spacing, and the page dash. Three consequences for encoding:
    - **`Langid` is not bookkeeping.** It decides quotation marks, key terms, punctuation and dashes on the page. Record it only where the *entry* is in that language, never because a title contains a foreign phrase.
    - **A `Langid` or `\foreignlanguage` naming a language the document has not loaded is a hard error, not a silent no-op.** Both must use an identifier biblatex knows: `norsk` or `nynorsk`, never `norwegian`; `ngerman`, not `german`. Fixing one and not the other still fails — `Bergby2009` carried the bad name in both.
    - The wrappers remain necessary regardless, because they mark foreign text *within* a field that the entry's own language does not cover.
    - **One exception: never wrap the `Title` of an `@Online` entry.** Under `autolang=other`, `\foreignlanguage` there makes babel's `\bbl@foreign@x` fail and the build stops. Compile-proved to be specific to that combination: the identical wrapper is harmless on `@Article` and `@Book` — 491 of them in this library — and harmless on the same entry's `Subtitle`, and it is not the `Url`. It is also redundant, because the entry's `Langid` already encloses the whole record.
    - **An empty field is not an absent one.** `Journaltitle = {\foreignlanguage{russian}{}}` looks empty but is emitted, so babel switches language for a zero-length string and fails. Where a value is unknown, omit the field; never leave it present and blank.
  - **Inside a French wrapper, never type the space before `?`, `!`, `;` or `:`.** French sets a thin space there, and `polyglossia` inserts it on its own: `\foreignlanguage{french}{…faire ?}` and `\foreignlanguage{french}{…faire?}` render *identically*, as `faire ?`, in the full title and in the short note alike. Compile-proved, so by the operational test above this is a question of choice and the house form is the unspaced one — the source then says what the page shows. Typing it also invites disagreement between fields: `Rayna2014` ended up with a spaced `Title` and an unspaced `Shorttitle`, which look like different strings and are not. Two further traps:
    - **A tie on *both* sides is a defect, not redundancy.** `transfert~:~ lecture` adds a space after the colon that `polyglossia` does not intend. Write `transfert: lecture`.
    - **This is `polyglossia`'s doing, not biblatex-chicago's**, so it applies only *inside* the wrapper. The colon biblatex supplies between `Title` and `Subtitle` is emitted outside any language environment and takes Chicago spacing — which is why splitting a French title changes `musicale : une introduction` to `musicale: une introduction`. That change is correct: the colon belongs to the citation, not to the title.
- Split a colon-separated title across `Title` (the part before the colon) and `Subtitle` (the part after), dropping the colon itself — biblatex-chicago supplies the colon and applies Chicago capitalisation to each part independently. The same applies to `Booktitle`/`Booksubtitle` and `Maintitle`/`Mainsubtitle`. Do **not** split when:
  - the colon falls inside a quoted or emphasised sub-phrase rather than at the entry's own title boundary (e.g. the reviewed work's title in a `@Review` entry — see `Dunsby1997` in the template); or
  - the title carries **more than one** top-level colon, so which one is the entry's own boundary is genuinely ambiguous (`An Introduction to the Mathematics of Digital Signal Processing: Part~I: Algebra…`); or
  - the title is a multi-part rhetorical construction rather than a title plus a subtitle — more than one top-level `?`/`!`, as in `Aesthetics---What? Why? and Wherefore?`, which is one continuous thought.
- **A title joined to its subtitle by terminal punctuation *is* splittable**, and the mark stays on `Title`: `Title = {How Social Is the Animal?}`, `Subtitle = {The Human Capacity for Caring}`. The style suppresses the colon after `.`, `!` or `?` by itself, so nothing is interpolated that the source does not have. Tier 1: `batson` in `notes-test.bib` and its `annote`; the mechanism is `\subtitlepunct` in `chicago-notes.cbx`.
  - A **full stop** is the one case to leave alone unless you have checked it by eye. TeX treats a period after a capital as an abbreviation, so `J.\,S. Bach`, `Op.\,110` and `Pitch vs. Timbre` are not sentence boundaries; splitting there would sever a name or an opus number. Where a period genuinely ends the title (`Sonagraph. A Cartoonified Spectral Model…`), splitting is correct — but decide it per entry, never by rule.
- `Shorttitle` is normally unnecessary once the title is split, because short notes fall back to `Title`, which no longer contains the subtitle. This holds for a terminal-punctuation split exactly as for a colon split. Use `Shorttitle` only when the title could not be split at all (the cases above) and the full title is more than six words long; its value is then the first part of the title, up to the mark. An entry should never carry both a `Subtitle` and a `Shorttitle`: if it does, the title was hoisted without truncating the parent, and the two fields disagree about where the work's title ends.
  - **"Could not be split" is not the same as "has no colon", and conflating the two silently destroys earned `Shorttitle` values.** A title whose boundary mark is sealed inside a macro still *has* a boundary; the splitter simply declines to act on it. `\foreignlanguage{russian}{История русской музыки: От Древней Руси…}` and `Kretschmer2008`'s `\foreignlanguage{ngerman}{\mkbibquote{…einer Idee?} Schenker-Analyse…}` both keep their `Shorttitle`, as does a title ending in `?` with nothing after the mark to hoist. The test is: *would the splitter act?* If not, and the title runs over six words and contains a `:`, `?` or `!` anywhere at any brace depth, the `Shorttitle` is earned.
  - A title carrying no boundary mark at all is the genuinely redundant case — there is nothing to shorten it *to* — and that is the only shape from which `Shorttitle` should be removed.
  - **`Shorttitle` is not decorative here: this project cites with `\shortcite`.** The short note is a form the maintainer uses directly, so verify a `Shorttitle` decision by compiling `\shortcite`, not only `\autocite` and `\printbibliography`. Doing so is what caught `Vanhande\"{l}` — a diaeresis planted on an `l`, invisible in the `.bib` and in the long note's line-breaking, but plain as `Vanhandel,̈` once the short form was set. Two commands make other fields load-bearing in the same way: **`\citejournal`** prints `Journaltitle` in place of the title, so that field's title case reaches the page on its own; and **`\headlesscite`** suppresses the author, so an entry whose author also appears in the title reads differently. Compiled confirmation that the rules above hold in the short form: `Cole, review of Sounds as They Are, by Beaudoin`; `Мусин, О воспитании дирижера`; `Gutierrez, \mkbibquote{An Enactive Approach to Learning Music Theory?}`; `Moore, \mkbibquote{… Digital Signal Processing: Part~I}`.
- A conference paper is `@Unpublished` only if it was never collected into a published proceedings volume. For those items, the `Note` field is used with the following content: `\autocap{p}aper presented at <Conference Name>`, for example: `\autocap{p}aper presented at the 9th Meeting of the Russian Society for Music Theory`. If the paper WAS published in a proceedings volume, use `@Inproceedings` instead (`Booktitle` = the proceedings volume's own title, `Eventtitle` = the conference name if distinct from the volume title) - never `@Unpublished` for a paper that has an actual publication to cite.
- For a review, encode the reviewed work directly in `Title` (and the compressed form in `Shorttitle`) rather than via `relatedtype`/`related`: `\bibstring{reviewof} \mkbibemph{<title of the work reviewed>}, \bibstring{by} <author of the work reviewed>`. See `Dunsby1997` in `biblio-template.bib` for a full worked example.
- A dissertation issued by a commercial publisher is a `@Book`, not a `@Thesis`. What is being cited is the published edition, so it takes the ordinary book apparatus - `Publisher`, `Location`, `Date` - and, where the volume states them, `Series` and `Number` for the academic series it appeared in. The dissertation origin is then relegated to `Note`, again only if the volume states it: `\autocap{o}riginally presented as the author's doctoral dissertation, <Institution>, <Year>`. Omit `Note` entirely rather than inferring a degree or an institution the source does not name. Reserve `@Thesis` for a dissertation consulted as a dissertation - a university copy, a repository PDF, a microfilm - where no commercial imprint exists to cite. The tell is an imprint page carrying a publisher, a place, and usually a series: a German volume's `Zugl.: <Place>, Univ., Diss., <Year>` line records the origin of a book, it does not make the item a thesis.
- `Series` takes the **name of the series alone**. Everything else the imprint page attaches to it goes in `Number`: a subseries or division (`Reihe XXXVI, Musikwissenschaft`, `2nd ser.`, `\bibstring{newseries}`), then the volume or number within the series. This is the package's own rule, not a presentation choice — `boxer:china` in `notes-test.bib` carries `Series = {Hakluyt Society Publications}`, `Number = {2nd ser., 106}`, and its `annote` says putting the division in `Number` "may seem counter-intuitive, but it's necessary for getting the punctuation to work out right." biblatex-chicago generates the punctuation between the two fields, so a division left inside `Series` is set wrongly. See also `wauchope:ceramics` (`Number = {\bibstring{volume} 1, \bibstring{number} 14}`) — "the name of the series alone goes in series, the rest in number" — and `palmatary:pottery`.
  - So a German volume whose CIP line reads `Europäische Hochschulschriften : Reihe 36, Musikwissenschaft ; Bd. 35` is encoded `Series = {\foreignlanguage{ngerman}{Europäische Hochschulschriften}}` and `Number = {\foreignlanguage{ngerman}{Reihe XXXVI, Musikwissenschaft}, 35}` — never as a single `Series` holding both, however the title page punctuates them. The spaced ` : ` and ` ; ` there are library-cataloguing convention, and mark the very boundaries along which the value should be split between the two fields.
- For more details on available fields and their use, see the reference sources below.

## Reference Documentation

In this repository, all four under `prompt-context/` (no network access needed; the last three are loaded into the extraction prompt):

- `biblatex-chicago-notes-ref.md` — **tier 2.** Condensed, hand-derived field and entry-type reference; the only local file listing all 40 entry types, and the quickest way to find one. Not a primary source: it is a summary of the manual, it has been wrong before, and a claim about *behaviour* taken from it should be checked against `notes-test.bib` or the manual before it is acted on. When it is wrong, correct it here rather than working around it elsewhere.
- `biblio-template.bib` — **tier 3.** This project's house style, one worked example per entry type, drawn from `biblio.bib` by `dev/build_template.py`. Authoritative on presentation, not on which types exist. Doubles as the regression fixture: `dev/bib_normalize.py` must report 0 edits against it and it must compile without biber warnings.
- `notes-test.bib` — **tier 1.** The package's annotated test suite (203 entries, 32 types), vendored verbatim. Authoritative on type and field semantics, and the first place to look when a field's behaviour is in question; most entries carry an `annote` saying why the type and field set follow from the source. Keep it byte-identical to upstream so it can be re-vendored and diffed.
- `cms-notes-intro-guide.md` — **tier 1.** The package author's own introduction, `cms-notes-intro.tex`, mechanically *extracted* rather than summarised by `dev/extract_intro.py` from the copy that ships with the package (`texdoc -l cms-notes-intro` locates it; it is not vendored here): LaTeX scaffolding and the trailing database appendix are dropped (the latter duplicates `notes-test.bib`), the prose is kept whole, and each `Type -> worked example` mapping is rendered inline as `@Type [key]`. Re-run the script after a package update and diff this file.

### The rules that are executable rather than written down

Much of what this project has learned about how entries go wrong is not prose in
this file but **rules in `dev/bib_audit.py`**, which is the right home for it:
they are checkable, they run in a second over the whole library, and they cannot
drift out of step with themselves. Consult them the way you would consult a
checklist — `python3 dev/bib_audit.py <file>` prints a count per rule.

They cover, among others: a value sitting in the wrong field (a page range in
`Volume`, a bare number in `Issuetitle`); text doubled by a botched import;
values truncated at an unclosed parenthesis; bibstring names written as commands
(`\reviewof{}`); typographic ligatures (U+FB03 for "ffi"); and `Shorttitle`
verdicts. Three programs, split by how they act:

- **`dev/bib_audit.py`** — read-only counting, and the home of the **shared
  predicates**. `bib_normalize.py` imports them; never reimplement one in the
  audit, or the two will disagree and the audit will be the wrong one.
- **`dev/bib_normalize.py`** — edits derived from a rule that fires across the
  corpus.
- **`dev/bib_apply.py`** — a named JSON edit list, for what judgement rather
  than rule has to settle.

All three write nothing without ten gates passing. `dev/normalization-plan.md`
records the design; the entry-level history lives outside the repository.

Upstream (consult when the local files are insufficient):

- biblatex-chicago package: https://ctan.org/tex-archive/macros/latex/contrib/biblatex-contrib/biblatex-chicago.
- Types and fields reference (the full manual, far more detailed than the condensed reference above): https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/biblatex-chicago.pdf.
- Examples: https://mirror.init7.net/ctan/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/cms-notes-intro.pdf — the rendered form of the package's `cms-notes-intro.tex`.

## Important Notes

- Use biblatex-chicago specific types and fields, not generic BibLaTeX.
- The Chicago style variant is "notes and bibliography", not "author-date".

### Operator note: model choice for reference works

*This is guidance for whoever runs the pipeline, not an instruction to the extracting model.*

Reference works — Grove Music Online, the Stanford Encyclopedia, Wikipedia and the like — are the hardest sources here, because a correct entry has to put the *work* in `Title` and the *article* in `Lista`, add `Entrysubtype = {online}` where there is no print counterpart, and prefer a stated revision date in `Urldate` qualified by `Userd` over a bare access date. Comparison runs on `plato.stanford.edu` and Grove showed `claude-sonnet-4-6` getting parts of this wrong (article name in `Title`) and `claude-sonnet-5` fabricating a publisher and a date, while `claude-opus-5` produced the full correct form including the `Urldate`/`Userd` revision-date pairing.

For a batch of reference works, run with `--model claude-opus-5`; the flag overrides `config.yaml` for that invocation only. It roughly doubles the cost — Opus prices at $5/$25 per 1M tokens against Sonnet's $3/$15, and its tokenizer makes the cached prefix about 27% larger — so it is worth reaching for on awkward sources rather than by default. `dev/estimate_cost.py --model claude-opus-5` prices it before you commit.

## Autonomous sessions

These rules govern any session where the maintainer is not watching. They exist
because this project has produced, in a single week, four separate faults of one
shape: a failure that reported success. An empty tab list read as an empty
browser; a refused permission read as no matching tab; an unparsable line skipped
by a bare `continue`; a marker regex that never matched and silently disabled
every regex behind it. In each case the tests passed and the run said Complete.

Treat a confident completion report as the thing most likely to be wrong.

### Never, without asking

- **Merge anything.** Open PRs; the maintainer merges.
- **Edit `dev/eval/expected.bib`.** It is ground truth. Everything else in the
  repository is measured against it, which makes a plausible-looking error here
  both the most costly and the least visible kind. If an entry appears wrong,
  report it with the citekey and the evidence, and stop.
- **Edit `CLAUDE.md` or anything under `prompt-context/`.** These form the cached
  prefix; a change silently alters extraction behaviour for every subsequent run
  and invalidates comparison against the current baseline. Propose the wording
  and wait.
- **Spend more than 10 live API calls in one session.** Prefer `--rescore`
  against `dev/eval/last-run/`, which is free. A full 61-entry run costs real
  money and needs asking for.
- **Claim a GUI-dependent thing works.** BibDesk colouring, Safari and Chrome tab
  capture, the Quick Action, Apple Events permissions: none is visible from a
  terminal. Say what you changed, name the check the maintainer should run, and
  stop there.

### Always

- **Branch per issue**, named `issue-N-slug`, off `main`. Small commits.
  `Closes #N` in the PR body, not in a commit message on a branch that may be
  rebased.
- **Run `python3 dev/eval/test_eval.py` and `python dev/test_setup.py` before
  every commit.** Plus the web-source suite when `src/web_source.py` is touched.
- **`--rescore` after any change to extraction, and put the result in the PR
  body**, against the most recent file in `dev/eval/baselines/`. A change that
  moves fewer than about two entries in 61 is within noise: report it as
  unchanged rather than as an improvement. One entry flipped between the first
  two baseline runs with no attributable cause.
- **Distinguish what was tested from what was assumed.** A stub encodes the
  format you expected, not the format the system emits — which is exactly how
  the AppleScript terminology collision survived a round of testing. When a test
  passes against the broken code too, say so.
- **State provenance for fixtures.** Recorded from the maintainer's real data, or
  constructed from documented grammar? Both are legitimate; conflating them is
  not.
- **Correct your own earlier claims when they turn out to be wrong**, in a commit
  and in the issue or PR that carries them. Two commit messages this week
  overstated a fix's scope and would have sent the maintainer re-checking a
  library that was never affected.

### When something fails

Trace before proposing. Report where control actually goes, then stop. Do not
change a mechanism on the assumption that it ran: three of this week's faults
looked like the wrong logic and were in fact code that never executed.

If a diagnosis rests on something unobservable from here — a permission, a window,
a process start time — say so and name what would settle it.

### On the queue

Implementing what is in front of you is the easy part. If the issue seems wrong —
badly scoped, already obsolete, or resting on a premise the baseline contradicts
— say so instead of building it. #7 was correctly closed unbuilt, on the grounds
that the measurement it proposed was below the noise floor of the instrument.
That judgement was worth more than the implementation would have been.
