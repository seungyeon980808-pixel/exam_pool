"""Exact 52-PDF corpus and fresh equation-residual baseline contract."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path


CORPUS_ROOT = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일"
EVIDENCE_ROOT = Path(".omo/evidence/pdf-hwp-generalization/equation-glyphs")
BASELINE_ROOT = EVIDENCE_ROOT / "fresh-baseline-agent"
BASELINE_FILES = (
    BASELINE_ROOT / "p1.json",
    BASELINE_ROOT / "multisubject-a.json",
    BASELINE_ROOT / "subjects-b2-e1-e2.json",
)


@dataclass(frozen=True, slots=True)
class ResidualItem:
    subject: str
    paper: str
    pdf_path: Path
    pdf_sha256: str
    item_number: int
    page_number: int
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class CorpusBaseline:
    papers: tuple[dict[str, object], ...]
    residuals: tuple[ResidualItem, ...]
    detected_count: int
    ready_count: int
    manual_count: int
    failure_count: int


def _file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_papers() -> frozenset[str]:
    p1_sessions = (
        "2020_06", "2020_09", "2020_11", "2021_06", "2021_09", "2021_11",
        "2022_06", "2022_09", "2022_11", "2023_06", "2023_09", "2023_11",
        "2024_06", "2024_09", "2024_11", "2025_06", "2025_09", "2025_11",
        "2026_06", "2026_09", "2026_11", "2027_06",
    )
    recent = ("2027_06", "2026_11", "2026_09", "2026_06", "2025_11")
    names = [f"p1_{session}" for session in p1_sessions]
    names.extend(
        f"{subject}_{session}"
        for subject in ("c1", "c2", "b1", "b2", "e1", "e2")
        for session in recent
    )
    return frozenset(names)


def load_fresh_baseline(paths: tuple[Path, ...] = BASELINE_FILES) -> CorpusBaseline:
    papers: list[dict[str, object]] = []
    totals = {"detected": 0, "ready": 0, "manual": 0, "failure": 0}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        papers.extend(payload["papers"])
        for key in totals:
            totals[key] += int(payload[f"{key}_count"])
    expected = _expected_papers()
    logical_names = [str(paper["logical_name"]) for paper in papers]
    if len(papers) != 52 or frozenset(logical_names) != expected:
        raise ValueError("fresh baseline must describe the exact 52-paper corpus")
    if totals != {"detected": 1040, "ready": 768, "manual": 272, "failure": 0}:
        raise ValueError(f"unexpected fresh baseline totals: {totals}")
    residuals: list[ResidualItem] = []
    seen_paths: set[Path] = set()
    root = CORPUS_ROOT.resolve()
    for paper in papers:
        source = Path(str(paper["source_path"])).resolve()
        if source.parent != root or source in seen_paths:
            raise ValueError(f"source path is outside or duplicated: {source}")
        seen_paths.add(source)
        if _file_sha(source) != str(paper["source_hash"]):
            raise ValueError(f"source hash mismatch: {source}")
        if int(paper["detected_count"]) != 20:
            raise ValueError(f"paper does not contain 20 items: {source}")
        subject = str(paper["logical_name"]).split("_", 1)[0]
        for item in paper["items"]:
            reason = str(item["detail"])
            if item["status"] == "manual" and "equation glyphs require manual review" in reason:
                residuals.append(ResidualItem(
                    subject, str(paper["logical_name"]), source,
                    str(paper["source_hash"]), int(item["item_number"]),
                    int(item["page_number"]), str(item["status"]), reason,
                ))
    if len(residuals) != 163:
        raise ValueError(f"fresh equation residual count is {len(residuals)}, expected 163")
    return CorpusBaseline(
        tuple(papers), tuple(residuals), totals["detected"], totals["ready"],
        totals["manual"], totals["failure"],
    )


def subject_counts(items: tuple[ResidualItem, ...]) -> dict[str, int]:
    return {
        subject: sum(item.subject == subject for item in items)
        for subject in ("p1", "c1", "c2", "b1", "b2", "e1", "e2")
    }


def contains_raw_pua(value: object) -> bool:
    if isinstance(value, str):
        return any(0xE000 <= ord(char) <= 0xF8FF for char in value)
    if isinstance(value, dict):
        return any(contains_raw_pua(key) or contains_raw_pua(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_raw_pua(item) for item in value)
    return False
