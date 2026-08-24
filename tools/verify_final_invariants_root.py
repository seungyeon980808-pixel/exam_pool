from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from verify_final_invariants_support import (
    JsonObject,
    collect_cleanup_state,
    collect_process_state,
    sha256,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".omo" / "evidence" / "pdf-hwp-generalization"
BASELINE_PATH = EVIDENCE / "protected-baseline.json"
PROCESS_BASELINE_PATH = EVIDENCE / "process-baseline.json"
OUTPUT_PATH = EVIDENCE / "protected-final-receipt.json"
EXPECTED_PROTECTED_BASELINE_SHA256 = "60b4d65809aa424af40220ea0628c6009bbc10a2acb98f0b658119fbd265bd9a"
EXPECTED_PROCESS_BASELINE_SHA256 = "10cd1193f08a25f59b846eb547b8aef230e5c40a86b851e204c4c439dcb50a37"
TASK_STARTED_AT = datetime.fromisoformat("2026-08-15T06:45:43.546528+00:00")


def main() -> None:
    baseline_authentication = {
        "protected_baseline_expected_sha256": EXPECTED_PROTECTED_BASELINE_SHA256,
        "protected_baseline_actual_sha256": sha256(BASELINE_PATH),
        "process_baseline_expected_sha256": EXPECTED_PROCESS_BASELINE_SHA256,
        "process_baseline_actual_sha256": sha256(PROCESS_BASELINE_PATH),
    }
    baseline_authentication["match"] = (
        baseline_authentication["protected_baseline_actual_sha256"]
        == EXPECTED_PROTECTED_BASELINE_SHA256
        and baseline_authentication["process_baseline_actual_sha256"]
        == EXPECTED_PROCESS_BASELINE_SHA256
    )
    if not baseline_authentication["match"]:
        receipt = {
            "schema_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "baseline_authentication": baseline_authentication,
            "summary": {
                "all_protected_hashes_match": False,
                "process_cleanup_match": False,
                "all_invariants_match": False,
            },
        }
        OUTPUT_PATH.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(receipt["summary"], ensure_ascii=False))
        print(OUTPUT_PATH)
        raise SystemExit(1)
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8-sig"))
    process_baseline = json.loads(PROCESS_BASELINE_PATH.read_text(encoding="utf-8-sig"))

    protected_results: list[JsonObject] = []
    for expected in baseline["protected_files"]:
        path = Path(expected["path"])
        exists = path.is_file()
        actual_length = path.stat().st_size if exists else None
        actual_sha256 = sha256(path) if exists else None
        protected_results.append(
            {
                "path": str(path),
                "kind": expected["kind"],
                "expected_length": expected["length"],
                "actual_length": actual_length,
                "expected_sha256": expected["sha256"],
                "actual_sha256": actual_sha256,
                "match": exists
                and actual_length == expected["length"]
                and actual_sha256 == expected["sha256"],
            }
        )

    registry_path = Path(baseline["registry"]["path"])
    registry_exists = registry_path.is_file()
    registry_actual_sha256 = sha256(registry_path) if registry_exists else None
    registry_match = (
        registry_exists
        and registry_actual_sha256 == baseline["registry"]["raw_sha256"]
    )

    active_package_results: list[JsonObject] = []
    packages_root = registry_path.parent / "packages"
    for record in baseline["registry"]["active_records"]:
        package_root = packages_root / record["digest"]
        file_results: list[JsonObject] = []
        for expected in record["files"]:
            path = package_root / Path(expected["relative_path"])
            exists = path.is_file()
            actual_length = path.stat().st_size if exists else None
            actual_sha256 = sha256(path) if exists else None
            file_results.append(
                {
                    "relative_path": expected["relative_path"],
                    "expected_length": expected["length"],
                    "actual_length": actual_length,
                    "expected_sha256": expected["sha256"],
                    "actual_sha256": actual_sha256,
                    "match": exists
                    and actual_length == expected["length"]
                    and actual_sha256 == expected["sha256"],
                }
            )
        active_package_results.append(
            {
                "style": record["style"],
                "digest": record["digest"],
                "package_root": str(package_root),
                "files": file_results,
                "all_match": all(result["match"] for result in file_results),
            }
        )

    process_state = collect_process_state(process_baseline)
    cleanup_state = collect_cleanup_state(ROOT, EVIDENCE, TASK_STARTED_AT)

    protected_mismatches = [
        result["path"] for result in protected_results if not result["match"]
    ]
    package_mismatches = [
        f"{package['style']}:{item['relative_path']}"
        for package in active_package_results
        for item in package["files"]
        if not item["match"]
    ]
    all_hashes_match = (
        not protected_mismatches
        and registry_match
        and not package_mismatches
    )
    process_cleanup_match = process_state.cleanup_match
    cleanup_surfaces_match = cleanup_state.cleanup_match

    receipt = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "baseline_path": str(BASELINE_PATH),
        "baseline_authentication": baseline_authentication,
        "protected_files": {
            "count": len(protected_results),
            "mismatch_count": len(protected_mismatches),
            "mismatches": protected_mismatches,
            "all_match": not protected_mismatches,
            "results": protected_results,
        },
        "registry": {
            "path": str(registry_path),
            "expected_sha256": baseline["registry"]["raw_sha256"],
            "actual_sha256": registry_actual_sha256,
            "match": registry_match,
            "active_packages": active_package_results,
            "package_mismatch_count": len(package_mismatches),
            "package_mismatches": package_mismatches,
        },
        "processes": {
            "baseline_hwp_pids": process_state.baseline_hwp_pids,
            "current_hwp_pids": process_state.current_hwp_pids,
            "added_hwp_pids": process_state.added_hwp_pids,
            "missing_hwp_pids": process_state.missing_hwp_pids,
            "expected_listener_pids_8632": process_state.expected_listener_pids,
            "current_listener_pids_8632": process_state.current_listener_pids,
            "current_hwp": process_state.current_hwp,
            "current_listener": process_state.current_listener,
            "cleanup_match": process_cleanup_match,
        },
        "cleanup_surfaces": {
            "scratch_contents": cleanup_state.scratch_contents,
            "temporary_root": cleanup_state.temporary_root,
            "temporary_patterns": list(cleanup_state.temporary_patterns),
            "matching_temporary_paths": cleanup_state.matching_temporary_paths,
            "task_lingering_temporary_paths": cleanup_state.task_lingering_temporary_paths,
            "overlay_cache_paths": cleanup_state.overlay_cache_paths,
            "overlay_staging_paths": cleanup_state.overlay_staging_paths,
            "temporary_evidence_files": cleanup_state.temporary_evidence_files,
            "headless_browser_processes": cleanup_state.headless_browser_processes,
            "task_started_at": cleanup_state.task_started_at,
            "task_headless_browser_processes": cleanup_state.task_headless_browser_processes,
            "debug_journal_exists": cleanup_state.debug_journal_exists,
            "cleanup_match": cleanup_surfaces_match,
        },
        "summary": {
            "all_protected_hashes_match": all_hashes_match,
            "process_cleanup_match": process_cleanup_match,
            "cleanup_surfaces_match": cleanup_surfaces_match,
            "all_invariants_match": (
                all_hashes_match and process_cleanup_match and cleanup_surfaces_match
            ),
        },
    }
    OUTPUT_PATH.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt["summary"], ensure_ascii=False))
    print(OUTPUT_PATH)
    raise SystemExit(0 if receipt["summary"]["all_invariants_match"] else 1)


if __name__ == "__main__":
    main()
