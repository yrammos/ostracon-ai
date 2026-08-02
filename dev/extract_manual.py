#!/usr/bin/env python3
r"""Extract §4.2 "Entry Fields" from biblatex-chicago.tex as extraction context.

Why extract rather than summarise. `biblatex-chicago-notes-ref.md` condensed
this section by hand, and a paraphrase loses the exception: it rendered
`afterword` as "Author of an afterword", where the manual says the field "has a
special meaning in the suppbook entry type… simply define afterword any way you
please". Believing the paraphrase destroyed a working entry. Field behaviour is
a pile of special cases and special cases are exactly what compression drops,
so this section is taken whole.

Scope is deliberately §4.2 alone:
  * §4.1 (Entry Types) is already covered three ways -- `notes-test.bib` gives
    32 types as worked examples with annotations, `cms-notes-intro-guide.md`
    gives the taxonomy by kind of source -- and types survive summarising in a
    way field behaviour does not.
  * §5 is the author-date variant and is irrelevant to this project.

Each field gets a `### name` heading so the file is navigable by field name,
and the `(See Manual 14.105; polakow:afterw.)` cross-references are kept: they
point at the CMS paragraph and at the worked example in notes-test.bib.

    python3 dev/extract_manual.py <biblatex-chicago.tex> <out.md>
"""

from __future__ import annotations

import re
import sys

START = r"\subsection{Entry Fields}"
STOP = r"\subsection{Commands}"


def drop_cmd(text, names, keep_arg=False):
    """Remove `\\cmd{...}` with brace matching, optionally keeping the argument."""
    pat = re.compile(r"\\(?:" + "|".join(names) + r")\*?\s*\{")
    while True:
        m = pat.search(text)
        if not m:
            return text
        i, depth = m.end(), 1
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        text = text[:m.start()] + (text[m.end():i - 1] if keep_arg else "") + text[i:]


def main():
    src = open(sys.argv[1], encoding="utf-8").read()
    body = src[src.index(START):src.index(STOP, src.index(START))]

    body = "\n".join(l for l in body.split("\n") if not l.lstrip().startswith("%"))

    # A field's name sits INSIDE the prose as \mymarginpar{\textbf{name}} --
    # "In most \mymarginpar{\textbf{afterword}} circumstances, this field..." --
    # so splitting the paragraph at the marker tears the sentence in half and
    # strands its opening words at the end of the previous field. Instead:
    # find the start of each field's discussion (\mybigspace, or \paragraph*
    # for a few), lift the name out to a heading, and delete the marker in
    # place so the sentence closes up and stays whole.
    body = re.sub(r"\\paragraph\*?\{\\protect\\mymarginpar\{\\textbf\{([^}]*)\}\}\}",
                  r"\\mybigspace \\mymarginpar{\\textbf{\1}} ", body)

    # Some marginpars name several fields at once, joined by \\ --
    # \mymarginpar{\textbf{eprint}\\\textbf{eprintclass}\\\textbf{eprinttype}} --
    # so match one *or more* \textbf groups and give each field its own heading.
    MARK = re.compile(r"\\mymarginpar\{((?:\\textbf\{[^{}]*\}(?:\\\\)?\s*)+)\}\s*")
    chunks = body.split(r"\mybigspace")
    rebuilt = [chunks[0]]
    for chunk in chunks[1:]:
        m = MARK.search(chunk)
        if not m:
            rebuilt.append("\n\n" + chunk)
            continue
        names = re.findall(r"\\textbf\{([^{}]*)\}", m.group(1))
        prose = (chunk[:m.start()] + chunk[m.end():]).strip()
        heads = "\n\n".join(f"### {n.strip()}" for n in names if n.strip())
        rebuilt.append(f"\n\n{heads}\n\n{prose}")
    body = "".join(rebuilt)

    body = re.sub(r"\\subsection\*?\{([^}]*)\}", r"\n\n## \1\n", body)

    # Cross-references to the manual's own numbered sections carry no meaning
    # once the surrounding document is gone; the CMS paragraph numbers and the
    # notes-test.bib keys in "(See Manual …)" are kept and do carry meaning.
    body = re.sub(r"\\(?:label|index|hypertarget|pageref)\{[^}]*\}", "", body)
    body = re.sub(r"\bsections?~?\s*\\ref\{[^}]*\}(,?\s*below)?", "elsewhere in this section", body)
    body = re.sub(r"\\ref\{[^}]*\}", "", body)
    body = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", body)

    body = drop_cmd(body, ["enquote"], keep_arg=True)
    for _ in range(6):
        body = re.sub(r"\\(?:textsf|texttt|emph|textbf|textsc|mkbibquote|mkbibemph)"
                      r"\{([^{}]*)\}", r"\1", body)
    body = re.sub(r"\\cmd\{([^}]*)\}", r"\\\1", body)
    body = re.sub(r"\\verb(.)(.*?)\1", r"`\2`", body)
    # Marginal notes that are NOT field names -- option names, "NB!" -- are
    # navigational furniture in a printed manual and their term always appears
    # in the adjacent prose, so they carry nothing here.
    body = drop_cmd(body, ["mymarginpar"])
    body = re.sub(r"\\setlength\{[^}]*\}\{[^}]*\}", "", body)

    body = re.sub(r"\\(?:begin|end)\{(?:description|enumerate)\}", "", body)
    body = re.sub(r"\\item\s*", "\n- ", body)
    body = drop_cmd(body, ["vspace", "enlargethispage", "mycolor"])
    body = re.sub(r"\\(?:mybigspace|mylittlespace|noindent|qquad|protect|"
                  r"baselineskip|par|hc|addspace)\b", " ", body)
    body = re.sub(r"\\TeX\b", "TeX", body)
    body = re.sub(r"\\LaTeX\b", "LaTeX", body)
    body = re.sub(r"\\&", "&", body)
    body = re.sub(r"\\,|\\(?=\s)", " ", body)
    body = re.sub(r"\\@?\.", ".", body)

    # Reflow: the source hard-wraps at ~70 columns, which leaves every rule
    # split across lines and so unfindable by a plain search.
    out = []
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block:
            continue
        if block.startswith("#"):
            out.append(block)
        elif block.startswith("- "):
            out.append("\n".join(re.sub(r"\s+", " ", b).strip()
                                 for b in block.split("\n- ") if b.strip()))
        else:
            out.append(re.sub(r"\s*\n\s*", " ", block))
    body = "\n\n".join(out)
    body = re.sub(r"[ \t]{2,}", " ", body)

    header = (
        "# biblatex-chicago (notes): Entry Fields\n\n"
        "Section 4.2 of `biblatex-chicago.tex`, extracted mechanically by\n"
        "`dev/extract_manual.py` — not summarised. This is the package author's\n"
        "own text with LaTeX markup removed and paragraphs reflowed; it is a\n"
        "tier-1 source and settles what a field does.\n\n"
        "Each field appears under a `### name` heading. Parenthetical references\n"
        "of the form `(See Manual 14.105; polakow:afterw.)` point to the numbered\n"
        "paragraph of the *Chicago Manual of Style* and to the worked example of\n"
        "that name in `notes-test.bib`.\n\n"
        "Scope is §4.2 only. Entry *types* are covered by `notes-test.bib` and\n"
        "`cms-notes-intro-guide.md`; §5 of the manual is the author-date variant\n"
        "and does not apply here.\n\n---\n"
    )
    open(sys.argv[2], "w", encoding="utf-8").write(header + body.strip() + "\n")
    fields = len(re.findall(r"^### ", body, re.M))
    print(f"wrote {sys.argv[2]}: {fields} field headings, "
          f"{len(body):,} chars (~{len(body)/3.39:,.0f} tokens)")


if __name__ == "__main__":
    sys.exit(main())
