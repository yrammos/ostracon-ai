"""Fetch a web page and return its readable text.

Used by the evidence step (biblio_agent._read_cited_pages) to read the pages
Gemini's grounded search cites. Gemini's links are Google redirects
(vertexaisearch.cloud.google.com/grounding-api-redirect/...); requests
follows them to the page itself, and `url` in the result is where it landed.

Failures return {'error': ...}, never text: an empty page must not read as a
page that says nothing.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

USER_AGENT = "Mozilla/5.0 (compatible; ostracon-ai bibliographic reader)"
MAX_BYTES = 2_000_000
MAX_PDF_BYTES = 12_000_000
PDF_PAGES = 6          # title page, copyright page, first page of text
MAX_CHARS = 60_000


def domain(url):
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def html_to_text(raw):
    """Visible text of an HTML page, plus the content of its citation_* and
    og: meta tags, which publishers use to state a work's record."""
    metas = []
    for m in re.finditer(r"<meta\s[^>]*>", raw, re.I):
        tag = m.group(0)
        name = re.search(r'(?:name|property)\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        content = re.search(r'content\s*=\s*["\']([^"\']*)["\']', tag, re.I)
        if name and content and re.match(r"(citation_|dc\.|og:|book:|prism\.)", name.group(1), re.I):
            metas.append(f"{name.group(1)}: {html.unescape(content.group(1))}")
    body = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", raw)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d|dd|dt|section|article)>", "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    lines = [re.sub(r"[ \t ]+", " ", l).strip() for l in body.splitlines()]
    text = "\n".join(l for l in lines if l)
    return ("\n".join(metas) + "\n\n" + text).strip() if metas else text


def pdf_text(data, pages=PDF_PAGES):
    """Text of a PDF's first pages, where a title page, a copyright page's
    catalog record or an article's first-page header sit."""
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((reader.pages[i].extract_text() or "") for i in range(min(pages, len(reader.pages))))


def _pdf(resp, final):
    raw = resp.raw.read(MAX_PDF_BYTES, decode_content=True) if getattr(resp, "raw", None) else resp.content[:MAX_PDF_BYTES]
    try:
        text = pdf_text(raw)
    except Exception as e:  # noqa: BLE001 - a broken or truncated PDF
        return {"error": f"unreadable PDF ({type(e).__name__})", "url": final}
    if not text.strip():
        return {"error": "PDF has no text layer", "url": final}
    return {"url": final, "domain": domain(final), "text": text[:MAX_CHARS]}


def read_page(url, timeout=20, session=None):
    """{'url', 'domain', 'text'} for the page at `url`, or {'error', 'url'}."""
    import requests
    get = (session or requests).get
    try:
        resp = get(url, timeout=timeout, allow_redirects=True, stream=True,
                   headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/pdf"})
    except Exception as e:  # noqa: BLE001 - every failure is reported the same way
        return {"error": f"{type(e).__name__}: {e}", "url": url}
    final = getattr(resp, "url", url) or url
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}", "url": final}
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "pdf" in ctype:
        return _pdf(resp, final)
    if "html" not in ctype and "xml" not in ctype and "text/plain" not in ctype:
        return {"error": f"not a web page ({ctype or 'no content type'})", "url": final}
    raw = resp.raw.read(MAX_BYTES, decode_content=True) if hasattr(resp, "raw") and resp.raw else resp.content[:MAX_BYTES]
    enc = resp.encoding or "utf-8"
    page = raw.decode(enc, errors="replace") if isinstance(raw, bytes) else str(raw)
    text = html_to_text(page) if "html" in ctype or "xml" in ctype else page
    if not text.strip():
        return {"error": "page has no text", "url": final}
    return {"url": final, "domain": domain(final), "text": text[:MAX_CHARS]}
