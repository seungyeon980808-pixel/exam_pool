#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["PyMuPDF>=1.28,<2"]
# ///

# ─── How to run ───
# 1. Install uv (if it is not installed):
#      powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
# 2. Run directly:
#      uv run tools/build_real_requested_folder_hwp_proof.py
# 3. ExamPool's current Windows runtime has no uv, so the verified fallback is:
#      python -B tools/build_real_requested_folder_hwp_proof.py
# ──────────────────

"""Prepare an exact requested-folder item for the additive no-photo HWP proof."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

import fitz


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from app import pdf_hwp_draft as draft_text  # noqa: E402
from app.export_palette import question_to_palette  # noqa: E402
from app.pdf_hwp_pipeline import build_editable_draft, detect_items  # noqa: E402
from app.pdf_hwp_pipeline_models import DetectedItem, FigureLayout  # noqa: E402
from app.pdf_hwp_question_structure import palette_question  # noqa: E402


SOURCE_ROOT = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일")
SOURCE_NAME = "p1_2026_09.pdf"
SOURCE_SHA256 = "61197f1bcc720aef13abd602f71a006d191ac5f48af6574cd43a43962c8485a6"
ITEM_NUMBER = 3
LABEL = "수능합답0사진5선지"
ASK_FRAME_ID = "ASK_BOGI_STANDARD"
ASK_FRAME = "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?"
DEFAULT_OUTPUT = (
    REPOSITORY
    / ".omo"
    / "evidence"
    / "pdf-hwp-generalization"
    / "additive-templates"
    / "external-root"
    / "runtime-input-real"
)


class ProofError(RuntimeError):
    """Raised when the fixed real-source proof contract drifts."""


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest().upper()


def _write_new(path: Path, value: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"stale evidence staging file: {temporary}")
    temporary.write_bytes(value)
    temporary.replace(path)


def _production_failure(source: Path, item: DetectedItem) -> str:
    with TemporaryDirectory(prefix="exampool-real-hwp-proof-") as raw:
        try:
            build_editable_draft(source, item, Path(raw) / "item-03")
        except ZeroDivisionError as error:
            return f"{type(error).__name__}: {error}"
    raise ProofError("the protected production path no longer reproduces the expected failure")


def _recover_text_fields(
    source: Path,
    item: DetectedItem,
) -> tuple[dict, tuple[str, ...]]:
    with fitz.open(source) as document:
        page = document[item.page_number - 1]
        decoder = draft_text._EquationDecoder(  # noqa: SLF001
            draft_text._pua_font_names(page, item.bbox)  # noqa: SLF001
        )
        words = draft_text._normalize_fractions(  # noqa: SLF001
            draft_text._page_words(page, item.bbox),  # noqa: SLF001
            decoder,
        )
        rows = draft_text._rows(words)  # noqa: SLF001
        choice_row = next(
            index
            for index, row in enumerate(rows)
            if any(
                any(marker in word.text for marker in draft_text._CIRCLED)  # noqa: SLF001
                for word in row
            )
        )
        question_row = next(
            index
            for index, row in enumerate(rows[:choice_row])
            if any("?" in word.text for word in row)
        )
        choice_start = next(
            word.bbox[1]
            for word in words
            if any(marker in word.text for marker in draft_text._CIRCLED)  # noqa: SLF001
        )
        choices = draft_text._choice_texts(words, choice_start)  # noqa: SLF001
        passage = re.sub(
            rf"^{item.item_number}\s*\.\s*",
            "",
            draft_text._join_rows(rows[:question_row]),  # noqa: SLF001
        )
        ask = draft_text._join_rows(rows[question_row:choice_row])  # noqa: SLF001

    if decoder.unknown:
        raise ProofError(f"unverified equation glyphs: {sorted(decoder.unknown)}")
    if len(choices) != 5 or any(not choice for choice in choices):
        raise ProofError(f"expected five non-empty text choices, got {choices!r}")
    question = palette_question(passage, ask, "", FigureLayout.ONE_SMALL)
    if question.get("qtype") != "합답형" or question.get("ask") != ASK_FRAME:
        raise ProofError(f"unexpected question frame: {question!r}")
    if len(question.get("bogi_items", ())) != 3:
        raise ProofError(f"expected three bogi claims: {question!r}")
    return question, choices


def _no_photo_markdown(question: dict, choices: tuple[str, ...]) -> str:
    choice_rows = [
        {"ord": index + 1, "text": text}
        for index, text in enumerate(choices)
    ]
    photo_markdown = question_to_palette(
        question,
        choice_rows,
        num=ITEM_NUMBER,
        layout_style="suneung",
    )
    lines = photo_markdown.splitlines()
    if len(lines) != 13 or lines[3] != "-":
        raise ProofError(f"unexpected one-photo hapdap slot plan: {lines!r}")
    lines[0] = f"\\{LABEL}\\"
    del lines[3]
    if len(lines) != 12:
        raise ProofError(f"expected label plus 11 values, got {len(lines)} lines")
    markdown = "\n".join(lines) + "\n"
    if re.search(r"[\ue000-\uf8ff]", markdown):
        raise ProofError("private-use characters remain in real-source markdown")
    if "page-" in markdown or "사진" in "\n".join(lines[1:]):
        raise ProofError("the no-photo value slots contain an asset token")
    return markdown


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source = (SOURCE_ROOT / SOURCE_NAME).resolve()
    if source.parent != SOURCE_ROOT.resolve() or not source.is_file():
        raise FileNotFoundError(source)
    detection = detect_items(source)
    if detection.source_hash != SOURCE_SHA256:
        raise ProofError(
            f"source hash drift: expected {SOURCE_SHA256}, got {detection.source_hash}"
        )
    item = next(value for value in detection.items if value.item_number == ITEM_NUMBER)
    production_failure = _production_failure(source, item)
    question, choices = _recover_text_fields(source, item)
    markdown = _no_photo_markdown(question, choices)
    markdown_bytes = markdown.encode("utf-8")

    output_dir = args.output_dir.resolve()
    markdown_path = output_dir / "p1_2026_09_item_03.md"
    provenance_path = output_dir / "p1_2026_09_item_03.provenance.json"
    provenance = {
        "schema_version": 1,
        "source_root": str(SOURCE_ROOT.resolve()),
        "source_path": str(source),
        "source_sha256": detection.source_hash,
        "source_page_count": detection.page_count,
        "item_number": item.item_number,
        "page_number": item.page_number,
        "detected_bbox": list(item.bbox),
        "production_build_result": production_failure,
        "recovery": "production text/formula/choice primitives with the false figure slot omitted",
        "frame_id": ASK_FRAME_ID,
        "frame_text": question["ask"],
        "passage": question["passage"],
        "bogi_items": question["bogi_items"],
        "choice_texts": list(choices),
        "template_label": LABEL,
        "slot_count": len(markdown.splitlines()) - 1,
        "markdown_path": str(markdown_path),
        "markdown_sha256": _sha256_bytes(markdown_bytes),
        "pdf_generation_invoked": False,
        "hwp_generation_invoked": False,
    }
    provenance_bytes = (
        json.dumps(provenance, ensure_ascii=True, indent=2) + "\n"
    ).encode("utf-8")
    _write_new(markdown_path, markdown_bytes)
    try:
        _write_new(provenance_path, provenance_bytes)
    except OSError:
        markdown_path.unlink(missing_ok=True)
        raise
    print(json.dumps(provenance, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
