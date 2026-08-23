"""Approved candidate selection and deterministic run namespace contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from typing import Final

from app.integrations.hwppalette import HwpPaletteProvider
from app.pdf_hwp_pipeline_models import LayoutStyle
from app.pdf_hwp_roundtrip_manifest import ApprovedFirstRunManifest
from app.pdf_hwp_roundtrip_models import SourceProfile


_CODE_GLOBS: Final = (
    "app/pdf_hwp*.py",
    "app/integrations/hwppalette*.py",
    "vendor/hwp_typesetter/**/*.py",
    "tools/pdf_hwp_roundtrip_harness*.py",
    "assets/hwp_templates/*.hwp",
    "vendor/hwp_typesetter/seed_data/**/*.hwp",
)
_CODE_FILES: Final = (
    "app/exam_items.py",
    "app/export_palette.py",
    "app/formula_markup.py",
    "app/integrations/palette_registry.py",
    "app/pdf_hwp_type_catalog.json",
)
_HWPPAL_RUNTIME_GLOBS: Final = (
    "hwp_palette/**/*.py",
    "typesetting_packs/**/*.json",
    "typesetting_packs/**/*.hwp",
)
_HWPPAL_RUNTIME_FILES: Final = ("UPSTREAM.json",)


class SourceGroup(StrEnum):
    KICE = "kice"
    EBS = "ebs"
    RASTER = "raster"


@dataclass(frozen=True, slots=True)
class Candidate:
    source_id: str
    group: SourceGroup
    path: Path
    sha256: str
    selected_numbers: tuple[int, ...]
    header_subject: str
    profile: SourceProfile
    regression_claims: tuple[str, ...] = ()
    companion_pdf_path: Path | None = None
    companion_pdf_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RunNamespace:
    namespace_id: str
    root: Path
    manifest_sha256: str
    code_dependency_sha256: str


@dataclass(frozen=True, slots=True)
class InvalidHarnessSelectionError(ValueError):
    detail: str

    def __str__(self) -> str:
        return self.detail


def _production_dependency_paths(project_root: Path) -> tuple[Path, ...]:
    discovered = {
        path.resolve()
        for pattern in _CODE_GLOBS
        for path in project_root.glob(pattern)
        if path.is_file()
    }
    discovered.update((project_root / relative).resolve() for relative in _CODE_FILES)
    return tuple(sorted(
        discovered,
        key=lambda path: path.relative_to(project_root).as_posix(),
    ))


def _hwppalette_runtime_dependency_paths(runtime_root: Path) -> tuple[Path, ...]:
    discovered = {
        path.resolve()
        for pattern in _HWPPAL_RUNTIME_GLOBS
        for path in runtime_root.glob(pattern)
        if path.is_file()
    }
    discovered.update(
        path.resolve()
        for relative in _HWPPAL_RUNTIME_FILES
        if (path := runtime_root / relative).is_file()
    )
    return tuple(sorted(
        discovered,
        key=lambda path: path.relative_to(runtime_root).as_posix(),
    ))


def _code_dependency_sha256(
    project_root: Path, runtime_root: Path | None = None,
) -> str:
    selected_runtime = (
        HwpPaletteProvider().root if runtime_root is None else runtime_root.resolve()
    )
    project_paths = _production_dependency_paths(project_root)
    project_path_set = {path.resolve() for path in project_paths}
    dependencies = [
        (path.relative_to(project_root).as_posix(), path)
        for path in project_paths
    ]
    dependencies.extend(
        (f"hwppalette-runtime/{path.relative_to(selected_runtime).as_posix()}", path)
        for path in _hwppalette_runtime_dependency_paths(selected_runtime)
        if path.resolve() not in project_path_set
    )
    digest = hashlib.sha256(b"pdf-hwp-production-dependencies-v3\0")
    for label, path in sorted(dependencies, key=lambda dependency: dependency[0]):
        relative = label.encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    for distribution in ("pydantic", "PyMuPDF", "Pillow"):
        dependency = f"{distribution}=={version(distribution)}".encode()
        digest.update(len(dependency).to_bytes(4, "big"))
        digest.update(dependency)
    return digest.hexdigest()


def manifest_candidates(manifest: ApprovedFirstRunManifest) -> tuple[Candidate, ...]:
    claims = {
        source_id: tuple(
            f"{case.case_id}:{case.item}:{case.expected}"
            for case in manifest.fixed_regressions if case.source_id == source_id
        )
        for source_id in {case.source_id for case in manifest.fixed_regressions}
    }
    candidates = [Candidate(
        paper.paper_id, SourceGroup.KICE, paper.path, paper.sha256,
        tuple(range(1, 21)), paper.subject, SourceProfile.KICE_STRUCTURAL,
        claims.get(paper.paper_id, ()),
    ) for paper in manifest.kice_papers]
    ebs = manifest.ebs_source
    candidates.append(Candidate(
        ebs.source_id, SourceGroup.EBS, ebs.path, ebs.sha256,
        tuple(sample.item for sample in ebs.sample), "물리학Ⅰ",
        SourceProfile.EBS_EDITABLE_REFLOW, claims.get(ebs.source_id, ()),
    ))
    raster = manifest.raster_fixture
    selected = (raster.companion_item,) if raster.companion_item is not None else (1,)
    candidates.append(Candidate(
        raster.source_id, SourceGroup.RASTER, raster.path, raster.sha256, selected, "",
        SourceProfile.KICE_STRUCTURAL,
        companion_pdf_path=raster.companion_pdf_path,
        companion_pdf_sha256=raster.companion_pdf_sha256,
    ))
    candidates.extend(Candidate(
        case.source_id, SourceGroup.KICE, case.path, case.sha256, (case.item,),
        "생명과학Ⅱ", SourceProfile.KICE_STRUCTURAL,
        (f"{case.case_id}:{case.item}:{case.expected}",),
    ) for case in manifest.fixed_regressions if case.path and case.sha256)
    return tuple(candidates)


def candidate_selection_contract(candidates: tuple[Candidate, ...]) -> tuple[str, ...]:
    """Fingerprint source selection, acceptance policy, and rendering choice."""
    return tuple(
        f"{candidate.source_id}|{candidate.group.value}|{candidate.sha256}|"
        f"{','.join(map(str, candidate.selected_numbers))}|{candidate.profile.value}|"
        f"{LayoutStyle.SUNEUNG.value}|{','.join(candidate.regression_claims)}|"
        f"{candidate.companion_pdf_sha256 or ''}"
        for candidate in candidates
    )


def selected_candidates(
    manifest: ApprovedFirstRunManifest,
    groups: tuple[SourceGroup, ...],
    source_ids: tuple[str, ...],
    max_sources: int | None,
) -> tuple[Candidate, ...]:
    approved = manifest_candidates(manifest)
    unknown = sorted(set(source_ids) - {candidate.source_id for candidate in approved})
    if unknown:
        raise InvalidHarnessSelectionError(f"unknown source ids: {','.join(unknown)}")
    if max_sources is not None and max_sources <= 0:
        raise InvalidHarnessSelectionError("max_sources must be positive")
    selected = tuple(
        candidate for candidate in approved
        if (not groups or candidate.group in groups)
        and (not source_ids or candidate.source_id in source_ids)
    )
    if not selected:
        raise InvalidHarnessSelectionError("source filters selected no approved sources")
    return selected if max_sources is None else selected[:max_sources]


def create_run_namespace(
    run_root: Path, manifest_path: Path, selection_contract: tuple[str, ...],
) -> tuple[RunNamespace, dict[str, str | int | tuple[str, ...]]]:
    project_root = Path(__file__).resolve().parents[1]
    manifest_sha = hashlib.sha256(manifest_path.resolve().read_bytes()).hexdigest()
    code_sha = _code_dependency_sha256(project_root)
    selection_sha = hashlib.sha256(json.dumps(
        selection_contract, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    identifier = hashlib.sha256(
        f"{manifest_sha}:{code_sha}:{selection_sha}".encode(),
    ).hexdigest()[:20]
    root = run_root.resolve() / "namespaces" / identifier
    (root / "checkpoints").mkdir(parents=True, exist_ok=True)
    namespace = RunNamespace(identifier, root, manifest_sha, code_sha)
    metadata: dict[str, str | int | tuple[str, ...]] = {
        "schema_version": 1, "namespace_id": identifier, "namespace_root": str(root),
        "manifest_sha256": manifest_sha, "code_dependency_sha256": code_sha,
        "selection_sha256": selection_sha, "selection_contract": selection_contract,
    }
    return namespace, metadata
