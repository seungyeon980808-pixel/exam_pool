"""대화형 문항 제작 세션·메시지·선택 반영 API."""
from __future__ import annotations

import copy
import base64
import json
from pathlib import Path
import queue
import re
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import db
from .authoring.codex_app_server import CodexAppServerError, codex_app_server
from .authoring.figures import FigureProviderError, get_figure_provider
from .authoring.providers import get_provider
from .integrations.hwppalette import hwppalette_provider
from .paths import data_dir

router = APIRouter(prefix="/api/authoring")

VALID_STATUSES = {
    "text_drafting", "text_confirmed", "figure_drafting",
    "figure_confirmed", "reviewing", "saved", "discarded",
}
APPLY_FIELDS = {"passage", "ask", "bogi_items", "choices", "answer", "explanation", "figure_plan"}
TEXT_FIELDS = ("passage", "ask", "bogi_items", "choices", "answer", "explanation")


class SessionIn(BaseModel):
    question_id: int | None = None
    provider: str = "codex_local"


class ProviderIn(BaseModel):
    provider: str = "codex_local"


class SettingsIn(BaseModel):
    authoring_mode: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None


class DraftIn(BaseModel):
    draft: dict


class MessageIn(BaseModel):
    content: str


class ApplyIn(BaseModel):
    message_id: int
    proposal_id: str


class BindIn(BaseModel):
    question_id: int


class FigureOptionsIn(BaseModel):
    provider: str = "fivee_assets"
    include_text: bool = False
    composition: str = "auto"


class FigureImageImportIn(BaseModel):
    panel_id: str = "main"
    filename: str = "figure.png"
    data_url: str


AUTHORING_MODES = {
    "quick": {"label": "빠르게 작성", "model": "gpt-5.6-luna", "reasoning_effort": "medium"},
    "precise": {"label": "정밀하게 수정", "model": "gpt-5.6-terra", "reasoning_effort": "medium"},
    "final": {"label": "최종 검수", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
}
FIGURE_OPTION_DEFAULTS = {
    "provider": "fivee_assets", "include_text": False, "composition": "auto",
}
VALID_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


def _loads(value: str | None, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return copy.deepcopy(fallback)


def _question_draft(conn, question_id: int) -> dict:
    q = conn.execute("SELECT * FROM question WHERE id=?", (question_id,)).fetchone()
    if not q:
        raise HTTPException(404, "문항을 찾을 수 없습니다.")
    q = dict(q)
    choices = [dict(r) for r in conn.execute(
        "SELECT * FROM choice WHERE question_id=? ORDER BY ord", (question_id,))]
    for c in choices:
        c["is_answer"] = bool(c["is_answer"])
        c["combo"] = _loads(c.get("combo"), [])
    return {
        "title": q.get("title", ""), "qtype": q.get("qtype", "정답형"),
        "is_negative": bool(q.get("is_negative")), "passage": q.get("passage", ""),
        "material": q.get("material", ""), "ask": q.get("ask", ""),
        "bogi_items": _loads(q.get("bogi_items"), []), "choices": choices,
        "answer": q.get("answer", ""), "explanation": q.get("explanation", ""),
        "default_points": q.get("default_points", 3), "difficulty": q.get("difficulty", "중"),
        "standard_code": q.get("standard_code"), "intent": q.get("intent", ""),
        "behavior": q.get("behavior", ""), "origin": q.get("origin", ""),
        "origin_note": q.get("origin_note", ""), "image_choices": bool(q.get("image_choices")),
        "question_status": q.get("status", "초안"), "review_note": q.get("review_note", "{}"),
    }


def _empty_draft() -> dict:
    return {
        "title": "", "qtype": "정답형", "is_negative": False,
        "passage": "", "material": "", "ask": "", "bogi_items": [],
        "choices": [], "answer": "", "explanation": "", "default_points": 3,
        "difficulty": "중", "standard_code": None, "intent": "", "behavior": "",
        "origin": "", "origin_note": "", "image_choices": False,
        "question_status": "초안", "review_note": "{}", "figure_plan": None,
    }


def _text_content(draft: dict) -> dict:
    """확정 후 재검토를 일으키는 문항 본문 계열만 비교한다."""
    return {key: draft.get(key) for key in TEXT_FIELDS}


def _transition_after_change(current: dict, draft: dict) -> tuple[str, list[str]]:
    confirmed = current["confirmed"]
    if not confirmed or _text_content(draft) == _text_content(confirmed):
        return current["status"], current["review_flags"]
    flags = ["answer", "explanation"]
    if current["figure"].get("status") not in (None, "none"):
        flags.append("figure")
    return "text_drafting", flags


def _session(conn, sid: int) -> dict:
    row = conn.execute("SELECT * FROM authoring_session WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "작성 세션을 찾을 수 없습니다.")
    d = dict(row)
    d["draft"] = _loads(d.pop("draft_json"), {})
    d["confirmed"] = _loads(d.pop("confirmed_json"), {})
    d["review_flags"] = _loads(d["review_flags"], [])
    mode = d.get("authoring_mode") or "quick"
    preset = AUTHORING_MODES.get(mode, AUTHORING_MODES["quick"])
    d["authoring_mode"] = mode
    d["effective_model"] = d.get("model") or preset["model"]
    d["effective_reasoning_effort"] = d.get("reasoning_effort") or preset["reasoning_effort"]
    fig = conn.execute("SELECT * FROM authoring_figure WHERE session_id=?", (sid,)).fetchone()
    d["figure"] = dict(fig) if fig else {"provider": "stub", "status": "none"}
    options = _loads(d["figure"].pop("options_json", ""), FIGURE_OPTION_DEFAULTS)
    d["figure"]["options"] = {**FIGURE_OPTION_DEFAULTS, **options}
    d["figure"]["assets"] = [dict(row) for row in conn.execute(
        "SELECT * FROM authoring_figure_asset WHERE session_id=? ORDER BY ord,id", (sid,)
    )]
    material = str(d["draft"].get("material") or "").split(",", 1)[0].strip()
    material_path = hwppalette_provider.resolve_photo(material)
    d["figure"]["material_name"] = material
    d["figure"]["material_image_path"] = str(material_path) if material_path else ""
    return d


def _messages(conn, sid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM authoring_message WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["proposals"] = _loads(d.pop("proposals_json"), [])
        out.append(d)
    return out


def _figure_context(conn, current: dict, sid: int) -> dict:
    """Attach the stable pipeline filename without changing the stored figure schema."""
    context = dict(current["figure"])
    qid = current.get("question_id")
    row = None
    if qid:
        row = conn.execute(
            "SELECT s.short_code,i.ord FROM set_item i JOIN exam_set s ON s.id=i.set_id "
            "WHERE i.question_id=? AND COALESCE(s.short_code,'')<>'' ORDER BY i.id LIMIT 1",
            (qid,),
        ).fetchone()
    if row:
        context["short_code"] = row["short_code"]
        context["figure_name"] = f"{row['short_code']}_{row['ord']:02d}"
    else:
        context["short_code"] = ""
        context["figure_name"] = current["draft"].get("material") or f"draft_{sid}"
    return context


@router.get("/connection")
def connection(provider: str = "codex_local"):
    selected = get_provider(provider)
    state = selected.connection_state()
    state["authoring_modes"] = AUTHORING_MODES
    state["authoring_protocol"] = getattr(selected, "protocol_version", "")
    return state


@router.post("/login")
def login():
    try:
        return codex_app_server.start_login()
    except CodexAppServerError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/sessions/{sid}/provider")
def set_provider(sid: int, body: ProviderIn):
    if body.provider not in {"codex_local", "mock"}:
        raise HTTPException(400, "지원하지 않는 provider입니다.")
    with db.transaction() as conn:
        _session(conn, sid)
        conn.execute(
            "UPDATE authoring_session SET provider=?,provider_thread_id='',provider_protocol='',"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (body.provider, sid))
        return _session(conn, sid)


@router.patch("/sessions/{sid}/settings")
def update_settings(sid: int, body: SettingsIn):
    with db.transaction() as conn:
        current = _session(conn, sid)
        mode = body.authoring_mode or current["authoring_mode"]
        if mode not in AUTHORING_MODES:
            raise HTTPException(400, "지원하지 않는 작업 모드입니다.")
        preset = AUTHORING_MODES[mode]
        model = body.model if body.model is not None else (
            preset["model"] if body.authoring_mode is not None else current.get("model", ""))
        effort = body.reasoning_effort if body.reasoning_effort is not None else (
            preset["reasoning_effort"] if body.authoring_mode is not None else current.get("reasoning_effort", ""))
        model = model.strip()
        effort = effort.strip()
        if model and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
            raise HTTPException(400, "올바르지 않은 모델 ID입니다.")
        if effort and effort not in VALID_EFFORTS:
            raise HTTPException(400, "지원하지 않는 추론 강도입니다.")
        conn.execute(
            "UPDATE authoring_session SET authoring_mode=?,model=?,reasoning_effort=?,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (mode, model, effort, sid),
        )
        return _session(conn, sid)


@router.post("/sessions/{sid}/cancel")
def cancel_message(sid: int):
    conn = db.connect()
    try:
        current = _session(conn, sid)
    finally:
        conn.close()
    thread_id = current.get("provider_thread_id")
    if current["provider"] != "codex_local" or not thread_id:
        return {"cancelled": False, "message": "중단할 ChatGPT 응답이 없습니다."}
    try:
        cancelled = codex_app_server.interrupt_turn(thread_id)
    except CodexAppServerError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"cancelled": cancelled}


@router.post("/sessions")
def create_session(body: SessionIn):
    with db.transaction() as conn:
        if body.question_id is not None:
            old = conn.execute(
                "SELECT id FROM authoring_session WHERE question_id=? AND status<>'discarded' "
                "ORDER BY id DESC LIMIT 1", (body.question_id,)).fetchone()
            if old:
                current = _session(conn, old["id"])
                # A saved conversation is history; the question table is the
                # source of truth for later edits made from the Pool or another
                # integration. Refresh only completed sessions so an in-flight
                # conversational draft is never overwritten on reopen.
                if current["status"] == "saved":
                    live = _question_draft(conn, body.question_id)
                    payload = json.dumps(live, ensure_ascii=False)
                    conn.execute(
                        "UPDATE authoring_session SET draft_json=?,confirmed_json=?,"
                        "updated_at=datetime('now','localtime') WHERE id=?",
                        (payload, payload, old["id"]),
                    )
                return {"session": _session(conn, old["id"]), "messages": _messages(conn, old["id"])}
            draft = _question_draft(conn, body.question_id)
        else:
            draft = _empty_draft()
        cur = conn.execute(
            "INSERT INTO authoring_session(question_id,provider,draft_json) VALUES(?,?,?)",
            (body.question_id, body.provider, json.dumps(draft, ensure_ascii=False)))
        sid = cur.lastrowid
        conn.execute(
            "INSERT INTO authoring_figure(session_id,options_json) VALUES(?,?)",
            (sid, json.dumps(FIGURE_OPTION_DEFAULTS, ensure_ascii=False)),
        )
        return {"session": _session(conn, sid), "messages": []}


@router.get("/sessions/{sid}")
def get_session(sid: int):
    conn = db.connect()
    try:
        current = _session(conn, sid)
        if current["status"] == "discarded":
            raise HTTPException(410, "폐기된 작성 세션입니다.")
        return {"session": current, "messages": _messages(conn, sid)}
    finally:
        conn.close()


@router.post("/sessions/{sid}/discard")
def discard_session(sid: int):
    """Soft-discard an attempt while preserving source questions and recovery data."""
    with db.transaction() as conn:
        current = _session(conn, sid)
        thread_id = str(current.get("provider_thread_id") or "")
        if thread_id:
            try:
                codex_app_server.interrupt_turn(thread_id)
            except CodexAppServerError:
                pass
        conn.execute(
            "UPDATE authoring_session SET status='discarded',provider_thread_id='',"
            "source_question_id=COALESCE(source_question_id,question_id),question_id=NULL,"
            "updated_at=datetime('now','localtime') WHERE id=?", (sid,),
        )
        return {
            "discarded": True, "session_id": sid,
            "source_question_preserved": current.get("question_id") is not None,
            "message": "작성 세션을 폐기했습니다. 기존 문항과 폐기 기록은 삭제하지 않았습니다.",
        }


@router.patch("/sessions/{sid}/figure/options")
def update_figure_options(sid: int, body: FigureOptionsIn):
    if body.provider not in {"fivee_assets", "raster_image"}:
        raise HTTPException(400, "지원하지 않는 그림 생성 방식입니다.")
    if body.composition not in {"auto", "combined", "separate"}:
        raise HTTPException(400, "지원하지 않는 그림 구성 방식입니다.")
    options = {
        "provider": body.provider,
        "include_text": bool(body.include_text),
        "composition": body.composition,
    }
    with db.transaction() as conn:
        _session(conn, sid)
        conn.execute(
            "UPDATE authoring_figure SET options_json=?,updated_at=datetime('now','localtime') "
            "WHERE session_id=?", (json.dumps(options, ensure_ascii=False), sid)
        )
        return _session(conn, sid)


@router.get("/sessions/{sid}/figure/image")
def get_figure_image(sid: int):
    conn = db.connect()
    try:
        current = _session(conn, sid)
    finally:
        conn.close()
    path = Path(
        current["figure"].get("rendered_image_path")
        or current["figure"].get("material_image_path")
        or ""
    )
    media_types = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    media_type = media_types.get(path.suffix.lower())
    if not path.is_file() or not media_type:
        raise HTTPException(404, "연결된 문항 그림이 없습니다.")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/sessions/{sid}/figure/assets/{asset_id}/image")
def get_figure_asset_image(sid: int, asset_id: int):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM authoring_figure_asset WHERE id=? AND session_id=?", (asset_id, sid)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "그림 패널을 찾을 수 없습니다.")
    path = Path(dict(row).get("rendered_image_path") or "")
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    if not path.is_file() or path.suffix.lower() not in media_types:
        raise HTTPException(404, "생성된 패널 이미지가 없습니다.")
    return FileResponse(path, media_type=media_types[path.suffix.lower()], filename=path.name)


@router.post("/sessions/{sid}/figure/import-image")
def import_figure_image(sid: int, body: FigureImageImportIn):
    match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,(.+)$", body.data_url, re.S)
    if not match:
        raise HTTPException(400, "PNG, JPEG 또는 WebP 이미지만 가져올 수 있습니다.")
    try:
        payload = base64.b64decode(match.group(2), validate=True)
    except ValueError as exc:
        raise HTTPException(400, "이미지 데이터가 올바르지 않습니다.") from exc
    if not payload or len(payload) > 20 * 1024 * 1024:
        raise HTTPException(400, "이미지는 20MB 이하여야 합니다.")
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[match.group(1)]
    with db.transaction() as conn:
        current = _session(conn, sid)
        context = _figure_context(conn, current, sid)
        assets = current["figure"].get("assets") or []
        target_asset = next((a for a in assets if a.get("panel_id") == body.panel_id), None)
        ordinal = int((target_asset or {}).get("ord") or len(assets) + 1)
        base_name = context.get("figure_name") or f"draft_{sid}"
        name = base_name if len(assets) <= 1 else f"{base_name}_{ordinal:02d}"
        source_dir = data_dir() / "authoring_figures" / f"session_{sid}" / "raster"
        source_dir.mkdir(parents=True, exist_ok=True)
        source = source_dir / f"{body.panel_id}{ext}"
        source.write_bytes(payload)
        output_dir = hwppalette_provider.photo_dir(str(context.get("short_code") or ""))
        hwppalette_provider.register_photo_dir(output_dir)
        rendered = output_dir / f"{name}{ext}"
        rendered.write_bytes(payload)
        previous = ""
        old_rendered = Path(current["figure"].get("rendered_image_path") or "")
        if ordinal == 1 and old_rendered.is_file():
            previous_path = source_dir / f"previous{old_rendered.suffix.lower()}"
            previous_path.write_bytes(old_rendered.read_bytes())
            previous = str(previous_path)
        if target_asset:
            conn.execute(
                "UPDATE authoring_figure_asset SET provider='raster_image',status='draft',"
                "source_image_path=?,rendered_image_path=?,revision=revision+1,"
                "updated_at=datetime('now','localtime') WHERE id=?",
                (str(source), str(rendered), target_asset["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO authoring_figure_asset(session_id,panel_id,ord,provider,status,"
                "source_image_path,rendered_image_path,revision) VALUES(?,?,?,?,?,?,?,1)",
                (sid, body.panel_id, ordinal, "raster_image", "draft", str(source), str(rendered)),
            )
        if ordinal == 1:
            conn.execute(
                "UPDATE authoring_figure SET provider='raster_image',status='draft',rendered_image_path=?,"
                "previous_image_path=?,revision=revision+1,updated_at=datetime('now','localtime') WHERE session_id=?",
                (str(rendered), previous, sid),
            )
        return _session(conn, sid)


@router.patch("/sessions/{sid}/draft")
def update_draft(sid: int, body: DraftIn):
    with db.transaction() as conn:
        current = _session(conn, sid)
        status, flags = _transition_after_change(current, body.draft)
        conn.execute(
            "UPDATE authoring_session SET draft_json=?,status=?,review_flags=?,revision=revision+1," 
            "updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(body.draft, ensure_ascii=False), status,
             json.dumps(flags, ensure_ascii=False), sid))
        return _session(conn, sid)


@router.post("/sessions/{sid}/confirm-text")
def confirm_text(sid: int):
    with db.transaction() as conn:
        current = _session(conn, sid)
        conn.execute(
            "UPDATE authoring_session SET status='text_confirmed',confirmed_json=draft_json," 
            "review_flags='[]',updated_at=datetime('now','localtime') WHERE id=?", (sid,))
        return _session(conn, sid)


@router.post("/sessions/{sid}/unconfirm-text")
def unconfirm_text(sid: int):
    with db.transaction() as conn:
        _session(conn, sid)
        conn.execute(
            "UPDATE authoring_session SET status='text_drafting',updated_at=datetime('now','localtime') WHERE id=?",
            (sid,))
        return _session(conn, sid)


@router.post("/sessions/{sid}/messages")
def send_message(sid: int, body: MessageIn):
    if not body.content.strip():
        raise HTTPException(400, "메시지가 비어 있습니다.")
    with db.transaction() as conn:
        current = _session(conn, sid)
        conn.execute("INSERT INTO authoring_message(session_id,role,content) VALUES(?,?,?)",
                     (sid, "user", body.content.strip()))

    provider = get_provider(current["provider"])
    protocol_version = getattr(provider, "protocol_version", "")
    provider_thread_id = current.get("provider_thread_id")
    if current.get("provider_protocol", "") != protocol_version:
        # App Server의 developerInstructions는 스레드를 처음 만들 때 고정된다.
        # 기능 명세가 바뀐 스레드를 재개하면 새 필드(예: figure_plan)를 모른다.
        provider_thread_id = None
    def events():
        try:
            yield "event: status\ndata: " + json.dumps({
                "stage": "accepted", "label": "요청을 전송했습니다"
            }, ensure_ascii=False) + "\n\n"
            prompt_draft = copy.deepcopy(current["draft"])
            prompt_draft["_figure_options"] = current.get("figure", {}).get("options", FIGURE_OPTION_DEFAULTS)
            provider_thread_id2, provider_events = provider.stream(
                body.content, prompt_draft, provider_thread_id,
                model=current.get("effective_model"),
                reasoning_effort=current.get("effective_reasoning_effort"))
            if provider_thread_id2 and (
                provider_thread_id2 != current.get("provider_thread_id")
                or current.get("provider_protocol", "") != protocol_version
            ):
                with db.transaction() as conn_thread:
                    conn_thread.execute(
                        "UPDATE authoring_session SET provider_thread_id=?,provider_protocol=?,"
                        "updated_at=datetime('now','localtime') WHERE id=?",
                        (provider_thread_id2, protocol_version, sid))

            yield "event: status\ndata: " + json.dumps({
                "stage": "generating", "label": "문항을 검토하고 답변을 작성하고 있습니다"
            }, ensure_ascii=False) + "\n\n"
            event_queue: queue.Queue = queue.Queue()

            def consume_provider() -> None:
                try:
                    for item in provider_events:
                        event_queue.put(item)
                except Exception as exc:  # forwarded to the SSE consumer
                    event_queue.put(("error", exc))
                finally:
                    event_queue.put(("eof", None))

            threading.Thread(target=consume_provider, daemon=True).start()
            while True:
                try:
                    kind, value = event_queue.get(timeout=1.5)
                except queue.Empty:
                    yield "event: heartbeat\ndata: {}\n\n"
                    continue
                if kind == "eof":
                    break
                if kind == "error":
                    raise value
                if kind == "delta":
                    yield "event: chunk\ndata: " + json.dumps({"delta": value}, ensure_ascii=False) + "\n\n"
                    continue
                if kind != "done":
                    continue
                reply = value
                with db.transaction() as conn2:
                    cur = conn2.execute(
                        "INSERT INTO authoring_message(session_id,role,content,proposals_json) VALUES(?,?,?,?)",
                        (sid, "assistant", reply.content, json.dumps(reply.proposals, ensure_ascii=False)))
                    message_id = cur.lastrowid
                yield "event: done\ndata: " + json.dumps({
                    "message": {"id": message_id, "role": "assistant", "content": reply.content,
                                "proposals": reply.proposals}
                }, ensure_ascii=False) + "\n\n"
        except Exception as exc:
            yield "event: error\ndata: " + json.dumps({"message": str(exc)}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@router.post("/sessions/{sid}/apply")
def apply_proposal(sid: int, body: ApplyIn):
    with db.transaction() as conn:
        current = _session(conn, sid)
        row = conn.execute(
            "SELECT proposals_json FROM authoring_message WHERE id=? AND session_id=? AND role='assistant'",
            (body.message_id, sid)).fetchone()
        if not row:
            raise HTTPException(404, "제안 메시지를 찾을 수 없습니다.")
        proposal = next((p for p in _loads(row["proposals_json"], [])
                         if p.get("id") == body.proposal_id), None)
        if not proposal:
            raise HTTPException(404, "제안을 찾을 수 없습니다.")
        field = proposal.get("field")
        if field not in APPLY_FIELDS:
            raise HTTPException(400, "반영할 수 없는 필드입니다.")
        before = current["draft"]
        after = copy.deepcopy(before)
        after[field] = proposal.get("value")
        conn.execute(
            "INSERT INTO authoring_revision(session_id,message_id,before_json,after_json) VALUES(?,?,?,?)",
            (sid, body.message_id, json.dumps(before, ensure_ascii=False),
             json.dumps(after, ensure_ascii=False)))
        status, flags = _transition_after_change(current, after)
        conn.execute(
            "UPDATE authoring_session SET draft_json=?,status=?,review_flags=?,revision=revision+1," 
            "updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(after, ensure_ascii=False), status,
             json.dumps(flags, ensure_ascii=False), sid))
        return _session(conn, sid)


@router.post("/sessions/{sid}/undo")
def undo_apply(sid: int):
    with db.transaction() as conn:
        _session(conn, sid)
        rev = conn.execute(
            "SELECT * FROM authoring_revision WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
        if not rev:
            raise HTTPException(409, "되돌릴 반영 내역이 없습니다.")
        before = _loads(rev["before_json"], {})
        current = _session(conn, sid)
        if current["confirmed"] and _text_content(before) == _text_content(current["confirmed"]):
            status, flags = "text_confirmed", []
        else:
            status, flags = _transition_after_change(current, before)
        conn.execute(
            "UPDATE authoring_session SET draft_json=?,status=?,review_flags=?,revision=revision+1," 
            "updated_at=datetime('now','localtime') WHERE id=?",
            (rev["before_json"], status, json.dumps(flags, ensure_ascii=False), sid))
        conn.execute("DELETE FROM authoring_revision WHERE id=?", (rev["id"],))
        return _session(conn, sid)


@router.post("/sessions/{sid}/bind")
def bind_question(sid: int, body: BindIn):
    with db.transaction() as conn:
        _session(conn, sid)
        if not conn.execute("SELECT 1 FROM question WHERE id=?", (body.question_id,)).fetchone():
            raise HTTPException(404, "문항을 찾을 수 없습니다.")
        conn.execute(
            "UPDATE authoring_session SET question_id=?,status='saved',"
            "updated_at=datetime('now','localtime') WHERE id=?", (body.question_id, sid))
        return _session(conn, sid)


@router.post("/sessions/{sid}/figure/{action}")
def figure_action(sid: int, action: str):
    if action not in {"create", "edit", "activate", "sync", "revert", "confirm"}:
        raise HTTPException(400, "지원하지 않는 그림 작업입니다.")
    with db.transaction() as conn:
        current = _session(conn, sid)
        if current["status"] == "text_drafting":
            raise HTTPException(409, "텍스트를 먼저 확정하세요.")
        provider_name = current["figure"].get("provider") or "stub"
        # 기존 stub 세션도 실제 그림 작업을 처음 시작할 때만 외부 5E adapter로 승격한다.
        if action == "create":
            requested = current["figure"].get("options", {}).get("provider", "fivee_assets")
            provider_name = "raster_image" if requested == "raster_image" else "fivee_local"
        figure_provider = get_figure_provider(provider_name)
        try:
            figure_context = _figure_context(conn, current, sid)
            figure = getattr(figure_provider, action)(sid, current["draft"], figure_context)
        except FigureProviderError as exc:
            raise HTTPException(503, str(exc)) from exc
        fig_status = figure["status"]
        authoring_status = "figure_confirmed" if action == "confirm" else "figure_drafting"
        previous_image_path = figure.get(
            "previous_image_path", current["figure"].get("previous_image_path", "")
        )
        revision_delta = 1 if action in {"create", "sync", "revert"} else 0
        conn.execute(
            "UPDATE authoring_figure SET provider=?,scene_spec_path=?,fivee_project_path=?,"
            "rendered_image_path=?,status=?,previous_image_path=?,revision=revision+?,"
            "updated_at=datetime('now','localtime') WHERE session_id=?",
            (figure.get("provider", figure_provider.name), figure.get("scene_spec_path", ""),
             figure.get("fivee_project_path", ""), figure.get("rendered_image_path", ""),
             fig_status, previous_image_path, revision_delta, sid))
        if "assets" in figure:
            conn.execute("DELETE FROM authoring_figure_asset WHERE session_id=?", (sid,))
            for asset in figure["assets"]:
                conn.execute(
                    "INSERT INTO authoring_figure_asset(session_id,panel_id,ord,provider,prompt,status,"
                    "scene_spec_path,fivee_project_path,source_image_path,rendered_image_path,revision) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, asset.get("panel_id", "main"), asset.get("ord", 1),
                     asset.get("provider", provider_name), asset.get("image_prompt", ""),
                     asset.get("status", fig_status),
                     asset.get("scene_spec_path", ""), asset.get("fivee_project_path", ""),
                     asset.get("source_image_path", ""), asset.get("rendered_image_path", ""), 1),
                )
        elif action in {"sync", "revert"}:
            conn.execute(
                "UPDATE authoring_figure_asset SET status=?,scene_spec_path=?,fivee_project_path=?,"
                "rendered_image_path=?,revision=revision+1,updated_at=datetime('now','localtime') "
                "WHERE id=(SELECT id FROM authoring_figure_asset WHERE session_id=? ORDER BY ord,id LIMIT 1)",
                (fig_status, figure.get("scene_spec_path", ""), figure.get("fivee_project_path", ""),
                 figure.get("rendered_image_path", ""), sid),
            )
        conn.execute(
            "UPDATE authoring_session SET status=?,updated_at=datetime('now','localtime') WHERE id=?",
            (authoring_status, sid))
        if action == "confirm" and figure.get("material"):
            draft = copy.deepcopy(current["draft"])
            draft["material"] = figure["material"]
            confirmed = copy.deepcopy(current["confirmed"])
            if confirmed:
                confirmed["material"] = figure["material"]
            conn.execute(
                "UPDATE authoring_session SET draft_json=?,confirmed_json=? WHERE id=?",
                (json.dumps(draft, ensure_ascii=False),
                 json.dumps(confirmed, ensure_ascii=False), sid),
            )
            if current.get("question_id"):
                conn.execute("UPDATE question SET material=? WHERE id=?",
                             (figure["material"], current["question_id"]))
        result = _session(conn, sid)
        # 실행 URL과 안내는 영속 데이터가 아니며, 인증 정보도 포함하지 않는다.
        for key in ("launch_url", "instructions"):
            if figure.get(key):
                result["figure"][key] = figure[key]
        return result


@router.post("/sessions/{sid}/figure/assets/{asset_id}/{action}")
def figure_asset_action(sid: int, asset_id: int, action: str):
    if action not in {"edit", "activate", "sync", "revert"}:
        raise HTTPException(400, "지원하지 않는 패널 그림 작업입니다.")
    with db.transaction() as conn:
        current = _session(conn, sid)
        row = conn.execute(
            "SELECT * FROM authoring_figure_asset WHERE id=? AND session_id=?", (asset_id, sid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "그림 패널을 찾을 수 없습니다.")
        asset = dict(row)
        if asset["provider"] not in {"fivee_assets", "raster_image"}:
            raise HTTPException(409, "5E 프로젝트가 있는 그림만 5E에서 편집할 수 있습니다.")
        context = {
            **_figure_context(conn, current, sid), **asset,
            "provider": "fivee_local",
            "figure_name": Path(asset.get("rendered_image_path") or f"panel_{asset['ord']}").stem,
            "previous_image_path": str(
                Path(asset.get("fivee_project_path") or "").with_name("figure.previous.png")
            ),
        }
        provider = get_figure_provider("fivee_local")
        try:
            figure = getattr(provider, action)(sid, current["draft"], context)
        except FigureProviderError as exc:
            raise HTTPException(503, str(exc)) from exc
        conn.execute(
            "UPDATE authoring_figure_asset SET status=?,scene_spec_path=?,fivee_project_path=?,"
            "rendered_image_path=?,revision=revision+?,updated_at=datetime('now','localtime') WHERE id=?",
            (figure.get("status", asset["status"]), figure.get("scene_spec_path", asset["scene_spec_path"]),
             figure.get("fivee_project_path", asset["fivee_project_path"]),
             figure.get("rendered_image_path", asset["rendered_image_path"]),
             1 if action in {"sync", "revert"} else 0, asset_id),
        )
        if asset["ord"] == 1:
            conn.execute(
                "UPDATE authoring_figure SET status=?,scene_spec_path=?,fivee_project_path=?,"
                "rendered_image_path=?,previous_image_path=?,revision=revision+?,"
                "updated_at=datetime('now','localtime') WHERE session_id=?",
                (figure.get("status", asset["status"]), figure.get("scene_spec_path", asset["scene_spec_path"]),
                 figure.get("fivee_project_path", asset["fivee_project_path"]),
                 figure.get("rendered_image_path", asset["rendered_image_path"]),
                 figure.get("previous_image_path", current["figure"].get("previous_image_path", "")),
                 1 if action in {"sync", "revert"} else 0, sid),
            )
        result = _session(conn, sid)
        for key in ("launch_url", "instructions"):
            if figure.get(key):
                result["figure"][key] = figure[key]
        return result
