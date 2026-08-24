from __future__ import annotations

import json
from pathlib import Path

import fitz

from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import LayoutStyle
from app.pdf_hwp_raster_draft import _editable_variables
from app.pdf_hwp_raster_ocr import (
    RasterOcrWord,
    read_sidecar,
    recognize_raster_document,
    write_sidecar,
)


class _FakeOcr:
    def recognize(self, image_path: Path) -> tuple[RasterOcrWord, ...]:
        assert image_path.is_file()
        return (
            RasterOcrWord("6. 그림 (가)는 물체 A가 운동하는 모습을 나타낸 것이다.", (24, 20, 580, 48), 0.99),
            RasterOcrWord("이에 대한 설명으로 옳은 것만을", (24, 140, 390, 158), 0.98),
            RasterOcrWord("고른 것은?", (24, 161, 180, 179), 0.98),
            RasterOcrWord("① 1", (24, 205, 80, 230), 0.99),
            RasterOcrWord("② 2", (130, 205, 186, 230), 0.99),
            RasterOcrWord("③ 3", (236, 205, 292, 230), 0.99),
            RasterOcrWord("④ 4", (342, 205, 398, 230), 0.99),
            RasterOcrWord("⑤ 5", (448, 205, 504, 230), 0.99),
        )


class _SplitChoiceMarkerOcr:
    def recognize(self, image_path: Path) -> tuple[RasterOcrWord, ...]:
        assert image_path.is_file()
        return (
            RasterOcrWord("9. 물체 A와 B가 운동하는 모습을 나타낸 것이다.", (24, 20, 580, 48), 0.99),
            RasterOcrWord("B의 운동량은 A의 8배이다.", (24, 51, 220, 75), 0.99),
            RasterOcrWord("속도 v0 A와 충돌한 경우 B와 충돌한 경우", (80, 100, 580, 122), 0.97),
            RasterOcrWord("물체가 받은 평균 힘의 크기는?", (24, 150, 390, 178), 0.98),
            RasterOcrWord("ㄱ. t12 동안 속도는 v0이다.", (24, 180, 310, 198), 0.96),
            RasterOcrWord("① 160 N", (24, 205, 92, 230), 0.99),
            RasterOcrWord("② 240 N", (130, 205, 198, 230), 0.99),
            RasterOcrWord("3", (236, 205, 252, 230), 0.81),
            RasterOcrWord("320 N", (252, 205, 306, 230), 0.99),
            RasterOcrWord("④", (342, 205, 358, 230), 0.88),
            RasterOcrWord("360 N", (358, 205, 412, 230), 0.99),
            RasterOcrWord("5", (448, 205, 464, 230), 0.86),
            RasterOcrWord("400 N", (464, 205, 518, 230), 0.99),
        )


class _BogiChoiceGlyphOcr:
    def recognize(self, image_path: Path) -> tuple[RasterOcrWord, ...]:
        assert image_path.is_file()
        return (
            RasterOcrWord("8. 그림 (가)는 수레의 운동을 나타낸 것이다.", (24, 20, 580, 48), 0.99),
            RasterOcrWord("수레 운동 자료", (80, 90, 400, 115), 0.97),
            RasterOcrWord("이에 대한 설명으로 옳은 것만을 고른 것은?", (24, 135, 500, 158), 0.98),
            RasterOcrWord("<보기>", (250, 165, 320, 184), 0.99),
            RasterOcrWord("ㄱ. 첫 번째 설명", (24, 188, 240, 207), 0.98),
            RasterOcrWord("ㄴ. 두 번째 설명", (24, 211, 240, 230), 0.98),
            RasterOcrWord("ㄷ. 세 번째 설명", (24, 234, 240, 253), 0.98),
            RasterOcrWord("①7", (24, 270, 64, 292), 0.88),
            RasterOcrWord("② L", (130, 270, 174, 292), 0.86),
            RasterOcrWord("③ㄷ", (236, 270, 276, 292), 0.99),
            RasterOcrWord("④ 7, ㄴ", (342, 270, 410, 292), 0.90),
            RasterOcrWord("⑤ㄴ,ㄷ", (448, 270, 510, 292), 0.99),
        )


def test_raster_formula_rules_restore_compact_time_intervals_and_initial_velocity() -> None:
    # Given: OCR flattened subscripts and an interval separator into ordinary characters.
    source = "ㄱ. t12 동안 속도는 v0이고 변위는 v0t이다."

    # When: deterministic raster formula recovery runs.
    restored = _editable_variables(source)

    # Then: every mathematical run uses the canonical editable formula contract.
    assert restored == (
        "ㄱ. [[formula:t_{1}]]~[[formula:t_{2}]] 동안 속도는 "
        "[[formula:v_{0}]]이고 변위는 [[formula:v_{0}t]]이다."
    )


def test_raster_formula_rules_restore_explicit_intervals_and_relations() -> None:
    # Given: OCR retained the interval separator but removed subscript geometry.
    source = "t1~t2 동안, t=0에서 v1>v2이다."

    # When: deterministic raster formula recovery runs.
    restored = _editable_variables(source)

    # Then: intervals, relations, and standalone indexed variables are native-formula markup.
    assert restored == (
        "[[formula:t_{1}]]~[[formula:t_{2}]] 동안, "
        "[[formula:t=0]]에서 [[formula:v_{1}>v_{2}]]이다."
    )


def test_raster_document_keeps_source_pixels_and_maps_ocr_geometry(tmp_path: Path) -> None:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 640, 260), False)
    pixmap.clear_with(255)

    result = recognize_raster_document(
        pixmap.tobytes("png"), "png", engine=_FakeOcr(), image_path=tmp_path / "input.png",
    )

    with fitz.open(stream=result.pdf_bytes, filetype="pdf") as document:
        assert document.page_count == 1
        assert document[0].get_images()
        assert document[0].get_text() == ""
        assert result.words[0].text.startswith("6.")
        assert result.words[0].bbox[0] < 30
        assert result.words[0].bbox[1] < result.words[1].bbox[1]


def test_ocr_sidecar_round_trips_korean_text_and_boxes(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf placeholder")
    words = _FakeOcr().recognize(source)

    write_sidecar(source, words)

    assert read_sidecar(source) == words


def test_raster_draft_makes_text_editable_and_keeps_only_material_region_as_image(tmp_path: Path) -> None:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 640, 260), False)
    pixmap.clear_with(255)
    result = recognize_raster_document(
        pixmap.tobytes("png"), "png", engine=_FakeOcr(), image_path=tmp_path / "input.png",
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(result.pdf_bytes)
    write_sidecar(source, result.words)

    item = detect_items(source).items[0]
    draft = build_editable_draft(source, item, tmp_path / "crops", layout_style=LayoutStyle.SUNEUNG)

    assert "물체 A가 운동" in draft.palette_markdown
    assert "이에 대한 설명으로 옳은 것만을 고른 것은?" in draft.palette_markdown
    assert all(str(value) in draft.palette_markdown for value in range(1, 6))
    assert draft.figure_asset is not None
    assert draft.figure_asset.image_path != draft.source_image.image_path
    figure_metadata = json.loads(draft.figure_asset.provenance_path.read_text(encoding="utf-8"))
    assert figure_metadata["bbox"][3] <= 132
    assert "수능원문1대사진" not in draft.palette_markdown


def test_raster_draft_recovers_choice_markers_ocr_split_into_plain_digits(tmp_path: Path) -> None:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 640, 260), False)
    pixmap.clear_with(255)
    result = recognize_raster_document(
        pixmap.tobytes("png"), "png",
        engine=_SplitChoiceMarkerOcr(), image_path=tmp_path / "input.png",
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(result.pdf_bytes)
    write_sidecar(source, result.words)

    item = detect_items(source).items[0]
    draft = build_editable_draft(source, item, tmp_path / "crops", layout_style=LayoutStyle.SUNEUNG)

    assert draft.choice_texts == ("160 N", "240 N", "320 N", "360 N", "400 N")
    assert "B의 운동량은 A의 8배이다." in draft.source_text
    assert "속도 [[formula:v_{0}]] A와 충돌한 경우" not in draft.source_text
    assert draft.figure_asset is not None
    figure_metadata = json.loads(draft.figure_asset.provenance_path.read_text(encoding="utf-8"))
    ask_top = next(word.bbox[1] for word in result.words if "평균 힘" in word.text)
    assert figure_metadata["bbox"][3] <= ask_top - 8
    assert r"\수식{t_{1}}~\수식{t_{2}}" in draft.palette_markdown
    assert r"\수식{v_{0}}" in draft.palette_markdown
    assert "수능원문1대사진" not in draft.palette_markdown


def test_raster_draft_restores_confused_bogi_choice_glyphs(tmp_path: Path) -> None:
    # Given: OCR reads ㄱ as 7 and ㄴ as L in an otherwise proven ㄱ/ㄴ/ㄷ combination set.
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 640, 320), False)
    pixmap.clear_with(255)
    result = recognize_raster_document(
        pixmap.tobytes("png"), "png", engine=_BogiChoiceGlyphOcr(), image_path=tmp_path / "input.png",
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(result.pdf_bytes)
    write_sidecar(source, result.words)

    # When: the editable raster draft is built through the real semantic route.
    item = detect_items(source).items[0]
    draft = build_editable_draft(source, item, tmp_path / "crops", layout_style=LayoutStyle.SUNEUNG)

    # Then: only the proven combination glyphs are restored to editable Korean letters.
    assert draft.choice_texts == ("ㄱ", "ㄴ", "ㄷ", "ㄱ, ㄴ", "ㄴ, ㄷ")


def test_standalone_launcher_bootstraps_ocr_in_an_isolated_runtime() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "PDF-HWP 웹앱 실행.bat").read_text(
        encoding="utf-8",
    )

    assert "pip install --target" in launcher
    assert "PDF_HWP_OCR_RUNTIME" in launcher
    assert "set \"PYTHONPATH=" in launcher
