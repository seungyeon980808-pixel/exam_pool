from __future__ import annotations

import hashlib
import json
from pathlib import Path

import fitz
from PIL import Image
import pytest

import app.pdf_hwp_draft as draft_extractor
from app.pdf_hwp_equation_font import EquationFontContext
from app.formula_markup import to_hwppalette_markup

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
    _typeset_timeout_seconds,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p1_2024_11.pdf")


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


def test_build_editable_draft_excludes_text_layer_inside_embedded_prompt_raster(
    tmp_path: Path,
) -> None:
    # Given: item 2, whose embedded prompt raster also has a selectable text layer.
    item2 = next(item for item in detect_items(SOURCE).items if item.item_number == 2)

    # When: the prompt raster is inserted as the item's figure asset.
    draft = build_editable_draft(SOURCE, item2, tmp_path)

    # Then: text represented by that raster is not duplicated into the editable stem.
    assert "MeV" not in draft.source_text
    assert "3i27" not in draft.source_text
    assert "17i6" not in draft.source_text
    assert "두 가지 핵반응" in draft.source_text
    provenance = json.loads(draft.figure_assets[0].provenance_path.read_text(encoding="utf-8"))
    figure_bbox = fitz.Rect(provenance["bbox"])
    with fitz.open(SOURCE) as document:
        words = document[item2.page_number - 1].get_text(
            "words", clip=fitz.Rect(item2.bbox), sort=True,
        )
    reaction_bbox = fitz.Rect(next(word[:4] for word in words if word[4] == "(가)"))
    table_bbox = fitz.Rect(next(word[:4] for word in words if word[4] == "입자"))
    assert figure_bbox.contains(reaction_bbox)
    assert figure_bbox.contains(table_bbox)
    assert figure_bbox.width > 250
    with Image.open(draft.figure_assets[0].image_path).convert("L") as rendered:
        x_scale = rendered.width / figure_bbox.width
        y_scale = rendered.height / figure_bbox.height
        for source_box in (reaction_bbox, table_bbox):
            pixel_box = (
                round((source_box.x0 - figure_bbox.x0) * x_scale),
                round((source_box.y0 - figure_bbox.y0) * y_scale),
                round((source_box.x1 - figure_bbox.x0) * x_scale),
                round((source_box.y1 - figure_bbox.y0) * y_scale),
            )
            assert rendered.crop(pixel_box).getbbox() is not None


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

    # Then: every item has an editable prompt, five choices, and its source figure/material.
    assert [draft.item_number for draft in drafts] == [*range(1, 21)]
    assert all(draft.palette_markdown.strip() for draft in drafts)
    assert all(draft.source_text.strip() for draft in drafts)
    assert all(
        len(draft.choice_texts) == 5 or len(draft.graphical_choice_assets) == 5
        for draft in drafts
    )
    assert all(draft.figure_assets for draft in drafts)
    assert {draft.item_number: len(draft.figure_assets) for draft in drafts} == {
        1: 1,
        2: 1,
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
    item2_provenance = json.loads(item2.figure_assets[0].provenance_path.read_text(encoding="utf-8"))
    assert item2_provenance["bbox"][0] < 110
    assert item2_provenance["bbox"][2] > 390
    assert item2_provenance["bbox"][1] > 550
    assert item2_provenance["bbox"][3] < 630


def test_real_figure_routing_distinguishes_two_panel_large_and_small_assets(tmp_path: Path) -> None:
    # Given: real source items containing a two-panel scene, a wide single scene,
    # and a compact single graph respectively.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: their editable drafts select assets and registered palette templates.
    item20 = build_editable_draft(SOURCE, items[20], tmp_path / "item-20")
    item19 = build_editable_draft(SOURCE, items[19], tmp_path / "item-19")
    item18 = build_editable_draft(SOURCE, items[18], tmp_path / "item-18")

    # Then: two source scenes retain readable large-photo slots, while one-scene
    # figures never acquire a ghost (나) slot and use measured large/small geometry.
    assert len(item20.figure_assets) == 2
    assert item20.palette_markdown.startswith("\\수능정답2대사진5선지\\\n")
    assert len(item19.figure_assets) == 1
    assert item19.palette_markdown.startswith("\\수능정답1대사진5선지\\\n")
    assert len(item18.figure_assets) == 1
    assert item18.palette_markdown.startswith("\\수능정답1소사진5선지\\\n")


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
