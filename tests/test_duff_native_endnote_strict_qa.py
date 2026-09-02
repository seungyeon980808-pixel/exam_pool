"""Copyright-free regression tests for the four-exam strict endnote gate."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app import duff_native_endnote_strict_qa as qa


EXAMS = qa.EXAM_KEYS


def _source_manifest(path: Path, source_hash: str) -> None:
    source = "x"
    payload = {
        "schema_version": "math-source-manifest-v1",
        "status": "VERIFIED",
        "source_pdf_sha256": source_hash,
        "page_count": 1,
        "uncertainties": [],
        "content_sha256": "b" * 64,
        "formula_count": 1,
        "pages": [{
            "pdf_page": 1,
            "status": "VERIFIED",
            "render_dpi": 600,
            "page_crop_sha256": "c" * 64,
            "items": [{
                "item_id": "SYN-COMMON-01",
                "formulas": [{
                    "ordinal": 1,
                    "review_status": "VERIFIED",
                    "bbox_pt": [1, 1, 10, 10],
                    "source_crop_sha256": "d" * 64,
                    "source": source,
                    "mathir": {"source_sha256": hashlib.sha256(source.encode()).hexdigest()},
                }],
            }],
        }],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _hwpx(path: Path, *, font: str = "HYhwpEQ", base: str = "1100") -> None:
    equations = "".join(
        f'<hp:equation font="{font}" baseUnit="{base}"><hp:script>x</hp:script></hp:equation>'
        for _ in range(2)
    )
    notes = "".join("<hp:endNote/>" for _ in range(46))
    autonums = "".join('<hp:autoNum numType="ENDNOTE"/>' for _ in range(46))
    xml = f'<hs:sec xmlns:hs="urn:syn" xmlns:hp="http://www.hancom.co.kr/schema/2011/hpf">{equations}{notes}{autonums}</hs:sec>'
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Contents/section0.xml", xml)


def _spec(tmp_path: Path, *, source_manifests: bool = True, com_status: str = "PASS", font: str = "HYhwpEQ") -> Path:
    source_hash = "a" * 64
    exams: dict[str, dict[str, str]] = {}
    for key in EXAMS:
        problem = tmp_path / f"{key}-problem.pdf"
        solution = tmp_path / f"{key}-solution.pdf"
        problem.write_bytes(b"pdf")
        solution.write_bytes(b"pdf")
        pm = tmp_path / f"{key}-problem-manifest.json"
        sm = tmp_path / f"{key}-solution-manifest.json"
        if source_manifests:
            _source_manifest(pm, source_hash)
            _source_manifest(sm, source_hash)
        hwpx = tmp_path / f"{key}.hwpx"
        _hwpx(hwpx, font=font)
        hwp = tmp_path / f"{key}.hwp"
        hwp.write_bytes(b"hwp")
        com = tmp_path / f"{key}-com.json"
        com.write_text(json.dumps({
            "status": com_status,
            "roundtrip": com_status == "PASS",
            "hwp_equation_count": 2,
            "hwpx_equation_count": 2,
            "endnote_count": 46,
        }), encoding="utf-8")
        visual = tmp_path / f"{key}-visual.json"
        visual.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        style = tmp_path / f"{key}-style.json"
        style.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        image = tmp_path / f"{key}-image.json"
        image.write_text(json.dumps({"forbidden_images": 0, "forbidden_endnote_images": 0}), encoding="utf-8")
        endnote = tmp_path / f"{key}-endnote-manifest.json"
        endnote.write_text(json.dumps({"items": []}), encoding="utf-8")
        exams[key] = {
            "problem_pdf": str(problem),
            "solution_pdf": str(solution),
            "problem_manifest": str(pm) if source_manifests else "",
            "solution_manifest": str(sm) if source_manifests else "",
            "hwpx": str(hwpx),
            "hwp": str(hwp),
            "com_report": str(com),
            "visual_report": str(visual),
            "style_report": str(style),
            "image_report": str(image),
            "endnote_manifest": str(endnote),
        }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"exams": exams}), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fake_pdf_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        qa,
        "_pdf_info",
        lambda path: {"path": str(path), "exists": True, "sha256": "a" * 64, "page_count": 1},
    )
    monkeypatch.setattr(qa, "audit_endnotes", lambda *_args: {"status": "PASS", "findings": []})


def test_missing_source_manifest_is_fail_closed(tmp_path: Path) -> None:
    spec = _spec(tmp_path, source_manifests=False)
    report = qa.audit_batch(spec)
    assert report["status"] == "FAIL"
    assert any(finding["code"] == "MATH_SOURCE_MANIFEST_MISSING" for row in report["exams"].values() for finding in row["findings"])


def test_com_roundtrip_is_required(tmp_path: Path) -> None:
    spec = _spec(tmp_path, com_status="NOT_RUN")
    report = qa.audit_batch(spec)
    assert report["status"] == "FAIL"
    assert all(any(finding["code"] == "COM_ROUNDTRIP_FAIL" for finding in row["findings"]) for row in report["exams"].values())


def test_equation_style_mismatch_is_independent_failure(tmp_path: Path) -> None:
    spec = _spec(tmp_path, font="HancomEQN")
    report = qa.audit_batch(spec)
    assert report["status"] == "FAIL"
    assert all(any(finding["code"] == "HWPX_EQUATION_STYLE_INVALID" for finding in row["findings"]) for row in report["exams"].values())


def test_clean_four_exam_spec_passes(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    report = qa.audit_batch(spec)
    assert report["status"] == "PASS"
    assert report["exam_count"] == 4
    assert all(row["status"] == "PASS" for row in report["exams"].values())
