# -*- coding: utf-8 -*-
"""시험지 조판팩 명세를 읽고 독립 실행용 library.json을 만든다.

GUI의 개인 ``data/library.json``은 사용자가 편집하는 작업 데이터다. ExamPool 같은
외부 프로그램은 그 파일을 직접 배포 계약으로 삼지 않고, 버전이 기록된 조판팩만
가져가야 한다. 이 모듈은 그 좁은 경계를 담당한다.
"""
from __future__ import annotations

import json
from pathlib import Path


SCHEMA_VERSION = 1


class PackError(ValueError):
    """조판팩 명세 또는 파일이 올바르지 않다."""


def load_pack(pack_dir: str | Path) -> dict:
    pack_dir = Path(pack_dir).resolve()
    manifest_path = pack_dir / "pack.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackError(f"조판팩 명세를 읽지 못했습니다: {manifest_path}") from exc

    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PackError(
            f"지원하지 않는 조판팩 스키마입니다: {manifest.get('schema_version')}")
    if not manifest.get("name") or not manifest.get("version"):
        raise PackError("조판팩 name과 version이 필요합니다")

    entries = [*([manifest["form"]] if manifest.get("form") else []),
               *manifest.get("templates", [])]
    labels: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackError("조판팩 form/templates 항목이 올바르지 않습니다")
        for key in ("key", "name", "label", "file", "slots"):
            if key not in entry:
                raise PackError(f"조판팩 항목에 {key}가 없습니다: {entry}")
        if entry["label"] in labels:
            raise PackError(f"중복 조판팩 라벨입니다: {entry['label']}")
        labels.add(entry["label"])
        if len(entry["slots"]) != len(set(entry["slots"])):
            raise PackError(f"중복 슬롯 이름이 있습니다: {entry['label']}")
        asset = (pack_dir / entry["file"]).resolve()
        if pack_dir not in asset.parents or not asset.is_file():
            raise PackError(f"조판팩 HWP 파일이 없습니다: {entry['file']}")

    return {**manifest, "_root": pack_dir}


def library_payload(manifest: dict) -> dict:
    """팩 명세를 hwpPalette 실행 엔진이 읽는 최소 library.json으로 변환한다."""
    payload = {"서식": [], "문자": [], "템플릿": [], "양식": []}

    def item(entry: dict, category: str) -> dict:
        return {
            "id": f"pack:{manifest['name']}:{entry['key']}",
            "name": entry["name"],
            "label": entry["label"],
            "file": Path(entry["file"]).name,
            "slot_count": len(entry["slots"]),
            "slot_names": list(entry["slots"]),
            "tags": ["시험지팩"],
            "subcat": manifest["name"],
            "pack_version": manifest["version"],
            "category": category,
        }

    if manifest.get("form"):
        payload["양식"].append(item(manifest["form"], "양식"))
    payload["템플릿"].extend(item(entry, "템플릿")
                           for entry in manifest["templates"])
    return payload


def contract(manifest: dict) -> dict[str, int]:
    """라벨별 슬롯 수 계약."""
    entries = [*([manifest["form"]] if manifest.get("form") else []),
               *manifest["templates"]]
    return {entry["label"]: len(entry["slots"]) for entry in entries}
