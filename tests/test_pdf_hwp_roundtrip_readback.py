from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
from typing import assert_never

import fitz
import pytest

from app.pdf_hwp_roundtrip_readback import (
    CommandOutput,
    CommandSpec,
    HwpExpectations,
    IssueCode,
    PdfExpectations,
    inspect_hwp,
    inspect_pdf,
)


@dataclass(frozen=True, slots=True)
class _FakeRunner:
    kordoc: CommandOutput
    info: CommandOutput
    dump: CommandOutput
    tables: CommandOutput

    def run(self, spec: CommandSpec) -> CommandOutput:
        match spec.argv[:2]:
            case ("kordoc", _):
                return self.kordoc
            case ("rhwp", "info"):
                return self.info
            case ("rhwp", "dump"):
                return self.dump
            case ("rhwp", "export-tables"):
                return self.tables
            case unreachable:
                assert_never(unreachable)


def _output(stdout: str, returncode: int = 0) -> CommandOutput:
    return CommandOutput(stdout=stdout, stderr="", returncode=returncode)


def _runner(
    blocks: list[dict[str, str | int]],
    markdown: str,
    table_cells: tuple[tuple[str, ...], ...] = (),
) -> _FakeRunner:
    kordoc = json.dumps({
        "success": True,
        "fileType": "hwp",
        "markdown": markdown,
        "blocks": blocks,
        "metadata": {"version": "5.x", "pageCount": 1},
        "pageCount": 1,
    }, ensure_ascii=False)
    info = "구역 수: 1\n페이지 수: 1\n총 문단 수: 5\n"
    dump = "\n".join((
        "--- 문단 0.0 --- cc=1, text_len=0, controls=1",
        "--- 문단 0.1 --- cc=10, text_len=9, controls=0",
        "=== 완료: 1 구역, 5 문단 ===",
    ))
    tables = json.dumps({
        "schemaVersion": "1.0",
        "source": "fake.hwp",
        "tableCount": len(table_cells),
        "tables": [
            {
                "cellCount": len(cells),
                "cells": [{"text": text} for text in cells],
            }
            for cells in table_cells
        ],
    }, ensure_ascii=False)
    return _FakeRunner(_output(kordoc), _output(info), _output(dump), _output(tables))


def test_hwp_readback_combines_kordoc_blocks_with_rhwp_counts(tmp_path: Path) -> None:
    # Given: observable text, table, choice, and rhwp paragraph evidence.
    source = tmp_path / "answer.hwp"
    source.write_bytes(b"fake boundary file")
    text = "<보기>\nㄱ. 첫째\nㄴ. 둘째\nㄷ. 셋째\n① ② ③ ④ ⑤"
    runner = _runner([
        {"type": "paragraph", "text": text, "pageNumber": 1},
        {"type": "table", "text": "<보기>", "pageNumber": 1},
    ], text)

    # When: both HWP readers are parsed through the typed boundary.
    report = inspect_hwp(source, HwpExpectations(
        require_bogi_box=True,
        require_bogi_claims=True,
        require_fifth_choice=True,
    ), runner)

    # Then: metadata and independent structure counts agree without overclaiming editability.
    assert report.issues == ()
    assert report.snapshot is not None
    assert report.snapshot.file_type == "hwp"
    assert report.snapshot.kordoc_page_count == 1
    assert report.snapshot.rhwp_page_count == 1
    assert report.snapshot.block_count == 2
    assert report.snapshot.rhwp_paragraph_count == 5
    assert report.snapshot.rhwp_text_paragraph_count == 1
    assert report.snapshot.rhwp_table_count == 0


def test_raster_only_hwp_gets_stable_noneditable_issue_codes(tmp_path: Path) -> None:
    # Given: kordoc sees only an image and rhwp sees no paragraph text.
    source = tmp_path / "raster.hwp"
    source.write_bytes(b"fake raster")
    runner = _runner([{"type": "image", "text": "", "pageNumber": 1}], "")
    runner = _FakeRunner(
        runner.kordoc,
        runner.info,
        _output("--- 문단 0.0 --- cc=1, text_len=0, controls=1\n=== 완료: 1 구역, 1 문단 ==="),
        runner.tables,
    )

    # When: editability-sensitive gates inspect only observable reader evidence.
    report = inspect_hwp(source, HwpExpectations(), runner)

    # Then: a whole-item raster and absent editable text are independently actionable.
    assert {issue.code for issue in report.issues} == {
        IssueCode.WHOLE_ITEM_RASTERIZED,
        IssueCode.MISSING_EDITABLE_TEXT,
    }


def test_unreadable_hwp_is_reported_without_untyped_subprocess_leak(tmp_path: Path) -> None:
    # Given: kordoc rejects an unreadable file.
    source = tmp_path / "broken.hwp"
    source.write_bytes(b"broken")
    failure = CommandOutput(stdout="", stderr="parse failed", returncode=2)
    runner = _FakeRunner(failure, _output(""), _output(""), _output(""))

    # When: the typed readback boundary handles the command failure.
    report = inspect_hwp(source, HwpExpectations(), runner)

    # Then: callers receive the stable issue and no partial snapshot.
    assert report.snapshot is None
    assert tuple(issue.code for issue in report.issues) == (IssueCode.UNREADABLE_HWP,)


def test_bogi_and_choice_gates_have_stable_codes(tmp_path: Path) -> None:
    # Given: editable prose lacks the required box, claims, and fifth choice.
    source = tmp_path / "incomplete.hwp"
    source.write_bytes(b"incomplete")
    runner = _runner([
        {"type": "paragraph", "text": "질문 ① ② ③ ④", "pageNumber": 1},
    ], "질문 ① ② ③ ④")

    # When: the expected exam structures are gated.
    report = inspect_hwp(source, HwpExpectations(True, True, True), runner)

    # Then: each missing structure has a machine-stable code.
    assert {issue.code for issue in report.issues} == {
        IssueCode.MISSING_BOGI_BOX,
        IssueCode.MISSING_BOGI_CLAIMS,
        IssueCode.MISSING_FIFTH_CHOICE,
    }


def test_arbitrary_table_does_not_satisfy_bogi_box_contract(tmp_path: Path) -> None:
    # Given: a real table exists, but its structure has no 보기 label or claims.
    source = tmp_path / "ordinary-table.hwp"
    source.write_bytes(b"ordinary table")
    markdown = "<table><tr><td>자료</td></tr></table>\nㄱ ㄴ ㄷ ⑤"
    runner = _runner([
        {"type": "paragraph", "text": "ㄱ ㄴ ㄷ ⑤", "pageNumber": 1},
        {"type": "table", "text": "", "pageNumber": 1},
    ], markdown, (("자료",),))

    # When: every hapdap structure is required.
    report = inspect_hwp(source, HwpExpectations(True, True, True), runner)

    # Then: unrelated table structure cannot stand in for the semantic 보기 box.
    assert tuple(issue.code for issue in report.issues) == (IssueCode.MISSING_BOGI_BOX,)


def test_rhwp_table_cells_prove_bogi_when_kordoc_uses_gfm_table(tmp_path: Path) -> None:
    # Given: kordoc exposes an empty table block and only a GFM table in Markdown.
    source = tmp_path / "gfm-hapdap.hwp"
    source.write_bytes(b"gfm hapdap")
    markdown = "| | <보 기> | |\n|---|---|---|\n| ㄱ ㄴ ㄷ | | |\n⑤"
    runner = _runner(
        [{"type": "table", "text": "", "pageNumber": 1},
         {"type": "paragraph", "text": "⑤", "pageNumber": 1}],
        markdown,
        (("",), ("<보 기>", "ㄱ. 첫째\nㄴ. 둘째\nㄷ. 셋째")),
    )

    # When: the typed rhwp table boundary is read with full hapdap expectations.
    report = inspect_hwp(source, HwpExpectations(True, True, True), runner)

    # Then: one semantic RHWP table, not document-wide marker coincidence, proves the box.
    assert report.issues == ()
    assert report.snapshot is not None
    assert report.snapshot.rhwp_table_count == 2
    assert report.snapshot.rhwp_table_cells[1] == ("<보 기>", "ㄱ. 첫째\nㄴ. 둘째\nㄷ. 셋째")


def test_pdf_readback_checks_page_count_and_required_text(tmp_path: Path) -> None:
    # Given: a real two-page PDF with text on only its first page.
    source = tmp_path / "roundtrip.pdf"
    with fitz.open() as document:
        first = document.new_page()
        first.insert_text((72, 72), "editable answer")
        document.new_page()
        document.save(source)

    # When: PDF structural expectations are checked.
    passing = inspect_pdf(source, PdfExpectations(2, ("editable answer",)))
    failing = inspect_pdf(source, PdfExpectations(1, ("missing text",)))

    # Then: page and text observations are parsed and failures remain stable.
    assert passing.issues == ()
    assert passing.snapshot is not None
    assert passing.snapshot.page_count == 2
    assert {issue.code for issue in failing.issues} == {
        IssueCode.PDF_PAGE_COUNT_MISMATCH,
        IssueCode.MISSING_PDF_TEXT,
    }


def test_real_hwp_readback_pins_installed_kordoc_and_rhwp_observations() -> None:
    # Given: the repository's stable CSAT science HWP fragment.
    source = Path("vendor/hwp_typesetter/seed_data/fragments/csat_science_direct.hwp")

    # When: installed kordoc 4.7.2 and rhwp 0.8.2 inspect the real binary.
    report = inspect_hwp(source, HwpExpectations(require_fifth_choice=True))

    # Then: only the readers' concrete page/block/paragraph observations are pinned.
    assert report.issues == ()
    assert report.snapshot is not None
    assert report.snapshot.kordoc_page_count == 1
    assert report.snapshot.block_count == 4
    assert report.snapshot.rhwp_section_count == 1
    assert report.snapshot.rhwp_page_count == 1
    assert report.snapshot.rhwp_paragraph_count == 5
    assert report.snapshot.rhwp_text_paragraph_count == 4
    assert report.snapshot.rhwp_table_count == 0


def test_real_hapdap_hwp_accepts_reader_visible_bogi_structure() -> None:
    # Given: the real hapdap fragment whose kordoc table block has empty text.
    source = Path("vendor/hwp_typesetter/seed_data/fragments/csat_science_hapdap.hwp")

    # When: every editable hapdap structure is required at the real reader boundary.
    report = inspect_hwp(source, HwpExpectations(
        require_bogi_box=True,
        require_bogi_claims=True,
        require_fifth_choice=True,
        require_editable_text=True,
    ))

    # Then: the structural table, claims, and choices satisfy the contract together.
    assert report.issues == ()
    assert report.snapshot is not None
    assert tuple(block.block_type for block in report.snapshot.blocks).count("table") == 1
    assert report.snapshot.rhwp_table_count == 1


def test_real_q234_gfm_hwp_accepts_rhwp_semantic_bogi_table() -> None:
    # Given: the retained exact q234 pilot output, identified by its content hash.
    candidates = tuple(Path(tempfile.gettempdir()).glob(
        "exampool-w6-q234-*/output/conversion/converted.hwp",
    ))
    if not candidates:
        pytest.skip("retained q234 pilot is unavailable")
    source = candidates[0]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "55e1612676263550dd046f0881b67fa22cc89ae92b7b5b32b5c603cd61b98939"
    )

    # When: the real GFM-producing document crosses every hapdap gate.
    report = inspect_hwp(source, HwpExpectations(True, True, True, True))

    # Then: its independently parsed RHWP table structure satisfies the box contract.
    assert report.issues == ()
    assert report.snapshot is not None
    assert report.snapshot.rhwp_table_count == 3
