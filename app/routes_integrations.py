"""Cross-application pipeline endpoints: review report and hwpPalette launch."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import checklist, db, export_palette
from .integrations.hwppalette import HwpPaletteError, hwppalette_provider
from .integrations import palette_registry
from .paths import data_dir
from .routes_question import QuestionIn, _set_items

router = APIRouter(prefix="/api")


class PaintVerdictIn(BaseModel):
    state: str
    message: str = ""


def _palette_test_photo() -> str | None:
    """Find one already registered photo for checking a template's image slot."""
    extensions = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
    for folder in hwppalette_provider.photo_dirs():
        if not folder.is_dir():
            continue
        try:
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in extensions:
                    return path.stem
        except OSError:
            continue
    return None


def _paint_test_markdown(item: dict, photo_label: str | None = None) -> tuple[str, list[str]]:
    """Build visible slot markers for one registered template paint."""
    slots = list(item.get("slot_names") or [])
    count = int(item.get("slot_count") or len(slots))
    slots.extend(f"슬롯 {index + 1}" for index in range(len(slots), count))
    has_bogi = any(str(name).strip() in {"ㄱ", "ㄴ", "ㄷ"} for name in slots)
    combos = {"1": "ㄱ", "2": "ㄴ", "3": "ㄷ", "4": "ㄱ, ㄴ", "5": "ㄱ, ㄷ"}
    warnings = []

    def fill(raw_name: str) -> str:
        name = str(raw_name or "").strip()
        compact = name.replace(" ", "").lower()
        if compact in {"문항번호", "번호", "num"}:
            return "1"
        if compact in {"문두", "지문", "passage"}:
            return "그림은 물체의 운동을 나타낸 것이다."
        if compact in {"발문", "질문", "ask"}:
            return ("이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?"
                    if has_bogi else "물체의 운동 방향으로 옳은 것은?")
        if compact in {"내용", "내용박스", "내용상자"}:
            return "{[내용 상자 첫째 줄]\n[내용 상자 둘째 줄]}"
        if compact.startswith("사진") or compact.startswith("photo"):
            if photo_label:
                return f"\\{photo_label}\\"
            if "사진 시험용 그림을 찾지 못해 사진 슬롯을 비웠습니다." not in warnings:
                warnings.append("사진 시험용 그림을 찾지 못해 사진 슬롯을 비웠습니다.")
            return "-"
        if name in {"ㄱ", "ㄴ", "ㄷ"}:
            return {"ㄱ": "물체의 속력은 증가한다.",
                    "ㄴ": "물체의 운동 방향은 일정하다.",
                    "ㄷ": "물체에 작용하는 알짜힘은 0이다."}[name]
        if compact in {"배점", "점수", "points"}:
            return "3"
        if name in {"1", "2", "3", "4", "5"}:
            return combos[name] if has_bogi else f"선지 {name}"
        return f"[{name or '이름 없는 슬롯'}]"

    lines = [f"\\{item.get('label', '')}\\", *(fill(name) for name in slots[:count])]
    return "\n".join(lines) + "\n", warnings


def _active_slot_contract() -> dict[str, int]:
    contract = {
        label: export_palette.TEMPLATES[label]["slot_count"]
        for label in set(export_palette.TEMPLATE_FOR.values())
    }
    active_labels = export_palette.active_suneung_labels()
    contract.update({label: spec["slot_count"]
                     for label, spec in export_palette.SUNEUNG_TEMPLATES.items()
                     if label != "수능합답1사진5선지" or label in active_labels})
    return contract


def _evidence_summary(question: dict, choices: list[dict]) -> tuple[int, int]:
    """Count the statements that need evidence, not combo answer rows."""
    sources = choices
    if question.get("qtype") == "합답형":
        raw = question.get("bogi_items") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = []
        sources = [item for item in raw if isinstance(item, dict)]
    linked = sum(bool(item.get("proposition_id") or item.get("variant_id") or
                      item.get("custom_evidence")) for item in sources)
    return linked, len(sources)


@router.get("/integrations/status")
def integration_status():
    slot_contract = hwppalette_provider.validate_slot_contract(_active_slot_contract())
    return {
        "hwppalette": {
            "available": hwppalette_provider.available(),
            "root": str(hwppalette_provider.root),
            "photo_root": str(hwppalette_provider.photo_root()),
            "slot_contract": slot_contract,
            "palettes": palette_registry.list_palettes(),
        }
    }


@router.get("/integrations/hwppalette/palettes")
def hwppalette_palettes():
    return palette_registry.list_palettes()


@router.post("/integrations/hwppalette/palettes")
async def register_hwppalette(request: Request, filename: str,
                              target_style: str | None = None):
    """HwpPalette가 내보낸 .hwpal을 등록하고 선택한 조판 양식에 즉시 적용한다."""
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > palette_registry.MAX_ARCHIVE_BYTES:
                raise HTTPException(413, "팔레트 파일은 32MB를 넘을 수 없습니다.")
        except ValueError:
            raise HTTPException(400, "Content-Length가 올바르지 않습니다.")
    content = await request.body()
    try:
        result = palette_registry.install_hwpal(content, filename, target_style)
        hwppalette_provider._data_root()  # 활성 팔레트를 즉시 실행 라이브러리에 반영
        result["slot_contract"] = hwppalette_provider.validate_slot_contract(
            _active_slot_contract())
        return result
    except (palette_registry.PalettePackageError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/integrations/hwppalette/templates")
async def register_hwp_template(request: Request, filename: str, label: str,
                                slot_names: str, target_style: str,
                                name: str = "", category: str = "템플릿",
                                base_package_id: str | None = None):
    """Register one HWP directly and preserve the active palette as a prior revision."""
    try:
        slots = json.loads(slot_names)
        if not isinstance(slots, list) or any(not isinstance(value, str) for value in slots):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "슬롯 호출명 목록이 올바르지 않습니다.") from exc
    content = await request.body()
    try:
        result = palette_registry.install_hwp_template(
            content, filename, label=label, slot_names=slots,
            target_style=target_style, name=name, category=category,
            base_package_id=base_package_id,
        )
        hwppalette_provider._data_root()
        result["slot_contract"] = hwppalette_provider.validate_slot_contract(
            _active_slot_contract())
        return result
    except palette_registry.PalettePackageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/integrations/hwppalette/palettes/{package_id}/items/{item_index}/edit")
def edit_palette_item(package_id: str, item_index: int):
    """Start the minimal HwpPalette edit bridge with a non-destructive HWP copy."""
    try:
        session = palette_registry.start_edit_session(package_id, item_index)
        hwppalette_provider.open_template_editor(Path(session["edit_file"]))
        return {key: session[key] for key in
                ("session_id", "package_id", "item_index", "item", "edit_file")}
    except (palette_registry.PalettePackageError, HwpPaletteError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/integrations/hwppalette/edit-sessions/{session_id}/save")
def save_palette_edit(session_id: str, target_style: str):
    """Import the saved edit copy as a new active palette revision."""
    try:
        result = palette_registry.save_edit_session(session_id, target_style)
        hwppalette_provider._data_root()
        result["slot_contract"] = hwppalette_provider.validate_slot_contract(
            _active_slot_contract())
        return result
    except palette_registry.PalettePackageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/integrations/hwppalette/palettes/{package_id}/activate/{style}")
def activate_hwppalette(package_id: str, style: str):
    try:
        result = palette_registry.activate(package_id, style)
        hwppalette_provider._data_root()
        return result
    except palette_registry.PalettePackageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/integrations/hwppalette/palettes/{package_id}/items/{item_index}/preview")
def preview_palette_item(package_id: str, item_index: int, style: str = "suneung"):
    """Render one active template paint with visible test values for visual verification."""
    try:
        package, item = palette_registry.package_item(package_id, item_index, style)
        if item.get("category") != "템플릿":
            raise palette_registry.PalettePackageError(
                "양식 물감은 템플릿 미리보기에 함께 적용되어 검증됩니다.")
        markdown, warnings = _paint_test_markdown(item, _palette_test_photo())
        result = hwppalette_provider.render_preview(
            markdown, scope="question", exam_page=True, layout_style=style,
        )
        key = f"{item.get('category', '')}:{item.get('label', '')}"
        previous_state = (package.get("item_tests") or {}).get(key, {}).get("state")
        # Reopening an unchanged preview must not erase a human pass/fail decision.
        if previous_state not in {"passed", "failed"}:
            palette_registry.save_item_test(package_id, item_index, "rendered")
        return {**result, "item": item, "test_markdown": markdown, "warnings": warnings}
    except palette_registry.PalettePackageError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HwpPaletteError as exc:
        try:
            palette_registry.save_item_test(package_id, item_index, "failed", str(exc))
        except palette_registry.PalettePackageError:
            pass
        raise HTTPException(503, str(exc)) from exc


@router.post("/integrations/hwppalette/palettes/{package_id}/items/{item_index}/verdict")
def verify_palette_item(package_id: str, item_index: int, body: PaintVerdictIn):
    """Save the user's visual pass/fail decision after inspecting a rendered paint."""
    try:
        if body.state not in {"passed", "failed"}:
            raise palette_registry.PalettePackageError("정상 또는 문제 있음으로 판정해 주세요.")
        return palette_registry.save_item_test(
            package_id, item_index, body.state, body.message)
    except palette_registry.PalettePackageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/integrations/hwppalette/palettes/active/{style}")
def deactivate_hwppalette(style: str):
    try:
        result = palette_registry.deactivate(style)
        hwppalette_provider._data_root()
        return result
    except palette_registry.PalettePackageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/integrations/hwppalette/slot-contract")
def hwppalette_slot_contract():
    return hwppalette_provider.validate_slot_contract(_active_slot_contract())


@router.post("/previews/question")
def preview_question(payload: QuestionIn):
    """Preview the current editor draft without saving it as a bank question."""
    question = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    choices = question.pop("choices", [])
    layout_style = question.get("layout_style", "school")
    reconstruction = (question.get("style_meta") or {}).get("reconstruction") or {}
    source_number = reconstruction.get("item_number") if reconstruction.get("enabled") else None
    number = int(source_number) if isinstance(source_number, int) and source_number > 0 else 1
    markdown = export_palette.question_to_palette(
        question, choices, num=number, layout_style=layout_style) + "\n"
    try:
        return hwppalette_provider.render_preview(
            markdown, scope="question", exam_page=True,
            layout_style=layout_style,
        )
    except HwpPaletteError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/sets/{sid}/preview")
def preview_set(sid: int):
    conn = db.connect()
    try:
        set_row = conn.execute("SELECT * FROM exam_set WHERE id=?", (sid,)).fetchone()
        if not set_row:
            raise HTTPException(404, "세트를 찾을 수 없습니다.")
        items = _set_items(conn, sid)
    finally:
        conn.close()
    if not items:
        raise HTTPException(409, "미리 볼 문항이 없습니다.")
    issues = checklist.check_set(dict(set_row), items)
    layout_style = dict(set_row).get("layout_style", "school")
    markdown = export_palette.set_to_markdown(
        [(item["question"], item["choices"]) for item in items],
        layout_style=layout_style,
    )
    try:
        result = hwppalette_provider.render_preview(
            markdown, scope=f"set:{sid}", exam_page=True,
            layout_style=layout_style,
        )
    except HwpPaletteError as exc:
        raise HTTPException(503, str(exc)) from exc
    result["count"] = len(items)
    result["issues"] = issues
    return result


@router.get("/previews/{token}/pages/{page_no}")
def preview_page(token: str, page_no: int):
    path = hwppalette_provider.preview_asset(token, "page", page_no)
    if not path:
        raise HTTPException(404, "미리보기 페이지를 찾을 수 없습니다.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/previews/{token}/{kind}")
def preview_document(token: str, kind: str):
    if kind not in {"hwp", "pdf"}:
        raise HTTPException(404, "미리보기 파일을 찾을 수 없습니다.")
    path = hwppalette_provider.preview_asset(token, kind)
    if not path:
        raise HTTPException(404, "미리보기 파일을 찾을 수 없습니다.")
    media_type = "application/pdf" if kind == "pdf" else "application/x-hwp"
    return FileResponse(path, media_type=media_type, filename=f"ExamPool-preview.{kind}")


@router.post("/sets/{sid}/typeset")
def typeset_set(sid: int):
    conn = db.connect()
    try:
        set_row = conn.execute("SELECT * FROM exam_set WHERE id=?", (sid,)).fetchone()
        if not set_row:
            raise HTTPException(404, "세트를 찾을 수 없습니다.")
        items = _set_items(conn, sid)
    finally:
        conn.close()
    if not items:
        raise HTTPException(409, "조판할 문항이 없습니다.")
    issues = checklist.check_set(dict(set_row), items)
    errors = [issue for issue in issues if issue["level"] == "error"]
    if errors:
        raise HTTPException(409, "세트 검토 오류를 먼저 해결하세요: " + errors[0]["message"])
    layout_style = dict(set_row).get("layout_style", "school")
    markdown = export_palette.set_to_markdown(
        [(item["question"], item["choices"]) for item in items],
        layout_style=layout_style,
    )
    export_dir = data_dir() / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"set_{sid}.md"
    path.write_text(markdown, encoding="utf-8")
    try:
        process = hwppalette_provider.launch(
            path, exam_page=True, layout_style=layout_style,
        )
    except HwpPaletteError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "pid": process.pid, "markdown_path": str(path), "count": len(items)}


@router.get("/integrations/hwppalette/process/{pid}")
def hwppalette_process_status(pid: int):
    try:
        return hwppalette_provider.process_status(pid)
    except HwpPaletteError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/sets/{sid}/review-report")
def review_report(sid: int):
    conn = db.connect()
    try:
        set_row = conn.execute("SELECT * FROM exam_set WHERE id=?", (sid,)).fetchone()
        if not set_row:
            raise HTTPException(404, "세트를 찾을 수 없습니다.")
        items = _set_items(conn, sid)
        # 배치로 그림 상태 한 번에 조회 (N+1 회피)
        qids = [it["question"]["id"] for it in items if it["question"]]
        figures = {}
        if qids:
            placeholders = ",".join("?" for _ in qids)
            for r in conn.execute(
                f"SELECT a.question_id, f.status, f.rendered_image_path "
                f"FROM authoring_session a JOIN authoring_figure f ON f.session_id=a.id "
                f"WHERE a.question_id IN ({placeholders})", qids
            ).fetchall():
                figures[r["question_id"]] = r
        rows = []
        for item in items:
            q = item["question"]
            choices = item["choices"]
            figure = figures.get(q["id"])
            linked, evidence_total = _evidence_summary(q, choices)
            rows.append({
                "ord": item["ord"], "question_id": q["id"], "ask": q["ask"],
                "standard_code": q.get("standard_code") or "",
                "origin": q.get("origin") or "", "status": q.get("status") or "",
                "evidence_count": linked, "choice_count": evidence_total,
                "issues": checklist.check_question(q, choices),
                "figure_status": figure["status"] if figure else "none",
                "has_figure": bool(figure and figure["rendered_image_path"] and
                                   Path(figure["rendered_image_path"]).is_file()),
            })
        set_issues = checklist.check_set(dict(set_row), items)
        return {"set": dict(set_row), "issues": set_issues, "items": rows}
    finally:
        conn.close()
