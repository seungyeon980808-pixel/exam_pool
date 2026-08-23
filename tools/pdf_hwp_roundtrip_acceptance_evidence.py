"""Emit deterministic C002/C003 acceptance evidence for an approved run."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from tempfile import gettempdir, TemporaryDirectory

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pdf_hwp_atomic import atomic_replace
from app.pdf_hwp_roundtrip_checkpoint import artifact_hash
from app.pdf_hwp_roundtrip_pdf_contract import generated_pdf_contract_request, inspect_generated_pdf_contract
from app.pdf_hwp_roundtrip_readback import HwpExpectations, PdfExpectations, inspect_hwp, inspect_pdf
from app.pdf_hwp_roundtrip_units import FailureCode, load_prepared_units
from tools.pdf_hwp_roundtrip_acceptance_fixture import (
    FixtureProcessError,
    run_fixture_process,
)


class _RunMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    schema_version: int
    namespace_id: str
    namespace_root: Path
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_dependency_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_contract: tuple[str, ...]


class EvidenceAssertionError(RuntimeError):
    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class C002Evidence:
    transcript: Path
    proof: Path


@dataclass(frozen=True, slots=True)
class C003Evidence:
    report: Path
    contact_sheet: Path


def _atomic_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_replace(temporary, path)
    return path


def generate_c002(evidence_dir: Path) -> C002Evidence:
    """Prove material-stage resume and isolated malformed-source quarantine."""
    output = evidence_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary_root: Path
    with TemporaryDirectory(prefix=".acceptance-c002-") as temporary:
        temporary_root = Path(temporary).resolve()
        contained = temporary_root.is_relative_to(Path(gettempdir()).resolve())
        paused_call = run_fixture_process(
            temporary_root, ("--stop-after-completed-stages", "2"),
        )
        resumed_call = run_fixture_process(temporary_root, ())
        known = temporary_root / "p1_2024_11.pdf"
        checkpoint_path = temporary_root / "checkpoints" / f"{artifact_hash(known)}.json"
        if not checkpoint_path.is_file():
            raise EvidenceAssertionError("known checkpoint was not persisted")
        checkpoint_before = checkpoint_path.read_bytes()
        quarantine_call = run_fixture_process(temporary_root, ("--malformed",))
        checkpoint_unchanged = checkpoint_path.read_bytes() == checkpoint_before
        paused = paused_call.payload
        resumed = resumed_call.payload
        quarantined = quarantine_call.payload
        paused_hashes = paused.artifact_hashes
        after_hashes = resumed.artifact_hashes
        reused = bool(paused_hashes) and after_hashes[:len(paused_hashes)] == paused_hashes
        if paused.completed_stages != ("route", "extract"):
            raise EvidenceAssertionError("C002 did not pause after the material checkpoint")
        if resumed.status != "succeeded" or not reused:
            raise EvidenceAssertionError("C002 resume did not reuse the extract artifact")
        malformed_hwp = temporary_root / "malformed.hwp"
        if quarantined.status != "quarantined" or malformed_hwp.exists():
            raise EvidenceAssertionError("malformed source was not safely quarantined")
        if not checkpoint_unchanged:
            raise EvidenceAssertionError("quarantine mutated the prior checkpoint")
        calls = (temporary_root / "calls.txt").read_text(encoding="utf-8").splitlines()
        invocations = (paused_call, resumed_call, quarantine_call)
        payload = {
            "schema_version": 1,
            "paused": paused.model_dump(mode="json"),
            "resumed": resumed.model_dump(mode="json"),
            "calls": calls,
            "resume_reused_extract": reused,
            "quarantine": {
                "status": quarantined.status, "route_kind": quarantined.route_kind,
                "hwp_artifact_exists": malformed_hwp.exists(),
            },
            "prior_checkpoint_unchanged": checkpoint_unchanged,
            "process_invocations": len(invocations),
            "commands": [
                {"argv": list(invocation.command), "returncode": invocation.returncode}
                for invocation in invocations
            ],
        }
    exists_after = temporary_root.exists()
    payload["cleanup_receipt"] = {
        "temporary_root_contained": contained,
        "removed": not exists_after,
        "exists_after": exists_after,
    }
    if not contained or exists_after:
        raise EvidenceAssertionError("C002 temporary root cleanup failed")
    proof = _atomic_text(output / "resume-proof.json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    transcript = _atomic_text(output / "C002-resume-transcript.txt", "\n".join((
        "C002 RESUME / QUARANTINE PROOF", f"paused={payload['paused']}",
        f"resumed={payload['resumed']}", f"calls={','.join(payload['calls'])}",
        f"resume_reused_extract={str(reused).lower()}",
        f"quarantined={quarantined.status}",
        f"prior_checkpoint_unchanged={str(checkpoint_unchanged).lower()}",
        f"cleanup_receipt={payload['cleanup_receipt']}", "",
    )))
    return C002Evidence(transcript, proof)


def _source_root(namespace: Path, prefix: str) -> Path:
    candidates = tuple(sorted((namespace / "sources").glob(f"{prefix}-*")))
    if len(candidates) != 1:
        raise EvidenceAssertionError(f"expected one {prefix} source root, found {len(candidates)}")
    return candidates[0]


def _paired_sheet(source: Path, generated: Path, target: Path) -> Path:
    with Image.open(source) as opened:
        left = opened.convert("RGB")
    with Image.open(generated) as opened:
        right = opened.convert("RGB")
    height = max(left.height, right.height)
    canvas = Image.new("RGB", (left.width + right.width, height), "white")
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width, 0))
    temporary = target.with_suffix(".tmp.png")
    canvas.save(temporary)
    atomic_replace(temporary, target)
    left.close(); right.close(); canvas.close()
    return target


def generate_c003(run_root: Path, evidence_dir: Path) -> C003Evidence:
    """Assert pinned regressions and emit a paired q234 visual artifact."""
    root = run_root.resolve()
    metadata = _RunMetadata.model_validate_json((root / "run-metadata.json").read_text(encoding="utf-8"))
    namespace = metadata.namespace_root.resolve()
    if not namespace.is_relative_to(root / "namespaces"):
        raise EvidenceAssertionError("active namespace escapes the run root")
    ebs = _source_root(namespace, "ebs_2027_physics1")
    e2 = _source_root(namespace, "e2_2024_09")
    prepared = load_prepared_units(ebs / "prepared-units.json")
    hapdap = tuple(unit for unit in prepared.prepared_units if unit.item_number in {234, 235, 237, 238})
    slot_counts = {str(unit.item_number): len(unit.palette_markdown.splitlines()[5:8]) for unit in hapdap}
    templates = {unit.item_number: unit.palette_markdown.splitlines()[0] for unit in hapdap}
    crop_failures = {str(failure.item_number): failure.code.value for failure in prepared.item_failures if failure.item_number in {35, 37}}
    e2_prepared = load_prepared_units(e2 / "prepared-units.json")
    e2_failure = next((failure.code.value for failure in e2_prepared.item_failures if failure.item_number == 1), None)
    if tuple(unit.item_number for unit in hapdap) != (234, 235, 237, 238):
        raise EvidenceAssertionError("C003 hapdap items are incomplete")
    if not all(label.startswith("\\수능합답") for label in templates.values()) or set(slot_counts.values()) != {3}:
        raise EvidenceAssertionError("C003 hapdap template or three-slot contract failed")
    if crop_failures != {"35": FailureCode.CROP_CONTAMINATION.value, "37": FailureCode.CROP_CONTAMINATION.value}:
        raise EvidenceAssertionError("C003 EBS crop regressions differ")
    if e2_failure != FailureCode.CROP_CLIPPING.value:
        raise EvidenceAssertionError("C003 e2 q1 clipping regression differs")
    conversion = ebs / "conversion"
    hwp = conversion / "converted.hwp"
    pdf = conversion / "converted.pdf"
    hwp_issues: list[str] = []
    pdf_issues: list[str] = []
    hwp_pages: int | None = None; hwp_kordoc_pages: int | None = None
    hwp_tables: int | None = None
    pdf_pages: int | None = None
    generated_contract: dict[str, JsonValue] | None = None
    if hwp.is_file():
        hwp_report = inspect_hwp(hwp, HwpExpectations(True, True, True, True))
        hwp_issues = [issue.code.value for issue in hwp_report.issues]
        if hwp_report.snapshot is not None:
            hwp_kordoc_pages = hwp_report.snapshot.kordoc_page_count; hwp_pages = hwp_report.snapshot.rhwp_page_count
            hwp_tables = hwp_report.snapshot.rhwp_table_count
    if pdf.is_file():
        page_count = len(tuple(conversion.glob("page-*.png")))
        if page_count == 0:
            raise EvidenceAssertionError("C003 PDF has no pinned rendered-page expectation")
        pdf_report = inspect_pdf(pdf, PdfExpectations(page_count))
        pdf_issues = [issue.code.value for issue in pdf_report.issues]
        if pdf_report.snapshot is not None:
            pdf_pages = pdf_report.snapshot.page_count
        request = generated_pdf_contract_request(pdf, prepared.prepared_units)
        contract = inspect_generated_pdf_contract(request)
        generated_contract = {
            "items": [{
                "item_number": item.item_number,
                "page_number": item.page_number,
                "bbox": list(item.bbox),
                "baselines": list(item.baselines),
                "issues": [issue.code.value for issue in item.issues],
            } for item in contract.items],
            "issues": [{"code": issue.code.value, "item_number": issue.item_number,
                        "detail": issue.detail} for issue in contract.issues],
        }
        if contract.issues:
            details = ",".join(f"{issue.item_number}:{issue.code.value}" for issue in contract.issues)
            raise EvidenceAssertionError(f"C003 generated PDF contract issues: {details}")
    if hwp_issues or pdf_issues:
        raise EvidenceAssertionError(f"C003 readback issues: hwp={hwp_issues},pdf={pdf_issues}")
    renders = ebs / "verification-evidence" / "renders"
    source_render = renders / "source-item-0234.png"
    generated_render = renders / "generated-item-0234.png"
    if not source_render.is_file() or not generated_render.is_file():
        raise EvidenceAssertionError("C003 q234 paired renders are absent")
    output = evidence_dir.resolve(); output.mkdir(parents=True, exist_ok=True)
    contact = _paired_sheet(source_render, generated_render, output / "regression-contact-sheet.png")
    payload = {
        "schema_version": 1, "namespace_id": metadata.namespace_id,
        "namespace_root": str(namespace), "hapdap_items": [unit.item_number for unit in hapdap],
        "hapdap_templates": {str(key): value for key, value in templates.items()},
        "bogi_slot_counts": slot_counts, "ebs_crop_failures": crop_failures,
        "e2_q1_failure": e2_failure, "hwp_present": hwp.is_file(), "hwp_issues": hwp_issues,
        "hwp_sha256": artifact_hash(hwp) if hwp.is_file() else None,
        "hwp_pages": hwp_pages, "hwp_kordoc_pages": hwp_kordoc_pages, "hwp_tables": hwp_tables,
        "pdf_present": pdf.is_file(), "pdf_issues": pdf_issues,
        "pdf_sha256": artifact_hash(pdf) if pdf.is_file() else None, "pdf_pages": pdf_pages,
        "generated_pdf_contract": generated_contract,
        "paired_render_hashes": {"source": artifact_hash(source_render), "generated": artifact_hash(generated_render)},
        "passed": True,
    }
    report = _atomic_text(output / "C003-regression.json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return C003Evidence(report, contact)


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        generate_c002(arguments.evidence_dir)
        generate_c003(arguments.run_root, arguments.evidence_dir)
    except (
        EvidenceAssertionError, FixtureProcessError, OSError,
        ValidationError, UnidentifiedImageError,
    ) as error:
        print(f"acceptance evidence failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
