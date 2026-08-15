"""Write a fresh protected-file and process receipt for the equation follow-up."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from verify_final_invariants_support import JsonObject, powershell_json, sha256


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".omo/evidence/pdf-hwp-generalization/protected-baseline.json"
OUTPUT = ROOT / (
    ".omo/evidence/pdf-hwp-generalization/equation-glyphs/"
    "followup-protected-process-receipt.json"
)
START_HWP_PIDS = [2168, 23900, 46896]
START_LISTENER_PIDS = [41992]


def _protected_results(baseline: JsonObject) -> list[JsonObject]:
    results: list[JsonObject] = []
    for expected in baseline["protected_files"]:
        path = Path(expected["path"])
        exists = path.is_file()
        actual_length = path.stat().st_size if exists else None
        actual_hash = sha256(path) if exists else None
        results.append({
            "path": str(path), "kind": expected["kind"],
            "expected_length": expected["length"], "actual_length": actual_length,
            "expected_sha256": expected["sha256"], "actual_sha256": actual_hash,
            "match": exists and actual_length == expected["length"]
            and actual_hash == expected["sha256"],
        })
    return results


def _registry_result(baseline: JsonObject) -> JsonObject:
    expected = baseline["registry"]
    path = Path(expected["path"])
    actual_hash = sha256(path) if path.is_file() else None
    packages_root = path.parent / "packages"
    files = []
    for record in expected["active_records"]:
        package_root = packages_root / record["digest"]
        for item in record["files"]:
            candidate = package_root / item["relative_path"]
            exists = candidate.is_file()
            actual_length = candidate.stat().st_size if exists else None
            actual_file_hash = sha256(candidate) if exists else None
            files.append({
                "style": record["style"], "digest": record["digest"],
                "path": str(candidate), "expected_length": item["length"],
                "actual_length": actual_length, "expected_sha256": item["sha256"],
                "actual_sha256": actual_file_hash,
                "match": exists and actual_length == item["length"]
                and actual_file_hash == item["sha256"],
            })
    return {
        "path": str(path), "expected_sha256": expected["raw_sha256"],
        "actual_sha256": actual_hash, "match": actual_hash == expected["raw_sha256"],
        "active_file_count": len(files),
        "active_file_mismatch_count": sum(not item["match"] for item in files),
        "active_files": files,
    }


def _process_result() -> JsonObject:
    hwp = powershell_json(
        "@(Get-CimInstance Win32_Process -Filter \"Name='Hwp.exe'\" | "
        "Select-Object ProcessId,Name,CommandLine,CreationDate) | "
        "ConvertTo-Json -Depth 4 -Compress"
    )
    listeners = powershell_json(
        "@(Get-NetTCPConnection -LocalPort 8632 -State Listen "
        "-ErrorAction SilentlyContinue | "
        "Select-Object LocalAddress,LocalPort,OwningProcess) | "
        "ConvertTo-Json -Depth 4 -Compress"
    )
    hwp_pids = sorted(int(str(item["ProcessId"])) for item in hwp)
    listener_pids = sorted(int(str(item["OwningProcess"])) for item in listeners)
    return {
        "start_hwp_pids": START_HWP_PIDS, "current_hwp_pids": hwp_pids,
        "start_listener_pids_8632": START_LISTENER_PIDS,
        "current_listener_pids_8632": listener_pids,
        "hwp": hwp, "listeners_8632": listeners,
        "match": hwp_pids == START_HWP_PIDS and listener_pids == START_LISTENER_PIDS,
    }


def main() -> int:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8-sig"))
    protected = _protected_results(baseline)
    immutable = [
        item for item in protected
        if item["kind"] in {"existing_hwp_template", "original_hwpal"}
    ]
    related_source = [
        item for item in protected if item["kind"] == "related_product_source"
    ]
    registry = _registry_result(baseline)
    processes = _process_result()
    cleanup_paths = [
        ROOT / ".debug-journal.md",
        ROOT / ".omo/evidence/pdf-hwp-generalization/equation-glyphs/"
        "followup-regression/scratch-p1",
        ROOT / ".omo/evidence/pdf-hwp-generalization/equation-glyphs/"
        "followup-regression/scratch-b2-e1-e2",
        ROOT / ".omo/evidence/pdf-hwp-generalization/equation-glyphs/"
        "followup-final-sequential/scratch-p1",
        ROOT / ".omo/evidence/pdf-hwp-generalization/equation-glyphs/"
        "followup-final-sequential/scratch-b2-e1-e2",
    ]
    cleanup = {
        "paths_expected_absent": [str(path) for path in cleanup_paths],
        "paths_still_present": [str(path) for path in cleanup_paths if path.exists()],
    }
    passed = (
        all(item["match"] for item in immutable)
        and registry["match"] and registry["active_file_mismatch_count"] == 0
        and processes["match"] and not cleanup["paths_still_present"]
    )
    receipt = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "task_start_snapshot_source": "debug journal captured before follow-up actions",
        "protected_baseline_path": str(BASELINE),
        "protected_baseline_sha256": sha256(BASELINE),
        "immutable_file_count": len(immutable),
        "immutable_file_mismatch_count": sum(not item["match"] for item in immutable),
        "immutable_files": immutable,
        "related_product_source_count": len(related_source),
        "related_product_source_mismatch_count": sum(
            not item["match"] for item in related_source
        ),
        "related_product_sources": related_source, "registry": registry,
        "processes": processes, "cleanup": cleanup, "passed": passed,
    }
    OUTPUT.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "output": str(OUTPUT), "immutable_file_count": len(immutable),
        "immutable_file_mismatch_count": receipt["immutable_file_mismatch_count"],
        "active_file_count": registry["active_file_count"],
        "active_file_mismatch_count": registry["active_file_mismatch_count"],
        "process_match": processes["match"], "cleanup_match": not cleanup["paths_still_present"],
        "passed": passed,
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
