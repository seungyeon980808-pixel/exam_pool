"""Dependency-free fakes for PDF→HWP workflow tests.

These providers are intentionally tiny: tests can inject deterministic success,
empty/partial extraction, exceptions, delays, item failures, and progress without
launching Hancom or OCR binaries.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class FakeExtractionProvider:
    responses: list[Any] = field(default_factory=list)
    delay_seconds: float = 0.0
    calls: int = 0

    def extract(self, source: Path) -> Any:
        self.calls += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        value = self.responses[min(self.calls - 1, max(0, len(self.responses) - 1))] if self.responses else ""
        if isinstance(value, BaseException):
            raise value
        return value


@dataclass
class FakeHwpProvider:
    mode: str = "success"
    failed_items: set[int] = field(default_factory=set)
    delay_seconds: float = 0.0
    progress: list[int] = field(default_factory=list)

    def typeset(self, item_numbers: list[int], output_dir: Path, on_progress: Callable[[int, int | None], None] | None = None) -> tuple[Path, Path]:
        if self.mode == "timeout":
            time.sleep(max(self.delay_seconds, 0.01))
            raise TimeoutError("fake HWP provider timed out")
        if self.mode == "failure":
            raise RuntimeError("fake HWP provider failed")
        output_dir.mkdir(parents=True, exist_ok=True)
        for index, item_number in enumerate(item_numbers, 1):
            if item_number in self.failed_items:
                raise RuntimeError(f"item {item_number} conversion failed")
            percent = round(index / max(1, len(item_numbers)) * 100)
            self.progress.append(percent)
            if on_progress:
                on_progress(percent, item_number)
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
        hwp = output_dir / "fake-output.hwp"
        pdf = output_dir / "fake-output.pdf"
        hwp.write_bytes(b"fake-hwp")
        pdf.write_bytes(b"%PDF-1.4\nfake")
        return hwp, pdf
