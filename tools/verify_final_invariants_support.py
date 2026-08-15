"""Typed process and cleanup collectors for the final invariant verifier."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]


class InvariantCollectionError(RuntimeError):
    """Raised when an invariant source does not match its typed contract."""


@dataclass(frozen=True, slots=True)
class ProcessState:
    """Current HWP/listener state compared with the authenticated baseline."""

    current_hwp: list[JsonObject]
    current_listener: list[JsonObject]
    baseline_hwp_pids: list[int]
    current_hwp_pids: list[int]
    added_hwp_pids: list[int]
    missing_hwp_pids: list[int]
    expected_listener_pids: list[int]
    current_listener_pids: list[int]

    @property
    def cleanup_match(self) -> bool:
        return (
            not self.added_hwp_pids
            and not self.missing_hwp_pids
            and self.current_listener_pids == self.expected_listener_pids
        )


@dataclass(frozen=True, slots=True)
class CleanupState:
    """Task-relative filesystem and headless-browser cleanup evidence."""

    scratch_contents: dict[str, list[str]]
    temporary_root: str
    temporary_patterns: tuple[str, ...]
    matching_temporary_paths: list[str]
    task_lingering_temporary_paths: list[str]
    overlay_cache_paths: list[str]
    overlay_staging_paths: list[str]
    temporary_evidence_files: list[str]
    headless_browser_processes: list[JsonObject]
    task_headless_browser_processes: list[JsonObject]
    task_started_at: str
    debug_journal_exists: bool

    @property
    def cleanup_match(self) -> bool:
        return (
            not any(self.scratch_contents.values())
            and not self.task_lingering_temporary_paths
            and not self.overlay_cache_paths
            and not self.overlay_staging_paths
            and not self.temporary_evidence_files
            and not self.task_headless_browser_processes
            and not self.debug_journal_exists
        )


def sha256(path: Path) -> str:
    """Hash one file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def powershell_json(script: str) -> list[JsonObject]:
    """Run a read-only PowerShell query and require object-shaped JSON."""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
    )
    raw = completed.stdout.strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    records = parsed if isinstance(parsed, list) else [parsed]
    if not all(isinstance(record, dict) for record in records):
        raise InvariantCollectionError("PowerShell query returned non-object JSON")
    return records


def _record_list(value: JsonValue, label: str) -> list[JsonObject]:
    if not isinstance(value, list) or not all(
        isinstance(record, dict) for record in value
    ):
        raise InvariantCollectionError(f"baseline {label} is not an object list")
    return value


def collect_process_state(process_baseline: JsonObject) -> ProcessState:
    """Compare live HWP and listener processes with the fixed baseline."""
    current_hwp = powershell_json(
        "@(Get-CimInstance Win32_Process -Filter \"Name='Hwp.exe'\" | "
        "Select-Object ProcessId,Name,CommandLine,CreationDate) | "
        "ConvertTo-Json -Depth 4 -Compress"
    )
    current_listener = powershell_json(
        "@(Get-NetTCPConnection -LocalPort 8632 -State Listen "
        "-ErrorAction SilentlyContinue | "
        "Select-Object LocalAddress,LocalPort,OwningProcess) | "
        "ConvertTo-Json -Depth 4 -Compress"
    )
    baseline_hwp = _record_list(
        process_baseline.get("hwp_processes"), "hwp_processes"
    )
    baseline_listeners = _record_list(
        process_baseline.get("listen_8632"), "listen_8632"
    )
    baseline_hwp_pids = sorted(int(str(record["ProcessId"])) for record in baseline_hwp)
    current_hwp_pids = sorted(int(str(record["ProcessId"])) for record in current_hwp)
    expected_listener_pids = sorted(
        int(str(record["OwningProcess"])) for record in baseline_listeners
    )
    current_listener_pids = sorted(
        int(str(record["OwningProcess"])) for record in current_listener
    )
    return ProcessState(
        current_hwp=current_hwp,
        current_listener=current_listener,
        baseline_hwp_pids=baseline_hwp_pids,
        current_hwp_pids=current_hwp_pids,
        added_hwp_pids=sorted(set(current_hwp_pids) - set(baseline_hwp_pids)),
        missing_hwp_pids=sorted(set(baseline_hwp_pids) - set(current_hwp_pids)),
        expected_listener_pids=expected_listener_pids,
        current_listener_pids=current_listener_pids,
    )


def collect_cleanup_state(
    root: Path,
    evidence: Path,
    task_started_at: datetime,
) -> CleanupState:
    """Collect task-relative cache, temporary-file, and browser residues."""
    headless = powershell_json(
        "@(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -match '^(chrome|msedge|chromium)\\.exe$' -and "
        "$_.CommandLine -match '(--headless|playwright_chromiumdev_profile)' } | "
        "Select-Object ProcessId,Name,CommandLine,@{Name='CreationDate';Expression={"
        "$_.CreationDate.ToUniversalTime().ToString('o')}}) | "
        "ConvertTo-Json -Depth 4 -Compress"
    )
    task_headless: list[JsonObject] = []
    for process in headless:
        raw_created_at = process.get("CreationDate")
        if not raw_created_at or datetime.fromisoformat(
            str(raw_created_at)
        ).astimezone(timezone.utc) >= task_started_at:
            task_headless.append(process)
    scratch_roots = (
        evidence / "corpus-c" / "scratch",
        root / ".omo" / "teams" / "team-2cdc6a66" / "artifacts" / "corpus-ui" / "scratch",
    )
    scratch_contents = {
        str(path): [str(item) for item in path.rglob("*")] if path.exists() else []
        for path in scratch_roots
    }
    temporary_root = Path(tempfile.gettempdir()).resolve()
    temporary_patterns = (
        "exampool-multisubject-a-*",
        "exampool-hash-proof-*",
        "exampool-real-input-probe-*",
        "exampool-real-hwp-proof-*",
        "playwright-artifacts-*",
        "pytest-of-user/pytest-*",
    )
    matching_paths = sorted(
        {
            path
            for pattern in temporary_patterns
            for path in temporary_root.glob(pattern)
        }
    )
    task_paths = sorted(
        str(path)
        for path in matching_paths
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) >= task_started_at
    )
    overlay_root = root / "data" / "hwppalette_additive_root"
    overlay_caches = sorted(
        str(path)
        for path in (*overlay_root.rglob("__pycache__"), *overlay_root.rglob("*.pyc"))
    ) if overlay_root.exists() else []
    return CleanupState(
        scratch_contents=scratch_contents,
        temporary_root=str(temporary_root),
        temporary_patterns=temporary_patterns,
        matching_temporary_paths=[str(path) for path in matching_paths],
        task_lingering_temporary_paths=task_paths,
        overlay_cache_paths=overlay_caches,
        overlay_staging_paths=sorted(
            str(path)
            for path in overlay_root.parent.glob(".hwppalette-additive-build-*")
        ),
        temporary_evidence_files=sorted(str(path) for path in evidence.rglob("*.tmp")),
        headless_browser_processes=headless,
        task_headless_browser_processes=task_headless,
        task_started_at=task_started_at.isoformat(),
        debug_journal_exists=(root / ".debug-journal.md").exists(),
    )
