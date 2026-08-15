from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "assets" / "hwp_templates"


def _dump(name: str) -> str:
    if shutil.which("rhwp") is None:
        pytest.skip("rhwp CLI is required for binary HWP geometry inspection")
    completed = subprocess.run(
        ["rhwp", "dump", str(TEMPLATES / name), "--section", "0"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


@pytest.mark.parametrize(
    "name, shape",
    [
        ("csat_direct_two_small_caption_free.hwp", "2행×2열"),
        ("csat_direct_two_large_caption_free.hwp", "2행×2열"),
        ("csat_direct_vertical_pair.hwp", "2행×1열"),
        ("csat_hapdap_two_small_caption_free.hwp", "2행×2열"),
        ("csat_hapdap_two_large_caption_free.hwp", "2행×2열"),
        ("csat_hapdap_vertical_pair.hwp", "2행×1열"),
    ],
)
def test_figure_table_follows_its_question_paragraph(name: str, shape: str) -> None:
    dump = _dump(name)

    assert shape in dump
    assert "treat_as_char=true" in dump
    assert "wrap=자리차지" in dump
    assert "vert=문단(0=0.0mm)" in dump
    assert "horz=문단(0=0.0mm)" in dump


@pytest.mark.parametrize(
    "name",
    [
        "csat_direct_two_small_caption_free.hwp",
        "csat_direct_two_large_caption_free.hwp",
        "csat_hapdap_two_small_caption_free.hwp",
        "csat_hapdap_two_large_caption_free.hwp",
    ],
)
def test_split_pair_keeps_one_caption_per_image(name: str) -> None:
    dump = _dump(name)

    assert 'text="(가)"' in dump
    assert 'text="(나)"' in dump


@pytest.mark.parametrize(
    "name",
    [
        "csat_direct_two_small_caption_free.hwp",
        "csat_direct_two_large_caption_free.hwp",
        "csat_hapdap_two_small_caption_free.hwp",
        "csat_hapdap_two_large_caption_free.hwp",
    ],
)
def test_split_pair_table_uses_full_column_without_left_overflow(name: str) -> None:
    dump = _dump(name)
    figure_paragraph = re.split(r"--- 문단 0\.\d+ ---", dump)[2]

    assert "margins: left=0 right=0 indent=0" in figure_paragraph


@pytest.mark.parametrize(
    "name",
    ["csat_direct_one_small.hwp", "csat_direct_one_large.hwp"],
)
def test_single_image_has_no_split_caption(name: str) -> None:
    dump = _dump(name)

    assert 'text="(가)"' not in dump
    assert 'text="(나)"' not in dump


@pytest.mark.parametrize(
    "name, paragraph_count",
    [
        ("csat_hapdap_two_small_caption_free.hwp", 5),
        ("csat_hapdap_two_large_caption_free.hwp", 5),
        ("csat_hapdap_one_large.hwp", 5),
        ("csat_direct_one_small.hwp", 4),
        ("csat_direct_one_large.hwp", 5),
        ("csat_direct_two_large_caption_free.hwp", 5),
    ],
)
def test_question_flow_templates_keep_korean_paragraphs_and_block_together(
    name: str, paragraph_count: int,
) -> None:
    dump = _dump(name)
    paragraphs = re.split(r"--- 문단 0\.\d+ ---", dump)[1:]

    assert len(paragraphs) == paragraph_count
    assert all(
        "keep: with_next=true keep_lines=true" in paragraph
        for paragraph in paragraphs[:-1]
    )
    assert "keep: with_next=false" in paragraphs[-1]


@pytest.mark.parametrize(
    "name",
    [
        "csat_direct_vertical_pair.hwp",
        "csat_hapdap_vertical_pair.hwp",
    ],
)
def test_vertical_composite_has_no_external_split_caption(name: str) -> None:
    dump = _dump(name)

    assert 'text="(가)"' not in dump
    assert 'text="(나)"' not in dump
    assert 'text="\\자료\\"' in dump


@pytest.mark.parametrize(
    "name",
    [
        "csat_direct_two_small_caption_free.hwp",
        "csat_direct_two_large_caption_free.hwp",
        "csat_direct_vertical_pair.hwp",
        "csat_hapdap_two_small_caption_free.hwp",
        "csat_hapdap_two_large_caption_free.hwp",
        "csat_hapdap_vertical_pair.hwp",
    ],
)
def test_composite_template_retains_all_five_choices(name: str) -> None:
    dump = _dump(name)

    assert "① \\1\\" in dump
    assert "⑤ \\5\\" in dump


def test_every_derived_template_physical_markers_match_declared_slots() -> None:
    from app.integrations.palette_registry import _DERIVED_TEMPLATES

    for label, (filename, declared_slots) in _DERIVED_TEMPLATES.items():
        physical_slots = re.findall(r"\\([^\\{}\n]+)\\", _dump(filename))
        assert sorted(physical_slots) == sorted(declared_slots), (
            f"{label} declares {declared_slots!r}, but {filename} physically contains "
            f"{physical_slots!r}"
        )
        assert len(physical_slots) == len(set(physical_slots))


@pytest.mark.parametrize(
    "name", ["csat_hapdap_one_small.hwp", "csat_hapdap_one_large.hwp"],
)
def test_hapdap_fifth_choice_sentinel_survives_real_pdf_render(
    name: str, tmp_path: Path,
) -> None:
    fitz = pytest.importorskip("fitz")
    filled = tmp_path / f"{Path(name).stem}-sentinel.hwp"
    rendered = tmp_path / f"{Path(name).stem}-sentinel.pdf"
    subprocess.run(
        [
            "rhwp", "edit", "replace-text", str(TEMPLATES / name),
            "--find", "\\5\\", "--replace", "FIFTH_CHOICE_SENTINEL",
            "-o", str(filled),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["rhwp", "export-pdf", str(filled), "-o", str(rendered)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    with fitz.open(rendered) as document:
        text = "".join(page.get_text() for page in document)
    assert "⑤FIFTH_CHOICE_SENTINEL" in text.replace(" ", "")


def test_palette_registry_exposes_full_question_pair_variants() -> None:
    from app.integrations.palette_registry import _DERIVED_TEMPLATES

    expected = {
        "수능정답2대사진5선지": "csat_direct_two_large_caption_free.hwp",
        "수능정답상하사진5선지": "csat_direct_vertical_pair.hwp",
        "수능합답2대사진5선지": "csat_hapdap_two_large_caption_free.hwp",
        "수능합답상하사진5선지": "csat_hapdap_vertical_pair.hwp",
    }
    for label, filename in expected.items():
        registered_filename, slots = _DERIVED_TEMPLATES[label]
        assert registered_filename == filename
        assert slots[-5:] == ["1", "2", "3", "4", "5"]
        assert (TEMPLATES / filename).is_file()

    assert "자료" in _DERIVED_TEMPLATES["수능정답상하사진5선지"][1]


def test_palette_registry_exposes_prompt_and_five_graphical_choice_slots() -> None:
    from app.integrations.palette_registry import _DERIVED_TEMPLATES

    filename, slots = _DERIVED_TEMPLATES["수능정답1대사진그림5선지"]

    assert filename == "csat_direct_one_large_graphical_choices.hwp"
    assert slots == [
        "문항번호", "문두", "사진1", "발문",
        "선지사진1", "선지사진2", "선지사진3", "선지사진4", "선지사진5",
    ]
    assert (TEMPLATES / filename).is_file()


def test_graphical_choice_template_contains_distinct_ordered_image_markers() -> None:
    dump = _dump("csat_direct_one_large_graphical_choices.hwp")

    assert 'text="\\사진1\\"' in dump
    for index in range(1, 6):
        assert f'text="\\선지사진{index}\\"' in dump


def test_three_panel_hapdap_template_has_editable_ordered_caption_slots() -> None:
    from app.integrations.palette_registry import _DERIVED_TEMPLATES

    filename, slots = _DERIVED_TEMPLATES["수능합답3소사진5선지"]
    assert filename == "csat_hapdap_three_small_captioned.hwp"
    assert slots == [
        "문항번호", "문두",
        "사진1", "(가)", "사진2", "(나)", "사진3", "(다)",
        "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
    ]
    dump = _dump(filename)
    assert "2행×3열" in dump
    for name in slots:
        assert f"\\{name}\\" in dump
    assert [dump.index(f"\\사진{index}\\") for index in range(1, 4)] == sorted(
        dump.index(f"\\사진{index}\\") for index in range(1, 4)
    )
    assert [dump.index(f"\\({caption})\\") for caption in "가나다"] == sorted(
        dump.index(f"\\({caption})\\") for caption in "가나다"
    )
    paragraphs = re.split(r"--- 문단 0\.\d+ ---", dump)[1:]
    assert len(paragraphs) == 5
    assert all(
        "keep: with_next=true keep_lines=true" in paragraph
        for paragraph in paragraphs[:4]
    )
    assert "keep: with_next=false" in paragraphs[4]


def test_graphical_choice_question_block_stays_with_final_choice_table() -> None:
    dump = _dump("csat_direct_one_large_graphical_choices.hwp")
    paragraphs = re.split(r"--- 문단 0\.\d+ ---", dump)[1:]

    assert len(paragraphs) == 4
    assert all("keep: with_next=true" in paragraph for paragraph in paragraphs[:3])
    assert "keep: with_next=false" in paragraphs[3]
    choice_table = dump.split("표: 2행×6열", maxsplit=1)[1]
    size = re.search(r"size=\d+×\d+\([^×]+×([^m]+)mm\)", choice_table)
    assert size is not None
    assert float(size.group(1)) <= 42.0


def test_palette_registry_exposes_one_large_hapdap_photo_contract() -> None:
    from app.integrations.palette_registry import _DERIVED_TEMPLATES

    filename, slots = _DERIVED_TEMPLATES["수능합답1대사진5선지"]

    assert filename == "csat_hapdap_one_large.hwp"
    assert slots == [
        "문항번호", "문두", "사진1", "발문",
        "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
    ]
    assert (TEMPLATES / filename).is_file()


def test_palette_registry_exposes_one_small_hapdap_photo_contract() -> None:
    from app.integrations.palette_registry import _DERIVED_TEMPLATES

    filename, slots = _DERIVED_TEMPLATES["수능합답1소사진5선지"]

    assert filename == "csat_hapdap_one_small.hwp"
    assert slots == [
        "문항번호", "문두", "사진1", "발문",
        "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
    ]
    assert (TEMPLATES / filename).is_file()
