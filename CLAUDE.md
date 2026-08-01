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
- **Precedence is split by question, not by file.** The two corpora are the biblatex-chicago package's own documentation and are authoritative on everything the package defines: which entry types exist, what each one means, which fields it takes, what those fields do, and the correct use of `entrysubtype`, `relatedtype`, `\bibstring`, and the rest of the package's machinery. `CLAUDE.md` (this file) and `biblio-template.bib` are authoritative only on **presentation choices this project has made** where the package permits more than one: which fields to omit (`ISSN`, `ISBN`, `keywords`), when `Url`/`Urldate` may appear, title case by language, `LastName, FirstName~Initials` name format, `Title`/`Subtitle` splitting, `Shorttitle` use, `\foreignlanguage`/`\mkbibquote` wrapping, and the `date-added`/`date-modified` stamps. On a *style* question the local files govern; on a *what does biblatex-chicago actually support* question the corpora govern, and a type or field absent from the local files is not thereby disallowed.
- Known local departures from the corpora, all stylistic: they populate `url` on printed works, use `keywords` and `annote`, and use the legacy `school`/`address` aliases (this project uses `institution` and `location`). Never copy an example's bibliographic data into an entry; they illustrate form, not content.
- Every new entry must include `date-added` and `date-modified` fields, both set to the current date, time, and timezone, in the format `date-added = {2026-03-22 14:30:00 +0200}`. The extraction prompt supplies the current timestamp — use exactly that value. Never infer one from the publication or guess: a plausible-looking wrong timestamp is worse than an obviously missing one, because nothing downstream will catch it.
- Do not populate the following fields:
  - ISSN
  - ISBN
  - keywords
  - reference
  - devonthink
  - URL - **exception:** populate `Url` and `Urldate` when the entry is typed `@Online` (the one type with no other locator to fall back on), when it is an online reference work (`@Inreference`/`@Reference` with `Entrysubtype = {online}`, which the package requires to carry both), or when the entry has no `Date` (in which case `Urldate` is the only dating evidence available and `Url` its necessary companion). This is a retention rule, not an instruction to invent an access date for a PDF that carries no URL of its own - and it applies by entry type, not by source: a `.webloc`-sourced page need not be typed `@Online` if the work it bookmarks is properly a book, article, etc. (see item 1 above), and conversely a PDF that turns out to be a printout of an online-only source is still typed `@Online` and keeps its `Url`/`Urldate`. If `Urldate` is populated with only an access date (no revision/last-modified date given by the source), Chicago prints "n.d." as the headline date - this is expected. If the source itself states a revision or last-modified date, put that date in `Urldate` instead and add `Userd = {last modified}` (or similar) so it prints as a qualified date rather than a bare access date.
- Use a single hyphen (`-`) for page ranges, date ranges, and any other ranges.
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
- For foreign-language text in any fields, use `\foreignlanguage{<language name>}{<text>}`. See examples in the template file.
- Split a colon-separated title across `Title` (the part before the colon) and `Subtitle` (the part after), dropping the colon itself — biblatex-chicago supplies the colon and applies Chicago capitalisation to each part independently. The same applies to `Booktitle`/`Booksubtitle` and `Maintitle`/`Mainsubtitle`. Do **not** split when:
  - the colon falls inside a quoted or emphasised sub-phrase rather than at the entry's own title boundary (e.g. the reviewed work's title in a `@Review` entry — see `Dunsby1997` in the template); or
  - the two parts are joined by punctuation other than a colon, such as a question mark or full stop (see `Kretschmer2008`) — biblatex would insert a colon the source doesn't have.
- `Shorttitle` is normally unnecessary once the title is split, because short notes fall back to `Title`, which no longer contains the subtitle. Use `Shorttitle` only when the title could not be split (the cases above) and the full title is more than six words long; its value is then the first part of the title, up to the colon or full stop.
- A conference paper is `@Unpublished` only if it was never collected into a published proceedings volume. For those items, the `Note` field is used with the following content: `\autocap{p}aper presented at <Conference Name>`, for example: `\autocap{p}aper presented at the 9th Meeting of the Russian Society for Music Theory`. If the paper WAS published in a proceedings volume, use `@Inproceedings` instead (`Booktitle` = the proceedings volume's own title, `Eventtitle` = the conference name if distinct from the volume title) - never `@Unpublished` for a paper that has an actual publication to cite.
- For a review, encode the reviewed work directly in `Title` (and the compressed form in `Shorttitle`) rather than via `relatedtype`/`related`: `\bibstring{reviewof} \mkbibemph{<title of the work reviewed>}, \bibstring{by} <author of the work reviewed>`. See `Dunsby1997` in `biblio-template.bib` for a full worked example.
- For more details on available fields and their use, see the reference sources below.

## Reference Documentation

In this repository, all four under `prompt-context/` (no network access needed; the last three are loaded into the extraction prompt):

- `biblatex-chicago-notes-ref.md` — condensed field and entry-type reference. The only local file listing all 40 entry types.
- `biblio-template.bib` — this project's house style, covering the 20 types used so far. Authoritative on presentation, not on which types exist.
- `notes-test.bib` — the package's annotated test suite (203 entries, 32 types). Authoritative on type and field semantics; see the precedence note above.
- `cms-notes-intro-guide.md` — entry-type guide derived from `cms-notes-intro.tex` by `dev/extract_intro.py`. Re-run that script to regenerate it after updating the upstream `.tex`.

Upstream (consult when the local files are insufficient):

- biblatex-chicago package: https://ctan.org/tex-archive/macros/latex/contrib/biblatex-contrib/biblatex-chicago.
- Types and fields reference (the full manual, far more detailed than the condensed reference above): https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/biblatex-chicago.pdf.
- Examples: https://mirror.init7.net/ctan/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/cms-notes-intro.pdf — the rendered form of the local `cms-notes-intro.tex`.

## Important Notes

- Use biblatex-chicago specific types and fields, not generic BibLaTeX.
- The Chicago style variant is "notes and bibliography", not "author-date".

### Operator note: model choice for reference works

*This is guidance for whoever runs the pipeline, not an instruction to the extracting model.*

Reference works — Grove Music Online, the Stanford Encyclopedia, Wikipedia and the like — are the hardest sources here, because a correct entry has to put the *work* in `Title` and the *article* in `Lista`, add `Entrysubtype = {online}` where there is no print counterpart, and prefer a stated revision date in `Urldate` qualified by `Userd` over a bare access date. Comparison runs on `plato.stanford.edu` and Grove showed `claude-sonnet-4-6` getting parts of this wrong (article name in `Title`) and `claude-sonnet-5` fabricating a publisher and a date, while `claude-opus-5` produced the full correct form including the `Urldate`/`Userd` revision-date pairing.

For a batch of reference works, run with `--model claude-opus-5`; the flag overrides `config.yaml` for that invocation only. It roughly doubles the cost — Opus prices at $5/$25 per 1M tokens against Sonnet's $3/$15, and its tokenizer makes the cached prefix about 27% larger — so it is worth reaching for on awkward sources rather than by default. `dev/estimate_cost.py --model claude-opus-5` prices it before you commit.
