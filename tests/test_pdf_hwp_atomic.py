from __future__ import annotations

from pathlib import Path

import pytest

import app.pdf_hwp_atomic as subject


def test_atomic_replace_retries_persistent_permission_error_then_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / ".result.tmp"
    target = tmp_path / "result.json"
    temporary.write_text("payload", encoding="utf-8")
    calls = 0
    delays: list[float] = []

    def always_fail(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        raise PermissionError(5, "simulated persistent deny", destination)

    monkeypatch.setattr(subject.os, "replace", always_fail)
    monkeypatch.setattr(subject, "sleep", delays.append)

    with pytest.raises(PermissionError, match="simulated persistent deny"):
        subject.atomic_replace(temporary, target)

    assert calls == 3
    assert delays == [subject._REPLACE_RETRY_SECONDS] * 2
    assert max(delays) <= 0.01
    assert not temporary.exists()
    assert not target.exists()


def test_atomic_replace_does_not_retry_non_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / ".result.tmp"
    target = tmp_path / "result.json"
    temporary.write_text("payload", encoding="utf-8")
    calls = 0

    def fail_once(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        raise OSError(28, "simulated disk full", destination)

    monkeypatch.setattr(subject.os, "replace", fail_once)

    with pytest.raises(OSError, match="simulated disk full"):
        subject.atomic_replace(temporary, target)

    assert calls == 1
    assert not temporary.exists()
    assert not target.exists()
