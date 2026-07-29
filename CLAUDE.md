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
- Every new entry must include `date-added` and `date-modified` fields, both set to the current date, time, and timezone. Run `date "+%Y-%m-%d %H:%M:%S %z"` to get the value. Format: `date-added = {2026-03-22 14:30:00 +0200}`.
- Do not populate the following fields:
  - ISSN
  - ISBN
  - keywords
  - reference
  - devonthink
  - URL (in @article, @book, @collection, @incollection, @inbook types) - **exception:** for entries sourced from a `.webloc` file rather than a PDF, populate `Url` (the bookmarked page's address) and `Urldate` (the date accessed). The general rule above assumes a PDF is always filed as the entry's locator instead; when there is no PDF, `Url`/`Urldate` is the locator.
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
- Use the `Shorttitle` field if the full title is more than six words long and includes a colon or full stop. In those cases, the `Shorttitle` is the first part of the full title (i.e. up to the colon or full stop).
- Conference papers are considered `@Unpublished`. For those items, the `Note` field is used with the following content: `\autocap{p}aper presented at <Conference Name>`, for example: `\autocap{p}aper presented at the 9th Meeting of the Russian Society for Music Theory`.
- For more details on available fields and their use, see the reference sources below.

## Reference Documentation

- biblatex-chicago package: https://ctan.org/tex-archive/macros/latex/contrib/biblatex-contrib/biblatex-chicago.
- Types and fields reference: https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/biblatex-chicago.pdf.
- Examples: https://mirror.init7.net/ctan/macros/latex/contrib/biblatex-contrib/biblatex-chicago/doc/cms-notes-intro.pdf.

## Important Notes

- Use biblatex-chicago specific types and fields, not generic BibLaTeX.
- The Chicago style variant is "notes and bibliography", not "author-date".
