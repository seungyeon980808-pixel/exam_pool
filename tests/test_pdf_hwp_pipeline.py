from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import fitz
from PIL import Image
import pytest

import app.pdf_hwp_draft as draft_extractor
import app.pdf_hwp_pipeline as pdf_pipeline
from app.pdf_hwp_crop_assets import crop_region
from app.pdf_hwp_equation_font import EquationFontContext
from app.formula_markup import to_hwppalette_markup

from app.pdf_hwp_final_figure_contract import FinalFigureContract, reconcile_final_figure_contract
from app.pdf_hwp_hwp_preflight import preflight_unit
from app.pdf_hwp_roundtrip_crop_audit import (
    CropAuditIssue,
    CropSourceRequest,
    audit_crop_geometry,
    read_crop_geometry,
)
from app.pdf_hwp_pipeline_models import (
    FigureAsset,
    FigureAssetMetadata,
    FigureArrangement,
    GraphicalChoiceAsset,
    GraphicalChoiceAssetMetadata,
)
from app.pdf_hwp_pipeline import (
    ConversionRequest,
    ConversionUnit,
    CropArtifact,
    EmptyConversionError,
    GeneratedDocument,
    LayoutStyle,
    PipelinePhase,
    crop_item,
    detect_items,
    typeset_conversion,
    build_editable_draft,
    UnsupportedDraftLayoutError,
    ConversionTypesetError,
    _typeset_timeout_seconds,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p1_2024_11.pdf")
LOCAL_PDF = ROOT / "PDF"
EBS_PHYSICS = Path(r"C:\Users\user\Desktop\project\31_hwp_palette\2027 수능특강 물리학 I 원본.pdf")


def _exam_pdf(name: str) -> Path:
    for candidate in (Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일") / name, LOCAL_PDF / name):
        if candidate.is_file():
            return candidate
    pytest.skip(f"missing exam PDF {name}")


class _RecordingTypesetter:
    def __init__(self) -> None:
        self.markdown = ""
        self.asset_dirs: tuple[Path, ...] = ()

    def typeset(
        self,
        markdown: str,
        output_dir: Path,
        layout_style: LayoutStyle,
        asset_dirs: tuple[Path, ...],
    ) -> GeneratedDocument:
        self.markdown = markdown
        self.asset_dirs = asset_dirs
        hwp_path = output_dir / "converted.hwp"
        pdf_path = output_dir / "converted.pdf"
        render_path = output_dir / "page-1.png"
        hwp_path.write_bytes(b"HWP fixture")
        document = fitz.open()
        document.new_page().insert_text((72, 72), "converted")
        document.save(pdf_path)
        document.close()
        with fitz.open(pdf_path) as rendered:
            rendered[0].get_pixmap(alpha=False).save(render_path)
        return GeneratedDocument(hwp_path=hwp_path, pdf_path=pdf_path, rendered_pages=(render_path,))


class _TransientHwpTypesetter(_RecordingTypesetter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def typeset(
        self,
        markdown: str,
        output_dir: Path,
        layout_style: LayoutStyle,
        asset_dirs: tuple[Path, ...],
    ) -> GeneratedDocument:
        self.calls += 1
        if self.calls == 1:
            raise ConversionTypesetError(
                detail="pywintypes.com_error: (-2147417851, 'server exception')",
            )
        return super().typeset(markdown, output_dir, layout_style, asset_dirs)


def test_real_typeset_timeout_scales_for_multi_item_documents() -> None:
    single = "\\수능합답1대사진5선지\\\n1\nstem"
    batch = "\n\n".join(single.replace("\n1\n", f"\n{number}\n") for number in range(1, 20))

    assert _typeset_timeout_seconds(single) == 90
    assert _typeset_timeout_seconds(batch) >= 1200


def test_detect_items_finds_real_item20_with_stable_source_identity() -> None:
    # Given: the original four-page 2024 Physics I source PDF.
    expected_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()

    # When: the generic pipeline detects question regions.
    result = detect_items(SOURCE)

    # Then: all twenty numbered questions and the real item-20 locator are preserved.
    assert result.page_count == 4
    assert result.source_hash == expected_hash
    assert len(result.items) == 20
    item20 = next(item for item in result.items if item.item_number == 20)
    assert item20.page_number == 4
    assert item20.bbox == (428.56, 572.745, 834.0, 1003.069)
    assert "20." in item20.source_text
    assert "확인 사항" not in item20.source_text
    assert "저작권" not in item20.source_text


def test_detect_items_keeps_choices_below_large_last_item_gap(tmp_path: Path) -> None:
    # Given: a last-in-column item has a large diagram gap before its question and choices.
    source = tmp_path / "last-item-with-large-gap.pdf"
    font = "C:/Windows/Fonts/malgun.ttf"
    with fitz.open() as document:
        page = document.new_page(width=420, height=1000)
        page.insert_text((50, 100), "7.", fontsize=12)
        page.insert_text((80, 130), "STEM", fontsize=12)
        page.insert_text((80, 160), "STEM CONTINUED", fontsize=12)
        page.insert_text((80, 190), "STEM CONTINUED AGAIN", fontsize=12)
        page.insert_text(
            (80, 800), "저항값의 비는?", fontsize=12,
            fontname="malgun", fontfile=font,
        )
        page.insert_text(
            (80, 850), "① 1:2  ② 1:4  ③ 2:1  ④ 2:3  ⑤ 4:1", fontsize=12,
            fontname="malgun", fontfile=font,
        )
        document.save(source)

    # When: the generic detector considers trimming trailing page matter.
    detected = detect_items(source)

    # Then: answer choices below the gap remain part of the item.
    assert len(detected.items) == 1
    assert "⑤" in detected.items[0].source_text
    assert detected.items[0].bbox[3] > 850


@pytest.mark.parametrize("item_number", [11, 14])
def test_old_exam_grid_vector_choices_crop_five_cells(
    tmp_path: Path, item_number: int,
) -> None:
    # Given: 2008 Physics uses 3+2 or 2+2+1 vector-graph choice grids.
    source = _exam_pdf("p1_2008_11.pdf")
    item = next(
        value for value in detect_items(source).items
        if value.item_number == item_number
    )

    # When: the editable draft extracts graphical choices.
    draft = build_editable_draft(source, item, tmp_path / f"q{item_number}")

    # Then: every numbered graph is preserved as one ordered choice asset.
    assert len(draft.graphical_choice_assets) == 5
    assert all(asset.width_px > 200 for asset in draft.graphical_choice_assets)
    assert all(asset.height_px > 150 for asset in draft.graphical_choice_assets)


def test_inline_image_without_xref_renders_from_pdf_coordinates(tmp_path: Path) -> None:
    # Given: a 2013 source stores item 12's prompt as an inline image with xref 0.
    source = _exam_pdf("p1_2013_11.pdf")
    item = next(
        value for value in detect_items(source).items
        if value.item_number == 12
    )

    # When: the draft extracts the prompt figure.
    draft = build_editable_draft(source, item, tmp_path / "q12")

    # Then: coordinate rendering preserves the figure without an invalid-xref crash.
    assert len(draft.figure_assets) >= 1
    assert draft.figure_assets[0].width_px > 200
    assert draft.figure_assets[0].height_px > 100


def test_ebs_textbook_detects_large_two_digit_question_markers_only() -> None:
    # Given: the EBS textbook resets two-digit question numbers by test section.
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS Physics source")

    # When: the generic source detector recognizes the textbook layout.
    detected = detect_items(EBS_PHYSICS)
    by_page = {
        page: [item.item_number for item in detected.items if item.page_number == page]
        for page in (13, 14)
    }

    # Then: visible large 01–08 markers are questions, not graph ticks or page numbers.
    assert by_page == {13: [1, 2, 3, 4], 14: [5, 6, 7, 8]}
    assert len(detected.items) == 278
    assert [item.item_number for item in detected.items] == list(range(1, 279))
    assert all(item.page_number < 193 for item in detected.items)
    last = detected.items[-1]
    with fitz.open(EBS_PHYSICS) as document:
        assert last.bbox[3] <= round(document[last.page_number - 1].rect.height - 48, 3)


def test_excessive_page_vectors_use_source_preserving_fallback() -> None:
    source = _exam_pdf("c2_2013_11.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 2)

    assert pdf_pipeline._requires_exact_source_fallback(source, item) is True


def test_dense_ebs_vector_page_remains_editable() -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(EBS_PHYSICS).items
        if value.item_number == 219
    )

    assert pdf_pipeline._requires_exact_source_fallback(EBS_PHYSICS, item) is False


def test_ebs_mixed_raster_vector_prompt_keeps_complete_editable_figure(
    tmp_path: Path,
) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(EBS_PHYSICS).items
        if value.item_number == 206
    )

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path)
    contract = reconcile_final_figure_contract(
        item.item_number, draft.palette_markdown, draft.figure_assets,
    )

    assert len(draft.choice_texts) == 5
    assert isinstance(contract, FinalFigureContract)
    assert draft.figure_assets[0].width_px > draft.figure_assets[0].height_px * 3
    assert draft.figure_assets[0].height_px > 100


def test_ebs_wrapped_prose_preserves_source_line_reading_order(
    tmp_path: Path,
) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(EBS_PHYSICS).items
        if value.item_number == 1
    )

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path)

    assert (
        "물체 [[formula:A]], 포물선 운동 하는 물체 [[formula:B]], "
        "일정한 속력으로 원운동 하는 물체 [[formula:C]]의 운동을 각각 "
        "일정한 시간 간격으로 나타낸 것이다."
    ) in draft.source_text


def test_ebs_wide_mixed_diagram_is_not_cropped_to_a_horizontal_strip(
    tmp_path: Path,
) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(EBS_PHYSICS).items
        if value.item_number == 96
    )

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path)

    assert len(draft.figure_assets) == 1
    assert draft.figure_assets[0].height_px > 250


@pytest.mark.parametrize(
    ("item_number", "ordered_statement"),
    [
        (206, r"ㄴ. 파동의 진행 속력은 [[formula:\frac{1}{2}fA]]이다."),
        (249, r"ㄱ. [[formula:\lambda=\frac{4}{3}x_0]]이다."),
    ],
)
def test_ebs_statement_formula_stays_with_its_source_marker(
    tmp_path: Path,
    item_number: int,
    ordered_statement: str,
) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(EBS_PHYSICS).items
        if value.item_number == item_number
    )

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path)

    assert ordered_statement in draft.source_text


@pytest.mark.parametrize("item_number", [3, 184, 232])
def test_ebs_proven_prompt_boundary_rasterizes_as_one_separate_figure(
    tmp_path: Path,
    item_number: int,
) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(EBS_PHYSICS).items
        if value.item_number == item_number
    )

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path)
    contract = reconcile_final_figure_contract(
        item.item_number, draft.palette_markdown, draft.figure_assets,
    )

    assert len(draft.choice_texts) == 5
    assert isinstance(contract, FinalFigureContract)


@pytest.mark.parametrize("item_number", [146, 215])
def test_ebs_vector_only_prompt_emits_a_nonblank_separate_figure(
    tmp_path: Path,
    item_number: int,
) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(EBS_PHYSICS).items
        if value.item_number == item_number
    )

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path)
    contract = reconcile_final_figure_contract(
        item.item_number, draft.palette_markdown, draft.figure_assets,
    )

    assert len(draft.choice_texts) == 5
    assert len(draft.figure_assets) == 1
    assert draft.figure_assets[0].height_px > 100
    assert isinstance(contract, FinalFigureContract)


def test_previously_exhausted_iterator_source_now_builds_editable_draft(
    tmp_path: Path,
) -> None:
    source = _exam_pdf("b1_2013_11.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 9)

    draft = build_editable_draft(source, item, tmp_path)

    assert draft.item_number == 9
    assert len(draft.choice_texts) == 5
    assert len(draft.figure_assets) == 1
    assert draft.source_image.image_path.is_file()


def test_crop_item_writes_high_resolution_png_and_complete_provenance(tmp_path: Path) -> None:
    # Given: the detected bounding box of real item 20.
    detection = detect_items(SOURCE)
    item20 = next(item for item in detection.items if item.item_number == 20)

    # When: the pipeline extracts the item at print-quality resolution.
    artifact = crop_item(SOURCE, item20, tmp_path, dpi=300)

    # Then: pixels and sidecar identify exactly what was cropped from which source.
    with Image.open(artifact.image_path) as image:
        assert image.format == "PNG"
        assert image.width >= 1600
    assert image.height >= 1750
    provenance = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))
    assert provenance["asset_mode"] == "pdf_item_crop_hd"
    assert provenance["source_hash"] == detection.source_hash
    assert provenance["page_number"] == 4
    assert provenance["item_number"] == 20
    assert provenance["bbox"] == [428.56, 572.745, 834.0, 1003.069]
    assert provenance["dpi"] == 300
    assert provenance["asset_hash"] == hashlib.sha256(artifact.image_path.read_bytes()).hexdigest()


def test_typeset_conversion_combines_units_and_reports_durable_outputs(tmp_path: Path) -> None:
    # Given: two already-parsed palette units and a recording progress sink.
    typesetter = _RecordingTypesetter()
    phases: list[PipelinePhase] = []
    request = ConversionRequest(
        job_key="physics-2024",
        units=(
            ConversionUnit(item_number=19, palette_markdown="\\direct\\\n19\nquestion 19"),
            ConversionUnit(item_number=20, palette_markdown="\\direct\\\n20\nquestion 20"),
        ),
        output_dir=tmp_path,
        layout_style=LayoutStyle.SUNEUNG,
        asset_dirs=(tmp_path / "crops",),
    )

    # When: the DB-agnostic orchestrator typesets the conversion.
    result = typeset_conversion(request, typesetter=typesetter, progress=lambda event: phases.append(event.phase))

    # Then: ordering, progress, outputs, and the manifest are observable and stable.
    assert typesetter.markdown.index("question 19") < typesetter.markdown.index("question 20")
    assert typesetter.asset_dirs == (tmp_path / "crops",)
    assert phases == [PipelinePhase.PREPARING, PipelinePhase.TYPESETTING, PipelinePhase.COMPLETE]
    assert result.hwp_path.is_file()
    assert result.pdf_path.is_file()
    assert result.rendered_pages[0].is_file()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["job_key"] == "physics-2024"
    assert manifest["item_numbers"] == [19, 20]
    assert manifest["layout_style"] == "suneung"
    assert manifest["hwp_sha256"] == hashlib.sha256(result.hwp_path.read_bytes()).hexdigest()
    assert manifest["pdf_sha256"] == hashlib.sha256(result.pdf_path.read_bytes()).hexdigest()


def test_typeset_conversion_restarts_hwp_once_after_transient_com_server_fault(
    tmp_path: Path,
) -> None:
    # Given: HWP faults once while applying a paragraph shape, then accepts a fresh process.
    typesetter = _TransientHwpTypesetter()
    request = ConversionRequest(
        job_key="transient-hwp",
        units=(ConversionUnit(item_number=1, palette_markdown="\\direct\\\n1\nquestion"),),
        output_dir=tmp_path,
        layout_style=LayoutStyle.SUNEUNG,
    )

    # When: the conversion pipeline typesets the document.
    result = typeset_conversion(request, typesetter=typesetter)

    # Then: the transient HWP process is replaced once and the file remains downloadable.
    assert typesetter.calls == 2
    assert result.hwp_path.is_file()


def test_typeset_conversion_rejects_empty_job_before_hwp_automation(tmp_path: Path) -> None:
    # Given: a conversion job without any selected items.
    typesetter = _RecordingTypesetter()
    request = ConversionRequest(
        job_key="empty",
        units=(),
        output_dir=tmp_path,
        layout_style=LayoutStyle.SUNEUNG,
    )

    # When/Then: the pipeline reports a typed boundary error without launching HWP.
    with pytest.raises(EmptyConversionError):
        typeset_conversion(request, typesetter=typesetter)
    assert typesetter.markdown == ""


def test_build_editable_draft_decodes_real_item20_without_handcrafted_seed(tmp_path: Path) -> None:
    # Given: only the original PDF and generic item-20 detection result.
    item20 = next(item for item in detect_items(SOURCE).items if item.item_number == 20)

    # When: the pipeline builds an editable HwpPalette draft deterministically.
    draft = build_editable_draft(SOURCE, item20, tmp_path)

    # Then: source wording, formulas, choices, and a figure-only asset are recovered.
    assert "질량이 [[formula:m]]인 물체 A" in draft.source_text
    assert "높이 [[formula:9h]]인 지점" in draft.source_text
    assert "[[formula:\\frac{7}{2}h]]인 지점" in draft.source_text
    assert draft.choice_texts == (
        "[[formula:\\frac{5}{17}h]]",
        "[[formula:\\frac{7}{17}h]]",
        "[[formula:\\frac{9}{17}h]]",
        "[[formula:\\frac{11}{17}h]]",
        "[[formula:\\frac{13}{17}h]]",
    )
    assert len(draft.figure_assets) == 2
    assert all(asset.image_path.is_file() for asset in draft.figure_assets)
    assert draft.source_image.image_path.is_file()
    assert all(asset.image_path.stem in draft.palette_markdown for asset in draft.figure_assets)
    assert "20" in draft.palette_markdown
    assert draft.palette_markdown.startswith("\\수능정답2대사진5선지\\\n")
    assert "\n-\n-\n-\n" not in draft.palette_markdown


def test_build_editable_draft_separates_real_bogi_claims_from_the_ask(tmp_path: Path) -> None:
    # Given: item 1 has a question sentence followed by a three-claim <보기> block.
    item1 = next(item for item in detect_items(SOURCE).items if item.item_number == 1)

    # When: the source item becomes editable palette markup.
    draft = build_editable_draft(SOURCE, item1, tmp_path)

    # Then: the ask and the three claims occupy their own template slots.
    lines = draft.palette_markdown.splitlines()
    assert lines[0] == "\\수능합답1대사진5선지\\"
    assert lines[4] == "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?"
    assert lines[5:8] == [
        "㉠은 가시광선 영역에 해당한다.",
        "진공에서 속력은 ㉠이 ㉡보다 크다.",
        "진공에서 파장은 ㉡이 ㉢보다 짧다.",
    ]


def test_build_editable_draft_never_leaks_pdf_private_use_characters(tmp_path: Path) -> None:
    # Given: the real item whose HYHWPEQ text layer exposes formulas as PUA glyphs.
    item20 = next(item for item in detect_items(SOURCE).items if item.item_number == 20)

    # When: an editable draft is normalized.
    draft = build_editable_draft(SOURCE, item20, tmp_path)

    # Then: no private-use code point survives any machine-consumed draft text.
    combined = "\n".join((draft.source_text, *draft.choice_texts, draft.palette_markdown))
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in combined)


def test_build_editable_draft_decodes_verified_real_equation_glyphs_without_nearest_guess(
    tmp_path: Path,
) -> None:
    # Given: real items containing decimal points, axis variables, and division signs
    # encoded as private-use HyhwpEQ glyphs in the source text layer.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: the source items become editable drafts.
    drafts = {
        number: build_editable_draft(SOURCE, items[number], tmp_path / f"item-{number}")
        for number in (6, 7, 11)
    }

    # Then: only verified glyph meanings reach machine-consumed formula markup.
    assert "[[formula:0.25]]Hz" in drafts[6].source_text
    assert "[[formula:t=0]]" in drafts[6].source_text
    assert "[[formula:x=5]]m" in drafts[6].source_text
    assert "[[formula:0.4]]초" in drafts[7].source_text
    assert "[[formula:10]]kg․m[[formula:/]]s" in drafts[7].source_text
    assert "[[formula:0.25]]" in drafts[11].source_text
    combined = "\n".join(draft.source_text for draft in drafts.values())
    assert all(token not in combined for token in ("0i25", "0i4", "[[formula:[]]", "[[formula:l]]"))


def test_build_editable_draft_recovers_prompt_table_and_formulas_without_rasterizing(
    tmp_path: Path,
) -> None:
    # Given: item 2, whose reactions and data table have a trustworthy text layer.
    item2 = next(item for item in detect_items(SOURCE).items if item.item_number == 2)

    # When: the prompt material is rebuilt as native table/formula markup.
    draft = build_editable_draft(SOURCE, item2, tmp_path)

    # Then: every semantic value remains editable and no redundant raster is emitted.
    assert "MeV" in draft.source_text
    assert "3i27" not in draft.source_text
    assert "17i6" not in draft.source_text
    assert "[[formula:+3.27]]" in draft.source_text
    assert "[[formula:+17.6]]" in draft.source_text
    assert "두 가지 핵반응" in draft.source_text
    assert "\\표4*2\\" in draft.palette_markdown
    assert draft.figure_assets == ()


def test_real_prompt_figure_adjacency_preserves_complete_source_sentences(tmp_path: Path) -> None:
    # Given: real source prose that touches an ordinary figure's padded detection box.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: the affected items become editable drafts.
    drafts = {
        number: build_editable_draft(SOURCE, items[number], tmp_path / f"item-{number}")
        for number in (3, 5, 11, 14, 16)
    }

    # Then: adjacency never deletes prose that is not rasterized into a proven mixed region.
    expected = {
        3: "순서 없이 나타낸 것이다.",
        5: "실선과 점선은 각각 마루와 골이다.",
        11: "받은 일은 [[formula:100]]J이다.",
        14: "sin[[formula:i]] 값에 따라 나타낸다.",
        16: "고른",
    }
    for number, sentence in expected.items():
        assert sentence in drafts[number].source_text
        assert to_hwppalette_markup(sentence) in drafts[number].palette_markdown


def test_real_stacked_equations_use_fraction_geometry_and_verified_glyph_meanings(
    tmp_path: Path,
) -> None:
    # Given: real HyhwpEQ equations whose fraction bars and nearby glyphs share PDF words.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: the source geometry is reconstructed into editable formula markup.
    drafts = {
        number: build_editable_draft(SOURCE, items[number], tmp_path / f"item-{number}")
        for number in (5, 10, 16, 18)
    }

    # Then: numerator/denominator position and verified codepoints preserve the equations.
    assert "[[formula:\\frac{3}{2}m/s]]" in drafts[5].source_text
    assert "[[formula:5mg]]" in drafts[10].source_text
    assert "[[formula:h\\frac{y_{0}}{v_{0}}]]" in drafts[16].source_text
    assert "[[formula:\\frac{1}{2}B_{0}]]" in drafts[18].choice_texts
    combined = "\n".join(
        text
        for draft in drafts.values()
        for text in (draft.source_text, *draft.choice_texts, draft.palette_markdown)
    )
    assert all(
        corrupt not in combined
        for corrupt in (
            "[[formula:-2]] [[formula:3]]m[[formula:/]]s",
            "[[formula:5mP]]",
            "[[formula:h-]] [[formula:y0]]",
            "[[formula:-2]][[formula:1B0]]",
        )
    )


def test_real_q2_nuclides_reconstruct_aligned_super_and_subscripts(tmp_path: Path) -> None:
    # Given: q2's HyhwpEQ nuclides, whose lower-left indices are separate PDF words.
    item2 = next(item for item in detect_items(SOURCE).items if item.item_number == 2)

    # When: the real source geometry is converted to editable formula markup.
    draft = build_editable_draft(SOURCE, item2, tmp_path)

    # Then: both indices stay attached to the element/particle instead of trailing prose.
    assert "[[formula:{}^{1}_{1}H]]이다." in draft.source_text
    assert "중성자( [[formula:{}^{1}_{0}n]])의 질량" in draft.source_text
    assert "중성자( [[formula:{}^{1}_{0}n]])이다." in draft.source_text
    assert "H이다. [[formula:1]]" not in draft.source_text
    assert "n)의 [[formula:0]] 질량" not in draft.source_text
    assert r"\수식{{}^{1}_{1}H}" in draft.palette_markdown
    assert r"\수식{{}^{1}_{0}n}" in draft.palette_markdown


def test_real_formula_subscripts_follow_lowered_hyhwp_span_geometry(tmp_path: Path) -> None:
    # Given: real formulas whose lowered suffix spans are smaller than their bases.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: the real PDF text layer is converted through draft and palette boundaries.
    drafts = {
        number: build_editable_draft(SOURCE, items[number], tmp_path / f"item-{number}")
        for number in (2, 10, 14, 16, 17, 18, 19)
    }

    # Then: the machine-consumed formula sources preserve every measured subscript.
    expected = {
        2: ("2M_{1}=M_{2}+M_{3}",),
        14: ("i_{0}=0.75", "i_{0}"),
        16: (r"h\frac{y_{0}}{v_{0}}",),
        17: ("B_{0}",),
        18: ("I_{0}", "I_{C}", "B_{0}", r"\frac{1}{2}B_{0}"),
        19: ("v_{A}", "v_{B}", r"\frac{v_{A}}{v_{B}}"),
    }
    for number, formulas in expected.items():
        source = "\n".join((drafts[number].source_text, *drafts[number].choice_texts))
        for formula in formulas:
            assert f"[[formula:{formula}]]" in source
            assert rf"\수식{{{formula}}}" in drafts[number].palette_markdown

    # Same-baseline alphanumeric runs are not scripts merely because they are formulas.
    ordinary = "\n".join(draft.source_text for draft in drafts.values())
    assert "[[formula:xy]]" in ordinary
    assert "[[formula:2d]]" in ordinary
    assert "[[formula:5mg]]" in ordinary


def test_ambiguous_script_stack_is_flagged_for_manual_review() -> None:
    # Given: two plausible lower indices aligned beneath the same upper-index/base word.
    words = [
        draft_extractor._Word((100.0, 100.0, 124.0, 114.0), "\ue034H", "\ue034H"),
        draft_extractor._Word((100.0, 107.0, 104.0, 114.5), "\ue034", "\ue034"),
        draft_extractor._Word((100.4, 109.0, 104.4, 116.5), "\ue03d", "\ue03d"),
    ]
    decoder = draft_extractor._EquationDecoder(EquationFontContext(
        frozenset(ord(char) for word in words for char in word.raw), (), (),
    ))

    # When: generic script geometry cannot select one lower index confidently.
    normalized = draft_extractor._normalize_fractions(words, decoder)

    # Then: no invented nuclide is emitted and the existing draft boundary will require review.
    assert "ambiguous-script-stack@100.00,100.00" in decoder.unknown
    assert all("{}^" not in word.text for word in normalized)


def test_ambiguous_lowered_formula_span_is_flagged_for_manual_review() -> None:
    # Given: span geometry reached the uncertain band rather than the proven subscript band.
    words = [
        draft_extractor._Word(
            (100.0, 100.0, 120.0, 114.0),
            "\ue00c\ue034",
            "\ue00c\ue034",
            ambiguous_subscript=True,
        ),
    ]
    decoder = draft_extractor._EquationDecoder(EquationFontContext(
        frozenset(ord(char) for word in words for char in word.raw), (), (),
    ))

    # When: the equation normalization boundary sees the uncertain annotation.
    normalized = draft_extractor._normalize_fractions(words, decoder)

    # Then: it keeps linear text but makes the enclosing real draft require review.
    assert normalized[0].text == "[[formula:M1]]"
    assert decoder.unknown == {"ambiguous-subscript@100.00,100.00"}


def test_build_editable_draft_routes_unverified_equation_glyph_to_manual_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a glyph that the decoder cannot verify even though other formula glyphs are valid.
    item20 = next(item for item in detect_items(SOURCE).items if item.item_number == 20)
    original = draft_extractor._EquationDecoder.char

    def mark_one_glyph_unverified(decoder: draft_extractor._EquationDecoder, raw: str) -> str:
        decoded = original(decoder, raw)
        if raw == "\ue0f1":
            decoder.unknown.add("U+E0F1")
        return decoded

    monkeypatch.setattr(draft_extractor._EquationDecoder, "char", mark_one_glyph_unverified)

    # When/Then: extraction stops at the manual-review boundary instead of persisting invented text.
    with pytest.raises(UnsupportedDraftLayoutError) as captured:
        build_editable_draft(SOURCE, item20, tmp_path)
    assert captured.value.item_number == 20
    assert captured.value.detail == "unverified equation glyphs require manual review: U+E0F1"
    assert captured.value.source_image.image_path.is_file()


def test_partial_mixed_prompt_region_requires_manual_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: object segmentation finds a narrow component and many nearby text/vector
    # spans, but their geometry does not prove one complete horizontal prompt group.
    item2 = next(item for item in detect_items(SOURCE).items if item.item_number == 2)
    image_path = tmp_path / "partial.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    provenance_path = image_path.with_suffix(".json")
    provenance_path.write_text(json.dumps({
        "bbox": [315.0, 565.0, 350.0, 615.0],
        "excluded_body_spans": [
            {"text": str(index), "bbox": [300.0, 570.0 + index, 320.0, 578.0 + index]}
            for index in range(8)
        ],
    }), encoding="utf-8")
    partial = CropArtifact(image_path, provenance_path, 100, 100)
    monkeypatch.setattr(draft_extractor, "crop_region", lambda *_args: partial)

    # When/Then: the extractor does not silently discard the unproven remainder.
    with pytest.raises(UnsupportedDraftLayoutError) as captured:
        draft_extractor._crop_prompt_region(
            SOURCE, item2, (90.0, 560.0, 410.0, 625.0), tmp_path, partial,
        )
    assert captured.value.detail == (
        "ambiguous mixed text/vector prompt region requires manual review"
    )


@pytest.mark.parametrize(("item_number", "source_bbox", "object_bbox"), (
    (2, (81.78, 487.245, 362.40, 627.405), (148.20, 535.141, 356.40, 641.341)),
    (5, (430.44, 807.045, 758.58, 909.361), (653.58, 815.161, 752.58, 903.361)),
    (18, (81.78, 674.445, 397.80, 888.301), (112.80, 755.701, 391.80, 882.301)),
))
def test_embedded_image_prompt_keeps_object_union_and_editable_prose(
    tmp_path: Path,
    item_number: int,
    source_bbox: tuple[float, float, float, float],
    object_bbox: tuple[float, float, float, float],
) -> None:
    source = _exam_pdf("p1_2019_11.pdf")
    item = next(value for value in detect_items(source).items
                if value.item_number == item_number)
    source_image = crop_region(source, item, source_bbox, tmp_path, "source")

    result = draft_extractor._crop_prompt_region(
        source, item, source_bbox, tmp_path, source_image,
    )
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))

    assert payload["asset_mode"] == "pdf_figure_object_crop_hd"
    assert payload["bbox"] == pytest.approx(object_bbox)
    assert payload["protected_texts"] == []
    assert payload["excluded_body_spans"]


@pytest.mark.parametrize(("item_number", "stem", "ask", "asset_count"), (
    (2, "그림 (가)는 병원에서", "이에 대한 설명으로", 2),
    (5, "그림과 같이 위성이", "이에 대한 설명으로", 1),
    (18, "그림 (가)와 같이", "(나)의 상황에 대한", 1),
))
def test_real_embedded_image_draft_preserves_prompt_and_excludes_prose(
    tmp_path: Path,
    item_number: int,
    stem: str,
    ask: str,
    asset_count: int,
) -> None:
    source = _exam_pdf("p1_2019_11.pdf")
    item = next(value for value in detect_items(source).items
                if value.item_number == item_number)

    draft = build_editable_draft(source, item, tmp_path / str(item_number))

    assert stem in draft.palette_markdown
    assert ask in draft.palette_markdown
    assert len(draft.figure_assets) == asset_count
    for asset in draft.figure_assets:
        payload = json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        geometry = read_crop_geometry(CropSourceRequest(
            source, item.page_number, item_number, item.bbox,
            tuple(payload["bbox"]), draft.palette_markdown, True,
        ))
        assert CropAuditIssue.CROP_CONTAMINATION not in audit_crop_geometry(geometry).issues


def test_p1_2019_boxed_text_q6_stays_editable_and_outside_image_fix(
    tmp_path: Path,
) -> None:
    source = _exam_pdf("p1_2019_11.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 6)

    draft = build_editable_draft(source, item, tmp_path)

    assert draft.figure_assets == ()
    assert "\\표1*1\\" in draft.palette_markdown


@pytest.mark.parametrize(
    ("paper", "item_number", "table_token"),
    [("b2_2026_11", 5, "\\표3*2\\"), ("c1_2027_06", 8, "\\표1*1\\")],
)
def test_table_prompt_stays_editable_without_redundant_mixed_region_raster(
    tmp_path: Path, paper: str, item_number: int, table_token: str,
) -> None:
    source = _exam_pdf(f"{paper}.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == item_number)

    draft = build_editable_draft(source, item, tmp_path / f"{paper}-q{item_number}")
    result = reconcile_final_figure_contract(
        item_number, draft.palette_markdown, draft.figure_assets,
    )

    assert draft.figure_assets == ()
    assert table_token in draft.palette_markdown
    assert isinstance(result, FinalFigureContract)


def test_vertical_pair_keeps_gutter_captions_from_emptying_the_lower_panel(tmp_path: Path) -> None:
    source = _exam_pdf("c1_2026_09.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 18)

    draft = build_editable_draft(source, item, tmp_path / "c1-vertical")
    metadata = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    ]
    result = reconcile_final_figure_contract(18, draft.palette_markdown, draft.figure_assets)

    assert len(metadata) == 2
    assert all(entry["image_bbox"][3] > entry["image_bbox"][1] for entry in metadata)
    assert all("empty panel geometry" not in entry.get("review_reasons", []) for entry in metadata)
    assert isinstance(result, FinalFigureContract)


def test_equation_decoder_never_decodes_unverified_font_or_codepoint() -> None:
    # Given: a known codepoint under the wrong font and an unknown codepoint under HyhwpEQ.
    wrong_font = draft_extractor._EquationDecoder(frozenset({"OtherEquationFont"}))
    unknown_codepoint = draft_extractor._EquationDecoder(frozenset({"HyhwpEQ"}))

    # When: both glyphs cross the equation decoding boundary.
    wrong_font_value = wrong_font.char("\ue053")
    unknown_value = unknown_codepoint.char("\ue200")

    # Then: neither is guessed, and both are marked for the manual-review boundary.
    assert wrong_font_value == "\ue053"
    assert wrong_font.unknown == {"U+E053"}
    assert unknown_value == "\ue200"
    assert unknown_codepoint.unknown == {"U+E200"}


def test_build_editable_draft_supports_every_editable_real_item_layout(tmp_path: Path) -> None:
    # Given: all editable items, including right-aligned and inline source figures.
    detected = detect_items(SOURCE).items

    # When: each item is converted without a handcrafted palette seed.
    drafts = tuple(
        build_editable_draft(SOURCE, item, tmp_path / f"item-{item.item_number}")
        for item in detected
    )

    # Then: every item has an editable prompt, five choices, and its source material.
    assert [draft.item_number for draft in drafts] == [*range(1, 21)]
    assert all(draft.palette_markdown.strip() for draft in drafts)
    assert all(draft.source_text.strip() for draft in drafts)
    assert all(
        len(draft.choice_texts) == 5 or len(draft.graphical_choice_assets) == 5
        for draft in drafts
    )
    assert all(
        draft.figure_assets
        or (draft.item_number == 2 and "\\표4*2\\" in draft.palette_markdown)
        for draft in drafts
    )
    assert {draft.item_number: len(draft.figure_assets) for draft in drafts} == {
        1: 1,
        2: 0,
        3: 3,
        4: 2,
        5: 1,
        6: 1,
        7: 2,
        8: 2,
        9: 2,
        10: 2,
        11: 1,
        12: 1,
        13: 2,
        14: 1,
        15: 1,
        16: 1,
        17: 1,
        18: 1,
        19: 1,
        20: 2,
    }
    assert all(
        asset.image_path.is_file()
        for draft in drafts
        for asset in (*draft.figure_assets, *draft.graphical_choice_assets)
    )
    combined = "\n".join(
        text
        for draft in drafts
        for text in (draft.source_text, *draft.choice_texts, draft.palette_markdown)
    )
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in combined)
    assert "저작권은 한국교육과정평가원에 있습니다" not in combined
    item2 = next(draft for draft in drafts if draft.item_number == 2)
    assert "\\표4*2\\" in item2.palette_markdown
    assert item2.figure_assets == ()


def test_real_figure_routing_distinguishes_two_panel_large_and_small_assets(tmp_path: Path) -> None:
    # Given: real source items containing a two-panel scene, a wide single scene,
    # and a compact single graph respectively.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: their editable drafts select assets and registered palette templates.
    item20 = build_editable_draft(SOURCE, items[20], tmp_path / "item-20")
    item19 = build_editable_draft(SOURCE, items[19], tmp_path / "item-19")
    item18 = build_editable_draft(SOURCE, items[18], tmp_path / "item-18")

    # Then: two source scenes retain readable large-photo slots, while one-scene
    # figures never acquire a ghost (나) slot. A medium single graph is promoted
    # to the large cell when the small cell would shrink it below readability.
    assert len(item20.figure_assets) == 2
    assert item20.palette_markdown.startswith("\\수능정답2대사진5선지\\\n")
    assert len(item19.figure_assets) == 1
    assert item19.palette_markdown.startswith("\\수능정답1대사진5선지\\\n")
    assert len(item18.figure_assets) == 1
    assert item18.palette_markdown.startswith("\\수능정답1대사진5선지\\\n")


def test_real_q3_splits_embedded_labels_into_three_captionless_panels(tmp_path: Path) -> None:
    # Given: q3 stores three horizontally separated scenes and their (가)/(나)/(다)
    # captions in one source raster.
    item = next(value for value in detect_items(SOURCE).items if value.item_number == 3)

    # When: the real source passes through object segmentation and final routing.
    draft = build_editable_draft(SOURCE, item, tmp_path / "item-3")
    source = json.loads(draft.figure_asset.provenance_path.read_text(encoding="utf-8"))
    panels = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    ]

    # Then: the labels are editable metadata and never remain burned into a panel.
    assert [panel["caption_text"] for panel in panels] == ["(가)", "(나)", "(다)"]
    assert [panel["panel_index"] for panel in panels] == [1, 2, 3]
    assert all(panel["asset_count"] == 3 for panel in panels)
    assert all(panel["panel_mode"] == "separate" for panel in panels)
    assert all(panel["arrangement"] == "horizontal" for panel in panels)
    assert all(panel["caption_in_image"] is False for panel in panels)
    assert all(panel["image_bbox"][3] <= panel["caption_bbox"][1] for panel in panels)
    assert source["panel_bboxes"] == [panel["image_bbox"] for panel in panels]
    assert source["manual_review_required"] is False


def test_split_panels_preserve_real_content_above_removed_captions(tmp_path: Path) -> None:
    # Given: source figures whose axis labels, ground lines, and dashed positions
    # extend below the main visual body near the (가)/(나) captions.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: their two panels are routed to the caption-free two-photo template.
    drafts = {
        number: build_editable_draft(SOURCE, items[number], tmp_path / f"item-{number}")
        for number in (4, 9, 10)
    }

    # Then: authoritative panel boxes end at the caption boundary while retaining
    # lower semantic ink such as 원자핵/파장, q/수평면, and the dashed A position.
    for draft in drafts.values():
        source = json.loads(draft.figure_asset.provenance_path.read_text(encoding="utf-8"))
        panels = []
        lower_ink_counts = []
        for asset in draft.figure_assets:
            panels.append(json.loads(asset.provenance_path.read_text(encoding="utf-8")))
            with Image.open(asset.image_path) as image:
                grayscale = image.convert("L")
                lower_band = grayscale.crop((0, int(image.height * 0.8), image.width, image.height))
                lower_ink_counts.append(sum(lower_band.histogram()[:240]))
        assert len(panels) == 2
        assert all(panel["caption_text"].strip() for panel in panels)
        assert all(panel["caption_in_image"] is False for panel in panels)
        assert all(panel["image_bbox"][3] <= panel["caption_bbox"][1] for panel in panels)
        assert all(panel["image_bbox"][3] < source["bbox"][3] for panel in panels)
        assert all(count > 100 for count in lower_ink_counts)
    assert drafts[4].palette_markdown.startswith("\\수능합답2대사진5선지\\\n")
    assert drafts[9].palette_markdown.startswith("\\수능합답2대사진5선지\\\n")
    assert drafts[10].palette_markdown.startswith("\\수능합답2대사진5선지\\\n")


def test_build_editable_draft_extracts_real_graphical_choices_in_marker_order(tmp_path: Path) -> None:
    # Given: item 17, whose five answer choices are graphs rather than editable text runs.
    item17 = next(item for item in detect_items(SOURCE).items if item.item_number == 17)

    # When: deterministic draft extraction reaches the graphical answer area.
    draft = build_editable_draft(SOURCE, item17, tmp_path)

    # Then: the prompt figure remains separate and five choice images follow ①–⑤ order.
    assert len(draft.figure_assets) == 1
    assert len(draft.graphical_choice_assets) == 5
    assert draft.choice_texts == ()
    boxes = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))["source_bbox"]
        for asset in draft.graphical_choice_assets
    ]
    assert boxes == [
        [114.06, 443.521, 224.46, 504.121],
        [271.98, 443.521, 382.38, 504.121],
        [114.06, 507.361, 224.46, 567.961],
        [271.98, 507.361, 382.38, 567.961],
        [114.06, 572.221, 224.46, 632.821],
    ]
    lines = draft.palette_markdown.splitlines()
    assert lines[0] == "\\수능정답1대사진그림5선지\\"
    assert len(lines) == 10
    assert lines[1] == "17"
    assert "p에 흐르는 유도 전류를" not in lines[2]
    assert lines[3] == f"\\{draft.figure_assets[0].image_path.stem}\\"
    assert lines[4].startswith("p에 흐르는 유도 전류를")
    assert lines[5:] == [
        f"\\{asset.image_path.stem}\\" for asset in draft.graphical_choice_assets
    ]
    metadata = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.graphical_choice_assets
    ]
    assert [entry["choice_index"] for entry in metadata] == [1, 2, 3, 4, 5]
    assert all(entry["asset_count"] == 5 for entry in metadata)
    assert all(entry["manual_review_required"] is False for entry in metadata)
    assert all(entry["review_reasons"] == [] for entry in metadata)


def test_build_editable_draft_keeps_malformed_graphical_choices_in_manual_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a graphical-choice page whose image-to-marker mapping is incomplete.
    item17 = next(item for item in detect_items(SOURCE).items if item.item_number == 17)
    monkeypatch.setattr("app.pdf_hwp_draft._graphical_choice_bboxes", lambda *_: ())

    # When/Then: extraction does not silently produce a partial answer set.
    with pytest.raises(UnsupportedDraftLayoutError) as captured:
        build_editable_draft(SOURCE, item17, tmp_path)
    assert captured.value.item_number == 17
    assert captured.value.detail == "graphical answer choices require manual review"
    assert captured.value.source_image.image_path.is_file()


def test_real_2026_06_q5_crops_stacked_vector_choice_rows(tmp_path: Path) -> None:
    # Given: ①–⑤ mark five arrow rows drawn as vectors, not five choice images.
    source = _exam_pdf("p1_2026_06.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 5)

    # When: draft extraction reaches the empty-text stacked choice band.
    draft = build_editable_draft(source, item, tmp_path / "q5")
    boxes = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))["source_bbox"]
        for asset in draft.graphical_choice_assets
    ]

    # Then: each marker row becomes one aligned choice crop, not invented images.
    assert draft.choice_texts == ()
    assert len(draft.graphical_choice_assets) == 5
    assert len(draft.figure_assets) == 1
    assert draft.palette_markdown.splitlines()[0] == "\\수능정답1대사진그림5선지\\"
    assert all(box[2] - box[0] > 40 for box in boxes)
    assert all(boxes[index][3] <= boxes[index + 1][1] + 0.01 for index in range(4))
    assert len({round(box[0], 1) for box in boxes}) == 1
    assert len({round(box[2], 1) for box in boxes}) == 1


@pytest.mark.parametrize("paper,item_number", [
    ("p1_2021_06", 2),
    ("p1_2022_06", 1),
    ("p1_2025_06", 13),
])
def test_real_item_without_text_below_figure_still_builds_a_draft(
    tmp_path: Path, paper: str, item_number: int,
) -> None:
    source = _exam_pdf(f"{paper}.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == item_number)

    draft = build_editable_draft(source, item, tmp_path)

    assert draft.palette_markdown.strip()
    assert draft.source_text.strip()


def test_raster_only_pdf_detects_each_image_page_for_source_preservation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "question-image.pdf"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 320, 180), False)
    pixmap.clear_with(255)
    image = fitz.open(stream=pixmap.tobytes("png"), filetype="png")
    pdf = fitz.open("pdf", image.convert_to_pdf())
    pdf.save(source)
    pdf.close()
    image.close()

    detected = detect_items(source)

    assert len(detected.items) == 1
    assert detected.items[0].page_number == 1
    assert detected.items[0].item_number == 1
    assert detected.items[0].bbox == (0.0, 0.0, 240.0, 135.0)


@pytest.mark.parametrize("page_number,item_number", [(13, 1), (70, 96), (174, 249)])
def test_ebs_items_build_editable_text_choices_and_separate_figure_draft(
    tmp_path: Path, page_number: int, item_number: int,
) -> None:
    source = Path(
        r"C:\Users\user\Desktop\project\31_hwp_palette\2027 수능특강 물리학 I 원본.pdf"
    )
    if not source.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(source).items
        if value.page_number == page_number and value.item_number == item_number
    )

    draft = build_editable_draft(source, item, tmp_path / f"p{page_number}-q{item_number}")

    assert draft.palette_markdown.strip()
    assert draft.palette_markdown.splitlines()[0] != "\\수능원문1대사진\\"
    assert len(draft.choice_texts) == 5
    assert draft.source_text.strip()
    assert "[26023-" not in draft.source_text
    assert re.match(r"^\d{2}\s", draft.source_text) is None
    assert draft.source_image not in draft.figure_assets
    assert isinstance(
        reconcile_final_figure_contract(item_number, draft.palette_markdown, draft.figure_assets),
        FinalFigureContract,
    )


def test_ebs_q2_keeps_body_editable_and_crops_only_the_three_panel_figure(
    tmp_path: Path,
) -> None:
    source = Path(
        r"C:\Users\user\Desktop\project\31_hwp_palette\2027 수능특강 물리학 I 원본.pdf"
    )
    if not source.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(
        value for value in detect_items(source).items
        if value.page_number == 13 and value.item_number == 2
    )

    draft = build_editable_draft(source, item, tmp_path / "q2")
    metadata = json.loads(draft.figure_assets[0].provenance_path.read_text(encoding="utf-8"))

    assert draft.source_text.startswith(
        "그림 (가)는 등속 원운동을 하는 학생 [[formula:A]], "
        "(나)는 그네에서 왕복 운동을 하는 학생 [[formula:B]]"
    )
    assert metadata["bbox"][1] > 460


@pytest.mark.parametrize(
    ("item_number", "expected"),
    [
        (5, r"[[formula:d_2=\frac{3}{2}d_1]]"),
        (8, r"[[formula:\frac{v_1}{v_2}]]은?"),
        (9, r"[[formula:\frac{v_1}{v_2}]]는?"),
        (10, r"[[formula:\frac{a_2}{a_1}]]는?"),
    ],
)
def test_ebs_legacy_equation_fonts_become_semantic_editable_formulas(
    tmp_path: Path, item_number: int, expected: str,
) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(value for value in detect_items(EBS_PHYSICS).items if value.item_number == item_number)

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path / f"q{item_number}")

    assert expected in draft.source_text
    assert re.search(r";\d+[!@#$%^&*()];", draft.source_text) is None


@pytest.mark.parametrize(
    ("item_number", "expected"),
    [
        (18, r"[[formula:\sqrt{\frac{gh}{3}}]]"),
        (70, r"[[formula:3d\sqrt{\frac{k_1}{m}}]]"),
        (71, r"[[formula:v_3=\sqrt{\frac{2E_0}{m}}]]"),
        (73, r"[[formula:x=4\sqrt{\frac{m}{k}}v]]"),
        (76, r"[[formula:2\sqrt{\frac{E_0}{5m}}]]"),
        (78, r"[[formula:3\sqrt{\frac{5m}{k}}]]"),
        (236, r"[[formula:\sqrt{\frac{3}{2}}]]"),
    ],
)
def test_ebs_stacked_radicals_become_semantic_formulas(
    tmp_path: Path, item_number: int, expected: str,
) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(value for value in detect_items(EBS_PHYSICS).items if value.item_number == item_number)

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path / f"root-q{item_number}")

    assert expected in draft.source_text


def test_ebs_multiline_root_choices_keep_their_denominators(tmp_path: Path) -> None:
    if not EBS_PHYSICS.is_file():
        pytest.skip("missing EBS physics PDF")
    item = next(value for value in detect_items(EBS_PHYSICS).items if value.item_number == 47)

    draft = build_editable_draft(EBS_PHYSICS, item, tmp_path / "q47-root-choices")

    assert draft.choice_texts == (
        r"[[formula:\frac{m\sqrt{2gh}}{3t}]]",
        r"[[formula:\frac{m\sqrt{2gh}}{2t}]]",
        r"[[formula:\frac{2m\sqrt{2gh}}{3t}]]",
        r"[[formula:\frac{m\sqrt{2gh}}{t}]]",
        r"[[formula:\frac{4m\sqrt{2gh}}{3t}]]",
    )


def test_real_2021_06_q1_keeps_ordered_three_panel_captions(tmp_path: Path) -> None:
    source = _exam_pdf("p1_2021_06.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 1)

    draft = build_editable_draft(source, item, tmp_path / "q1")
    metadata = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    ]
    result = reconcile_final_figure_contract(1, draft.palette_markdown, draft.figure_assets)

    assert [entry["caption_text"] for entry in metadata] == ["(가)", "(나)", "(다)"]
    assert all(entry["caption_bbox"] is not None for entry in metadata)
    assert isinstance(result, FinalFigureContract)


def test_real_unlabeled_three_illustrations_use_one_hapdap_photo(tmp_path: Path) -> None:
    source = _exam_pdf("p1_2021_06.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 2)

    draft = build_editable_draft(source, item, tmp_path / "q2")

    assert len(draft.figure_assets) == 1
    assert draft.palette_markdown.startswith("\\수능합답1")
    assert "(가)" not in "\n".join(
        json.loads(asset.provenance_path.read_text(encoding="utf-8")).get("caption_text", "")
        for asset in draft.figure_assets
    )


def test_real_graphical_choice_multiline_passage_fills_the_photo_slot(tmp_path: Path) -> None:
    source = _exam_pdf("p1_2021_06.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 5)

    draft = build_editable_draft(source, item, tmp_path / "q5")
    result = reconcile_final_figure_contract(5, draft.palette_markdown, draft.figure_assets)
    markdown = result.palette_markdown if isinstance(result, FinalFigureContract) else draft.palette_markdown
    assets = tuple(
        FigureAsset(
            artifact.image_path,
            FigureAssetMetadata.model_validate_json(artifact.provenance_path.read_text(encoding="utf-8")),
        )
        for artifact in draft.figure_assets
    )
    choices = tuple(
        GraphicalChoiceAsset(
            artifact.image_path,
            GraphicalChoiceAssetMetadata.model_validate_json(
                artifact.provenance_path.read_text(encoding="utf-8"),
            ),
        )
        for artifact in draft.graphical_choice_assets
    )

    assert len(draft.graphical_choice_assets) == 5
    assert draft.palette_markdown.splitlines()[2].startswith("{")
    preflight_unit(
        ConversionUnit(5, markdown, assets, choices),
        LayoutStyle.SUNEUNG,
    )


def test_real_abc_three_panel_captions_keep_separate_geometry(tmp_path: Path) -> None:
    source = _exam_pdf("p1_2022_06.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 5)

    draft = build_editable_draft(source, item, tmp_path / "abc")
    metadata = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    ]
    result = reconcile_final_figure_contract(5, draft.palette_markdown, draft.figure_assets)

    assert [entry["caption_text"] for entry in metadata] == ["A", "B", "C"]
    assert all(entry["caption_bbox"] is not None for entry in metadata)
    assert isinstance(result, FinalFigureContract)


def test_real_caption_titles_below_three_panels_keep_geometry(tmp_path: Path) -> None:
    source = _exam_pdf("e1_2026_09.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 1)

    draft = build_editable_draft(source, item, tmp_path / "e1-titles")
    metadata = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    ]
    result = reconcile_final_figure_contract(1, draft.palette_markdown, draft.figure_assets)

    assert [entry["caption_text"] for entry in metadata] == ["(가)", "(나)", "(다)"]
    assert all(entry["caption_bbox"] is not None for entry in metadata)
    assert isinstance(result, FinalFigureContract)
    assert result.palette_markdown.splitlines()[0] == "\\수능합답3소사진5선지\\"


def test_real_inquiry_steps_do_not_force_three_panel_split(tmp_path: Path) -> None:
    source = _exam_pdf("e1_2025_11.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 4)

    draft = build_editable_draft(source, item, tmp_path / "e1-inquiry")
    metadata = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    ]
    result = reconcile_final_figure_contract(4, draft.palette_markdown, draft.figure_assets)

    assert all(not entry.get("manual_review_required") for entry in metadata)
    assert isinstance(result, FinalFigureContract)


def test_real_direct_three_panel_uses_registered_hapdap_template(tmp_path: Path) -> None:
    source = _exam_pdf("c1_2026_11.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 18)

    draft = build_editable_draft(source, item, tmp_path / "c1-three")
    result = reconcile_final_figure_contract(18, draft.palette_markdown, draft.figure_assets)

    assert isinstance(result, FinalFigureContract)
    assert result.palette_markdown.splitlines()[0] == "\\수능합답3소사진5선지\\"
    assert len(draft.figure_assets) == 3


def test_real_two_column_graphical_choices_pair_by_overlap(tmp_path: Path) -> None:
    source = _exam_pdf("c1_2026_11.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 4)

    draft = build_editable_draft(source, item, tmp_path / "c1-choices")

    assert len(draft.graphical_choice_assets) == 5
    assert draft.palette_markdown.lstrip().startswith("\\수능정답0사진그림5선지\\")


def test_real_abc_captions_left_of_panels_still_have_geometry(tmp_path: Path) -> None:
    source = _exam_pdf("p1_2026_09.pdf")
    item = next(value for value in detect_items(source).items if value.item_number == 1)

    draft = build_editable_draft(source, item, tmp_path / "abc-left")
    metadata = [
        json.loads(asset.provenance_path.read_text(encoding="utf-8"))
        for asset in draft.figure_assets
    ]
    result = reconcile_final_figure_contract(1, draft.palette_markdown, draft.figure_assets)

    assert [entry["caption_text"] for entry in metadata] == ["A", "B", "C"]
    assert all(entry["caption_bbox"] is not None for entry in metadata)
    assert isinstance(result, FinalFigureContract)
