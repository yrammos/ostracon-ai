#!/usr/bin/env python3
"""
Self-test for extract_pages.py's thin-yield check: the amber flag a PDF gets
when extraction completes having produced almost nothing.

The failure this guards against is not a crash. It is Motte2004 - 232 words
across every page of the source, where comparable sources in the evaluation
sample gave 1,000-3,100 - producing a correctly typed, entirely confident
entry with no indication that the text it was built from carried no
bibliographic data at all. OCR completing is not OCR succeeding, and a
process that exits 0 having read nothing is indistinguishable downstream from
one that read a title page. See issue #16.

No PDF is needed and no OCR runs: extract_all_text() is replaced with canned
page text, which is the input the check actually consumes. That keeps the
word counts exact rather than approximately whatever a fixture happened to
contain, and it means these tests exercise the arithmetic and the threshold
rather than pypdf.

The OCR engine tests (issue #129) cover the choice between Google, through
ocrdoc, and Tesseract, and each road back to Tesseract; ocrdoc is stubbed in
all but the last, which runs the real ocrdoc CLI offline with its fake engine
(Tesseract answering as Google) when config.yaml sets ocrdoc_path.

    python dev/test_extract_pages.py     # the project environment's Python
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import extract_pages  # noqa: E402


class _Canned:
    """Run extract_pdf()/extract_content() over canned page text, with no file
    and no OCR, restoring the module afterwards.

    min_words_threshold=0 keeps extract_content() off the OCR path entirely -
    the question here is what happens to a yield that has already settled,
    however it got there.
    """

    def __init__(self, page_texts):
        self.page_texts = page_texts

    def __enter__(self):
        self._all_text = extract_pages.extract_all_text
        self._metadata = extract_pages.extract_pdf_metadata
        extract_pages.extract_all_text = lambda path: (self.page_texts, len(self.page_texts))
        extract_pages.extract_pdf_metadata = lambda path: {}

        def run(**opts):
            return extract_pages.extract_pdf(
                Path("canned.pdf"), quiet=True, min_words_threshold=0, **opts)
        return run

    def __exit__(self, *exc):
        extract_pages.extract_all_text = self._all_text
        extract_pages.extract_pdf_metadata = self._metadata
        return False


def _words(n):
    """One page holding exactly n words."""
    return " ".join(f"w{i}" for i in range(n))


def test_thin_yield_is_flagged_amber():
    with _Canned([_words(120), _words(112)]) as run:   # 232, Motte2004's figure
        content = run(thin_yield_words=400)
    assert content.amber is True, content
    assert "232 words" in content.amber_reason, content.amber_reason
    assert "400" in content.amber_reason, content.amber_reason
    return True


def test_ordinary_yield_is_not_flagged():
    with _Canned([_words(900), _words(900)]) as run:
        content = run(thin_yield_words=400)
    assert content.amber is False, content
    assert content.amber_reason is None, content
    return True


def test_threshold_boundary_is_exclusive():
    # Exactly at the threshold is not thin; one word under is. Stated because
    # an off-by-one here is invisible - it changes which entries get a flag,
    # and nothing downstream would disagree with either answer.
    with _Canned([_words(400)]) as run:
        assert run(thin_yield_words=400).amber is False
    with _Canned([_words(399)]) as run:
        assert run(thin_yield_words=400).amber is True
    return True


def test_zero_threshold_disables_the_check():
    with _Canned([_words(3)]) as run:
        content = run(thin_yield_words=0)
    assert content.amber is False, content
    return True


def test_default_threshold_sits_above_ocr_threshold():
    # A floor at or below the OCR threshold could never fire: anything under
    # that has already been through OCR by the time extract_pdf() looks.
    assert extract_pages.DEFAULT_THIN_YIELD_WORDS > 100
    with _Canned([_words(232)]) as run:
        assert run().amber is True          # default applies with no argument
    return True


def test_extract_content_returns_the_yield_not_the_excerpt_length():
    # The count has to be the whole PDF's, not the returned excerpt's - the
    # excerpt is capped at min_first_words + last_words, so deriving the yield
    # from it would report the cap for every long source.
    # Two pages, so the excerpt is genuinely an excerpt: page 1 clears
    # min_first_words and is taken whole, the rest is not.
    with _Canned([_words(500), _words(4500)]):
        text, total = extract_pages.extract_content(
            Path("canned.pdf"), quiet=True, min_words_threshold=0)
    assert total == 5000, total
    # The excerpt the model actually sees begins with 500 of those 5,000 - so
    # the count is demonstrably the PDF's, not the excerpt's. (The returned
    # string is not simply shorter than the source: it also carries a
    # headers/footers section that repeats page text, which is exactly why
    # counting the returned string would be the wrong way to get this number.)
    assert "--- BEGINNING (500 words) ---" in text, text[:80]
    return True


def test_extract_content_reports_zero_words_on_error():
    real = extract_pages.extract_all_text
    extract_pages.extract_all_text = lambda path: ("Error: File not found: x.pdf", 0)
    try:
        text, total = extract_pages.extract_content(Path("missing.pdf"), quiet=True)
    finally:
        extract_pages.extract_all_text = real
    assert text.startswith("Error:"), text
    assert total == 0, total
    # And extract_pdf passes the error straight through rather than wrapping
    # an errored extraction in a SourceContent that looks merely thin.
    extract_pages.extract_all_text = lambda path: ("Error: File not found: x.pdf", 0)
    try:
        result = extract_pages.extract_pdf(Path("missing.pdf"), quiet=True)
    finally:
        extract_pages.extract_all_text = real
    assert isinstance(result, str) and result.startswith("Error:"), result
    return True


def test_pdf_creation_date_is_not_passed_on():
    # Constructed: a one-page PDF whose file metadata carries a CreationDate.
    import tempfile
    from pypdf import PdfWriter
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "m.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.add_metadata({"/Title": "A Title", "/CreationDate": "D:20190101000000"})
        with open(path, "wb") as f:
            writer.write(f)
        meta = extract_pages.extract_pdf_metadata(path)
    assert meta.get("Title") == "A Title", meta
    assert "CreationDate" not in meta, meta
    return True


# ---------------------------------------------------------------- OCR engine (issue #129)
#
# The choice between Google (ocrdoc) and Tesseract, and every road from ocrdoc
# back to Tesseract. ocrdoc is stubbed here: its preflight, its process, or
# run_google_ocr() as a whole, as each test names. Constructed fixtures only.

class _Patch:
    """Replace module attributes of extract_pages for the duration of a block,
    and clear the preflight cache on both sides."""

    def __init__(self, **attrs):
        self.attrs = attrs

    def __enter__(self):
        self.saved = {k: getattr(extract_pages, k) for k in self.attrs}
        for k, v in self.attrs.items():
            setattr(extract_pages, k, v)
        extract_pages._preflight_cache.clear()
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(extract_pages, k, v)
        extract_pages._preflight_cache.clear()
        return False


def _blank_pdf(directory, pages=6):
    """A PDF of blank pages: no words, so extract_content() goes to OCR."""
    from pypdf import PdfWriter
    path = Path(directory) / "blank.pdf"
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        writer.write(f)
    return path


class _Tesseract:
    """Stands in for run_ocr(): records its calls, returns `words` per page."""

    def __init__(self, words=60):
        self.calls, self.words = [], words

    def __call__(self, pdf_path, page_numbers, language="eng", timeout=180, force=False):
        self.calls.append({"pages": list(page_numbers), "force": force, "language": language})
        return {n: _words(self.words) for n in page_numbers}


def _ocr_run(engine="auto", ocrdoc_path="/stub/ocrdoc", **patches):
    """extract_content() on a 6-page blank PDF with the given stubs; returns
    (text, total_words, log lines, tesseract stub)."""
    import tempfile
    tesseract = patches.pop("run_ocr", None) or _Tesseract()
    lines = []
    with tempfile.TemporaryDirectory() as td, _Patch(run_ocr=tesseract, **patches):
        text, total = extract_pages.extract_content(
            _blank_pdf(td), quiet=True, ocr_engine=engine, ocrdoc_path=ocrdoc_path,
            ocr_timeout=7, log=lines.append, language_prompt_fn=lambda: "deu")
    return text, total, lines, tesseract


def _preflight(ok, why=""):
    calls = []

    def stub(path):
        calls.append(path)
        return (ok, why)
    stub.calls = calls
    return stub


def test_choose_engine():
    usable, broken = _preflight(True), _preflight(False, "preflight failed: no sign-in")
    with _Patch(ocrdoc_preflight=usable):
        assert extract_pages.choose_ocr_engine("auto", "/stub") == ("google", None)
        assert extract_pages.choose_ocr_engine("auto", None) == ("tesseract", None)
        assert extract_pages.choose_ocr_engine(None, None) == ("tesseract", None)
        assert extract_pages.choose_ocr_engine("google", "/stub") == ("google", None)
        n = len(usable.calls)
        assert extract_pages.choose_ocr_engine("tesseract", "/stub") == ("tesseract", None)
        assert len(usable.calls) == n, "tesseract must never run ocrdoc, not even its preflight"
        engine, msg = extract_pages.choose_ocr_engine("google", None)
        assert engine == "error" and "ocrdoc_path" in msg, msg
        engine, msg = extract_pages.choose_ocr_engine("vision", "/stub")
        assert engine == "error" and "vision" in msg, msg
    with _Patch(ocrdoc_preflight=broken):
        engine, note = extract_pages.choose_ocr_engine("auto", "/stub")
        assert engine == "tesseract" and "no sign-in" in note, note
        engine, msg = extract_pages.choose_ocr_engine("google", "/stub")
        assert engine == "error" and "no sign-in" in msg, msg
    return True


def test_preflight_runs_once_and_reports_why():
    calls = []

    def run_group(cmd, timeout, cwd=None):
        calls.append(cmd)
        return 1, "", "Google: no sign-in (run `python docai.py login` once)\n"
    with _Patch(_ocrdoc_command=lambda path, *a: (["pdflayer", *a], None), _run_group=run_group):
        first = extract_pages.ocrdoc_preflight("/stub")
        second = extract_pages.ocrdoc_preflight("/stub")
    assert first == second and first[0] is False, first
    assert "no sign-in" in first[1], first
    assert len(calls) == 1, calls
    assert calls[0][-2:] == ["--preflight", "ocr"], calls
    # A path with no pdflayer.py never reaches micromamba.
    with _Patch():
        ok, why = extract_pages.ocrdoc_preflight("/nonexistent/ocrdoc")
    assert ok is False and "pdflayer.py" in why, why
    return True


def test_auto_uses_google_when_present():
    google_calls = []

    def google(pdf_path, page_numbers, ocrdoc_path, language="eng", timeout=180):
        google_calls.append((list(page_numbers), ocrdoc_path, language, timeout))
        return {n: _words(80) for n in page_numbers}
    _, total, lines, tesseract = _ocr_run(ocrdoc_preflight=_preflight(True),
                                          run_google_ocr=google)
    assert google_calls == [([1, 2, 3, 5, 6], "/stub/ocrdoc", "deu", 7)], google_calls
    assert tesseract.calls == [], "Tesseract ran although Google succeeded"
    assert total == 400, total
    assert any("Google OCR via ocrdoc successful (400 words)" in l for l in lines), lines
    return True


def test_auto_uses_tesseract_when_absent():
    def google(*a, **k):
        raise AssertionError("ocrdoc run with ocrdoc_path unset")
    _, total, lines, tesseract = _ocr_run(ocrdoc_path=None, run_google_ocr=google,
                                          ocrdoc_preflight=google)
    assert len(tesseract.calls) == 1 and tesseract.calls[0]["force"] is False, tesseract.calls
    assert total == 300, total
    assert not any("ocrdoc" in l for l in lines), lines
    return True


def test_auto_logs_unusable_ocrdoc_and_uses_tesseract():
    _, _, lines, tesseract = _ocr_run(
        ocrdoc_preflight=_preflight(False, "preflight failed: no sign-in"))
    assert len(tesseract.calls) == 1, tesseract.calls
    assert any("ocrdoc unusable" in l and "no sign-in" in l for l in lines), lines
    return True


def test_google_mode_fails_loudly_when_unusable():
    text, total, lines, tesseract = _ocr_run(
        engine="google", ocrdoc_preflight=_preflight(False, "preflight failed: no sign-in"))
    assert text.startswith("Error:") and "no sign-in" in text, text
    assert total == 0 and tesseract.calls == [], tesseract.calls
    assert any("no sign-in" in l for l in lines), lines
    return True


def _fallback(run_group, **extra):
    """extract_content() with ocrdoc's process replaced by run_group."""
    return _ocr_run(ocrdoc_preflight=_preflight(True),
                    _ocrdoc_command=lambda path, *a: (["pdflayer", *a], None),
                    _run_group=run_group, **extra)


def test_fallback_on_exit_code():
    seen = []

    def run_group(cmd, timeout, cwd=None):
        seen.append(cmd)
        return 1, "tmpabc.pdf: refused (preflight: ['encrypted'])\n", ""
    _, total, lines, tesseract = _fallback(run_group)
    assert seen and seen[0][1] == "ocr" and seen[0][-3:] == ["--langs", "deu", "--yes"], seen
    assert len(tesseract.calls) == 1, tesseract.calls
    assert total == 300, total
    hit = [l for l in lines if "fell back to Tesseract" in l]
    assert len(hit) == 1 and "exit 1" in hit[0] and "encrypted" in hit[0], lines
    return True


def test_fallback_on_timeout():
    import subprocess

    def run_group(cmd, timeout, cwd=None):
        raise subprocess.TimeoutExpired(cmd, timeout)
    _, total, lines, tesseract = _fallback(run_group)
    assert len(tesseract.calls) == 1, tesseract.calls
    hit = [l for l in lines if "fell back to Tesseract" in l]
    assert len(hit) == 1 and "timed out after 7 seconds" in hit[0], lines
    return True


def test_fallback_on_thin_yield_reaches_the_forced_retry():
    # ocrdoc exits 0 having written nothing (its "nothing to do"): the subset
    # comes back blank. Tesseract then runs - and, finding the pages blank
    # too, its --force-ocr retry, which the fallback must not bypass.
    def run_group(cmd, timeout, cwd=None):
        return 0, "tmpabc.pdf: nothing to do (no page to read)\n", ""
    _, total, lines, tesseract = _fallback(run_group, run_ocr=_Tesseract(words=0))
    hit = [l for l in lines if "fell back to Tesseract" in l]
    assert len(hit) == 1 and "thin yield, 0 words (under 100)" in hit[0], lines
    assert [c["force"] for c in tesseract.calls] == [False, True], tesseract.calls
    return True


def test_timeout_kills_ocrdoc_child_processes():
    # pdflayer.py runs each file in a child process; a timeout that killed
    # only the direct child would leave that one reading pages (and paying
    # Google) after Tesseract took over. Constructed: a shell that starts a
    # background sleeper and records its pid.
    import os
    import subprocess
    import tempfile
    import time
    with tempfile.TemporaryDirectory() as td:
        pidfile = Path(td) / "pid"
        cmd = ["/bin/sh", "-c", f"sleep 30 & echo $! > '{pidfile}'; wait"]
        try:
            extract_pages._run_group(cmd, timeout=1)
            raise AssertionError("no timeout")
        except subprocess.TimeoutExpired:
            pass
        pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    os.kill(pid, 9)
    raise AssertionError(f"background child {pid} survived the timeout")


def _configured_ocrdoc_path():
    try:
        import yaml
        with open(ROOT / "config.yaml") as f:
            value = (yaml.safe_load(f) or {}).get("ocrdoc_path")
    except Exception:
        return None
    if not value:
        return None
    p = Path(value).expanduser()
    return str(p if p.is_absolute() else (ROOT / p).resolve())


_SCAN = r"""
import sys
from PIL import Image, ImageDraw, ImageFont
try:
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Times New Roman.ttf", 40)
except Exception:
    font = ImageFont.load_default()
pages = []
for p in range(int(sys.argv[2])):
    im = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(im)
    for i in range(20):
        d.text((150, 150 + 90 * i), f"Page {p + 1} line {i + 1}: The history of music theory",
               fill="black", font=font)
    pages.append(im)
pages[0].save(sys.argv[1], "PDF", resolution=200, save_all=True, append_images=pages[1:])
"""


def test_end_to_end_with_ocrdoc_fake_engine():
    # The real ocrdoc CLI, offline: OCRDOC_DOCAI_FAKE answers with Tesseract
    # in place of Google, so this proves the plumbing (preflight, the subset,
    # --langs, the in-place write, the read-back), not Google's quality.
    # Skipped when config.yaml sets no ocrdoc_path.
    import os
    import subprocess
    import tempfile
    ocrdoc_path = _configured_ocrdoc_path()
    if not ocrdoc_path:
        return "skipped: config.yaml sets no ocrdoc_path"
    saved = {k: os.environ.get(k) for k in
             ("OCRDOC_DOCAI_FAKE", "OCRDOC_DOCAI_STORE", "OCRDOC_STATE")}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "store").mkdir()
        (td / "state").mkdir()
        # Set unconditionally, before anything runs ocrdoc: never a live call.
        os.environ.update(OCRDOC_DOCAI_FAKE="1", OCRDOC_DOCAI_STORE=str(td / "store"),
                          OCRDOC_STATE=str(td / "state"))
        try:
            # Constructed fixture: an 8-page image-only PDF, drawn in ocrdoc's
            # environment (it has Pillow; ostracon's does not).
            scan = td / "scan.pdf"
            subprocess.run([extract_pages._micromamba_bin(), "run", "-n", extract_pages.OCRDOC_ENV,
                            "python", "-c", _SCAN, str(scan), "8"],
                           check=True, capture_output=True, timeout=120)

            def no_tesseract(*a, **k):
                raise AssertionError("Tesseract fallback ran: ocrdoc did not succeed")
            lines = []
            with _Patch(run_ocr=no_tesseract):
                text, total = extract_pages.extract_content(
                    scan, quiet=True, ocr_engine="google", ocrdoc_path=ocrdoc_path,
                    ocr_timeout=300, log=lines.append, language_prompt_fn=lambda: "eng")
            assert not text.startswith("Error:"), (text, lines)
            assert not any("fell back" in l for l in lines), lines
            assert any("Google OCR via ocrdoc successful" in l for l in lines), lines
            assert total >= 100 and "music theory" in text, (total, text[:200])
            # ocrdoc's own record of the run: a "done" row for the temporary
            # subset, never for the source file.
            log = (td / "state" / "log.tsv").read_text().splitlines()
            rows = [l.split("\t") for l in log[1:]]
            assert len(rows) == 1 and rows[0][1:3] == ["ocr", "done"], log
            assert Path(rows[0][3]).name != scan.name, rows[0][3]
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return True


TESTS = [
    test_pdf_creation_date_is_not_passed_on,
    test_thin_yield_is_flagged_amber,
    test_ordinary_yield_is_not_flagged,
    test_threshold_boundary_is_exclusive,
    test_zero_threshold_disables_the_check,
    test_default_threshold_sits_above_ocr_threshold,
    test_extract_content_returns_the_yield_not_the_excerpt_length,
    test_extract_content_reports_zero_words_on_error,
    test_choose_engine,
    test_preflight_runs_once_and_reports_why,
    test_auto_uses_google_when_present,
    test_auto_uses_tesseract_when_absent,
    test_auto_logs_unusable_ocrdoc_and_uses_tesseract,
    test_google_mode_fails_loudly_when_unusable,
    test_fallback_on_exit_code,
    test_fallback_on_timeout,
    test_fallback_on_thin_yield_reaches_the_forced_retry,
    test_timeout_kills_ocrdoc_child_processes,
    test_end_to_end_with_ocrdoc_fake_engine,
]


def main():
    failures = []
    for test in TESTS:
        try:
            result = test()
            if isinstance(result, str) and result.startswith("skipped"):
                print(f"  - {test.__name__}: {result}")
                continue
            assert result is True
            print(f"  ✓ {test.__name__}")
        except Exception as e:
            failures.append((test.__name__, e))
            print(f"  ✗ {test.__name__}: {e}")

    print()
    if failures:
        print(f"{len(failures)}/{len(TESTS)} test(s) failed.")
        return 1
    print(f"All {len(TESTS)} extract_pages self-tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
