# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This repository contains academic publications - PDFs, and `.webloc` bookmarks to online-only publications with no PDF. The task is to extract bibliographic information from each source in the `./pdf-in` folder and store it in a BibLaTeX file using the **biblatex-chicago** standard (notes and bibliography variant, not author-date).

## Bibliographic Extraction Guidelines

- Look for PDFs and `.webloc` files in the `./pdf-in` folder.
- **Read only** the beginning (max of first page or ~450 words) and last ~150 words of each PDF for bibliographic data.
- For a `.webloc` file, fetch the page it bookmarks and extract from that instead - the same recognition/formatting rules apply, source text and metadata just come from the fetched webpage (main body text and `citation_*`/`og:*`/JSON-LD metadata) rather than from PDF pages and embedded file metadata.
- IMPORTANT: Use `biblio-template.bib` as reference for publication types and fields. Try to use the types and fields in this template.
- Select the appropriate entry type (@Book, @Article, etc.) and populate relevant fields.
- Two worked-example corpora carry the entry-type reasoning, and both are loaded into the extraction prompt automatically via `example_files` in `config.yaml`. Use them for **classification** — working out what kind of thing the source is and which of the ~40 types therefore fits — and not merely as a tie-breaker between two candidates you have already narrowed down to:
  - `cms-notes-intro-guide.md` — organised *by kind of source*, which is what makes it a classification aid. Its taxonomy sections (standard types; the less familiar types; online materials) map source kinds onto entry types; its "Online materials" section governs the access-mode question directly ("an online edition of a printed book still calls for a `@Book` entry"); and its `entrysubtype` section covers the cases where the type alone is insufficient (magazine, newspaper, classical, letter). It also carries a type-labelled index of worked entries, so `Online: [stenger:privacy]` shows at a glance what an `@Online` entry actually looks like. Bracketed names point at the corresponding entries in `notes-test.bib`.
  - `notes-test.bib` — the biblatex-chicago package's annotated test suite. Nearly every entry carries an `annote` stating what the source *is* and why that type and field set follow from it. Consult it both to recognise a type and to settle the harder boundaries: `@Inproceedings` vs. `@Incollection`, `@Proceedings` vs. `@Collection`, `@Thesis` vs. `@Report` vs. `@Unpublished`, `@Reference` vs. `@Inreference`, and `@Online` vs. a print-equivalent type.
- Classification precedes discrimination: identify the kind of source first, from the corpora's taxonomy, and only then resolve between whichever confusable types remain.
- Both corpora follow the biblatex-chicago package's own house style, which departs from this file's rules in several respects: they populate `url` on printed works, use `keywords` and `annote`, and use the legacy `school`/`address` aliases. Treat them as authoritative on *type and field semantics* only. **Where they conflict with this file, this file governs.** Never copy an example's bibliographic data into an entry; they illustrate form, not content.
- Every new entry must include `date-added` and `date-modified` fields, both set to the current date, time, and timezone. Run `date "+%Y-%m-%d %H:%M:%S %z"` to get the value. Format: `date-added = {2026-03-22 14:30:00 +0200}`.
- Do not populate the following fields:
  - ISSN
  - ISBN
  - keywords
  - reference
  - devonthink
  - URL - **exception:** populate `Url` and `Urldate` when the entry is typed `@Online` (the one type with no other locator to fall back on), or when the entry has no `Date` (in which case `Urldate` is the only dating evidence available and `Url` its necessary companion). This is a retention rule, not an instruction to invent an access date for a PDF that carries no URL of its own - and it applies by entry type, not by source: a `.webloc`-sourced page need not be typed `@Online` if the work it bookmarks is properly a book, article, etc. (see item 1 above), and conversely a PDF that turns out to be a printout of an online-only source is still typed `@Online` and keeps its `Url`/`Urldate`. If `Urldate` is populated with only an access date (no revision/last-modified date given by the source), Chicago prints "n.d." as the headline date - this is expected. If the source itself states a revision or last-modified date, put that date in `Urldate` instead and add `Userd = {last modified}` (or similar) so it prints as a qualified date rather than a bare access date.
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

In this repository (no network access needed; the last three are loaded into the extraction prompt):

- `biblatex-chicago-notes-ref.md` — condensed field and entry-type reference.
- `biblio-template.bib` — one example per entry type, in this project's own conventions. The authority for house style.
- `notes-test.bib` — the package's annotated test suite (203 entries). The authority for type discrimination; see the note above about its divergent house style.
- `cms-notes-intro-guide.md` — entry-type guide derived from `cms-notes-intro.tex` by `extract_intro.py`. Re-run that script to regenerate it after updating the upstream `.tex`.

Upstream (consult when the local files are insufficient):

- biblatex-chicago package: https://ctan.org/tex-archive/macros/latex/contrib/biblatex-contrib/biblatex-chicago.
- Types and fields reference (the full manual, far more detailed than the condensed reference above): https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/biblatex-chicago.pdf.
- Examples: https://mirror.init7.net/ctan/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/cms-notes-intro.pdf — the rendered form of the local `cms-notes-intro.tex`.

## Important Notes

- Use biblatex-chicago specific types and fields, not generic BibLaTeX.
- The Chicago style variant is "notes and bibliography", not "author-date".
