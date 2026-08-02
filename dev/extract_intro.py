#!/usr/bin/env python3
"""Derive clean prose from cms-notes-intro.tex for use as extraction context.

Keeps the section structure and the entry keys (which point at examples in
notes-test.bib), drops LaTeX scaffolding, endnote plumbing, and the
\\printbibliography calls whose output comes from notes-test.bib's annote
fields anyway.

The input is not vendored here -- it ships with the package, and the generated
guide is what this project actually consumes. Upstream drift shows up as a diff
in that guide when the script is re-run, which is the artifact that matters:

    python3 dev/extract_intro.py \\
      /usr/local/texlive/2025/texmf-dist/doc/latex/biblatex-chicago/cms-notes-intro.tex \\
      prompt-context/cms-notes-intro-guide.md
"""
import re
import sys

src = open(sys.argv[1], encoding='utf-8').read()

# Body starts at the first real section.
src = src[src.index(r'\section{Standard entry types}'):]

# Discretionary hyphens (`Collec\-tion`, `biblatex-chi\-ca\-go`) are line-break
# hints, not content. Strip before anything else so the words read whole.
src = src.replace('\\-', '')

# Drop full-line comments.
src = '\n'.join(l for l in src.split('\n') if not l.lstrip().startswith('%'))

# Drop the trailing "The Database File" appendix: ~60 lstlisting blocks that
# reproduce notes-test.bib entries verbatim, minus their annotations. Keeping
# them would duplicate a file already supplied in full, and the listings' URLs
# are broken mid-string by the original's column width (e.g.
# "http://www.cnn.com/1999/TECH/ ptech/12/20/implant.device/"), which is
# actively misleading as example data. The prose sections' own
# \endnote[\value{Type}]{\cite{key}} type-to-example mappings are untouched.
src = re.sub(r'\\begin\{lstlisting\}(\[[^\]]*\])?.*?\\end\{lstlisting\}', '',
             src, flags=re.DOTALL)
src = re.sub(r'\\twocolumn\[[^\]]*\]', '', src)

# \endnote[\value{Type}]{\cite{key}.} and \lnbackref{Type}{key} both encode a
# TYPE -> worked-example mapping. That is the most useful thing in the file,
# so render it rather than stripping it.
#
# Render INLINE. These appear mid-sentence as often as in lists -- "An online
# edition of a printed book still calls for a \endnote[\value{Book}]{...} entry"
# -- so emitting a newline and a bullet tore sentences in half and left the
# governing rules unreadable. Inline, the list contexts still read correctly,
# because the source already separates them with commas.
#
# The inner command is NOT always \cite. The "Short notes" section uses
# \shortcite throughout, and matching only \cite here cost 24 worked-example
# keys: the rule below fell through to the generic \endnote strip at line ~57,
# whose non-greedy `\{.*?\}` stops at the FIRST closing brace -- the one closing
# \shortcite{garaud:gatine} -- and left the outer `}` stranded in the prose. The
# section rendered as "the short forms ... : }, }, }, }, ..." for 22 works.
# The optional argument matters: `\shortcite[Aristotle]{wikiped:bibtex}` names a
# sub-entry, and without `(?:\[..\])*` here that one InReference example was the
# last `}` left stranded in the Short-notes list.
CITECMD = r'(?:short|auto|full|headless|surname|journal)?cite\*?(?:\[[^\]]*\])*'
src = re.sub(r'\\endnote\[\\value\{([A-Za-z]+)\}\]\{\\' + CITECMD +
             r'\{([^}]*)\}\.?\}', r'@\1 [\2]', src)
# Same shape without the \value{} type label: still a worked example, so keep
# the key even though the type has to be read from the surrounding sentence.
src = re.sub(r'\\endnote\{\\' + CITECMD + r'\{([^}]*)\}\.?\}', r'[\1]', src)
src = re.sub(r'\\lnbackref\{([A-Za-z]+)\}\{([^}]*)\}', r'\1: [\2]', src)

# Remaining plumbing has no informational content.
src = re.sub(r'\\theendnotes', '', src)
src = re.sub(r'\\printbibliography(\[[^\]]*\])?', '', src)
src = re.sub(r'\{?\\renewcommand\{[^}]*\}\{[^}]*\}[^}]*\}?', '', src)
src = re.sub(r'\\(?:hyperlink|getrefbykeydefault|colorbox|color)\{[^}]*\}(\{[^}]*\})?', '', src)
src = re.sub(r'\\endnote(\[[^\]]*\])?\{.*?\}\.?,?', '', src, flags=re.DOTALL)

# Section headings -> markdown.
def _headings(text):
    r'''`\section{...}` -> `## ...`, matching the brace rather than the first `}`.

    `\section{The \texttt{entrysubtype} field}` contains a nested group, so a
    non-greedy `\{(.*?)\}` captured only "The \texttt{entrysubtype" and spilled
    "field}" into the prose as a stray line.
    '''
    out, i = [], 0
    while True:
        m = re.compile(r'\\section\{').search(text, i)
        if not m:
            out.append(text[i:])
            return ''.join(out)
        j, depth = m.end(), 1
        while j < len(text) and depth:
            if text[j] == '{': depth += 1
            elif text[j] == '}': depth -= 1
            j += 1
        inner = re.sub(r'\\[a-zA-Z]+\{([^{}]*)\}', r'\1', text[m.end():j - 1])
        out.append(text[i:m.start()] + '\n\n## ' + inner.strip() + '\n')
        i = j

src = _headings(src)

# Citation keys are worth keeping: they name examples in notes-test.bib.
src = re.sub(r'\\(?:auto)?cite\*?(?:\[[^\]]*\])*\{([^}]*)\}', r'[\1]', src)
src = re.sub(r'\\cmslink\{([^}]*)\}', r'[\1]', src)
src = re.sub(r'\\href\{[^}]*\}\{([^}]*)\}', r'\1', src)

# Cross-references into biblatex-chicago.pdf: keep the field/topic name only.
src = re.sub(r'\\cmssecref\[([^\]]*)\]\{[^}]*\}', r'', src)
src = re.sub(r'\\cms(?:sec|tab)ref\{[^}]*\}', '', src)

# Inline formatting -> plain text.
for _ in range(4):
    src = re.sub(r'\\(?:textsf|emph|texttt|textbf|mkbibquote|mkbibemph)\{([^{}]*)\}',
                 r'\1', src)

# Environments and layout noise.
src = re.sub(r'\\(?:begin|end)\{[^}]*\}(\[[^\]]*\])?', '', src)
# `\pageref`/`\ref` point at page numbers in the rendered PDF, which mean
# nothing in the extracted guide; the sentences read correctly without them.
src = re.sub(r'\\(?:label|index|hypertarget|phantomsection|pageref|ref)\{[^}]*\}', '', src)
src = re.sub(r'\\(?:mylittlespace|noindent|newpage|clearpage|bigskip|medskip|smallskip|par)\b', '', src)
src = re.sub(r'\\cmd\{([^}]*)\}', r'\\\1', src)

# Late-stage macro cleanup. Brace-matched removal handles nesting.
def drop_cmd(text, names, keep_arg=False):
    pat = re.compile(r'\\(?:' + '|'.join(names) + r')\*?\{')
    while True:
        m = pat.search(text)
        if not m: return text
        i, depth = m.end(), 1
        while i < len(text) and depth:
            if text[i] == '{': depth += 1
            elif text[i] == '}': depth -= 1
            i += 1
        text = text[:m.start()] + (text[m.end():i-1] if keep_arg else '') + text[i:]

src = re.sub(r'\\footnote\[[^\]]*\]', r'\\footnote', src)   # \footnote[1]{..} -> \footnote{..}
src = drop_cmd(src, ['footnote', 'vspace', 'twocolumn', 'enlargethispage'])
src = drop_cmd(src, ['headlesscite', 'fullcite', 'citejournal', 'textcite',
                     'surnamecite', 'shortcite', 'textsc'], keep_arg=True)
import unicodedata
src = re.sub(r"\\c\{([A-Za-z])\}",
             lambda m: unicodedata.normalize('NFC', m.group(1) + '\u0327'), src)

# Late-stage macro cleanup.
src = re.sub(r'\\(?:footnote|enlargethispage|baselineskip|setlength)\{[^{}]*\}', '', src)
src = re.sub(r'\\(?:textsc|textcite|surnamecite|shortcite)\{([^{}]*)\}', r'\1', src)
src = src.replace(r'\TeX', 'TeX').replace(r'\LaTeX', 'LaTeX')

# Remaining bare macros with no argument we care about.
src = re.sub(r'\\[a-zA-Z@]+\*?(\[[^\]]*\])?\{\}', '', src)
src = re.sub(r'\\(?:autocap|bibstring)\{([^}]*)\}', r'\1', src)

# Escapes and ligatures.
src = (src.replace(r'\&', '&').replace(r'\%', '%').replace(r'\_', '_')
          .replace(r'\#', '#').replace('~', ' ').replace('---', '—').replace('--', '-'))
src = re.sub(r'\\ (?=\w)', ' ', src)
src = re.sub(r'\\ ', ' ', src)
src = re.sub(r'^\s*%\s*$', '', src, flags=re.M)
src = re.sub(r'\\\\', '\n', src)

# Whitespace normalisation: collapse runs, keep paragraph breaks.
src = re.sub(r'[ \t]+', ' ', src)
src = re.sub(r' *\n *', '\n', src)
src = re.sub(r'\n{3,}', '\n\n', src)
src = re.sub(r' ([,.;:)])', r'\1', src)

header = (
    "# The Chicago Notes & Bibliography Specification: entry-type guide\n\n"
    "DERIVED WORK. Generated by `extract_intro.py` from `cms-notes-intro.tex`\n"
    "in the biblatex-chicago package (v2.3b), Copyright (c) 2008-2024 David\n"
    "Fussner, licensed under the LaTeX Project Public License (LPPL) v1.3.\n"
    "LaTeX scaffolding has been stripped and the type/example cross-references\n"
    "rendered as plain text; no wording was otherwise changed.\n\n"
    "Explains which entry TYPE to choose for a given kind of source. Names in\n"
    "[brackets] are entry keys of worked examples in `notes-test.bib`.\n"
    "NB this reflects the package's own house style, which departs from this\n"
    "project's conventions in places; CLAUDE.md takes precedence.\n"
)
# Reflow. The .tex hard-wraps at ~70 columns, which is a typesetting artifact,
# not content: preserved verbatim it leaves every rule split across lines and so
# unfindable by a plain search. Join lines within a paragraph, keeping blank
# lines as paragraph boundaries and leaving headings alone.
def reflow(text):
    out = []
    for block in re.split(r'\n\s*\n', text):
        block = block.strip()
        if not block:
            continue
        if block.startswith('#'):
            out.append(block)
        else:
            out.append(re.sub(r'\s*\n\s*', ' ', block))
    return '\n\n'.join(out)

src = reflow(src)

open(sys.argv[2], 'w', encoding='utf-8').write(header + src.strip() + '\n')
print(f"wrote {sys.argv[2]}")
