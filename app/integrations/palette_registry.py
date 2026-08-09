"""HwpPalette ``.hwpal`` 시험지 팔레트 등록소.

ExamPool은 HwpPalette의 편집 UI를 복제하지 않는다. HwpPalette에서 완성해 내보낸
칩을 읽고, 포함된 양식/템플릿만 내장 조판 라이브러리 위에 덮어쓴다. 라벨이 같은
조각만 교체하므로 팔레트가 일부 양식만 담아도 나머지 내장 양식은 유지된다.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..paths import data_dir


REGISTRY_SCHEMA = 1
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_FILES = 512
STYLES = ("school", "suneung")
FILE_CATEGORIES = ("템플릿", "양식")


class PalettePackageError(ValueError):
    pass


def _root() -> Path:
    return data_dir() / "typesetting_palettes"


def _registry_path() -> Path:
    return _root() / "registry.json"


def _empty_registry() -> dict:
    return {"schema_version": REGISTRY_SCHEMA, "active": {}, "packages": []}


def _load_registry() -> dict:
    try:
        value = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_registry()
    if value.get("schema_version") != REGISTRY_SCHEMA:
        return _empty_registry()
    value.setdefault("active", {})
    value.setdefault("packages", [])
    return value


def _save_registry(value: dict) -> None:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    path = _registry_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _safe_members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > MAX_FILES:
        raise PalettePackageError(f"팔레트 파일이 너무 많습니다 ({len(infos)}개).")
    total = 0
    out: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if (not name or pure.is_absolute() or ".." in pure.parts or
                name.startswith("/") or ":" in pure.parts[0]):
            raise PalettePackageError(f"안전하지 않은 팔레트 경로입니다: {info.filename}")
        if info.flag_bits & 0x1:
            raise PalettePackageError("암호화된 팔레트는 등록할 수 없습니다.")
        if name in out:
            raise PalettePackageError(f"팔레트 안에 중복 파일이 있습니다: {name}")
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise PalettePackageError("압축을 푼 팔레트 크기가 제한을 넘습니다.")
        out[name] = info
    return out


def _json_member(zf: zipfile.ZipFile, members: dict, name: str, required=False) -> dict | None:
    if name not in members:
        if required:
            raise PalettePackageError(f"올바른 .hwpal이 아닙니다: {name}이 없습니다.")
        return None
    try:
        value = json.loads(zf.read(members[name]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, OSError) as exc:
        raise PalettePackageError(f"{name}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise PalettePackageError(f"{name} 형식이 올바르지 않습니다.")
    return value


def _normal_label(value) -> str:
    return str(value or "").strip().strip("\\").strip()


def inspect_hwpal(content: bytes, filename: str = "palette.hwpal") -> tuple[dict, list[tuple[str, bytes]]]:
    """칩을 검사하고 정규화 메타데이터와 조각 바이트를 돌려준다."""
    if len(content) > MAX_ARCHIVE_BYTES:
        raise PalettePackageError("팔레트 파일은 32MB를 넘을 수 없습니다.")
    if not filename.lower().endswith(".hwpal"):
        raise PalettePackageError("HwpPalette에서 내보낸 .hwpal 파일을 선택해 주세요.")

    import io
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as exc:
        raise PalettePackageError("손상되었거나 올바르지 않은 .hwpal 파일입니다.") from exc

    with zf:
        members = _safe_members(zf)
        chip = _json_member(zf, members, "chip.json", required=True)
        library = _json_member(zf, members, "library.json", required=True)
        exam = _json_member(zf, members, "exam.json") or {}
        if library.get("version") != 1:
            raise PalettePackageError(
                f"지원하지 않는 HwpPalette 라이브러리 버전입니다: {library.get('version')}")
        raw_items = library.get("items")
        if not isinstance(raw_items, list):
            raise PalettePackageError("library.json에 물감 목록이 없습니다.")

        normalized, fragments, labels, ignored = [], [], set(), 0
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                raise PalettePackageError(f"{index + 1}번째 물감 정보가 올바르지 않습니다.")
            category = raw.get("category")
            if category not in FILE_CATEGORIES:
                ignored += 1
                continue
            label = _normal_label(raw.get("label"))
            if not label:
                raise PalettePackageError(f"{category} 물감에 호출 라벨이 없습니다.")
            if (category, label) in labels:
                raise PalettePackageError(f"같은 라벨이 두 번 들어 있습니다: {label}")
            labels.add((category, label))
            source_name = Path(str(raw.get("file") or "")).name
            archive_name = f"fragments/{source_name}"
            if not source_name or archive_name not in members:
                raise PalettePackageError(f"{label}의 HWP 조각이 없습니다: {archive_name}")
            slots = raw.get("slot_names") or []
            if not isinstance(slots, list) or any(not isinstance(v, str) for v in slots):
                raise PalettePackageError(f"{label}의 슬롯 이름 형식이 올바르지 않습니다.")
            count = int(raw.get("slot_count") or len(slots))
            if slots and count != len(slots):
                raise PalettePackageError(
                    f"{label}의 슬롯 개수({count})와 이름 수({len(slots)})가 다릅니다.")
            stored_name = f"{index:03d}_{source_name}"
            normalized.append({
                "category": category,
                "name": str(raw.get("name") or label),
                "label": label,
                "file": stored_name,
                "slot_count": count,
                "slot_names": slots,
            })
            fragments.append((stored_name, zf.read(members[archive_name])))

    if not normalized:
        raise PalettePackageError("시험지 양식이나 템플릿이 들어 있지 않은 팔레트입니다.")
    hint = str(exam.get("layout_style") or "auto").lower()
    if hint not in (*STYLES, "auto"):
        hint = "auto"
    return ({
        "name": str(chip.get("name") or Path(filename).stem),
        "note": str(chip.get("note") or ""),
        "author": str(chip.get("author") or ""),
        "made_with": str(chip.get("made_with") or ""),
        "layout_style_hint": hint,
        "items": normalized,
        "ignored_items": ignored,
        "contract": {item["label"]: item["slot_count"] for item in normalized},
    }, fragments)


def install_hwpal(content: bytes, filename: str, target_style: str | None = None) -> dict:
    if target_style is not None and target_style not in STYLES:
        raise PalettePackageError("조판 양식은 school 또는 suneung이어야 합니다.")
    metadata, fragments = inspect_hwpal(content, filename)
    digest = hashlib.sha256(content).hexdigest()
    package_id = digest[:16]
    folder = _root() / "packages" / digest
    fragments_dir = folder / "fragments"
    folder.mkdir(parents=True, exist_ok=True)
    fragments_dir.mkdir(parents=True, exist_ok=True)
    (folder / "package.hwpal").write_bytes(content)
    for name, payload in fragments:
        (fragments_dir / name).write_bytes(payload)

    record = {
        "id": package_id,
        "digest": digest,
        "filename": Path(filename).name,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    (folder / "normalized.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    registry = _load_registry()
    registry["packages"] = [p for p in registry["packages"] if p.get("digest") != digest]
    registry["packages"].append(record)
    style = target_style or metadata["layout_style_hint"]
    if style in STYLES:
        registry["active"][style] = digest
    _save_registry(registry)
    return {**_public_record(record, registry), "installed": True}


def _public_record(record: dict, registry: dict) -> dict:
    active_for = [style for style, digest in registry.get("active", {}).items()
                  if digest == record.get("digest")]
    return {key: record.get(key) for key in (
        "id", "digest", "filename", "name", "note", "author", "made_with",
        "layout_style_hint", "items", "ignored_items", "contract", "imported_at"
    )} | {"active_for": active_for}


def list_palettes() -> dict:
    registry = _load_registry()
    packages = [_public_record(p, registry) for p in reversed(registry["packages"])]
    return {"packages": packages,
            "active": {style: digest[:16] for style, digest in registry["active"].items()}}


def activate(package_id: str, style: str) -> dict:
    if style not in STYLES:
        raise PalettePackageError("조판 양식은 school 또는 suneung이어야 합니다.")
    registry = _load_registry()
    matches = [p for p in registry["packages"] if p.get("id") == package_id]
    if len(matches) != 1:
        raise PalettePackageError("등록된 시험지 팔레트를 찾을 수 없습니다.")
    registry["active"][style] = matches[0]["digest"]
    _save_registry(registry)
    return _public_record(matches[0], registry)


def deactivate(style: str) -> dict:
    if style not in STYLES:
        raise PalettePackageError("조판 양식은 school 또는 suneung이어야 합니다.")
    registry = _load_registry()
    registry["active"].pop(style, None)
    _save_registry(registry)
    return {"ok": True, "style": style}


def _active_digest() -> str:
    registry = _load_registry()
    payload = json.dumps(registry.get("active", {}), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def materialize_active(runtime_dir: Path, seed_dir: Path, force: bool = False) -> None:
    """내장 seed 위에 활성 팔레트를 합쳐 HwpPalette 실행 데이터를 만든다."""
    marker = runtime_dir / ".active_palettes.json"
    fingerprint = _active_digest()
    if not force:
        try:
            if json.loads(marker.read_text(encoding="utf-8")).get("fingerprint") == fingerprint:
                return
        except (OSError, ValueError):
            pass

    runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed_dir / "library.json", runtime_dir / "library.json")
    target_fragments = runtime_dir / "fragments"
    if target_fragments.exists():
        shutil.rmtree(target_fragments)
    shutil.copytree(seed_dir / "fragments", target_fragments)
    library = json.loads((runtime_dir / "library.json").read_text(encoding="utf-8"))
    registry = _load_registry()

    for style in STYLES:
        digest = registry.get("active", {}).get(style)
        if not digest:
            continue
        folder = _root() / "packages" / digest
        try:
            package = json.loads((folder / "normalized.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PalettePackageError(f"활성 {style} 팔레트 파일이 손상되었습니다.") from exc
        for index, item in enumerate(package.get("items", [])):
            category = item["category"]
            label = _normal_label(item["label"])
            library.setdefault(category, [])
            library[category] = [old for old in library[category]
                                 if _normal_label(old.get("label")) != label]
            source = folder / "fragments" / item["file"]
            target_name = f"hwpal_{digest[:12]}_{item['file']}"
            shutil.copy2(source, target_fragments / target_name)
            library[category].append({
                "id": f"hwpal:{digest}:{index}",
                "name": item["name"], "label": label, "file": target_name,
                "slot_count": item["slot_count"], "slot_names": item["slot_names"],
                "tags": ["ExamPool 등록"], "subcat": package.get("name", "팔레트"),
                "from_chip": package.get("name", "팔레트"),
            })

    temporary = runtime_dir / "library.json.tmp"
    temporary.write_text(json.dumps(library, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(runtime_dir / "library.json")
    marker.write_text(json.dumps({"fingerprint": fingerprint}, indent=2) + "\n",
                      encoding="utf-8")
