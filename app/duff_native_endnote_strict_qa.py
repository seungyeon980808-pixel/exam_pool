"""Fail-closed aggregate QA for the four Duff native-endnote exams.

This module is intentionally an audit boundary, not a converter.  It refuses
to call a font-normalized HWPX a finished result when reviewed PDF provenance
or the HWP/COM round trip is missing.  Copyrighted PDFs and generated files
are supplied by an external JSON spec at runtime and are never stored in the
repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
import xml.etree.ElementTree as ET

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover - optional at import time
    fitz = None

from .endnote_qa_gate import audit_hwpx as audit_endnotes
from .math_source_manifest import load_and_validate


SCHEMA_VERSION = "duff-native-endnote-strict-qa-v1"
EXAM_KEYS = ("july_2026", "jongro_apr_2026", "daesung_apr17_2026", "may_2026")
TARGET_FORMULA_FONT = "HYhwpEQ"
TARGET_BASE_UNIT = "1100"
EXPECTED_ITEMS = 46


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1].split(":", 1)[-1].lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_info(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return result
    result.update({"sha256": _sha256(path), "bytes": path.stat().st_size})
    if fitz is None:
        result["page_count"] = None
        result["error"] = "PyMuPDF (fitz) is unavailable; page verification cannot be proven"
        return result
    try:
        with fitz.open(path) as document:
            result["page_count"] = len(document)
            result["text_chars"] = sum(len(page.get_text("text")) for page in document)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _hwpx_info(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "equation_count": 0,
        "script_count": 0,
        "endnote_count": 0,
        "autonum_count": 0,
        "fonts": Counter(),
        "base_units": Counter(),
        "scripts": [],
        "xml_errors": [],
        "forbidden_images": 0,
    }
    if not path.is_file():
        return result
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        result["xml_errors"].append({"member": "", "error": f"invalid ZIP: {exc}"})
        return result
    with archive:
        for member in archive.namelist():
            if not member.lower().endswith(".xml"):
                continue
            payload = archive.read(member)
            try:
                root = ET.fromstring(payload)
            except ET.ParseError as exc:
                result["xml_errors"].append({"member": member, "error": str(exc)})
                continue
            for node in root.iter():
                name = _local_name(node.tag)
                if name == "equation":
                    result["equation_count"] += 1
                    font = next((str(v) for k, v in node.attrib.items() if _local_name(k) == "font"), "")
                    base = next((str(v) for k, v in node.attrib.items() if _local_name(k) == "baseunit"), "")
                    result["fonts"][font] += 1
                    result["base_units"][base] += 1
                    scripts = ["".join(child.itertext()) for child in node.iter() if _local_name(child.tag) == "script"]
                    script = scripts[0] if scripts else ""
                    result["scripts"].append(script)
                    result["script_count"] += len(scripts)
                elif name in {"endnote", "end_note"}:
                    result["endnote_count"] += 1
                elif name in {"autonum", "auto_num"}:
                    value = next((str(v) for k, v in node.attrib.items() if _local_name(k) == "numtype"), "")
                    if value.upper() == "ENDNOTE":
                        result["autonum_count"] += 1
    result["fonts"] = dict(result["fonts"])
    result["base_units"] = dict(result["base_units"])
    result["script_sha256"] = hashlib.sha256(
        "\n".join(result["scripts"]).encode("utf-8")
    ).hexdigest()
    result["package_valid"] = not result["xml_errors"]
    result["style_ok"] = (
        result["equation_count"] > 0
        and result["fonts"] == {TARGET_FORMULA_FONT: result["equation_count"]}
        and result["base_units"] == {TARGET_BASE_UNIT: result["equation_count"]}
        and result["script_count"] == result["equation_count"]
        and all(str(script).strip() for script in result["scripts"])
    )
    return result


def _json(path_value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not path_value:
        return None, "missing path"
    path = Path(str(path_value))
    if not path.is_file():
        return None, f"file not found: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"cannot read {path}: {exc}"


def _manifest_check(path_value: Any, pdf: dict[str, Any], role: str) -> tuple[dict[str, Any], int]:
    path = Path(str(path_value)) if path_value else Path("")
    if not path.is_file():
        return {
            "status": "FAIL",
            "role": role,
            "path": str(path),
            "findings": [{"code": "MATH_SOURCE_MANIFEST_MISSING", "role": role}],
        }, 0
    report = load_and_validate(path)
    findings = list(report.get("findings", []))
    expected_hash = str(pdf.get("sha256", ""))
    manifest_payload, read_error = _json(path)
    if read_error:
        findings.append({"code": "MATH_SOURCE_MANIFEST_READ_ERROR", "role": role, "message": read_error})
    elif expected_hash and manifest_payload.get("source_pdf_sha256") != expected_hash:
        findings.append({"code": "SOURCE_PDF_HASH_MISMATCH", "role": role})
    if pdf.get("page_count") is None:
        findings.append({"code": "PDF_PAGE_COUNT_UNVERIFIED", "role": role})
    elif manifest_payload and manifest_payload.get("page_count") != pdf.get("page_count"):
        findings.append({"code": "PDF_PAGE_COUNT_MISMATCH", "role": role})
    passed = report.get("status") == "PASS" and not findings
    return {
        "status": "PASS" if passed else "FAIL",
        "role": role,
        "path": str(path),
        "counts": report.get("counts", {}),
        "findings": findings,
        "manifest_report": report,
    }, int(report.get("counts", {}).get("formulas", 0) or 0)


def audit_exam(key: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if key not in EXAM_KEYS:
        findings.append({"code": "EXAM_KEY_INVALID", "exam": key})
    problem_pdf = _pdf_info(Path(str(spec.get("problem_pdf", ""))))
    solution_pdf = _pdf_info(Path(str(spec.get("solution_pdf", ""))))
    if not problem_pdf.get("exists") or not solution_pdf.get("exists"):
        findings.append({"code": "SOURCE_PDF_PAIR_MISSING"})
    problem_manifest, problem_formula_count = _manifest_check(spec.get("problem_manifest"), problem_pdf, "problem")
    solution_manifest, solution_formula_count = _manifest_check(spec.get("solution_manifest"), solution_pdf, "solution")
    findings.extend(problem_manifest.get("findings", []))
    findings.extend(solution_manifest.get("findings", []))

    hwpx = _hwpx_info(Path(str(spec.get("hwpx", ""))))
    source_formula_count = problem_formula_count + solution_formula_count
    if hwpx.get("equation_count") != source_formula_count:
        findings.append({
            "code": "FORMULA_COUNT_CHAIN_MISMATCH",
            "source": source_formula_count,
            "hwpx": hwpx.get("equation_count"),
        })
    if not hwpx.get("style_ok"):
        findings.append({"code": "HWPX_EQUATION_STYLE_INVALID"})
    if hwpx.get("endnote_count") != EXPECTED_ITEMS:
        findings.append({"code": "ENDNOTE_COUNT_MISMATCH", "expected": EXPECTED_ITEMS, "actual": hwpx.get("endnote_count")})
    if hwpx.get("autonum_count") != EXPECTED_ITEMS:
        findings.append({"code": "ENDNOTE_AUTONUM_MISMATCH", "expected": EXPECTED_ITEMS, "actual": hwpx.get("autonum_count")})
    if not hwpx.get("package_valid"):
        findings.append({"code": "HWPX_PACKAGE_INVALID"})

    hwp_path = Path(str(spec.get("hwp", "")))
    if not hwp_path.is_file():
        findings.append({"code": "HWP_OUTPUT_MISSING"})

    com_report, com_error = _json(spec.get("com_report"))
    if com_error:
        findings.append({"code": "COM_ROUNDTRIP_MISSING", "message": com_error})
    elif com_report.get("status") != "PASS" or not com_report.get("roundtrip"):
        findings.append({"code": "COM_ROUNDTRIP_FAIL"})
    elif com_report.get("hwp_equation_count") != source_formula_count or com_report.get("hwpx_equation_count") != source_formula_count:
        findings.append({"code": "COM_FORMULA_COUNT_MISMATCH"})
    elif com_report.get("endnote_count") != EXPECTED_ITEMS:
        findings.append({"code": "COM_ENDNOTE_COUNT_MISMATCH"})

    for label, required_status in (("visual_report", "PASS"), ("style_report", "PASS")):
        report, error = _json(spec.get(label))
        if error:
            findings.append({"code": f"{label.upper()}_MISSING", "message": error})
        elif report.get("status") != required_status:
            findings.append({"code": f"{label.upper()}_FAIL"})
    image_report, image_error = _json(spec.get("image_report"))
    if image_error:
        findings.append({"code": "IMAGE_AUDIT_MISSING", "message": image_error})
    elif image_report.get("forbidden_images", 0) != 0 or image_report.get("forbidden_endnote_images", 0) != 0:
        findings.append({"code": "FORBIDDEN_IMAGE_PRESENT"})

    endnote_manifest = spec.get("endnote_manifest")
    if endnote_manifest:
        try:
            endnote_report = audit_endnotes(spec.get("hwpx"), endnote_manifest)
        except Exception as exc:
            endnote_report = {"status": "FAIL", "findings": [{"code": "ENDNOTE_AUDIT_ERROR", "message": str(exc)}]}
        if endnote_report.get("status") != "PASS":
            findings.extend(endnote_report.get("findings", []))
    else:
        endnote_report = {"status": "FAIL", "findings": [{"code": "ENDNOTE_MANIFEST_MISSING"}]}
        findings.extend(endnote_report["findings"])

    return {
        "exam": key,
        "status": "PASS" if not findings else "FAIL",
        "source": {"problem": problem_pdf, "solution": solution_pdf},
        "source_manifests": {"problem": problem_manifest, "solution": solution_manifest},
        "source_formula_count": source_formula_count,
        "hwpx": hwpx,
        "hwp": {"path": str(hwp_path), "exists": hwp_path.is_file()},
        "endnote_report": endnote_report,
        "findings": findings,
    }


def audit_batch(spec_path: str | Path) -> dict[str, Any]:
    payload, error = _json(spec_path)
    if error or not isinstance(payload, Mapping):
        return {"schema_version": SCHEMA_VERSION, "status": "FAIL", "findings": [{"code": "SPEC_READ_ERROR", "message": error or "spec must be an object"}]}
    exams = payload.get("exams")
    findings: list[dict[str, Any]] = []
    if not isinstance(exams, Mapping) or set(exams) != set(EXAM_KEYS):
        findings.append({"code": "EXAM_SET_MISMATCH", "expected": list(EXAM_KEYS), "actual": sorted(exams) if isinstance(exams, Mapping) else []})
        exams = exams if isinstance(exams, Mapping) else {}
    rows = {key: audit_exam(key, exams.get(key, {})) for key in EXAM_KEYS}
    for key, row in rows.items():
        if row["status"] != "PASS":
            findings.append({"code": "EXAM_FAIL", "exam": key, "finding_count": len(row["findings"])})
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not findings else "FAIL",
        "exam_count": len(rows),
        "findings": findings,
        "exams": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed QA for four Duff native-endnote exams")
    parser.add_argument("spec", type=Path, help="external JSON spec containing the four exam paths")
    parser.add_argument("--json", dest="json_path", type=Path)
    args = parser.parse_args(argv)
    report = audit_batch(args.spec)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(encoded + "\n", encoding="utf-8")
    # Windows consoles commonly default to cp949; reports contain Unicode
    # math symbols and Korean paths.  Never let report serialization fail after
    # the audit itself completed.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    print(encoded)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
