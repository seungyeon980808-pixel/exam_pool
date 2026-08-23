from __future__ import annotations

import hashlib
import os
from pathlib import Path

import fitz
import pytest
from PIL import Image

from app.pdf_hwp_roundtrip_models import SourceKind
from app.pdf_hwp_roundtrip_router import route_source
from app.pdf_hwp_roundtrip_source import (
    SelectionIssueKind,
    derive_source_facts,
    normalize_source,
    select_detected_items,
)


KICE = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p1_2019_11.pdf")
EBS = Path(r"C:\Users\user\Desktop\project\31_hwp_palette\2027 수능특강 물리학 I 원본.pdf")
RASTER = Path(__file__).resolve().parents[1] / "assets" / "item_figures" / "item20_original.png"
EBS_ITEMS = (
    1, 12, 24, 35, 37, 48, 60, 72, 84, 96, 108, 120, 132, 144, 156,
    168, 180, 192, 204, 216, 228, 234, 235, 237, 238, 246, 254, 262, 270, 278,
)


def test_real_sources_derive_facts_that_route_to_all_three_kinds(tmp_path: Path) -> None:
    raster = normalize_source(RASTER, tmp_path)

    routes = (
        route_source(derive_source_facts(KICE, KICE.name)),
        route_source(derive_source_facts(EBS, EBS.name)),
        route_source(derive_source_facts(raster.pipeline_pdf, RASTER.name)),
    )

    assert tuple(route.kind for route in routes) == (
        SourceKind.KICE, SourceKind.EBS, SourceKind.RASTER,
    )


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".webp"])
def test_normalize_image_preserves_original_and_exposes_cleanup(
    tmp_path: Path, suffix: str,
) -> None:
    source = tmp_path / f"source{suffix}"
    Image.new("RGB", (16, 12), "white").save(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    run_dir = tmp_path / "run"

    normalized = normalize_source(source, run_dir)

    assert normalized.original_path == source.resolve()
    assert normalized.pipeline_pdf.is_file()
    assert normalized.temporary
    assert normalized.cleanup_target == normalized.pipeline_pdf
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with fitz.open(normalized.pipeline_pdf) as document:
        assert document.page_count == 1


def test_normalize_pdf_is_non_temporary_passthrough(tmp_path: Path) -> None:
    normalized = normalize_source(KICE, tmp_path)

    assert normalized.pipeline_pdf == KICE.resolve()
    assert not normalized.temporary
    assert normalized.cleanup_target is None


def test_normalize_image_replaces_corrupt_cached_pdf(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 12), "white").save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    target = run_dir / f"source-{digest}.normalized.pdf"
    target.write_bytes(b"not a pdf")

    normalized = normalize_source(source, run_dir)

    assert normalized.pipeline_pdf == target
    with fitz.open(target) as document:
        assert document.page_count == 1


def test_normalize_image_reuses_valid_cached_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 12), "white").save(source)
    first = normalize_source(source, tmp_path / "run")
    before = first.pipeline_pdf.read_bytes()

    monkeypatch.setattr(os, "replace", lambda *_: pytest.fail("valid cache was replaced"))

    second = normalize_source(source, tmp_path / "run")

    assert second == first
    assert second.pipeline_pdf.read_bytes() == before


def test_normalize_image_publishes_through_unique_same_directory_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 12), "white").save(source)
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def record_replace(source_path: Path, target_path: Path) -> None:
        replacements.append((Path(source_path), Path(target_path)))
        real_replace(source_path, target_path)

    monkeypatch.setattr(os, "replace", record_replace)

    normalized = normalize_source(source, tmp_path / "run")

    assert len(replacements) == 1
    temporary, target = replacements[0]
    assert target == normalized.pipeline_pdf
    assert temporary.parent == target.parent
    assert temporary != target
    assert not temporary.exists()


def test_normalize_image_replace_failure_preserves_source_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 12), "white").save(source)
    before = source.read_bytes()
    run_dir = tmp_path / "run"

    def fail_replace(_source_path: Path, _target_path: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        normalize_source(source, run_dir)

    assert source.read_bytes() == before
    assert tuple(run_dir.glob("*.normalized.pdf")) == ()
    assert tuple(run_dir.glob(".*.tmp.pdf")) == ()


def test_real_detection_selects_all_kice_exact_ebs_and_raster(tmp_path: Path) -> None:
    raster = normalize_source(RASTER, tmp_path)

    selected = (
        select_detected_items(KICE, None),
        select_detected_items(EBS, EBS_ITEMS),
        select_detected_items(raster.pipeline_pdf, (1,)),
    )

    assert tuple(len(result.items) for result in selected) == (20, 30, 1)
    assert all(result.ok for result in selected)
    assert tuple(item.item_number for item in selected[1].items) == EBS_ITEMS


def test_selection_reports_missing_and_duplicate_in_stable_order(tmp_path: Path) -> None:
    source = tmp_path / "duplicates.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=600)
        page.insert_text((50, 50), "1. first")
        page.insert_text((50, 250), "1. second")
        document.save(source)

    result = select_detected_items(source, (2, 1))

    assert tuple((issue.kind, issue.item_number) for issue in result.issues) == (
        (SelectionIssueKind.DUPLICATE, 1),
        (SelectionIssueKind.MISSING, 2),
    )
    assert not result.ok
    assert result.items == ()
