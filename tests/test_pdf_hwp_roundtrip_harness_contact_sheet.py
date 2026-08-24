from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

from app.pdf_hwp_roundtrip_models import ArtifactHash, SourceKind
from app.pdf_hwp_roundtrip_runner import RunStatus, SourceRunResult
from tools.pdf_hwp_roundtrip_harness_support import (
    ExtraFailure,
    ReportSource,
    _write_contact_sheet,
)


def _source(tmp_path: Path, source_id: str, first_row: tuple[int, int, int] | None) -> ReportSource:
    output_dir = tmp_path / source_id
    if first_row is not None:
        evidence = output_dir / "verification-evidence" / "item-failures.png"
        evidence.parent.mkdir(parents=True)
        sheet = Image.new("RGB", (884, 3536), (0, 0, 255))
        sheet.paste(first_row, (0, 0, 884, 320))
        sheet.save(evidence)
        sheet.close()
    result = SourceRunResult(
        Path(f"{source_id}.pdf"), ArtifactHash(source_id), SourceKind.KICE,
        RunStatus.FAILED, (),
    )
    return ReportSource(source_id, output_dir, result)


def test_contact_sheet_uses_two_column_first_failure_previews(tmp_path: Path) -> None:
    sources = (
        _source(tmp_path, "source-a", (255, 0, 0)),
        _source(tmp_path, "source-b", (0, 255, 0)),
    )
    failures = tuple(
        ExtraFailure(source.result.artifact_hash, source.result.source_path, "pdf", "mismatch", "")
        for source in sources
    )
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"

    _write_contact_sheet(sources, failures, first)
    _write_contact_sheet(sources, failures, second)

    with Image.open(first) as contact:
        colors = {color for _, color in contact.getcolors(maxcolors=1_000_000) or ()}
        assert contact.size == (900, 260)
        assert {(255, 0, 0), (0, 255, 0)} <= colors
        assert (0, 0, 255) not in colors
    assert first.read_bytes() == second.read_bytes()


def test_contact_sheet_summarizes_failure_without_image(tmp_path: Path) -> None:
    image_source = _source(tmp_path, "source-image", (255, 0, 0))
    text_source = _source(tmp_path, "source-text", None)
    failures = (
        ExtraFailure(image_source.result.artifact_hash, image_source.result.source_path,
                     "pdf", "visual_mismatch", "item 1"),
        ExtraFailure(text_source.result.artifact_hash, text_source.result.source_path,
                     "extract", "draft_failed", "item 20"),
    )
    target = tmp_path / "overview.png"

    _write_contact_sheet((image_source, text_source), failures, target)

    with Image.open(target) as contact:
        text_region = contact.crop((460, 68, 880, 160))
    background = Image.new("RGB", text_region.size, "#f1f3f5")
    assert ImageChops.difference(text_region, background).getbbox() is not None
    text_region.close()
    background.close()
