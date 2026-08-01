# biblatex-chicago Notes & Bibliography: Entry Types and Fields

Condensed reference for the notes-and-bibliography variant. Source: biblatex-chicago package documentation (§§ 4.1–4.2).

---

## Entry Types

### Monographs and Books

**@Book** — A single-volume monograph. Key fields: `author`, `title`, `publisher`, `location`, `date`, `edition`, `volume`, `series`, `translator`, `editor`.

**@MvBook** — Multi-volume book as a whole. Same fields as @Book plus `volumes`. Individual volumes cited via @Book with a `crossref` or using the `maintitle` + `relatedtype=maintitle` mechanism.

**@Bookinbook** — A work originally published independently but reprinted as part of a book. Treated like @Inbook but the nested work is more self-contained. Uses `bookauthor` for the author of the host volume.

**@SuppBook** — Supplemental material in a book (preface, index, etc.). Use `author` for the supplement author, `bookauthor` for the main book author.

**@Inbook** — A chapter or self-contained part of a book, including titled forewords, introductions, etc. Key fields: `author`, `title`, `booktitle`, `bookauthor`, `editor`, `pages`, `publisher`, `location`, `date`.

**@Collection** — An edited collection with no single author; the editor(s) take the primary name role. Key fields: `editor`, `title`, `publisher`, `location`, `date`.

**@MvCollection** — Multi-volume edited collection. See @MvBook.

**@SuppCollection** — Supplemental material in a collection.

**@Incollection** — A contribution (essay, chapter) in an edited collection. Key fields: `author`, `title`, `editor`, `booktitle`, `pages`, `publisher`, `location`, `date`.

**@Reference** — A reference work (encyclopedia, dictionary, handbook). Similar to @Collection.

**@MvReference** — Multi-volume reference work.

**@Inreference** — An entry or article in a reference work. Similar to @Incollection.

**@Booklet** — A small printed work without a clear publisher or sponsoring institution (pamphlets, brochures). Use `howpublished` if needed.

**@Manual** — Technical or other documentation, not necessarily printed. Key fields: `author`/`editor`, `title`, `organization`, `location`, `date`, `type`, `version`.

---

### Serials and Periodicals

**@Article** — An article in a journal, magazine, or newspaper. Key fields: `author`, `title`, `journaltitle`, `volume`, `number`, `date`, `pages`, `doi`. For newspaper articles: `journaltitle` = newspaper name, use `entrysubtype = {newspaper}` or `entrysubtype = {magazine}` as appropriate.

**@Periodical** — An entire issue of a periodical. `editor` takes the primary role. Key fields: `editor`, `journaltitle`, `issuetitle`, `volume`, `number`, `date`.

**@SuppPeriodical** — Supplemental material in a periodical.

**@Review** — A review published in a journal or periodical. Chicago-specific. Uses `relatedtype = reviewof` to link to the work reviewed. Key fields: `author`, `title` (if the review has one), `journaltitle`, `volume`, `number`, `date`, `pages`.

---

### Proceedings

**@Proceedings** — A complete conference proceedings volume. Key fields: `editor`, `title`, `publisher`, `location`, `date`, `series`, `number`.

**@MvProceedings** — Multi-volume proceedings.

**@Inproceedings** — A paper in a conference proceedings volume. Key fields: `author`, `title`, `editor`, `booktitle`, `eventtitle`, `pages`, `publisher`, `location`, `date`.

---

### Unpublished and Reports

**@Unpublished** — Unpublished work, including conference papers and manuscripts not in an archive. For conference papers, use: `note = {\autocap{p}aper presented at <Conference Name>}`. Key fields: `author`, `title`, `date`, `note`.

**@Report** — A technical or research report. Key fields: `author`, `title`, `type`, `institution`, `location`, `date`, `number`.

**@Thesis** — A thesis or dissertation. Use `type` field: `type = {PhD dissertation}` or `type = {MA thesis}`. Key fields: `author`, `title`, `type`, `institution`, `location`, `date`.

---

### Archival and Special Materials

**@CustomC** — Chicago-specific type for archival/manuscript material (unpublished, held in a collection). Key fields: `author`, `title`, `date`, `lista` (archive name), `listb` (archive location), `note`.

**@Letter** — Correspondence (personal letters, emails, etc.). Key fields: `author`, `title` (description of letter), `date`, `note`. For published correspondence, use `howpublished`.

**@Misc** — Fallback for material that does not fit other types. Key fields: `author`, `title`, `date`, `howpublished`, `note`. Use `entrysubtype` to qualify.

---

### Online and Audiovisual

**@Online** — A website, blog, or other online resource with no print counterpart. Key fields: `author`, `title`, `date`, `url`, `urldate`, `organization`.

**@Audio** — An audio recording (non-musical, e.g. podcasts, radio programmes, spoken word). Key fields: `author`/`editor`, `title`, `publisher`/`organization`, `date`, `type`.

**@Music** — A musical recording. Key fields: `author` (composer), `namea` (performer), `title`, `publisher`, `date`, `type` (e.g. `{CD}`). The `pubstate = {reprint}` mechanism handles reissues.

**@Video** — A film, DVD, or online video. Key fields: `author`/`editor`, `title`, `publisher`, `date`, `type`. For TV episodes, the episode is `title` and the series name is `booktitle`; biblatex-chicago may prefer `booktitle` before `title` in that context.

**@Image** — A still image (photograph, map, etc.). Key fields: `author`, `title`, `date`, `type`, `location` (collection), `publisher`.

**@Artwork** — A work of visual art. Key fields: `author`, `title`, `date`, `type` (medium), `location` (museum/collection).

**@Performance** — A live performance. Key fields: `author`, `title`, `date`, `venue`, `location`, `note`.

---

### Legal

**@Jurisdiction** — Court decisions and rulings.

**@Legislation** — Laws, statutes, regulations.

**@Legal** — Other legal documents (treaties, etc.).

**@Patent** — A patent. Key fields: `author`, `title`, `holder`, `number`, `date`, `location` (country), `type`.

**@Standard** — A technical standard. Key fields: `author`/`organization`, `title`, `number`, `institution`, `date`.

---

## Key Fields Reference

### Names

| Field | Description |
|---|---|
| `author` | Primary author(s). Format: `Last, First~I.` |
| `editor` | Editor(s). Roles via `editortype` (editor, compiler, translator, redactor). |
| `editora`, `editorb`, `editorc` | Additional named roles; set role with `editoratype` etc. |
| `bookauthor` | Author of the host book in @Inbook / @Bookinbook. |
| `translator` | Translator(s). |
| `namea`, `nameb`, `namec` | Supplemental name fields; role set via `nameatype` etc. |
| `annotator` | Author of annotations. |
| `commentator` | Author of a commentary. |
| `foreword` | Author of a foreword. |
| `introduction` | Author of an introduction. |
| `afterword` | Author of an afterword. |
| `holder` | Patent holder. |
| `shortauthor` | Abbreviated author name for short citations. |
| `shorteditor` | Abbreviated editor name for short citations. |
| `nameaddon` | Addition to the name field (e.g. "Jr.", institutional affiliation). |

All proper-name fields use `Last, First~I.` format. Separate multiple names with `and`. Use `and others` to truncate.

### Titles

| Field | Description |
|---|---|
| `title` | Main title of the work. |
| `subtitle` | Subtitle of the work. |
| `titleaddon` | Appendix to the title (e.g. original-language title). |
| `shorttitle` | Short title for citations (use when full title > 6 words with a colon or full stop; value = first part of title up to that mark). |
| `shorthand` | Abbreviated citation label (replaces short title in notes). |
| `booktitle` | Title of the host book. |
| `booksubtitle` | Subtitle of the host book. |
| `booktitleaddon` | Appendix to the booktitle. |
| `maintitle` | Title of the multi-volume work. |
| `mainsubtitle` | Subtitle of the multi-volume work. |
| `maintitleaddon` | Appendix to the maintitle. |
| `journaltitle` | Title of the journal / periodical. |
| `journalsubtitle` | Subtitle of the journal. |
| `journaltitleaddon` | Appendix to journaltitle (e.g. original-language name). |
| `issuetitle` | Title of a specific journal issue. |
| `issuesubtitle` | Subtitle of a journal issue. |
| `eventtitle` | Name of the conference / event. |
| `eventtitleaddon` | Appendix to eventtitle. |
| `reprinttitle` | Title of the source in which a work is reprinted. |

**Title case rules:**
- English titles: Chicago title case (all major words capitalised).
- Russian, French: title case (first word + proper nouns).
- German: German title case (nouns capitalised, adjectives lowercased).
- Do not use all caps (except a single standalone word already all-caps in the source).
- For quoted sub-phrases within a title, use `\mkbibquote{...}` instead of quotation marks.
- For foreign-language text, use `\foreignlanguage{french}{...}` (or the appropriate language name).

### Publication Data

| Field | Description |
|---|---|
| `date` | Full date (preferred format: `YYYY`, `YYYY-MM`, `YYYY-MM-DD`; use `YYYY/YYYY` for ranges). |
| `year` | Year only (use `date` whenever possible). |
| `month` | Month (use with `year` if full `date` unavailable). |
| `origdate` | Date of the original publication (for reprints, translations). |
| `publisher` | Publisher name. |
| `location` | Place of publication (city). Multiple locations: `{City1 \and City2}`. |
| `origpublisher` | Publisher of the original edition. |
| `origlocation` | Place of publication of the original edition. |
| `edition` | Edition (use numerals: `2` for "2nd ed."). |
| `pubstate` | Publication state: `forthcoming`, `inpreparation`, `inpress`, `submitted`, `reprint`, `selfpublished`. |
| `howpublished` | Publication method for @Misc, @Manual, @Booklet. |
| `institution` | Issuing institution for @Report, @Thesis. |
| `organization` | Sponsoring organisation (for @Manual, @Online, etc.). |
| `type` | Type of report/thesis/patent/performance medium (e.g. `{PhD dissertation}`, `{technical report}`). |

### Volume, Number, and Pages

| Field | Description |
|---|---|
| `volume` | Volume number. |
| `volumes` | Total number of volumes. |
| `number` | Issue number (journals) or number in a series. |
| `series` | Series name. |
| `shortseries` | Abbreviated series name. |
| `part` | Part of a volume. |
| `chapter` | Chapter number. |
| `pages` | Page range (use single hyphen: `123-145`). |
| `pagetotal` | Total pages. |
| `eid` | Electronic article identifier (replaces `pages` for online-only articles). |
| `issue` | Non-numeric issue designation (e.g. `{Spring}`). |
| `version` | Version number for software, standards. |

### Identifiers and Access

| Field | Description |
|---|---|
| `doi` | Digital Object Identifier (without the `https://doi.org/` prefix). |
| `url` | URL. Package-legal on any entry type, but per project guidelines populate only when the entry is `@Online` or has no `date` (see note below). |
| `urldate` | Date the URL was last accessed (format: `YYYY-MM-DD`). An access-date-only `urldate` (no `userd`) does not stand in for `date` in citations - Chicago still prints "n.d." there; `urldate` is recorded as separate access evidence, not a substitute headline date. This is standard, documented behavior for undated online material, not a workaround. |
| `eprint` | Eprint identifier (e.g. arXiv ID). |
| `eprinttype` | Eprint archive type (e.g. `arxiv`). |
| `eprintclass` | Subject class within the eprint archive. |
| `isbn` | ISBN. Omit per project guidelines. |
| `issn` | ISSN. Omit per project guidelines. |

### Notes and Annotations

| Field | Description |
|---|---|
| `note` | Miscellaneous note, printed in the reference. For conference papers (@Unpublished): `\autocap{p}aper presented at <Name>`. |
| `addendum` | Additional text printed at the very end of the bibliography entry only. |
| `annotation` | Annotation for annotated bibliography; not printed by default. |

### Language and Cross-references

| Field | Description |
|---|---|
| `language` | Language of the work (e.g. `french`, `russian`, `german`). Affects formatting. |
| `origlanguage` | Original language of a translation. Prints "Originally published in [language]" phrase. |
| `crossref` | Cite key of parent entry; child inherits fields from parent. |
| `xref` | Cite key of related entry; no field inheritance. |
| `related` | Cite key(s) for related entries (translations, reprints). |
| `relatedtype` | Relationship type: `bytranslator`, `reprintfrom`, `origpubas`, `reviewof`, `maintitle`, etc. |
| `relatedstring` | Custom string linking main entry to its related entry. |
| `userf` | Cross-reference to the original work of a translation (old mechanism; prefer `related` + `relatedtype = bytranslator`). |
| `entrysubtype` | Entry subtype (e.g. `newspaper`, `magazine` in @Article; `classical` for ancient sources). |
| `options` | Per-entry package options (e.g. `skipbib`, `dataonly`, `usetranslator=false`). |

### Sorting Helpers

| Field | Description |
|---|---|
| `sortkey` | Manual sort key (overrides automatic sorting). |
| `sortname` | Name used for sorting only. |
| `sorttitle` | Title used for sorting only. |
| `sortyear` | Year used for sorting only. |

---

## Fields to Omit (per project guidelines)

Do **not** populate: `isbn`, `issn`, `keywords`. `url`/`urldate` are populated only for `@Online` entries or entries with no `date`; omit otherwise, regardless of entry type.

## Choosing @Online vs. a Print-Equivalent Type

Per the package doc's Table 1 ("Online materials and notes & bibliography entry types"): for an online edition of traditionally published matter, **use the same entry type you would choose from a printed source** (`@Book`, `@Article`, etc.), not `@Online` - `@Online` is reserved for material that "does not have (and never had) a printed counterpart." A `.webloc` bookmark to, say, a publisher's book landing page is still a `@Book`; only genuinely online-native material (a blog post, a social-media post, a website with no print analogue) is `@Online`. Entry type follows the nature of the work, not how it was sourced (PDF vs. `.webloc`).

---

## Common Patterns

### Conference paper
```bibtex
@Unpublished{key,
  author   = {Last, First},
  title    = {Title of Paper},
  date     = {2023},
  note     = {\autocap{p}aper presented at the Annual Conference of X},
}
```

### Article in a journal
```bibtex
@Article{key,
  author       = {Last, First~A.},
  title        = {Article Title},
  journaltitle = {Journal Title},
  volume       = {12},
  number       = {3},
  date         = {2022},
  pages        = {45-67},
  doi          = {10.xxxx/xxxxx},
}
```

### Chapter in edited collection
```bibtex
@Incollection{key,
  author    = {Last, First},
  title     = {Chapter Title},
  editor    = {EdLast, EdFirst},
  booktitle = {Collection Title},
  pages     = {100-120},
  publisher = {Publisher Name},
  location  = {City},
  date      = {2020},
}
```

### Book with `shorttitle`
```bibtex
@Book{key,
  author     = {Last, First},
  title      = {Long Title: A Subtitle That Makes It Long},
  shorttitle = {Long Title},
  publisher  = {Publisher Name},
  location   = {City},
  date       = {2019},
}
```

### Translation with original language
```bibtex
@Book{key,
  author       = {Last, First},
  title        = {English Title},
  translator   = {TrLast, TrFirst},
  publisher    = {Publisher},
  location     = {City},
  date         = {2015},
  origlanguage = {russian},
  origdate     = {1985},
}
```

### Archival document
```bibtex
@CustomC{key,
  author = {Last, First},
  title  = {Title or Description of Document},
  date   = {1923},
  lista  = {Archive Name},
  listb  = {Archive Location},
  note   = {Folder or box information},
}
```
