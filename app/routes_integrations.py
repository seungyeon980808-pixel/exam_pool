"""Cross-application pipeline endpoints: review report and hwpPalette launch."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import checklist, db, export_palette
from .integrations.hwppalette import HwpPaletteError, hwppalette_provider
from .integrations import palette_registry
from .paths import data_dir
from .routes_question import QuestionIn, _set_items

router = APIRouter(prefix="/api")


def _active_slot_contract() -> dict[str, int]:
    contract = {
        label: export_palette.TEMPLATES[label]["slot_count"]
        for label in set(export_palette.TEMPLATE_FOR.values())
    }
    contract.update({label: spec["slot_count"]
                     for label, spec in export_palette.SUNEUNG_TEMPLATES.items()})
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


@router.post("/integrations/hwppalette/palettes/{package_id}/activate/{style}")
def activate_hwppalette(package_id: str, style: str):
    try:
        result = palette_registry.activate(package_id, style)
        hwppalette_provider._data_root()
        return result
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
    markdown = export_palette.question_to_palette(
        question, choices, num=1, layout_style=layout_style) + "\n"
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
