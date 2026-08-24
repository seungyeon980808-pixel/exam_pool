"""Structural HWP/PDF readback gates backed by kordoc, rhwp, and PyMuPDF."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import shutil
import subprocess
from typing import Final, Protocol

import fitz
from pydantic import BaseModel, ConfigDict, Field, ValidationError


_COMMAND_TIMEOUT_SECONDS: Final = 30.0
_SECTION_COUNT: Final = re.compile(r"구역 수:\s*(\d+)")
_PAGE_COUNT: Final = re.compile(r"페이지 수:\s*(\d+)")
_PARAGRAPH_COUNT: Final = re.compile(r"총 문단 수:\s*(\d+)")
_DUMP_TEXT_LENGTH: Final = re.compile(r"text_len=(\d+)")
_MARKDOWN_TABLE: Final = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


class IssueCode(str, Enum):
    """Stable machine-facing structural failure codes."""

    UNREADABLE_HWP = "unreadable_hwp"
    WHOLE_ITEM_RASTERIZED = "whole_item_rasterized"
    MISSING_BOGI_BOX = "missing_bogi_box"
    MISSING_BOGI_CLAIMS = "missing_bogi_claims"
    MISSING_FIFTH_CHOICE = "missing_fifth_choice"
    MISSING_EDITABLE_TEXT = "missing_editable_text"
    UNREADABLE_PDF = "unreadable_pdf"
    PDF_PAGE_COUNT_MISMATCH = "pdf_page_count_mismatch"
    MISSING_PDF_TEXT = "missing_pdf_text"


@dataclass(frozen=True, slots=True)
class StructuralIssue:
    code: IssueCode
    detail: str


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: tuple[str, ...]
    timeout_seconds: float = _COMMAND_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class CommandOutput:
    stdout: str
    stderr: str
    returncode: int


class CommandRunner(Protocol):
    def run(self, spec: CommandSpec) -> CommandOutput: ...


@dataclass(frozen=True, slots=True)
class CommandExecutionError(RuntimeError):
    argv: tuple[str, ...]
    detail: str

    def __str__(self) -> str:
        return f"command {self.argv!r} failed: {self.detail}"


@dataclass(frozen=True, slots=True)
class ReadbackParseError(ValueError):
    source: str
    detail: str

    def __str__(self) -> str:
        return f"cannot parse {self.source} readback: {self.detail}"


@dataclass(frozen=True, slots=True)
class SubprocessRunner:
    """Run one argv-only command with a bounded execution time."""

    def run(self, spec: CommandSpec) -> CommandOutput:
        if not spec.argv:
            raise CommandExecutionError(spec.argv, "empty argv")
        executable = shutil.which(spec.argv[0])
        if executable is None:
            raise CommandExecutionError(spec.argv, "executable not found")
        argv = (executable, *spec.argv[1:])
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=spec.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandExecutionError(spec.argv, "timeout") from exc
        except OSError as exc:
            raise CommandExecutionError(spec.argv, str(exc)) from exc
        return CommandOutput(completed.stdout, completed.stderr, completed.returncode)


class _KordocBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    block_type: str = Field(alias="type")
    text: str = ""
    page_number: int | None = Field(default=None, alias="pageNumber")


class _KordocMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: str
    page_count: int = Field(alias="pageCount")


class _KordocPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    success: bool
    file_type: str = Field(alias="fileType")
    markdown: str
    blocks: tuple[_KordocBlock, ...]
    metadata: _KordocMetadata
    page_count: int = Field(alias="pageCount")


class _RhwpTableCell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    text: str


class _RhwpTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    cell_count: int = Field(alias="cellCount", ge=0)
    cells: tuple[_RhwpTableCell, ...]


class _RhwpTablesPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    schema_version: str = Field(alias="schemaVersion")
    table_count: int = Field(alias="tableCount", ge=0)
    tables: tuple[_RhwpTable, ...]


@dataclass(frozen=True, slots=True)
class BlockSummary:
    block_type: str
    text: str
    page_number: int | None


@dataclass(frozen=True, slots=True)
class HwpSnapshot:
    file_type: str
    version: str
    text: str
    blocks: tuple[BlockSummary, ...]
    kordoc_page_count: int
    rhwp_section_count: int
    rhwp_page_count: int
    rhwp_paragraph_count: int
    rhwp_text_paragraph_count: int
    rhwp_table_count: int
    rhwp_table_cells: tuple[tuple[str, ...], ...]

    @property
    def block_count(self) -> int:
        return len(self.blocks)


@dataclass(frozen=True, slots=True)
class HwpExpectations:
    require_bogi_box: bool = False
    require_bogi_claims: bool = False
    require_fifth_choice: bool = False
    require_editable_text: bool = True


@dataclass(frozen=True, slots=True)
class HwpReadbackReport:
    snapshot: HwpSnapshot | None
    issues: tuple[StructuralIssue, ...]


@dataclass(frozen=True, slots=True)
class PdfSnapshot:
    page_count: int
    page_text: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PdfExpectations:
    page_count: int
    required_text: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PdfReadbackReport:
    snapshot: PdfSnapshot | None
    issues: tuple[StructuralIssue, ...]


def _checked_run(runner: CommandRunner, spec: CommandSpec) -> str:
    output = runner.run(spec)
    if output.returncode != 0:
        raise CommandExecutionError(spec.argv, output.stderr.strip() or f"exit {output.returncode}")
    return output.stdout


def _count(pattern: re.Pattern[str], text: str, label: str) -> int:
    found = pattern.search(text)
    if found is None:
        raise ReadbackParseError("rhwp", f"missing {label}")
    return int(found.group(1))


def _snapshot_hwp(path: Path, runner: CommandRunner) -> HwpSnapshot:
    kordoc_text = _checked_run(runner, CommandSpec(("kordoc", str(path), "--format", "json")))
    payload = _KordocPayload.model_validate_json(kordoc_text)
    if not payload.success:
        raise ReadbackParseError("kordoc", "success=false")
    info = _checked_run(runner, CommandSpec(("rhwp", "info", str(path))))
    dump = _checked_run(runner, CommandSpec(("rhwp", "dump", str(path))))
    dump_lengths = tuple(int(value) for value in _DUMP_TEXT_LENGTH.findall(dump))
    if not dump_lengths:
        raise ReadbackParseError("rhwp", "missing dump paragraph lengths")
    tables_text = _checked_run(runner, CommandSpec(("rhwp", "export-tables", str(path), "--json")))
    tables = _RhwpTablesPayload.model_validate_json(tables_text)
    return HwpSnapshot(
        file_type=payload.file_type,
        version=payload.metadata.version,
        text=payload.markdown,
        blocks=tuple(BlockSummary(block.block_type, block.text, block.page_number) for block in payload.blocks),
        kordoc_page_count=payload.page_count,
        rhwp_section_count=_count(_SECTION_COUNT, info, "section count"),
        rhwp_page_count=_count(_PAGE_COUNT, info, "page count"),
        rhwp_paragraph_count=_count(_PARAGRAPH_COUNT, info, "paragraph count"),
        rhwp_text_paragraph_count=sum(length > 0 for length in dump_lengths),
        rhwp_table_count=tables.table_count,
        rhwp_table_cells=tuple(
            tuple(cell.text for cell in table.cells) for table in tables.tables
        ),
    )


def _has_bogi_box(snapshot: HwpSnapshot, require_claims: bool) -> bool:
    table_blocks = tuple(block for block in snapshot.blocks if block.block_type == "table")
    if any("<보기>" in "".join(block.text.split()) for block in table_blocks):
        return True
    if bool(table_blocks) and any(
        "<보기>" in "".join(table.split())
        and (not require_claims or all(marker in table for marker in ("ㄱ", "ㄴ", "ㄷ")))
        for table in _MARKDOWN_TABLE.findall(snapshot.text)
    ):
        return True
    return any(
        "<보기>" in "".join("".join(cells).split())
        and (not require_claims or all(marker in "\n".join(cells) for marker in ("ㄱ", "ㄴ", "ㄷ")))
        for cells in snapshot.rhwp_table_cells
    )


def _hwp_issues(snapshot: HwpSnapshot, expected: HwpExpectations) -> tuple[StructuralIssue, ...]:
    issues: list[StructuralIssue] = []
    paragraph_text = tuple(block.text for block in snapshot.blocks if block.block_type == "paragraph" and block.text.strip())
    editable_observed = bool(paragraph_text) and snapshot.rhwp_text_paragraph_count > 0
    has_image = any(block.block_type == "image" for block in snapshot.blocks)
    if has_image and not editable_observed:
        issues.append(StructuralIssue(IssueCode.WHOLE_ITEM_RASTERIZED, "image blocks without reader-visible text"))
    if expected.require_editable_text and not editable_observed:
        issues.append(StructuralIssue(IssueCode.MISSING_EDITABLE_TEXT, "kordoc/rhwp text evidence is absent"))
    has_bogi_box = _has_bogi_box(snapshot, expected.require_bogi_claims)
    if expected.require_bogi_box and not has_bogi_box:
        issues.append(StructuralIssue(IssueCode.MISSING_BOGI_BOX, "kordoc did not expose a 보기 table block"))
    if expected.require_bogi_claims and not all(marker in snapshot.text for marker in ("ㄱ", "ㄴ", "ㄷ")):
        issues.append(StructuralIssue(IssueCode.MISSING_BOGI_CLAIMS, "ㄱ/ㄴ/ㄷ claims are incomplete"))
    if expected.require_fifth_choice and "⑤" not in snapshot.text:
        issues.append(StructuralIssue(IssueCode.MISSING_FIFTH_CHOICE, "fifth choice marker is absent"))
    return tuple(issues)


def inspect_hwp(
    path: Path, expected: HwpExpectations, runner: CommandRunner | None = None,
) -> HwpReadbackReport:
    """Read HWP structure with both tools and return stable gate issues."""
    selected = SubprocessRunner() if runner is None else runner
    try:
        snapshot = _snapshot_hwp(path.resolve(), selected)
    except (CommandExecutionError, ReadbackParseError, ValidationError) as exc:
        return HwpReadbackReport(None, (StructuralIssue(IssueCode.UNREADABLE_HWP, str(exc)),))
    return HwpReadbackReport(snapshot, _hwp_issues(snapshot, expected))


def inspect_pdf(path: Path, expected: PdfExpectations) -> PdfReadbackReport:
    """Read PDF page/text observations and compare them with typed expectations."""
    try:
        with fitz.open(path.resolve()) as document:
            snapshot = PdfSnapshot(document.page_count, tuple(page.get_text() for page in document))
    except (fitz.FileDataError, OSError) as exc:
        return PdfReadbackReport(None, (StructuralIssue(IssueCode.UNREADABLE_PDF, str(exc)),))
    issues: list[StructuralIssue] = []
    if snapshot.page_count != expected.page_count:
        issues.append(StructuralIssue(IssueCode.PDF_PAGE_COUNT_MISMATCH, "page count differs"))
    combined = "\n".join(snapshot.page_text)
    if not all(fragment in combined for fragment in expected.required_text):
        issues.append(StructuralIssue(IssueCode.MISSING_PDF_TEXT, "required PDF text is absent"))
    return PdfReadbackReport(snapshot, tuple(issues))
