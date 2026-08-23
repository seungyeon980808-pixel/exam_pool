from __future__ import annotations

import json
from pathlib import Path

import fitz
from PIL import Image
import pytest

import tools.pdf_hwp_roundtrip_acceptance_evidence as subject
from app.pdf_hwp_pipeline_models import ConversionUnit, LayoutStyle
from app.pdf_hwp_roundtrip_readback import HwpReadbackReport, HwpSnapshot
from app.pdf_hwp_roundtrip_structure import parse_prepared_structure
from app.pdf_hwp_roundtrip_unit_store import (
    FailureCode,
    ItemFailure,
    PreparationPayload,
    PreparedUnitRecord,
    write_prepared_units,
)
from tools.pdf_hwp_roundtrip_acceptance_evidence import (
    generate_c002,
    generate_c003,
    main,
)


def _hapdap(item_number: int) -> ConversionUnit:
    values = (
        "\\수능합답1대사진5선지\\", str(item_number), "passage", "figure",
        "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?",
        "first claim", "second claim", "third claim", "ㄱ", "ㄷ", "ㄱ, ㄴ", "ㄴ, ㄷ", "ㄱ, ㄴ, ㄷ",
    )
    return ConversionUnit(item_number, "\n".join(values))


def _prepared_fixture(root: Path, *, ebs: bool) -> None:
    source = root / "source.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF fixture")
    if ebs:
        units = tuple(
            PreparedUnitRecord(
                _hapdap(number), f"{number:064x}",
                parse_prepared_structure(
                    _hapdap(number), 1, (0.0, 0.0, 100.0, 100.0),
                    LayoutStyle.SUNEUNG,
                ),
            )
            for number in (234, 235, 237, 238)
        )
        failures = tuple(ItemFailure(
            number, FailureCode.CROP_CONTAMINATION, "pinned crop", f"{number + 1:064x}",
        ) for number in (35, 37))
    else:
        units = ()
        failures = (ItemFailure(
            1, FailureCode.CROP_CLIPPING, "pinned clipping", f"{1:064x}",
        ),)
    write_prepared_units(root / "prepared-units.json", PreparationPayload(
        source, "a" * 64, LayoutStyle.SUNEUNG, units, failures,
    ))


def _generated_hapdap_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    font = Path(r"C:\Windows\Fonts\malgun.ttf")
    with fitz.open() as document:
        page = document.new_page(width=600, height=900)
        for index, number in enumerate((234, 235, 237, 238)):
            top = 60 + index * 200
            for offset, text in enumerate((
                f"{number}. prompt",
                "<보기> ㄱ. one ㄴ. two ㄷ. three",
                "① ㄱ ② ㄷ ③ ㄱ, ㄴ ④ ㄴ, ㄷ ⑤ ㄱ, ㄴ, ㄷ",
            )):
                page.insert_text(
                    (36, top + offset * 34), text, fontsize=11,
                    fontname="acceptance-font", fontfile=font,
                )
        document.save(path)


def test_c002_proves_material_checkpoint_resume_and_quarantine(tmp_path: Path) -> None:
    evidence = tmp_path.joinpath(*("long-evidence-segment" for _ in range(4)))

    result = generate_c002(evidence)

    proof = json.loads(result.proof.read_text(encoding="utf-8"))
    assert result.transcript.stat().st_size > 0
    assert proof["paused"]["completed_stages"] == ["route", "extract"]
    assert proof["calls"] == ["route:p1_2024_11.pdf", "extract:p1_2024_11.pdf", "typeset:p1_2024_11.pdf", "verify:p1_2024_11.pdf", "route:malformed.bin"]
    assert proof["resume_reused_extract"] is True
    assert proof["quarantine"]["status"] == "quarantined"
    assert proof["quarantine"]["hwp_artifact_exists"] is False
    assert proof["prior_checkpoint_unchanged"] is True
    assert proof["process_invocations"] == 3
    assert proof["cleanup_receipt"] == {
        "exists_after": False,
        "removed": True,
        "temporary_root_contained": True,
    }
    assert all(row["returncode"] == 0 for row in proof["commands"])
    assert not tuple(evidence.glob(".acceptance-c002-*"))


def test_c003_reads_dynamic_namespace_and_emits_paired_contact_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run"
    namespace = run_root / "namespaces" / "fixture-namespace"
    sources = namespace / "sources"
    ebs = sources / "ebs_2027_physics1-fixture"
    e2 = sources / "e2_2024_09-fixture"
    _prepared_fixture(ebs, ebs=True)
    _prepared_fixture(e2, ebs=False)
    conversion = ebs / "conversion"
    _generated_hapdap_pdf(conversion / "converted.pdf")
    (conversion / "converted.hwp").write_bytes(b"hwp fixture")
    snapshot = HwpSnapshot("hwp", "5.1", "editable", (), 1, 1, 7, 10, 5, 4, ())
    monkeypatch.setattr(subject, "inspect_hwp", lambda path, expected: HwpReadbackReport(snapshot, ()))
    Image.new("RGB", (24, 32), "white").save(conversion / "page-1.png")
    renders = ebs / "verification-evidence" / "renders"
    renders.mkdir(parents=True)
    Image.new("RGB", (24, 32), "white").save(renders / "source-item-0234.png")
    Image.new("RGB", (24, 32), "black").save(renders / "generated-item-0234.png")
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "run-metadata.json").write_text(json.dumps({
        "schema_version": 1,
        "namespace_id": "fixture-namespace",
        "namespace_root": str(namespace.resolve()),
        "manifest_sha256": "b" * 64,
        "code_dependency_sha256": "c" * 64,
        "selection_sha256": "d" * 64,
        "selection_contract": [],
    }), encoding="utf-8")

    result = generate_c003(run_root, tmp_path / "evidence")

    payload = json.loads(result.report.read_text(encoding="utf-8"))
    assert payload["namespace_id"] == "fixture-namespace"
    assert payload["hapdap_items"] == [234, 235, 237, 238]
    assert payload["bogi_slot_counts"] == {str(number): 3 for number in (234, 235, 237, 238)}
    assert payload["ebs_crop_failures"] == {"35": "crop_contamination", "37": "crop_contamination"}
    assert payload["e2_q1_failure"] == "crop_clipping"
    assert payload["hwp_pages"] == 7
    assert payload["hwp_kordoc_pages"] == 1
    contract = payload["generated_pdf_contract"]
    assert [item["item_number"] for item in contract["items"]] == [234, 235, 237, 238]
    assert contract["issues"] == []
    assert payload["passed"] is True
    assert result.contact_sheet.stat().st_size > 0


def test_cli_returns_nonzero_when_acceptance_assertion_fails(tmp_path: Path) -> None:
    assert main(("--run-root", str(tmp_path), "--evidence-dir", str(tmp_path / "evidence"))) == 1
