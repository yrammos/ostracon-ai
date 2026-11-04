#!/usr/bin/env python3
"""Self-tests for process_batch() running sources in parallel (batch_workers).

Stubbed: extract_bibtex (sleeps, then returns a constructed result, logging a
few lines), save_entry and move_to_processed (record calls), notifications.
Not stubbed: the batch loop, its worker pool, the held logs, the save lock and
the outcome bookkeeping. No API calls; no GUI.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import biblio_agent  # noqa: E402
from extraction_result import ExtractionResult  # noqa: E402

DELAY = 0.2


def _agent(workers, fail=(), boom=()):
    agent = biblio_agent.BiblioAgent.__new__(biblio_agent.BiblioAgent)
    agent.config = {"batch_workers": workers, "verbose": False, "model": "m",
                    "interface": {"notifications": False}}
    agent.usage = []
    agent._local = threading.local()
    agent._lock = threading.Lock()
    agent._ask_lock = threading.Lock()
    agent._progress_callback = None
    log, saves, active, peak = [], [], [0], [0]
    agent._progress_callback = lambda m, lvl: log.append(m)
    guard = threading.Lock()

    def extract(path, batch_info=None, model_override=None):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        agent._log(f"start {path.name}")
        agent.inferred_fields = [path.name]
        time.sleep(DELAY)
        agent._log(f"end {path.name}")
        with guard:
            active[0] -= 1
        if path.name in boom:
            raise RuntimeError("kaput")
        assert agent.inferred_fields == [path.name], "inferred_fields leaked across threads"
        if path.name in fail:
            return ExtractionResult(entry=None, error="no entry")
        return ExtractionResult(entry="@book{x,}")

    def save(result, path):
        assert agent._lock.locked(), "save_entry ran outside the lock"
        saves.append(path.name)
        return True

    agent.extract_bibtex = extract
    agent.save_entry = save
    agent.move_to_processed = lambda p: None
    agent.notify_progress = lambda *a, **k: None
    return agent, log, saves, peak


def _run(agent, n):
    with tempfile.TemporaryDirectory() as td:
        files = []
        for i in range(n):
            f = Path(td) / f"s{i}.pdf"
            f.write_text("x")
            files.append(f)
        t = time.monotonic()
        res = agent.process_batch(move_files=False, pdf_files=files)
        return res, time.monotonic() - t


def test_parallel_is_faster_and_saves_every_source():
    agent, log, saves, peak = _agent(3)
    res, took = _run(agent, 7)
    assert sorted(res["success"]) == [f"s{i}.pdf" for i in range(7)], res
    assert sorted(saves) == sorted(res["success"])
    assert peak[0] == 3, peak
    # 1 alone, then 6 in two rounds of 3: about 3 delays, not 7.
    assert took < 5 * DELAY, took


def test_one_worker_is_sequential():
    agent, _, _, peak = _agent(1)
    res, took = _run(agent, 3)
    assert peak[0] == 1 and len(res["success"]) == 3
    assert took >= 3 * DELAY


def test_each_sources_messages_stay_together():
    agent, log, _, _ = _agent(3)
    _run(agent, 6)
    lines = [m for m in log if m.startswith(("start", "end"))]
    for i in range(0, len(lines), 2):
        assert lines[i].split()[1] == lines[i + 1].split()[1], lines


def test_failures_and_exceptions_are_counted_not_fatal():
    agent, log, saves, _ = _agent(3, fail={"s2.pdf"}, boom={"s3.pdf"})
    res, _ = _run(agent, 5)
    failed = dict(res["failed"])
    assert failed["s2.pdf"] == "no entry", failed
    assert failed["s3.pdf"].startswith("RuntimeError"), failed
    assert len(res["success"]) == 3 and "s3.pdf" not in saves


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"  ✗ {t.__name__}: {e!r}")
    print(f"{failed}/{len(tests)} test(s) failed." if failed else f"All {len(tests)} batch self-tests passed.")
    sys.exit(1 if failed else 0)
