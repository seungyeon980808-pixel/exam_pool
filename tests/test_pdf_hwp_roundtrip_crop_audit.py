from __future__ import annotations

import json
from pathlib import Path

from app.pdf_hwp_roundtrip_crop_audit import (
    CropAuditIssue,
    CropGeometry,
    TextGeometry,
    audit_crop_geometry,
)


FIXTURE = Path(__file__).parent / "fixtures" / "pdf_hwp_roundtrip" / "known_failures.json"


def test_known_bad_crop_geometry_is_rejected_with_the_pinned_reason() -> None:
    # Given: source hashes and decisive geometry captured from the known bad crops.
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))

    # When: each crop crosses the geometry audit boundary.
    observed = {
        record["case_id"]: [issue.value for issue in audit_crop_geometry(
            CropGeometry.model_validate(record)
        ).issues]
        for record in records
    }

    # Then: prose-contaminated and clipped crops cannot silently pass as figures.
    assert observed == {
        record["case_id"]: record["expected_issues"] for record in records
    }


def test_crop_outside_detected_item_is_source_boundary_spill() -> None:
    # Given: a crop whose right edge escapes the detected source item.
    geometry = CropGeometry(
        source_sha256="0" * 64,
        page_number=1,
        item_number=7,
        item_bbox=(10.0, 20.0, 110.0, 220.0),
        crop_bbox=(50.0, 60.0, 115.0, 160.0),
    )

    # When: geometry is audited.
    result = audit_crop_geometry(geometry)

    # Then: the escaped crop is quarantined by its stable machine code.
    assert result.issues == (CropAuditIssue.SOURCE_BOUNDARY_SPILL,)


def test_short_diagram_labels_and_contained_image_are_safe() -> None:
    # Given: a contained figure with only compact diagram labels.
    geometry = CropGeometry(
        source_sha256="1" * 64,
        page_number=1,
        item_number=8,
        item_bbox=(0.0, 0.0, 300.0, 300.0),
        crop_bbox=(50.0, 50.0, 250.0, 250.0),
        text_regions=(TextGeometry(
            bbox=(90.0, 80.0, 110.0, 92.0), character_count=2, word_count=1,
        ),),
        image_bboxes=((70.0, 70.0, 230.0, 220.0),),
    )

    # When/Then: diagram-local geometry produces no false-positive gate.
    assert audit_crop_geometry(geometry).issues == ()


def test_rasterized_figure_text_not_repeated_in_editable_text_is_safe() -> None:
    geometry = CropGeometry(
        source_sha256="2" * 64,
        page_number=1,
        item_number=2,
        item_bbox=(0.0, 0.0, 400.0, 300.0),
        crop_bbox=(20.0, 20.0, 360.0, 180.0),
        editable_text="이에 대한 설명으로 옳은 것만을 고른 것은?",
        text_regions=(TextGeometry(
            bbox=(30.0, 30.0, 350.0, 55.0),
            character_count=34,
            word_count=10,
            text="그림 가는 방사선을 검출하는 장치의 구조를 나타낸 것이다",
        ),),
    )

    assert audit_crop_geometry(geometry).issues == ()


def test_prose_repeated_in_editable_text_remains_contamination() -> None:
    duplicated = "물체가 실로 연결되어 정지해 있을 때 가속도의 크기를 구한다"
    geometry = CropGeometry(
        source_sha256="3" * 64,
        page_number=1,
        item_number=35,
        item_bbox=(0.0, 0.0, 400.0, 300.0),
        crop_bbox=(20.0, 20.0, 360.0, 180.0),
        editable_text=f"그림과 같이 {duplicated}. 이에 대한 설명은?",
        text_regions=(TextGeometry(
            bbox=(30.0, 30.0, 350.0, 55.0),
            character_count=30,
            word_count=10,
            text=duplicated,
        ),),
    )

    assert audit_crop_geometry(geometry).issues == (
        CropAuditIssue.CROP_CONTAMINATION,
    )


def test_incidental_image_sliver_is_not_clipping_but_material_overlap_is() -> None:
    base = {
        "source_sha256": "4" * 64,
        "page_number": 1,
        "item_number": 24,
        "item_bbox": (0.0, 0.0, 400.0, 400.0),
        "crop_bbox": (20.0, 20.0, 360.0, 200.0),
    }
    sliver = CropGeometry(**base, image_bboxes=((100.0, 195.0, 200.0, 250.0),))
    material = CropGeometry(**base, image_bboxes=((100.0, 170.0, 200.0, 300.0),))

    assert audit_crop_geometry(sliver).issues == ()
    assert audit_crop_geometry(material).issues == (CropAuditIssue.CROP_CLIPPING,)


def test_semantically_accepted_source_crop_does_not_use_raw_image_bounds() -> None:
    geometry = CropGeometry(
        source_sha256="5" * 64,
        page_number=1,
        item_number=8,
        item_bbox=(0.0, 0.0, 400.0, 400.0),
        crop_bbox=(100.0, 100.0, 300.0, 220.0),
        semantic_selection=True,
        image_bboxes=((110.0, 110.0, 290.0, 260.0),),
    )

    assert audit_crop_geometry(geometry).issues == ()
