"""OCR sidecar support for pasted raster questions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import json
import os
from io import BytesIO
from pathlib import Path
from typing import Protocol

import fitz
from PIL import Image


class RasterOcrError(RuntimeError):
    """The OCR runtime or raster source could not produce an editable document."""


@dataclass(frozen=True, slots=True)
class RasterOcrWord:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float


@dataclass(frozen=True, slots=True)
class RasterOcrDocument:
    pdf_bytes: bytes
    words: tuple[RasterOcrWord, ...]


class RasterOcrEngine(Protocol):
    def recognize(self, image_path: Path) -> tuple[RasterOcrWord, ...]: ...


class PaddleKoreanOcr:
    """Lazy PP-OCRv5 adapter; normal PDF jobs never load the OCR runtime."""

    def recognize(self, image_path: Path) -> tuple[RasterOcrWord, ...]:
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        os.environ.setdefault(
            "PADDLE_PDX_CACHE_HOME",
            str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ExamPool" / "paddle-cache"),
        )
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RasterOcrError(
                f"이미지 문항 OCR 구성 요소를 불러오지 못했습니다: {exc}"
            ) from exc
        result = next(iter(_paddle_pipeline(PaddleOCR).predict(str(image_path))))
        payload = result.json["res"]
        return tuple(
            RasterOcrWord(
                str(text).strip(),
                _polygon_bbox(tuple(float(value) for point in polygon for value in point)),
                float(score),
            )
            for text, score, polygon in zip(
                payload["rec_texts"], payload["rec_scores"], payload["rec_polys"], strict=True,
            )
            if str(text).strip() and float(score) >= 0.45
        )


@lru_cache(maxsize=1)
def _paddle_pipeline(factory):
    return factory(
        lang="korean",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
    )


def _polygon_bbox(values: tuple[float, ...]) -> tuple[float, float, float, float]:
    xs = values[0::2]
    ys = values[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def recognize_raster_document(
    payload: bytes,
    extension: str,
    *,
    image_path: Path,
    engine: RasterOcrEngine | None = None,
) -> RasterOcrDocument:
    """Wrap source pixels in PDF and map OCR pixel boxes into PDF coordinates."""
    filetype = extension.lower().lstrip(".")
    if filetype == "jpg":
        filetype = "jpeg"
    try:
        with Image.open(BytesIO(payload)) as source_image:
            width_px, height_px = source_image.size
        with fitz.open(stream=payload, filetype=filetype) as image_document:
            raster_pdf = image_document.convert_to_pdf()
    except (fitz.FileDataError, RuntimeError, ValueError) as exc:
        raise RasterOcrError("이미지 문항을 읽을 수 없습니다.") from exc
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(payload)
    try:
        pixel_words = (engine or PaddleKoreanOcr()).recognize(image_path)
    finally:
        image_path.unlink(missing_ok=True)
    if not pixel_words:
        raise RasterOcrError("이미지에서 편집 가능한 문항 텍스트를 찾지 못했습니다.")
    with fitz.open(stream=raster_pdf, filetype="pdf") as document:
        page = document[0]
        scale_x = page.rect.width / width_px
        scale_y = page.rect.height / height_px
    words = tuple(
        RasterOcrWord(
            word.text,
            (
                word.bbox[0] * scale_x, word.bbox[1] * scale_y,
                word.bbox[2] * scale_x, word.bbox[3] * scale_y,
            ),
            word.confidence,
        )
        for word in pixel_words
    )
    return RasterOcrDocument(raster_pdf, words)


def sidecar_path(source_pdf: Path) -> Path:
    return source_pdf.with_suffix(".ocr.json")


def write_sidecar(source_pdf: Path, words: tuple[RasterOcrWord, ...]) -> None:
    sidecar_path(source_pdf).write_text(
        json.dumps({"version": 1, "words": [asdict(word) for word in words]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_sidecar(source_pdf: Path) -> tuple[RasterOcrWord, ...]:
    path = sidecar_path(source_pdf)
    if not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        RasterOcrWord(str(word["text"]), tuple(word["bbox"]), float(word["confidence"]))
        for word in payload.get("words", ())
    )
