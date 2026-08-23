from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.pdf_hwp_roundtrip_manifest import (
    ApprovedFirstRunManifest,
    load_manifest,
    verify_manifest_sources,
)
from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import LayoutStyle


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools" / "pdf_hwp_roundtrip_approved_first_run.json"


def test_loads_pinned_approved_first_run() -> None:
    manifest = load_manifest(MANIFEST)

    assert manifest.run_id == "approved-first-run"
    assert len(manifest.kice_papers) == 10
    assert len(manifest.ebs_source.sample) == 30
    assert len(manifest.fixed_regressions) == 7
    assert manifest.exclusions.union == 60
    assert manifest.exclusions.selected_overlap == 0
    assert {paper.subject for paper in manifest.kice_papers} == {
        "p1", "p2", "c1", "c2", "b1", "b2", "e1", "e2"
    }
    assert {35, 37, 234, 235, 237, 238} <= {
        sample.item for sample in manifest.ebs_source.sample
    }


def test_manifest_is_frozen_and_rejects_bad_hash_and_duplicate_item() -> None:
    manifest = load_manifest(MANIFEST)
    with pytest.raises(ValidationError):
        setattr(manifest, "run_id", "changed")

    payload = manifest.model_dump(mode="json")
    payload["raster_fixture"]["sha256"] = "bad"
    with pytest.raises(ValidationError):
        ApprovedFirstRunManifest.model_validate(payload)

    payload = manifest.model_dump(mode="json")
    payload["ebs_source"]["sample"][1] = payload["ebs_source"]["sample"][0]
    with pytest.raises(ValidationError, match="duplicate EBS sample items"):
        ApprovedFirstRunManifest.model_validate(payload)


def test_verify_sources_returns_typed_failures(tmp_path: Path) -> None:
    present = tmp_path / "source.pdf"
    present.write_bytes(b"fixture")
    digest = hashlib.sha256(b"fixture").hexdigest()
    manifest = load_manifest(MANIFEST)
    papers = list(manifest.kice_papers)
    papers[0] = papers[0].model_copy(update={"path": present, "sha256": digest})
    papers[1] = papers[1].model_copy(update={"path": tmp_path / "missing.pdf"})
    changed = manifest.model_copy(update={"kice_papers": tuple(papers)})

    result = verify_manifest_sources(changed)

    by_id = {check.source_id: check for check in result.checks}
    assert by_id[changed.kice_papers[0].paper_id].ok
    assert not by_id[changed.kice_papers[1].paper_id].exists
    assert not result.ok


def test_raster_companion_hash_mismatch_is_rejected() -> None:
    manifest = load_manifest(MANIFEST)
    raster = manifest.raster_fixture.model_copy(update={
        "companion_pdf_sha256": "0" * 64,
    })

    result = verify_manifest_sources(
        manifest.model_copy(update={"raster_fixture": raster}),
    )

    companion = next(check for check in result.checks if check.source_id.endswith(":companion"))
    assert companion.exists
    assert companion.hash_matches is False
    assert not result.ok


def test_real_raster_companion_item_builds_editable_draft(tmp_path: Path) -> None:
    raster = load_manifest(MANIFEST).raster_fixture
    item = next(
        detected for detected in detect_items(raster.companion_pdf_path).items
        if detected.item_number == raster.companion_item
    )

    draft = build_editable_draft(
        raster.companion_pdf_path, item, tmp_path, layout_style=LayoutStyle.SUNEUNG,
    )

    assert draft.item_number == 20
    assert "\\수능정답2대사진5선지\\" in draft.palette_markdown
    assert len(draft.choice_texts) == 5
    assert "\\frac{13}{17}h" in draft.choice_texts[4]
    assert len(draft.figure_assets) == 2
