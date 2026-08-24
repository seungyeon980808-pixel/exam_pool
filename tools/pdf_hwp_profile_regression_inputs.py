# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic"]
# ///
# ─── How to run ───
# Imported by pdf_hwp_profile_regression_evidence.py; it has no standalone CLI.
"""Typed namespace, profile, and readback inputs for C003 evidence."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.pdf_hwp_roundtrip_checkpoint import artifact_hash
from app.pdf_hwp_roundtrip_models import SourceProfile
from app.pdf_hwp_roundtrip_readback import (
    HwpExpectations, PdfExpectations, inspect_hwp, inspect_pdf,
)


class ProfileInputError(RuntimeError):
    """Reject incoherent profile-regression inputs without freezing traceback state."""

    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def __str__(self) -> str:
        return self.detail


class RunMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    namespace_id: str
    namespace_root: Path
    code_dependency_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Issue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    code: str
    item_number: int | None = None


class Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    passed: bool
    issues: tuple[Issue, ...] = ()


class ProfileReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    profile: SourceProfile
    editability: Check
    hwp: Check
    pdf_semantic: Check
    image_ownership: Check
    blocking_issues: tuple[Issue, ...] = ()


class ConversionPaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    hwp_path: Path
    pdf_path: Path
    rendered_pages: tuple[Path, ...]


class ReadbackEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: str
    hwp_sha256: str
    pdf_sha256: str
    hwp_pages: int
    pdf_pages: int
    hwp_tables: int
    issues: tuple[str, ...] = ()


def parse_metadata(namespace: Path) -> RunMetadata:
    """Parse and bind a namespace to its active run metadata."""
    root = namespace.resolve()
    run_root = root.parent.parent
    if root.parent.name != "namespaces":
        raise ProfileInputError("namespace must be a direct child of a namespaces directory")
    path = run_root / "run-metadata.json"
    if not path.is_file():
        raise ProfileInputError("namespace run-metadata.json is absent")
    metadata = RunMetadata.model_validate_json(path.read_text(encoding="utf-8"))
    if metadata.namespace_root.resolve() != root or metadata.namespace_id != root.name:
        raise ProfileInputError("namespace does not match active run metadata")
    return metadata


def read_profile(root: Path, expected: SourceProfile) -> ProfileReport:
    """Parse one source profile and require its common structural gates."""
    report = ProfileReport.model_validate_json(
        (root / "profile-verification.json").read_text(encoding="utf-8")
    )
    if report.profile is not expected:
        raise ProfileInputError(f"unexpected profile for {root.name}: {report.profile.value}")
    if not (report.editability.passed and report.hwp.passed and report.pdf_semantic.passed):
        raise ProfileInputError(f"profile structural checks failed for {root.name}")
    return report


def readback(root: Path, *, hapdap: bool) -> ReadbackEvidence:
    """Read the real HWP/PDF pair and return hash-addressed evidence."""
    paths = ConversionPaths.model_validate_json(
        (root / "backend-conversion.json").read_text(encoding="utf-8")
    )
    hwp = inspect_hwp(paths.hwp_path, HwpExpectations(hapdap, hapdap, True, True))
    pdf = inspect_pdf(paths.pdf_path, PdfExpectations(len(paths.rendered_pages)))
    issues = tuple(issue.code.value for issue in (*hwp.issues, *pdf.issues))
    if issues or hwp.snapshot is None or pdf.snapshot is None:
        raise ProfileInputError(f"readback failed for {root.name}: {issues}")
    return ReadbackEvidence(
        source=root.name,
        hwp_sha256=artifact_hash(paths.hwp_path),
        pdf_sha256=artifact_hash(paths.pdf_path),
        hwp_pages=hwp.snapshot.rhwp_page_count,
        pdf_pages=pdf.snapshot.page_count,
        hwp_tables=hwp.snapshot.rhwp_table_count,
        issues=issues,
    )
