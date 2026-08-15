"""HwpPalette ``.hwpal`` 시험지 팔레트 등록소.

ExamPool은 HwpPalette의 편집 UI를 복제하지 않는다. HwpPalette에서 완성해 내보낸
칩을 읽고, 포함된 양식/템플릿만 내장 조판 라이브러리 위에 덮어쓴다. 라벨이 같은
조각만 교체하므로 팔레트가 일부 양식만 담아도 나머지 내장 양식은 유지된다.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..paths import data_dir


REGISTRY_SCHEMA = 1
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_FILES = 512
MAX_HWP_BYTES = 32 * 1024 * 1024
STYLES = ("school", "suneung")
FILE_CATEGORIES = ("템플릿", "양식")
_DIRECT_ONE_PHOTO_SLOTS = ["문항번호", "문두", "사진1", "발문", "1", "2", "3", "4", "5"]
_DIRECT_TWO_PHOTO_SLOTS = ["문항번호", "문두", "사진1", "사진2", "발문", "1", "2", "3", "4", "5"]
_DIRECT_VERTICAL_PHOTO_SLOTS = [
    "문항번호", "문두", "사진1", "자료", "발문", "1", "2", "3", "4", "5",
]
_DIRECT_GRAPHICAL_CHOICE_SLOTS = [
    "문항번호", "문두", "사진1", "발문",
    "선지사진1", "선지사진2", "선지사진3", "선지사진4", "선지사진5",
]
_HAPDAP_TWO_PHOTO_SLOTS = [
    "문항번호", "문두", "사진1", "사진2", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
]
_HAPDAP_ONE_PHOTO_SLOTS = [
    "문항번호", "문두", "사진1", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
]
_HAPDAP_VERTICAL_PHOTO_SLOTS = [
    "문항번호", "문두", "사진1", "자료", "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
]
_HAPDAP_THREE_CAPTIONED_PHOTO_SLOTS = [
    "문항번호", "문두",
    "사진1", "(가)", "사진2", "(나)", "사진3", "(다)",
    "발문", "ㄱ", "ㄴ", "ㄷ", "1", "2", "3", "4", "5",
]
_DERIVED_TEMPLATES = {
    "수능정답1대사진5선지": ("csat_direct_one_large.hwp", _DIRECT_ONE_PHOTO_SLOTS),
    "수능정답1소사진5선지": ("csat_direct_one_small.hwp", _DIRECT_ONE_PHOTO_SLOTS),
    "수능정답2소사진무캡션5선지": (
        "csat_direct_two_small_caption_free.hwp", _DIRECT_TWO_PHOTO_SLOTS,
    ),
    "수능정답2대사진5선지": (
        "csat_direct_two_large_caption_free.hwp", _DIRECT_TWO_PHOTO_SLOTS,
    ),
    "수능정답상하사진5선지": (
        "csat_direct_vertical_pair.hwp", _DIRECT_VERTICAL_PHOTO_SLOTS,
    ),
    "수능정답1대사진그림5선지": (
        "csat_direct_one_large_graphical_choices.hwp", _DIRECT_GRAPHICAL_CHOICE_SLOTS,
    ),
    "수능합답1대사진5선지": (
        "csat_hapdap_one_large.hwp", _HAPDAP_ONE_PHOTO_SLOTS,
    ),
    "수능합답1소사진5선지": (
        "csat_hapdap_one_small.hwp", _HAPDAP_ONE_PHOTO_SLOTS,
    ),
    "수능합답2소사진무캡션5선지": (
        "csat_hapdap_two_small_caption_free.hwp", _HAPDAP_TWO_PHOTO_SLOTS,
    ),
    "수능합답2대사진5선지": (
        "csat_hapdap_two_large_caption_free.hwp", _HAPDAP_TWO_PHOTO_SLOTS,
    ),
    "수능합답상하사진5선지": (
        "csat_hapdap_vertical_pair.hwp", _HAPDAP_VERTICAL_PHOTO_SLOTS,
    ),
    "수능합답3소사진5선지": (
        "csat_hapdap_three_small_captioned.hwp", _HAPDAP_THREE_CAPTIONED_PHOTO_SLOTS,
    ),
}


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
            options = raw.get("slot_options") or [{} for _ in range(count)]
            if (not isinstance(options, list) or len(options) != count
                    or any(not isinstance(value, dict) for value in options)):
                raise PalettePackageError(
                    f"{label}의 슬롯 옵션 수가 슬롯 개수({count})와 다릅니다.")
            stored_name = f"{index:03d}_{source_name}"
            normalized.append({
                "category": category,
                "name": str(raw.get("name") or label),
                "label": label,
                "file": stored_name,
                "slot_count": count,
                "slot_names": slots,
                "slot_options": options,
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
    previous = next((p for p in registry["packages"] if p.get("digest") == digest), None)
    if previous and isinstance(previous.get("item_tests"), dict):
        record["item_tests"] = previous["item_tests"]
    registry["packages"] = [p for p in registry["packages"] if p.get("digest") != digest]
    registry["packages"].append(record)
    style = target_style or metadata["layout_style_hint"]
    if style in STYLES:
        registry["active"][style] = digest
    _save_registry(registry)
    return {**_public_record(record, registry), "installed": True}


def _package_by_id(package_id: str) -> dict:
    registry = _load_registry()
    matches = [item for item in registry.get("packages", []) if item.get("id") == package_id]
    if len(matches) != 1:
        raise PalettePackageError("등록된 시험지 팔레트를 찾을 수 없습니다.")
    return matches[0]


def _valid_hwp(content: bytes, filename: str) -> None:
    if not filename.lower().endswith(".hwp"):
        raise PalettePackageError("한글 HWP 파일을 선택해 주세요.")
    if not content or len(content) > MAX_HWP_BYTES:
        raise PalettePackageError("HWP 파일은 비어 있지 않은 32MB 이하 파일이어야 합니다.")
    if not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise PalettePackageError("올바른 바이너리 HWP 파일이 아닙니다.")


def _revision_archive(base: dict | None, replacement: dict, payload: bytes,
                      target_style: str) -> tuple[bytes, str]:
    """Build one valid .hwpal revision while preserving the other paints."""
    items: list[tuple[dict, bytes]] = []
    if base:
        folder = _root() / "packages" / base["digest"] / "fragments"
        for old in base.get("items") or []:
            try:
                old_payload = (folder / Path(old["file"]).name).read_bytes()
            except OSError as exc:
                raise PalettePackageError(f"기존 물감 파일을 읽지 못했습니다: {old.get('label')}") from exc
            items.append((dict(old), old_payload))

    wanted = (replacement["category"], _normal_label(replacement["label"]))
    items = [(item, body) for item, body in items
             if (item.get("category"), _normal_label(item.get("label"))) != wanted]
    items.append((replacement, payload))

    now = datetime.now().astimezone()
    base_name = str((base or {}).get("name") or "ExamPool 직접 등록")
    revision_name = f"{base_name} · {now:%m.%d %H:%M} 수정본"
    library_items = []
    fragment_entries = []
    for index, (item, body) in enumerate(items):
        fragment_name = f"{index:03d}_{uuid.uuid4().hex}.hwp"
        slots = list(item.get("slot_names") or [])
        options = list(item.get("slot_options") or [{} for _ in slots])
        if len(options) != len(slots):
            options = [{} for _ in slots]
        library_items.append({
            "name": str(item.get("name") or item.get("label")),
            "label": _normal_label(item.get("label")),
            "file": fragment_name,
            "slot_count": len(slots),
            "slot_names": slots,
            "slot_options": options,
            "category": item.get("category") or "템플릿",
        })
        fragment_entries.append((f"fragments/{fragment_name}", body))

    exam_items = [{key: item[key] for key in
                   ("category", "name", "label", "slot_count", "slot_names")}
                  for item in library_items]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("chip.json", json.dumps({
            "chip_version": 1, "name": revision_name,
            "note": "ExamPool에서 만든 비파괴 수정본",
            "author": "", "made_with": "ExamPool HwpPalette bridge",
        }, ensure_ascii=False, indent=2))
        zf.writestr("library.json", json.dumps({"version": 1, "items": library_items},
                                               ensure_ascii=False, indent=2))
        zf.writestr("exam.json", json.dumps({
            "schema_version": 1, "kind": "exam_palette", "name": revision_name,
            "layout_style": target_style, "items": exam_items,
        }, ensure_ascii=False, indent=2))
        for name, body in fragment_entries:
            zf.writestr(name, body)
    return output.getvalue(), revision_name


def install_hwp_template(content: bytes, filename: str, *, label: str,
                         slot_names: list[str], target_style: str,
                         name: str = "", category: str = "템플릿",
                         base_package_id: str | None = None) -> dict:
    """Register one HWP directly as a new, rollback-safe palette revision."""
    _valid_hwp(content, filename)
    if target_style not in STYLES:
        raise PalettePackageError("조판 양식은 school 또는 suneung이어야 합니다.")
    if category not in FILE_CATEGORIES:
        raise PalettePackageError("물감 종류는 템플릿 또는 양식이어야 합니다.")
    clean_label = _normal_label(label)
    if not clean_label:
        raise PalettePackageError("템플릿 호출명을 입력해 주세요.")
    slots = [str(value).strip() for value in slot_names if str(value).strip()]
    if category == "템플릿" and not slots:
        raise PalettePackageError("템플릿의 슬롯 호출명을 한 개 이상 입력해 주세요.")

    registry = _load_registry()
    base = None
    if base_package_id:
        base = _package_by_id(base_package_id)
    else:
        digest = registry.get("active", {}).get(target_style)
        base = next((item for item in registry.get("packages", [])
                     if item.get("digest") == digest), None)
    previous_options = None
    if base:
        previous = next((item for item in (base.get("items") or [])
                         if _normal_label(item.get("label")) == clean_label
                         and item.get("category") == category), None)
        if previous and list(previous.get("slot_names") or []) == slots:
            candidate = previous.get("slot_options")
            if isinstance(candidate, list) and len(candidate) == len(slots):
                previous_options = candidate
    replacement = {
        "category": category, "name": str(name or clean_label), "label": clean_label,
        "slot_names": slots, "slot_count": len(slots),
        "slot_options": previous_options or [{} for _ in slots],
    }
    archive, revision_name = _revision_archive(base, replacement, content, target_style)
    result = install_hwpal(archive, f"{revision_name}.hwpal", target_style)
    result["replaces"] = base.get("id") if base else None
    result["edited_label"] = clean_label
    return result


def start_edit_session(package_id: str, item_index: int) -> dict:
    """Copy one paint to an isolated edit file; the registered revision stays intact."""
    package = _package_by_id(package_id)
    items = package.get("items") or []
    if item_index < 0 or item_index >= len(items):
        raise PalettePackageError("수정할 물감을 찾을 수 없습니다.")
    item = items[item_index]
    source = _root() / "packages" / package["digest"] / "fragments" / Path(item["file"]).name
    if not source.is_file():
        raise PalettePackageError("수정할 HWP 조각이 없습니다.")
    session_id = uuid.uuid4().hex
    folder = _root() / "edit_sessions" / session_id
    folder.mkdir(parents=True, exist_ok=False)
    edit_file = folder / f"{_normal_label(item.get('label')) or 'template'}.hwp"
    shutil.copy2(source, edit_file)
    metadata = {
        "session_id": session_id, "package_id": package_id, "item_index": item_index,
        "item": item, "edit_file": str(edit_file),
        "created_at": datetime.now(timezone.utc).isoformat(), "saved": False,
    }
    (folder / "session.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def save_edit_session(session_id: str, target_style: str | None = None) -> dict:
    try:
        safe_id = uuid.UUID(hex=session_id).hex
    except ValueError as exc:
        raise PalettePackageError("올바르지 않은 편집 세션입니다.") from exc
    folder = _root() / "edit_sessions" / safe_id
    try:
        metadata = json.loads((folder / "session.json").read_text(encoding="utf-8"))
        edit_file = Path(metadata["edit_file"])
        content = edit_file.read_bytes()
    except (OSError, ValueError, KeyError) as exc:
        raise PalettePackageError("편집 파일을 찾거나 읽지 못했습니다.") from exc
    item = metadata.get("item") or {}
    # An edit is a revision of its source palette.  Derive the destination from
    # that palette's declared layout style instead of whichever activation
    # badge happened to be visible in the browser.  A suneung palette can be
    # temporarily activated in the school slot while testing; inheriting that
    # slot used to save the corrected template to the wrong family.
    base = _package_by_id(str(metadata.get("package_id") or ""))
    inherited_style = str(base.get("layout_style_hint") or "").lower()
    resolved_style = inherited_style if inherited_style in STYLES else target_style
    if resolved_style not in STYLES:
        raise PalettePackageError("수정본을 저장할 조판 양식을 확인할 수 없습니다.")
    result = install_hwp_template(
        content, edit_file.name, label=item.get("label", ""),
        slot_names=item.get("slot_names") or [], target_style=resolved_style,
        name=item.get("name", ""), category=item.get("category", "템플릿"),
        base_package_id=metadata.get("package_id"),
    )
    result["target_style"] = resolved_style
    metadata["saved"] = True
    metadata["saved_package_id"] = result.get("id")
    metadata["saved_at"] = datetime.now(timezone.utc).isoformat()
    (folder / "session.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _public_record(record: dict, registry: dict) -> dict:
    active_for = [style for style, digest in registry.get("active", {}).items()
                  if digest == record.get("digest")]
    return {key: record.get(key) for key in (
        "id", "digest", "filename", "name", "note", "author", "made_with",
        "layout_style_hint", "items", "ignored_items", "contract", "imported_at", "item_tests"
    )} | {"active_for": active_for}


def list_palettes() -> dict:
    registry = _load_registry()
    packages = [_public_record(p, registry) for p in reversed(registry["packages"])]
    return {"packages": packages,
            "active": {style: digest[:16] for style, digest in registry["active"].items()}}


def package_item(package_id: str, item_index: int, style: str | None = None) -> tuple[dict, dict]:
    """Return one registered paint and optionally require it to be active for a style."""
    registry = _load_registry()
    matches = [p for p in registry["packages"] if p.get("id") == package_id]
    if len(matches) != 1:
        raise PalettePackageError("등록된 시험지 팔레트를 찾을 수 없습니다.")
    package = matches[0]
    items = package.get("items") or []
    if item_index < 0 or item_index >= len(items):
        raise PalettePackageError("등록된 물감을 찾을 수 없습니다.")
    if style is not None:
        if style not in STYLES:
            raise PalettePackageError("조판 양식은 school 또는 suneung이어야 합니다.")
        if registry.get("active", {}).get(style) != package.get("digest"):
            raise PalettePackageError(
                f"이 팔레트를 먼저 {'학교' if style == 'school' else '수능'} 양식으로 적용해 주세요.")
    return package, items[item_index]


def active_template(style: str, label: str) -> dict | None:
    """Return a template from the palette currently active for ``style``."""
    if style not in STYLES:
        return None
    registry = _load_registry()
    digest = registry.get("active", {}).get(style)
    if not digest:
        return None
    wanted = _normal_label(label)
    if style == "suneung" and wanted in _DERIVED_TEMPLATES:
        filename, slot_names = _DERIVED_TEMPLATES[wanted]
        return {
            "category": "템플릿", "name": wanted, "label": wanted,
            "file": filename,
            "slot_count": len(slot_names),
            "slot_names": list(slot_names),
            "slot_options": [{} for _ in slot_names],
        }
    package = next((p for p in registry.get("packages", [])
                    if p.get("digest") == digest), None)
    if not package:
        return None
    return next((item for item in package.get("items", [])
                 if item.get("category") == "템플릿"
                 and _normal_label(item.get("label")) == wanted), None)


def save_item_test(package_id: str, item_index: int, state: str, message: str = "") -> dict:
    """Persist the latest render/visual-verification result for a paint."""
    if state not in {"rendered", "passed", "failed"}:
        raise PalettePackageError("물감 시험 상태가 올바르지 않습니다.")
    registry = _load_registry()
    matches = [p for p in registry["packages"] if p.get("id") == package_id]
    if len(matches) != 1:
        raise PalettePackageError("등록된 시험지 팔레트를 찾을 수 없습니다.")
    package = matches[0]
    items = package.get("items") or []
    if item_index < 0 or item_index >= len(items):
        raise PalettePackageError("등록된 물감을 찾을 수 없습니다.")
    item = items[item_index]
    key = f"{item.get('category', '')}:{item.get('label', '')}"
    result = {
        "state": state,
        "message": str(message or "")[:500],
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }
    package.setdefault("item_tests", {})[key] = result
    _save_registry(registry)
    return result


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
    derived = {}
    for label, (filename, _) in _DERIVED_TEMPLATES.items():
        path = Path(__file__).resolve().parents[2] / "assets" / "hwp_templates" / filename
        derived[label] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    payload = json.dumps(
        {"active": registry.get("active", {}), "derived": derived},
        sort_keys=True, ensure_ascii=False,
    )
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
                "slot_options": item.get("slot_options", []),
                "tags": ["ExamPool 등록"], "subcat": package.get("name", "팔레트"),
                "from_chip": package.get("name", "팔레트"),
            })

    for index, (label, (filename, slot_names)) in enumerate(_DERIVED_TEMPLATES.items()):
        source = Path(__file__).resolve().parents[2] / "assets" / "hwp_templates" / filename
        if not source.is_file():
            raise PalettePackageError(f"ExamPool 직접형 템플릿이 없습니다: {source}")
        target_name = f"exampool_{filename}"
        shutil.copy2(source, target_fragments / target_name)
        library.setdefault("템플릿", [])
        library["템플릿"] = [
            old for old in library["템플릿"]
            if _normal_label(old.get("label")) != label
        ]
        library["템플릿"].append({
            "id": f"exampool:direct:{index}",
            "name": label, "label": label, "file": target_name,
            "slot_count": len(slot_names),
            "slot_names": list(slot_names),
            "slot_options": [{} for _ in slot_names],
            "tags": ["ExamPool 파생"], "subcat": "ExamPool 파생 템플릿",
            "from_chip": "ExamPool",
        })

    temporary = runtime_dir / "library.json.tmp"
    temporary.write_text(json.dumps(library, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(runtime_dir / "library.json")
    marker.write_text(json.dumps({"fingerprint": fingerprint}, indent=2) + "\n",
                      encoding="utf-8")
