from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.pdf_hwp_choice_text import choice_texts
from app.pdf_hwp_equation_rows import group_rows
from app.pdf_hwp_equation_types import EquationWord
from app.pdf_hwp_pipeline import LayoutStyle, build_editable_draft, detect_items
from app.pdf_hwp_roundtrip_crop_audit import (
    CropSourceRequest,
    audit_crop_geometry,
    read_crop_geometry,
)


def _word(text: str, bbox: tuple[float, float, float, float]) -> EquationWord:
    return EquationWord(bbox=bbox, raw=text, text=text)


def test_tall_fraction_moves_to_the_baseline_it_visually_overlaps() -> None:
    words = [
        _word("같고,", (10, 10, 35, 22)),
        _word("시간은", (10, 32, 40, 44)),
        _word("[[formula:\\frac{t_0}{2}]]이다.", (45, 22, 78, 44)),
    ]

    grouped = group_rows(words)

    assert [[word.text for word in row] for row in grouped] == [
        ["같고,"],
        ["시간은", "[[formula:\\frac{t_0}{2}]]이다."],
    ]


def test_text_choices_keep_spaces_between_formula_and_prose_words() -> None:
    markers = [
        _word(marker, (10, 10 + index * 20, 18, 20 + index * 20))
        for index, marker in enumerate("①②③④⑤")
    ]
    words = [*markers]
    for index, marker in enumerate(markers):
        y = marker.bbox[1]
        words.extend((
            _word("[[formula:n=2]]에서", (22, y, 55, y + 10)),
            _word("전이할", (60, y, 90, y + 10)),
            _word("때", (95, y, 105, y + 10)),
        ))

    choices = choice_texts(words, 10)

    assert choices == tuple("[[formula:n=2]]에서 전이할 때" for _ in range(5))


def test_horizontal_choice_text_stays_with_the_preceding_marker() -> None:
    markers = [
        _word(marker, (10 + index * 80, 10, 18 + index * 80, 20))
        for index, marker in enumerate("①②③④⑤")
    ]
    words = [*markers]
    values = ("ㄱ", "ㄴ", "ㄱ, ㄷ", "ㄴ, ㄷ", "ㄱ, ㄴ, ㄷ")
    for marker, value in zip(markers, values, strict=True):
        x = marker.bbox[2] + 3
        for token in value.split():
            words.append(_word(token, (x, 10, x + 18, 20)))
            x += 21

    choices = choice_texts(words, 10)

    assert choices == ("ㄱ", "ㄴ", "ㄱ,ㄷ", "ㄴ,ㄷ", "ㄱ,ㄴ,ㄷ")


def test_three_claim_choice_discards_page_header_words_at_the_right_margin() -> None:
    markers = [
        _word(marker, (10 + index * 60, 10, 18 + index * 60, 20))
        for index, marker in enumerate("①②③④⑤")
    ]
    words = [*markers]
    for marker, value in zip(
        markers, ("ㄱ", "ㄴ", "ㄱ,ㄷ", "ㄴ,ㄷ", "ㄱ, 학 ㄴ,ㄷ II"), strict=True,
    ):
        words.append(_word(value, (marker.bbox[2] + 3, 10, marker.bbox[2] + 53, 20)))

    assert choice_texts(words, 10) == ("ㄱ", "ㄴ", "ㄱ,ㄷ", "ㄴ,ㄷ", "ㄱ,ㄴ,ㄷ")


@pytest.mark.parametrize(("pdf_name", "item_number"), (
    ("e2_2023_11.pdf", 4),
    ("e2_2025_09.pdf", 5),
))
def test_real_hapdap_fifth_choice_excludes_page_edge_subject_header(
    tmp_path: Path, pdf_name: str, item_number: int,
) -> None:
    source = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일") / pdf_name
    if not source.is_file():
        pytest.skip(f"missing exam PDF {pdf_name}")
    item = next(row for row in detect_items(source).items if row.item_number == item_number)

    draft = build_editable_draft(
        source, item, tmp_path / f"{pdf_name}-{item_number}",
        layout_style=LayoutStyle.SUNEUNG,
    )

    assert draft.choice_texts[4] == "ㄱ,ㄴ,ㄷ"


@pytest.mark.parametrize("pdf_name", ("p1_2024_11.pdf", "p1_2019_11.pdf"))
def test_real_q20_ask_stops_before_formula_choices(
    tmp_path: Path, pdf_name: str,
) -> None:
    source = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일") / pdf_name
    if not source.is_file():
        pytest.skip(f"missing exam PDF {pdf_name}")
    item = next(row for row in detect_items(source).items if row.item_number == 20)

    draft = build_editable_draft(
        source, item, tmp_path / pdf_name, layout_style=LayoutStyle.SUNEUNG,
    )
    ask = draft.palette_markdown.splitlines()[-6]

    assert all(choice.replace("[[formula:", "\\수식{").replace("]]", "}") not in ask
               for choice in draft.choice_texts)


@pytest.mark.parametrize(("item_number", "template", "table_mark"), (
    (3, "\\수능AI실제비교선지형\\", "\\표4*2\\"),
    (6, "\\수능AI실제비교선지형\\", "\\표1*1\\"),
))
def test_real_structured_prompt_and_choice_headers_stay_editable(
    tmp_path: Path, item_number: int, template: str, table_mark: str,
) -> None:
    source = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p1_2019_11.pdf")
    if not source.is_file():
        pytest.skip("missing p1_2019_11.pdf")
    item = next(row for row in detect_items(source).items if row.item_number == item_number)

    draft = build_editable_draft(
        source, item, tmp_path / str(item_number), layout_style=LayoutStyle.SUNEUNG,
    )

    assert draft.palette_markdown.startswith(template)
    assert table_mark in draft.palette_markdown
    assert not draft.figure_assets
    if item_number == 3:
        assert "(가)～(다)에 해당하는 정보 저장 장치는?" in draft.palette_markdown
    else:
        assert "옳은 것은?\n양성자\t중성자" in draft.palette_markdown


def test_real_q4_experiment_visual_assets_exclude_editable_prose(tmp_path: Path) -> None:
    source = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p1_2019_11.pdf")
    if not source.is_file():
        pytest.skip("missing p1_2019_11.pdf")
    item = next(row for row in detect_items(source).items if row.item_number == 4)

    draft = build_editable_draft(
        source, item, tmp_path / "q4", layout_style=LayoutStyle.SUNEUNG,
    )
    assert draft.palette_markdown.startswith("\\수능합답실험1대사진5선지\\")
    assert len(draft.figure_assets) == 1
    payload = json.loads(
        draft.figure_assets[0].provenance_path.read_text(encoding="utf-8"),
    )
    component_bboxes = tuple(tuple(box) for box in payload["component_bboxes"])
    audits = tuple(
        audit_crop_geometry(read_crop_geometry(CropSourceRequest(
            source, item.page_number, item.item_number, item.bbox,
            bbox, draft.palette_markdown,
        )))
        for bbox in component_bboxes
    )

    assert len(component_bboxes) == 3
    assert all(not audit.issues for audit in audits)
    with Image.open(draft.figure_assets[0].image_path) as composite:
        assert composite.width > composite.height
    print_bbox = payload["bbox"]
    assert (print_bbox[2] - print_bbox[0]) > (print_bbox[3] - print_bbox[1])


def test_real_c1_q19_large_editable_box_starts_on_fresh_page(tmp_path: Path) -> None:
    source = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\c1_2024_06.pdf")
    if not source.is_file():
        pytest.skip("missing c1_2024_06.pdf")
    item = next(row for row in detect_items(source).items if row.item_number == 19)

    draft = build_editable_draft(
        source, item, tmp_path / "c1-q19", layout_style=LayoutStyle.SUNEUNG,
    )

    assert draft.palette_markdown.startswith("\\수능AI실제직접형새쪽\\")
    assert draft.palette_markdown.splitlines()[-5:] == [
        "\\수식{10}", "\\수식{20}", "\\수식{30}", "\\수식{40}", "\\수식{50}",
    ]


@pytest.mark.parametrize(("item_number", "expected"), (
    (4, (
        "[[formula:\\frac{1}{3}]] m/s",
        "[[formula:\\frac{\\sqrt{2}}{3}]] m/s",
        "[[formula:\\frac{1}{2}]] m/s",
        "[[formula:\\frac{2\\sqrt{2}}{3}]] m/s",
        "1m/s",
    )),
    (11, (
        "[[formula:\\frac{1}{2}]] Qº",
        "[[formula:\\frac{2}{3}]] Qº",
        "Qº",
        "[[formula:\\frac{4}{3}]] Qº",
        "[[formula:\\frac{3}{2}]] Qº",
    )),
))
def test_real_p2_stacked_fraction_choices_remain_editable(
    tmp_path: Path, item_number: int, expected: tuple[str, ...],
) -> None:
    source = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p2_2013_11.pdf")
    if not source.is_file():
        pytest.skip("missing p2_2013_11.pdf")
    item = next(row for row in detect_items(source).items if row.item_number == item_number)

    draft = build_editable_draft(
        source, item, tmp_path / str(item_number), layout_style=LayoutStyle.SUNEUNG,
    )

    assert draft.choice_texts == expected


def test_real_p2_wrapped_fifth_choice_keeps_its_continuation(tmp_path: Path) -> None:
    source = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p2_2013_11.pdf")
    if not source.is_file():
        pytest.skip("missing p2_2013_11.pdf")
    item = next(row for row in detect_items(source).items if row.item_number == 13)

    draft = build_editable_draft(
        source, item, tmp_path / "13", layout_style=LayoutStyle.SUNEUNG,
    )

    assert draft.choice_texts[4].endswith("영역의전자기파보다 짧다.")
