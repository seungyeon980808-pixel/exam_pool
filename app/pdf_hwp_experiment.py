"""Recover experiment-result tables as editable HwpPalette structures."""
from __future__ import annotations

from dataclasses import dataclass
import re

import fitz
from pymupdf.table import Table

from .pdf_hwp_equation_layout import join_rows, rows
from .pdf_hwp_equation_types import EquationWord
from .pdf_hwp_pipeline_models import BoundingBox, DetectedItem


_EXPERIMENT_MARKER = re.compile(r"[\[<]실험\s*과정(?:\s*및\s*결과)?[\]>]")
_EXPERIMENT_SECTION = re.compile(
    r"\[(?:자료(?:\s*조사\s*내용)?|실험\s*(?:과정|결과))\]",
)
_PALETTE_TABLE = re.compile(r"^\\표(?P<rows>\d+)\*(?P<cols>\d+)\\\s*$")


@dataclass(frozen=True, slots=True)
class ExperimentTable:
    bbox: BoundingBox
    row_count: int
    column_count: int
    grid: tuple[tuple[str, ...], ...]

    def palette_markup(self) -> str:
        table = f"\\표{self.row_count}*{self.column_count}\\"
        body = (
            "&".join(cell.replace("&", "&&") if cell else "-" for cell in row)
            for row in self.grid
        )
        return "\n".join((table, *body))


@dataclass(frozen=True, slots=True)
class ExperimentPassageParts:
    """Typed registered-template fields recovered from one experiment passage."""

    intro: str
    body: str
    result_tables: tuple[str, ...]

    @property
    def result_markup(self) -> str:
        return "\n".join(self.result_tables)


def split_experiment_passage(passage: str) -> ExperimentPassageParts:
    """Separate an outer source box from editable tables contained by that box.

    PDF recovery represents the source border as a 1x1 table whose single cell
    contains the experiment prose.  That border is already supplied by the HWP
    experiment template, so serializing it beside the real result table creates
    a second nested table and moves later slots out of their parent flow.
    """
    lines = str(passage or "").strip().splitlines()
    prose: list[str] = []
    result_tables: list[str] = []
    boxed_body: str | None = None
    index = 0
    while index < len(lines):
        match = _PALETTE_TABLE.fullmatch(lines[index].strip())
        if match is None:
            prose.append(lines[index])
            index += 1
            continue
        row_count = int(match.group("rows"))
        column_count = int(match.group("cols"))
        end = min(index + row_count + 1, len(lines))
        markup = "\n".join(lines[index:end]).strip()
        rows = lines[index + 1:end]
        if (
            row_count == 1
            and column_count == 1
            and len(rows) == 1
            and _EXPERIMENT_SECTION.search(rows[0]) is not None
        ):
            boxed_body = rows[0].replace("&&", "&").strip()
        else:
            result_tables.append(markup)
        index = end

    section_at = next(
        (index for index, line in enumerate(prose) if _EXPERIMENT_SECTION.search(line)),
        len(prose),
    )
    intro = "\n".join(prose[:section_at]).strip()
    body = boxed_body or "\n".join(prose[section_at:]).strip()
    return ExperimentPassageParts(intro, body, tuple(result_tables))


def is_experiment_text(text: str) -> bool:
    return _EXPERIMENT_MARKER.search(text) is not None


def has_editable_experiment_region(
    source_text: str,
    embedded_image_bbox: BoundingBox | None,
    tables: tuple[ExperimentTable, ...],
) -> bool:
    """Return whether inferred whitespace is owned by editable experiment data.

    A ruled result table plus native PDF text is already a complete structural
    representation of an experiment box.  In the absence of a real embedded
    image, treating the surrounding whitespace as a figure duplicates all of
    that prose as a raster crop and removes the table's cell words.
    """
    return (
        is_experiment_text(source_text)
        and embedded_image_bbox is None
        and bool(tables)
    )


def recover_experiment_tables(
    page: fitz.Page,
    item: DetectedItem,
    words: list[EquationWord],
    choice_start: float,
) -> tuple[ExperimentTable, ...]:
    """Read ruled PDF tables inside an experiment before the answer choices."""
    if not is_experiment_text(item.source_text):
        return ()
    return recover_ruled_tables(page, item, words, choice_start)


def recover_ruled_tables(
    page: fitz.Page,
    item: DetectedItem,
    words: list[EquationWord],
    choice_start: float,
) -> tuple[ExperimentTable, ...]:
    """Read meaningful ruled data tables before the answer choices.

    One-cell borders are commonly prompt or <보기> decoration rather than data
    tables, so they are deliberately excluded.
    """
    candidates = page.find_tables(clip=fitz.Rect(item.bbox)).tables
    return tuple(
        _recover_table(table, words)
        for table in candidates
        if table.bbox[1] < choice_start
        and table.row_count > 0 and table.col_count > 0
        and (table.row_count > 1 or table.col_count > 1)
    )


def recover_bulleted_box(
    rows_in_box: list[list[EquationWord]],
) -> tuple[ExperimentTable, ...]:
    """Represent a separated two-bullet source box as one editable table cell."""
    visible = tuple(word for row in rows_in_box for word in row if not word.suppressed)
    bullet_rows = sum(
        1 for row in rows_in_box
        if (text := join_rows([row]).lstrip()).startswith(("◦", "○"))
    )
    if len(rows_in_box) < 2 or bullet_rows < 2:
        return ()
    bounds = fitz.Rect(visible[0].bbox)
    for word in visible[1:]:
        bounds |= fitz.Rect(word.bbox)
    return (ExperimentTable(
        tuple(float(value) for value in bounds), 1, 1,
        ((join_rows(rows_in_box),),),
    ),)


def without_table_words(
    words: list[EquationWord], tables: tuple[ExperimentTable, ...],
) -> list[EquationWord]:
    """Remove cell text from the prose stream so tables are not duplicated."""
    boxes = tuple(fitz.Rect(table.bbox) for table in tables)
    return [word for word in words if not any(_contains_word(box, word) for box in boxes)]


def insert_result_tables(passage: str, tables: tuple[ExperimentTable, ...]) -> str:
    """Place recovered tables directly after the experiment-result heading."""
    if not tables:
        return passage
    table_markup = "\n".join(table.palette_markup() for table in tables)
    result_marker = re.search(r"\[실험\s*결과\]", passage)
    if result_marker is None:
        return "\n".join((passage.rstrip(), table_markup))
    return "\n".join((
        passage[:result_marker.end()].rstrip(),
        table_markup,
        passage[result_marker.end():].strip(),
    )).strip()


def _recover_table(table: Table, words: list[EquationWord]) -> ExperimentTable:
    grid = tuple(
        tuple(_cell_text(cell, words) if cell is not None else "" for cell in row.cells)
        for row in table.rows
    )
    return ExperimentTable(
        bbox=tuple(float(value) for value in table.bbox),
        row_count=table.row_count,
        column_count=table.col_count,
        grid=grid,
    )


def _cell_text(cell: tuple[float, float, float, float], words: list[EquationWord]) -> str:
    box = fitz.Rect(cell)
    selected = [word for word in words if _contains_word(box, word)]
    return join_rows(rows(selected)) if selected else ""


def _contains_word(box: fitz.Rect, word: EquationWord) -> bool:
    bounds = fitz.Rect(word.bbox)
    return box.contains(fitz.Point((bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2))
