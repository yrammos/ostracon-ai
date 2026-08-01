#!/usr/bin/env python3
"""
Extract bibliographic text from PDF files.
Extracts first N and last M words, with OCR fallback for scanned documents.
"""

import os
import sys
import subprocess
import shutil
import tempfile
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from pypdf import PdfReader, PdfWriter


@dataclass
class SourceContent:
    """Normalized shape produced by every source extractor (PDF, webloc, ...).

    This is the entire interface the rest of the pipeline (prompt building,
    enrichment, verification, saving) needs - it never inspects which kind of
    source produced it.
    """
    text: str
    metadata: dict = field(default_factory=dict)
    label: str = "PDF"          # human-readable source kind, used once in build_prompt
    url: Optional[str] = None   # set only when the source has a canonical access URL


def extract_pdf_metadata(pdf_path):
    """
    Extract embedded PDF file metadata (Title, Author, Subject, CreationDate)
    when present.

    This is a first-party signal from the file itself, distinct from the
    document's body text - and it's sometimes the ONLY textual clue to
    authorship available (e.g. a chapter-only excerpt with no title/copyright
    page), so it's worth surfacing even when it can't be fully trusted.

    Returns:
        dict of {label: value} for whichever fields are present and non-empty.
    """
    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return {}

    meta = reader.metadata
    if not meta:
        return {}

    fields = {}
    for key, label in [('/Title', 'Title'), ('/Author', 'Author'),
                        ('/Subject', 'Subject'), ('/CreationDate', 'CreationDate')]:
        try:
            value = meta.get(key)
            value = value.get_object() if hasattr(value, 'get_object') else value
        except Exception:
            value = None
        if value:
            fields[label] = str(value)
    return fields


def _key_page_numbers(num_pages):
    """1-indexed page numbers most likely to carry bibliographic metadata:
    first 3 and last 2 pages (or all pages for short documents)."""
    if num_pages <= 5:
        return list(range(1, num_pages + 1))
    return [1, 2, 3, num_pages - 1, num_pages]


def run_ocr(pdf_path, page_numbers, language="eng", timeout=180, force=False):
    """
    OCR only the given pages by extracting them into a small temp PDF and
    running ocrmypdf on that temp file, instead of in-place on the whole
    source document. ocrmypdf re-reads/re-writes the entire PDF stream even
    when --pages limits which pages get OCR'd, so on a large scan that's
    where a 2-minute timeout gets blown despite only needing ~5 pages of text.

    Note: the source PDF is left untouched, so these pages will NOT be
    searchable in the archived/BibDesk-filed copy.

    Args:
        pdf_path: Path to source PDF
        page_numbers: 1-indexed page numbers to OCR
        language: Tesseract language code (e.g., "eng", "rus", "deu")
        timeout: Seconds before giving up
        force: Use --force-ocr instead of --skip-text, rasterizing each page
            and OCR'ing it whatever it already contains. Only for the retry in
            extract_content(): --skip-text treats a page as done if it carries
            *any* text object, so a scan whose text layer holds nothing but
            whitespace is skipped and ocrmypdf exits 0 having produced nothing.
            Forcing recovers those, at the cost of discarding a genuine text
            layer where one exists - hence a fallback, never the default.

    Returns:
        dict mapping page_number -> extracted text, or an error string on failure.
    """
    ocrmypdf_bin = shutil.which("ocrmypdf") or next(
        (p for p in ["/opt/homebrew/bin/ocrmypdf", "/usr/local/bin/ocrmypdf"] if Path(p).exists()),
        None
    )
    if not ocrmypdf_bin:
        return "Error: ocrmypdf not installed. Install with: brew install ocrmypdf"

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    for n in page_numbers:
        writer.add_page(reader.pages[n - 1])

    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".pdf")
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)

    def _read_subset_text():
        subset_reader = PdfReader(str(tmp_path))
        return {
            n: (subset_reader.pages[i].extract_text() or "")
            for i, n in enumerate(page_numbers)
        }

    try:
        with open(tmp_path, "wb") as f:
            writer.write(f)

        cmd = [ocrmypdf_bin, "--force-ocr" if force else "--skip-text",
               "--optimize", "0", "-l", language or "eng",
               str(tmp_path), str(tmp_path)]

        env = os.environ.copy()
        for brew_bin in ["/opt/homebrew/bin", "/usr/local/bin"]:
            if brew_bin not in env.get("PATH", ""):
                env["PATH"] = brew_bin + ":" + env.get("PATH", "")

        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        return _read_subset_text()
    except subprocess.TimeoutExpired:
        return f"OCR Error: Process timed out after {timeout} seconds"
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        if "already has text" in stderr.lower():
            return _read_subset_text()
        return f"OCR Error: {stderr}"
    finally:
        tmp_path.unlink(missing_ok=True)


def extract_all_text(pdf_path):
    """
    Extract text from all pages of a PDF.

    Returns:
        tuple: (list of page texts, num_pages) or (error_string, 0)
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        return f"Error: File not found: {pdf_path}", 0

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        return f"Error: Could not read PDF: {e}", 0

    num_pages = len(reader.pages)

    if num_pages == 0:
        return "Error: PDF has no pages", 0

    page_texts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        page_texts.append(text)

    return page_texts, num_pages


def split_into_words(text):
    """Split text into words."""
    return [w for w in text.split() if w]


def snap_to_sentence_end(text, target_word_count, from_end=False):
    """
    Extract approximately target_word_count words, snapping to sentence boundary.

    Args:
        text: Full text
        target_word_count: Approximate number of words to extract
        from_end: If True, extract from end of text

    Returns:
        str: Extracted text snapped to sentence boundary
    """
    words = split_into_words(text)

    if len(words) <= target_word_count:
        return text.strip()

    if from_end:
        subset_words = words[-target_word_count:]
        subset_text = " ".join(subset_words)

        # Find first sentence boundary and start from there
        match = re.search(r'[.!?]\s+[A-Z]', subset_text)
        if match:
            return subset_text[match.end()-1:].strip()
        return subset_text
    else:
        subset_words = words[:target_word_count]
        subset_text = " ".join(subset_words)

        # Find last sentence boundary
        matches = list(re.finditer(r'[.!?](?:\s|$)', subset_text))
        if matches:
            last_match = matches[-1]
            return subset_text[:last_match.end()].strip()
        return subset_text


def extract_headers_footers(page_texts, num_pages, lines_per_edge=3):
    """
    Capture the first and last few lines of each key page (first 3 + last 2).

    Volume/issue numbers, page ranges, and chapter numbers often live in
    running headers/footers that fall outside the beginning/end word-count
    window used for the main extraction (which only looks at page 1 and the
    last page's tail) - this makes them visible regardless of which page
    they land on.
    """
    sections = []
    for n in _key_page_numbers(num_pages):
        text = page_texts[n - 1] if 0 <= n - 1 < len(page_texts) else ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        head = lines[:lines_per_edge]
        tail = lines[-lines_per_edge:] if len(lines) > lines_per_edge else []
        snippet = "\n".join(head + (["..."] + tail if tail else []))
        sections.append(f"[Page {n}]\n{snippet}")
    return "\n\n".join(sections)


def extract_content(pdf_path, min_first_words=450, last_words=150, min_words_threshold=100,
                     quiet=False, language_prompt_fn=None, ocr_timeout=180):
    """
    Extract beginning and end of PDF for bibliographic extraction.

    Beginning: max(first page, min_first_words) - ensures all page 1 metadata is captured.
    End: last_words from the end.

    Args:
        pdf_path: Path to PDF file
        min_first_words: Minimum words from beginning (takes more if page 1 is longer)
        last_words: Number of words to extract from end
        min_words_threshold: If total words below this, attempt OCR
        quiet: Suppress status messages
        language_prompt_fn: Optional callable () -> str that returns a tesseract language
            code (e.g. "eng", "rus"). Called interactively when OCR is needed.
        ocr_timeout: Seconds to allow ocrmypdf before giving up

    Returns:
        str: Extracted text with section markers
    """
    pdf_path = Path(pdf_path)

    # First pass: extract all text
    result, num_pages = extract_all_text(pdf_path)

    if isinstance(result, str) and result.startswith("Error:"):
        return result

    page_texts = result
    full_text = "\n\n".join(page_texts)
    words = split_into_words(full_text)
    total_words = len(words)

    # Check if OCR is needed
    if total_words < min_words_threshold:
        if not quiet:
            print(f"⚠️  Only {total_words} words extracted. Attempting OCR on key pages...", file=sys.stderr)

        # Determine OCR language
        language = "eng"
        if language_prompt_fn is not None:
            language = language_prompt_fn() or "eng"

        # OCR first 3 + last 2 pages (or all if short doc)
        ocr_pages = _key_page_numbers(num_pages)
        ocr_result = run_ocr(pdf_path, ocr_pages, language=language, timeout=ocr_timeout)

        if isinstance(ocr_result, dict):
            for n, text in ocr_result.items():
                page_texts[n - 1] = text
            full_text = "\n\n".join(page_texts)
            words = split_into_words(full_text)
            total_words = len(words)

            # --skip-text considers a page done if it carries any text object
            # at all, so a scan whose text layer is nothing but whitespace is
            # skipped and ocrmypdf still exits 0. That reads as success while
            # yielding no words, and the entry then gets built from the PDF's
            # embedded metadata alone - typically just a creation date. Retry
            # forced before giving up.
            if total_words < min_words_threshold:
                if not quiet:
                    print(f"⚠️  OCR returned {total_words} words - the text layer is "
                          "likely blank rather than absent. Retrying with --force-ocr...",
                          file=sys.stderr)
                forced = run_ocr(pdf_path, ocr_pages, language=language,
                                 timeout=ocr_timeout, force=True)
                if isinstance(forced, dict):
                    forced_texts = list(page_texts)
                    for n, text in forced.items():
                        forced_texts[n - 1] = text
                    forced_full = "\n\n".join(forced_texts)
                    forced_words = split_into_words(forced_full)
                    # Keep the forced pass only if it actually did better;
                    # rasterizing can also lose text on a page that had some.
                    if len(forced_words) > total_words:
                        page_texts, full_text = forced_texts, forced_full
                        words, total_words = forced_words, len(forced_words)
                elif not quiet:
                    print(f"⚠️  Forced OCR failed: {forced}", file=sys.stderr)

            if not quiet:
                print(f"✓ OCR successful ({total_words} words)", file=sys.stderr)
        else:
            return f"Error: {ocr_result}"

    # Determine beginning section: max(first page, min_first_words)
    first_page_text = page_texts[0] if page_texts else ""
    first_page_word_count = len(split_into_words(first_page_text))

    if first_page_word_count >= min_first_words:
        # First page is long enough, use it entirely
        first_section = first_page_text.strip()
    else:
        # First page is short, take min_first_words (snap to sentence)
        first_section = snap_to_sentence_end(full_text, min_first_words, from_end=False)

    first_count = len(split_into_words(first_section))

    # Short documents: return everything
    if total_words <= first_count + last_words:
        return f"--- FULL TEXT ({total_words} words) ---\n{full_text.strip()}"

    # Extract last M words (snap to sentence)
    last_section = snap_to_sentence_end(full_text, last_words, from_end=True)
    last_count = len(split_into_words(last_section))

    output = [
        f"--- BEGINNING ({first_count} words) ---",
        first_section,
        f"\n--- END ({last_count} words) ---",
        last_section
    ]

    headers_footers = extract_headers_footers(page_texts, num_pages)
    if headers_footers:
        output.append(
            "\n--- HEADERS/FOOTERS FROM KEY PAGES "
            "(volume, issue, page range, and chapter numbers often appear here) ---"
        )
        output.append(headers_footers)

    return "\n".join(output)


def extract_pdf(pdf_path, **opts):
    """
    Extract a PDF's bibliographic content as a SourceContent.

    Thin wrapper around extract_content()/extract_pdf_metadata() that
    presents the same interface as other source extractors (e.g.
    web_source.extract_webloc). Accepts the same keyword options as
    extract_content() (min_first_words, last_words, min_words_threshold,
    quiet, language_prompt_fn, ocr_timeout).

    Returns:
        SourceContent on success, or a string starting with "Error:" on failure.
    """
    text = extract_content(pdf_path, **opts)
    if isinstance(text, str) and text.startswith("Error:"):
        return text
    return SourceContent(text=text, metadata=extract_pdf_metadata(pdf_path), label="PDF")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_pages.py <pdf_file>", file=sys.stderr)
        sys.exit(1)

    result = extract_content(sys.argv[1])
    print(result)
