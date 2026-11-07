#!/usr/bin/env python3
"""Self-tests for the progress window's Search choice, without AppKit: the
choices offered, the current one read from config, a choice applied to
config, and the batch applying it before a file. The window itself (the
popup) is GUI and is not exercised here."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import biblio_agent as ba  # noqa: E402


def test_choices_offer_gemini_only_with_a_key():
    assert [v for v, _ in ba.search_choices({})] == ["off", "claude", "ai-mode"]
    assert [v for v, _ in ba.search_choices({"gemini_api_key": "K"})][-1] == "gemini"


def test_current_search_reads_config():
    assert ba.current_search({}) == "off"
    assert ba.current_search({"gemini_type_check": True}) == "claude"
    assert ba.current_search({"gemini_type_check": True, "type_search": "ai-mode"}) == "ai-mode"
    assert ba.current_search({"gemini_type_check": True, "type_search": "bogus"}) == "claude"


def test_apply_choice_sets_both_keys():
    c = {"gemini_type_check": True, "type_search": "claude"}
    ba.apply_search_choice(c, "off")
    assert c["gemini_type_check"] is False and ba.current_search(c) == "off"
    ba.apply_search_choice(c, "ai-mode")
    assert c == {"gemini_type_check": True, "type_search": "ai-mode"}


def test_the_batch_applies_the_windows_choice_before_a_file():
    import tempfile
    from unittest import mock
    agent = ba.BiblioAgent(str(ROOT / "config.yaml"))
    agent.config.update(gemini_type_check=False, type_search="claude")
    seen = []

    def fake_extract(path, batch_info=None, model_override=None):
        seen.append(ba.current_search(agent.config))
        return type("R", (), {"failed": True, "error": "stub"})()

    win = type("W", (), {"cancelled": False, "selected_model": None, "selected_search": "ai-mode",
                         "set_progress": lambda self, i, n: None})()
    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with mock.patch.object(agent, "extract_bibtex", fake_extract), \
                mock.patch.object(agent, "_log"), mock.patch.object(agent, "notify_progress"):
            agent.process_batch(move_files=False, pdf_files=[pdf], progress_window=win)
    assert seen == ["ai-mode"], seen


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {t.__name__}: {e!r}")
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} search-choice self-tests passed.")
    sys.exit(1 if failed else 0)
