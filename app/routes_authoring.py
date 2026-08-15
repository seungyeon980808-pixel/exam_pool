"""대화형 문항 제작 세션·메시지·선택 반영 API."""
from __future__ import annotations

import copy
import base64
import hashlib
import json
from pathlib import Path
import queue
import re
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from . import db, pdf_indexer
from .authoring.codex_app_server import CodexAppServerError, codex_app_server
from .authoring.figures import FigureProviderError, get_figure_provider, required_figure_count
from .authoring.providers import get_provider
from .authoring.item_rules import enrich_style_metadata, validate_draft, validate_evidence_links
from .authoring.reference_context import build_reference_bundle
from .authoring.source_crop import SourceCropMetadata, parse_source_crop
from .formula_markup import normalize_reconstruction_draft_formulas
from .integrations.hwppalette import hwppalette_provider
from .paths import data_dir

router = APIRouter(prefix="/api/authoring")

VALID_STATUSES = {
    "text_drafting", "text_confirmed", "figure_drafting",
    "figure_confirmed", "reviewing", "saved", "discarded",
}
APPLY_FIELDS = {
    "title", "qtype", "standard_code", "difficulty", "default_points", "intent",
    "passage", "ask", "bogi_items", "choices", "answer", "explanation", "figure_plan",
}
TEXT_FIELDS = ("passage", "ask", "bogi_items", "choices", "answer", "explanation")


class SessionIn(BaseModel):
    question_id: int | None = None
    provider: str = "codex_local"


class ProviderIn(BaseModel):
    provider: str = "codex_local"


class SettingsIn(BaseModel):
    authoring_mode: str | None = None
    workflow_mode: str | None = None
    purpose_mode: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None


class DraftIn(BaseModel):
    draft: dict


class MessageIn(BaseModel):
    content: str


class ApplyIn(BaseModel):
    message_id: int
    proposal_id: str


class ApplyAllIn(BaseModel):
    message_id: int


class BindIn(BaseModel):
    question_id: int


class FigureOptionsIn(BaseModel):
    provider: str = "fivee_assets"
    include_text: bool = False
    composition: str = "auto"


def _normalize_visible_text_fields(draft: dict | None) -> dict:
    """Keep proposal metadata envelopes out of human-visible text fields."""
    value = dict(draft or {})
    for field in ("passage", "ask"):
        raw = value.get(field)
        if isinstance(raw, dict):
            value[field] = str(raw.get("text") or "")
        elif isinstance(raw, str) and raw.strip().lower() == "[object object]":
            value[field] = ""
        elif raw is not None and not isinstance(raw, str):
            value[field] = ""
    return value


class FigureImageImportIn(BaseModel):
    panel_id: str = "main"
    filename: str = "figure.png"
    data_url: str


class FigureReferenceIn(BaseModel):
    filename: str = "reference.png"
    data_url: str
    source_label: str = ""
    source_text: str = ""
    usage: str = "both"
    source_meta: dict = {}


class FigureReferenceUsageIn(BaseModel):
    usage: str = "both"


class AutoReferencesIn(BaseModel):
    query: str = ""


AUTHORING_MODES = {
    "quick": {"label": "빠르게 작성", "model": "gpt-5.6-luna", "reasoning_effort": "medium"},
    "precise": {"label": "정밀하게 수정", "model": "gpt-5.6-terra", "reasoning_effort": "medium"},
    "final": {"label": "최종 검수", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
}
WORKFLOW_MODES = {
    "auto": {"label": "알아서 완성", "description": "조건이 부족하면 합리적으로 보완해 완성안을 제안합니다."},
    "dialogue": {"label": "대화로 설계", "description": "중요한 조건을 먼저 확인한 뒤 제안합니다."},
}
PURPOSE_MODES = {
    "create": {"label": "새 문항 출제", "description": "기출은 근거와 형식만 참고하고 새 문항을 만듭니다."},
    "reconstruct": {"label": "기출 원본 복원", "description": "선택한 기출 한 문항의 원문·순서·구도를 충실히 복원합니다."},
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
    return enrich_style_metadata({
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
        "style_meta": _loads(q.get("style_meta"), {}),
    })


def _empty_draft() -> dict:
    return {
        "title": "", "qtype": "정답형", "is_negative": False,
        "passage": "", "material": "", "ask": "", "bogi_items": [],
        "choices": [], "answer": "", "explanation": "", "default_points": 3,
        "difficulty": "중", "standard_code": None, "intent": "", "behavior": "",
        "origin": "", "origin_note": "", "image_choices": False,
        "question_status": "초안", "review_note": "{}", "figure_plan": None,
        "style_meta": {},
    }


def _auto_title(draft: dict, sid: int | None = None) -> str:
    """Give every working tab a stable, human-recognisable topic name."""
    explicit = str(draft.get("title") or "").strip()
    if explicit and not re.fullmatch(r"문항\s*\d+", explicit):
        return explicit[:28]
    seeds = [draft.get("intent"), draft.get("passage"), draft.get("ask")]
    for seed in seeds:
        text = re.sub(r"\s+", " ", str(seed or "")).strip()
        text = re.sub(r"^(다음은|그림(?:\s*\([가-힣]\))?(?:와|과)?|이에 대한 설명으로)\s*", "", text)
        text = re.sub(r"[.?!].*$", "", text).strip(" .,:;()[]〈〉<>")
        if len(text) >= 2:
            return text[:24] + ("…" if len(text) > 24 else "")
    code = str(draft.get("standard_code") or "").strip()
    return f"{code} 문항" if code else (f"문항 {sid}" if sid else "새 문항")


def _request_title(content: str, sid: int) -> str:
    text = re.sub(r"\s+", " ", content).strip()
    text = re.sub(r"(?:관련|에 대한)?\s*(?:문항|문제)(?:을|를)?\s*(?:여러\s*개|\d+\s*개)?\s*(?:만들어|제작|출제|설계).*$", "", text)
    text = re.sub(r"(?:해|해줘|해주세요|해봐)$", "", text).strip(" .,:;?!")
    return (text[:24] + ("…" if len(text) > 24 else "")) if len(text) >= 2 else f"문항 {sid}"


def _standard_candidates(conn, context: str, limit: int = 5) -> list[dict]:
    """Rank standards from local curriculum and proposition text without requiring user selection."""
    terms = {
        word for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", context)
        if word not in {"문항", "문제", "관련", "대한", "설명", "만들어", "만들어줘", "사진", "그림"}
    }
    if not terms:
        return []
    rows = conn.execute(
        "SELECT s.code,s.text,COALESCE(group_concat(p.text,' '),'') proposition_text "
        "FROM standard s LEFT JOIN proposition p ON p.standard_code=s.code GROUP BY s.code,s.text"
    ).fetchall()
    ranked = []
    for row in rows:
        standard_text = str(row["text"] or "")
        proposition_text = str(row["proposition_text"] or "")
        score = sum((2 if term in standard_text else 0) + (3 if term in proposition_text else 0) for term in terms)
        if score:
            ranked.append({"code": row["code"], "text": standard_text, "score": score})
    return sorted(ranked, key=lambda item: (-item["score"], item["code"]))[:limit]


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
    d["draft"] = _normalize_visible_text_fields(_loads(d.pop("draft_json"), {}))
    d["confirmed"] = _normalize_visible_text_fields(_loads(d.pop("confirmed_json"), {}))
    d["review_flags"] = _loads(d["review_flags"], [])
    mode = d.get("authoring_mode") or "quick"
    preset = AUTHORING_MODES.get(mode, AUTHORING_MODES["quick"])
    d["authoring_mode"] = mode
    d["workflow_mode"] = d.get("workflow_mode") if d.get("workflow_mode") in WORKFLOW_MODES else "auto"
    d["purpose_mode"] = d.get("purpose_mode") if d.get("purpose_mode") in PURPOSE_MODES else "create"
    d["effective_model"] = d.get("model") or preset["model"]
    d["effective_reasoning_effort"] = d.get("reasoning_effort") or preset["reasoning_effort"]
    fig = conn.execute("SELECT * FROM authoring_figure WHERE session_id=?", (sid,)).fetchone()
    d["figure"] = dict(fig) if fig else {"provider": "stub", "status": "none"}
    options = _loads(d["figure"].pop("options_json", ""), FIGURE_OPTION_DEFAULTS)
    d["figure"]["options"] = {**FIGURE_OPTION_DEFAULTS, **options}
    assets = []
    for row in conn.execute(
        "SELECT * FROM authoring_figure_asset WHERE session_id=? ORDER BY ord,id", (sid,)
    ):
        asset = dict(row)
        asset["bbox"] = _loads(asset.pop("bbox_json", "[]"), [])
        assets.append(asset)
    d["figure"]["assets"] = assets
    d["figure"]["references"] = [dict(row) for row in conn.execute(
        "SELECT * FROM authoring_figure_reference WHERE session_id=? ORDER BY id", (sid,)
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
        # Unsaved sessions own one stable namespace. Reusing the current material
        # list here makes every regeneration append suffixes to old asset names
        # (for example ``draft_7_01,draft_7_02_01``).
        context["figure_name"] = f"draft_{sid}"
    return context


def _route_source_crop(
    conn, current: dict, reference: dict, source_crop: SourceCropMetadata
) -> None:
    """Materialize one immutable source crop as the editor and print asset."""
    sid = int(current["id"])
    source_path = Path(reference["image_path"])
    context = _figure_context(conn, current, sid)
    output_dir = hwppalette_provider.photo_dir(str(context.get("short_code") or ""))
    output_dir.mkdir(parents=True, exist_ok=True)
    hwppalette_provider.register_photo_dir(output_dir)
    rendered = output_dir / f"{context['figure_name']}{source_path.suffix.lower()}"
    rendered.write_bytes(source_path.read_bytes())
    conn.execute("DELETE FROM authoring_figure_asset WHERE session_id=?", (sid,))
    conn.execute(
        "INSERT INTO authoring_figure_asset("
        "session_id,panel_id,ord,provider,status,source_image_path,rendered_image_path,"
        "asset_mode,asset_role,source_pdf,page_no,bbox_json,dpi,width_px,height_px,"
        "aspect_ratio,source_hash,revision) VALUES(?, 'main', 1, 'source_crop_hd', "
        "'confirmed', ?, ?, 'source_crop_hd', 'original_source', ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            sid, str(source_path), str(rendered), source_crop.source_pdf, source_crop.page_no,
            json.dumps(list(source_crop.bbox)), source_crop.dpi, source_crop.width_px,
            source_crop.height_px, source_crop.aspect_ratio, source_crop.source_hash,
        ),
    )
    options = {"provider": "source_crop_hd", "include_text": False, "composition": "combined"}
    conn.execute(
        "UPDATE authoring_figure SET provider='source_crop_hd',status='confirmed',"
        "scene_spec_path='',fivee_project_path='',rendered_image_path=?,options_json=?,"
        "revision=revision+1,updated_at=datetime('now','localtime') WHERE session_id=?",
        (str(rendered), json.dumps(options, ensure_ascii=False), sid),
    )
    draft = copy.deepcopy(current["draft"])
    draft["material"] = rendered.stem
    confirmed = copy.deepcopy(current["confirmed"])
    if confirmed:
        confirmed["material"] = rendered.stem
    conn.execute(
        "UPDATE authoring_session SET status='figure_confirmed',draft_json=?,confirmed_json=?,"
        "updated_at=datetime('now','localtime') WHERE id=?",
        (json.dumps(draft, ensure_ascii=False), json.dumps(confirmed, ensure_ascii=False), sid),
    )
    if current.get("question_id"):
        conn.execute(
            "UPDATE question SET material=?,updated_at=datetime('now','localtime') WHERE id=?",
            (rendered.stem, current["question_id"]),
        )


@router.get("/connection")
def connection(provider: str = "codex_local"):
    selected = get_provider(provider)
    state = selected.connection_state()
    state["authoring_modes"] = AUTHORING_MODES
    state["workflow_modes"] = WORKFLOW_MODES
    state["purpose_modes"] = PURPOSE_MODES
    state["authoring_protocol"] = getattr(selected, "protocol_version", "")
    return state


@router.post("/connection/refresh")
def refresh_connection(provider: str = "codex_local"):
    if provider == "codex_local":
        try:
            codex_app_server.restart()
        except CodexAppServerError as exc:
            raise HTTPException(503, str(exc)) from exc
    return connection(provider)


@router.post("/login")
def login():
    try:
        # The long-lived child process may predate a successful Codex Desktop/CLI login.
        # Restart first and reuse that managed login instead of opening another OAuth flow.
        codex_app_server.restart()
        state = codex_app_server.account_state()
        if state.get("signed_in") or state.get("account"):
            return {"alreadySignedIn": True}
        return codex_app_server.start_login()
    except CodexAppServerError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/login/device")
def device_login():
    try:
        codex_app_server.restart()
        state = codex_app_server.account_state()
        if state.get("signed_in") or state.get("account"):
            return {"alreadySignedIn": True}
        return codex_app_server.start_device_login()
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
        workflow_mode = body.workflow_mode or current.get("workflow_mode") or "auto"
        if workflow_mode not in WORKFLOW_MODES:
            raise HTTPException(400, "지원하지 않는 제작 진행 방식입니다.")
        purpose_mode = body.purpose_mode or current.get("purpose_mode") or "create"
        if purpose_mode not in PURPOSE_MODES:
            raise HTTPException(400, "지원하지 않는 제작 목적입니다.")
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
            "UPDATE authoring_session SET authoring_mode=?,workflow_mode=?,purpose_mode=?,model=?,reasoning_effort=?,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (mode, workflow_mode, purpose_mode, model, effort, sid),
        )
        references = current.get("figure", {}).get("references", [])
        if (
            purpose_mode == "reconstruct"
            and current["figure"].get("provider") != "source_crop_hd"
            and len(references) == 1
        ):
            try:
                source_crop = parse_source_crop(_loads(references[0]["source_meta_json"], {}))
            except ValidationError as exc:
                raise HTTPException(422, "원본 크롭 메타데이터가 올바르지 않습니다.") from exc
            if source_crop:
                source_path = Path(references[0]["image_path"])
                expected_hash = source_crop.asset_hash or source_crop.source_hash
                if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_hash:
                    raise HTTPException(422, "원본 크롭 해시가 이미지 바이트와 일치하지 않습니다.")
                _route_source_crop(conn, current, references[0], source_crop)
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
        current = _session(conn, sid)
        if current["figure"].get("provider") == "source_crop_hd":
            raise HTTPException(409, "원본 고해상도 크롭은 생성 방식을 변경할 수 없습니다.")
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
        if current["figure"].get("provider") == "source_crop_hd":
            raise HTTPException(409, "원본 고해상도 크롭은 다른 이미지로 교체할 수 없습니다.")
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


@router.post("/sessions/{sid}/figure/references")
def add_figure_reference(sid: int, body: FigureReferenceIn):
    if body.usage not in {"content", "image", "both"}:
        raise HTTPException(400, "참고 자료 용도는 내용, 그림 또는 모두 중 하나여야 합니다.")
    match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,(.+)$", body.data_url, re.S)
    if not match:
        raise HTTPException(400, "PNG, JPEG 또는 WebP 이미지만 참고 이미지로 넣을 수 있습니다.")
    try:
        payload = base64.b64decode(match.group(2), validate=True)
    except ValueError as exc:
        raise HTTPException(400, "참고 이미지 데이터가 올바르지 않습니다.") from exc
    if not payload or len(payload) > 20 * 1024 * 1024:
        raise HTTPException(400, "참고 이미지는 파일당 20MB 이하여야 합니다.")
    try:
        source_crop = parse_source_crop(body.source_meta)
    except ValidationError as exc:
        raise HTTPException(422, "원본 크롭 메타데이터가 올바르지 않습니다.") from exc
    expected_hash = source_crop.asset_hash or source_crop.source_hash if source_crop else ""
    if source_crop and hashlib.sha256(payload).hexdigest() != expected_hash:
        raise HTTPException(422, "원본 크롭 해시가 이미지 바이트와 일치하지 않습니다.")
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[match.group(1)]
    with db.transaction() as conn:
        current = _session(conn, sid)
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM authoring_figure_reference WHERE session_id=?", (sid,)
        ).fetchone()["n"]
        if count >= 6:
            raise HTTPException(409, "참고 이미지는 문항당 최대 6개까지 넣을 수 있습니다.")
        folder = data_dir() / "authoring_figures" / f"session_{sid}" / "references"
        folder.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", Path(body.filename).stem)[:60] or "reference"
        path = folder / f"{count + 1:02d}_{safe_name}{ext}"
        path.write_bytes(payload)
        reference_id = conn.execute(
            "INSERT INTO authoring_figure_reference("
            "session_id,filename,image_path,source_label,source_text,usage,source_meta_json"
            ") VALUES(?,?,?,?,?,?,?)",
            (sid, Path(body.filename).name[:120], str(path), body.source_label.strip()[:240],
             body.source_text.strip()[:5000], body.usage,
             json.dumps(body.source_meta, ensure_ascii=False)),
        ).lastrowid
        if current.get("purpose_mode") == "reconstruct" and source_crop:
            reference = conn.execute(
                "SELECT * FROM authoring_figure_reference WHERE id=?", (reference_id,)
            ).fetchone()
            _route_source_crop(conn, current, dict(reference), source_crop)
        return _session(conn, sid)


@router.post("/sessions/{sid}/figure/references/auto")
def auto_figure_references(sid: int, body: AutoReferencesIn):
    """Pick up to three distinct textbook/past-exam pages for content and composition reference."""
    conn = db.connect()
    try:
        current = _session(conn, sid)
        if len(current.get("figure", {}).get("references", [])) >= 3:
            return current
        last = conn.execute(
            "SELECT content FROM authoring_message WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
    finally:
        conn.close()
    source = " ".join(str(value or "") for value in (
        current["draft"].get("intent"), current["draft"].get("passage"),
        current["draft"].get("ask"), body.query, last["content"] if last else "",
    ))
    stop = {"문항", "문제", "만들어", "만들어줘", "대한", "설명", "옳은", "것은", "그림", "사진", "자료"}
    terms = [word for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", source) if word not in stop]
    candidates, seen = [], set()
    for term in terms[:10]:
        for doc_type in ("기출", "교과서", "교육과정"):
            try:
                rows = pdf_indexer.search(term, limit=5, doc_type=doc_type).get("items", [])
            except Exception:
                rows = []
            for row in rows:
                key = (row.get("document_id"), row.get("page_no"))
                if key in seen or int(row.get("document_id") or 0) <= 0:
                    continue
                seen.add(key); candidates.append(row)
                if len(candidates) >= 3:
                    break
            if len(candidates) >= 3:
                break
        if len(candidates) >= 3:
            break
    if not candidates:
        raise HTTPException(409, "현재 문항 주제로 검색되는 기출·교과서 페이지가 없습니다. 출제 의도에 핵심 개념을 한두 단어로 적어 주세요.")
    folder = data_dir() / "authoring_figures" / f"session_{sid}" / "references"
    folder.mkdir(parents=True, exist_ok=True)
    with db.transaction() as conn2:
        for index, row in enumerate(candidates, 1):
            try:
                payload = pdf_indexer.render_page_png(int(row["document_id"]), int(row["page_no"]), dpi=110)
            except Exception:
                continue
            path = folder / f"auto_{int(row['document_id'])}_{int(row['page_no'])}.png"
            path.write_bytes(payload)
            clean_snippet = re.sub(r"[\[\]]", "", str(row.get("snippet") or ""))
            conn2.execute(
                "INSERT INTO authoring_figure_reference(session_id,filename,image_path,source_label,source_text,usage,source_meta_json) "
                "VALUES(?,?,?,?,?,'both',?)",
                (sid, path.name, str(path), f"{row.get('doc_title','자료')} {row.get('page_no')}쪽", clean_snippet,
                 json.dumps({"automatic": True, "document_id": row.get("document_id"), "page_no": row.get("page_no")}, ensure_ascii=False)),
            )
        return _session(conn2, sid)


@router.patch("/sessions/{sid}/figure/references/{reference_id}")
def update_figure_reference(sid: int, reference_id: int, body: FigureReferenceUsageIn):
    if body.usage not in {"content", "image", "both"}:
        raise HTTPException(400, "참고 자료 용도는 내용, 그림 또는 모두 중 하나여야 합니다.")
    with db.transaction() as conn:
        _session(conn, sid)
        exists = conn.execute(
            "SELECT 1 FROM authoring_figure_reference WHERE id=? AND session_id=?",
            (reference_id, sid),
        ).fetchone()
        if not exists:
            raise HTTPException(404, "참고 자료를 찾을 수 없습니다.")
        conn.execute(
            "UPDATE authoring_figure_reference SET usage=? WHERE id=?", (body.usage, reference_id)
        )
        return _session(conn, sid)


@router.get("/sessions/{sid}/figure/references/{reference_id}/image")
def get_figure_reference(sid: int, reference_id: int):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM authoring_figure_reference WHERE id=? AND session_id=?",
            (reference_id, sid),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "참고 이미지를 찾을 수 없습니다.")
    path = Path(row["image_path"])
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    if not path.is_file() or path.suffix.lower() not in media_types:
        raise HTTPException(404, "참고 이미지 파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type=media_types[path.suffix.lower()], filename=path.name)


@router.delete("/sessions/{sid}/figure/references/{reference_id}")
def delete_figure_reference(sid: int, reference_id: int):
    with db.transaction() as conn:
        current = _session(conn, sid)
        if current["figure"].get("provider") == "source_crop_hd":
            raise HTTPException(409, "사용 중인 원본 고해상도 크롭은 삭제할 수 없습니다.")
        row = conn.execute(
            "SELECT image_path FROM authoring_figure_reference WHERE id=? AND session_id=?",
            (reference_id, sid),
        ).fetchone()
        if not row:
            raise HTTPException(404, "참고 이미지를 찾을 수 없습니다.")
        conn.execute("DELETE FROM authoring_figure_reference WHERE id=?", (reference_id,))
    path = Path(row["image_path"])
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
    return {"ok": True}


@router.patch("/sessions/{sid}/draft")
def update_draft(sid: int, body: DraftIn):
    with db.transaction() as conn:
        current = _session(conn, sid)
        draft = enrich_style_metadata(_normalize_visible_text_fields(copy.deepcopy(body.draft)))
        draft["title"] = _auto_title(draft, sid)
        required_count = required_figure_count(draft)
        if required_count > 1:
            options = dict(current.get("figure", {}).get("options") or FIGURE_OPTION_DEFAULTS)
            if options.get("composition") != "separate":
                options["composition"] = "separate"
                conn.execute(
                    "UPDATE authoring_figure SET options_json=?,updated_at=datetime('now','localtime') "
                    "WHERE session_id=?", (json.dumps(options, ensure_ascii=False), sid),
                )
        status, flags = _transition_after_change(current, draft)
        conn.execute(
            "UPDATE authoring_session SET draft_json=?,status=?,review_flags=?,revision=revision+1," 
            "updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(draft, ensure_ascii=False), status,
             json.dumps(flags, ensure_ascii=False), sid))
        return _session(conn, sid)


@router.post("/sessions/{sid}/confirm-text")
def confirm_text(sid: int):
    with db.transaction() as conn:
        current = _session(conn, sid)
        draft = copy.deepcopy(current["draft"])
        if current.get("purpose_mode") == "reconstruct":
            draft = normalize_reconstruction_draft_formulas(draft)
        draft = enrich_style_metadata(draft)
        advisories = []
        if current.get("provider") != "mock":
            advisories = validate_draft(draft)
            if not str(draft.get("standard_code") or "").strip():
                advisories.append({"level": "warning", "code": "standard_missing",
                                   "message": "성취기준이 아직 배정되지 않았습니다. 자동 추천이나 직접 선택이 가능합니다.",
                                   "field": "standard_code"})
            all_refs = current.get("figure", {}).get("references", [])
            content_refs = (all_refs if current.get("purpose_mode") == "reconstruct" else
                            [row for row in all_refs if row.get("usage") in {"content", "both"}])
            if not content_refs:
                advisories.append({"level": "warning", "code": "reference_missing",
                                   "message": "연결된 내용 근거가 없습니다. 생성은 계속할 수 있고 최종 검토 전에 보완할 수 있습니다.",
                                   "field": "references"})
            elif current.get("purpose_mode") != "reconstruct":
                advisories.extend(validate_evidence_links(
                    draft, {int(row["id"]) for row in content_refs if row.get("id") is not None}
                ))
            blocking = [item for item in advisories if item.get("code") in {"no_ask", "formula_invalid"}]
            if blocking:
                detail = {"message": "출력에 꼭 필요한 항목을 확인해 주세요.", "issues": blocking}
                raise HTTPException(409, detail)
        conn.execute(
            "UPDATE authoring_session SET status='text_confirmed',draft_json=?,confirmed_json=?,"
            "review_flags='[]',updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(draft, ensure_ascii=False), json.dumps(draft, ensure_ascii=False), sid))
        result = _session(conn, sid)
        result["advisories"] = [{**item, "level": "warning"} for item in advisories]
        return result


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
        reconstruction_reference = None
        if current.get("purpose_mode") == "reconstruct":
            references = current.get("figure", {}).get("references", [])
            if len(references) != 1:
                raise HTTPException(
                    409,
                    "기출 원본 복원은 왼쪽에서 복원할 기출 한 문항만 참고 자료로 연결해야 합니다.",
                )
            reconstruction_reference = references[0]
        candidates = _standard_candidates(conn, body.content.strip())
        inferred = copy.deepcopy(current["draft"])
        if reconstruction_reference is not None:
            style_meta = dict(inferred.get("style_meta") or {})
            style_meta["reconstruction"] = {
                "enabled": True,
                "reference_id": reconstruction_reference.get("id"),
                "source_label": reconstruction_reference.get("source_label", ""),
                "source_meta": _loads(reconstruction_reference.get("source_meta_json"), {}),
            }
            inferred["style_meta"] = style_meta
            inferred["origin"] = "기출복원"
            inferred["origin_note"] = reconstruction_reference.get("source_label", "")
        if current.get("workflow_mode") == "auto":
            if not str(inferred.get("standard_code") or "").strip() and candidates:
                inferred["standard_code"] = candidates[0]["code"]
            if "합답" in body.content or re.search(r"(?:ㄱ|ㄴ|ㄷ).*(?:보기|선지)", body.content):
                inferred["qtype"] = "합답형"
            elif "서술" in body.content:
                inferred["qtype"] = "서술형"
            elif not inferred.get("qtype"):
                inferred["qtype"] = "정답형"
        if re.fullmatch(r"(?:새 문항|문항\s*\d+)?", str(current["draft"].get("title") or "").strip()):
            inferred["title"] = _request_title(body.content, sid)
        if inferred != current["draft"]:
            conn.execute(
                "UPDATE authoring_session SET draft_json=?,updated_at=datetime('now','localtime') WHERE id=?",
                (json.dumps(inferred, ensure_ascii=False), sid),
            )
            current["draft"] = inferred
        current["draft"]["_standard_candidates"] = candidates
        reference_bundle = build_reference_bundle(
            conn, current["draft"], current.get("figure", {}).get("references", []),
            body.content.strip(), current.get("purpose_mode", "create"),
        )
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
            figure_options = dict(current.get("figure", {}).get("options", FIGURE_OPTION_DEFAULTS))
            figure_count = required_figure_count(prompt_draft)
            if figure_count > 1:
                figure_options["composition"] = "separate"
            prompt_draft["_figure_options"] = figure_options
            prompt_draft["_required_figure_count"] = figure_count
            prompt_draft["_workflow_mode"] = current.get("workflow_mode", "auto")
            prompt_draft["_purpose_mode"] = current.get("purpose_mode", "create")
            prompt_draft["_references"] = [
                {
                    "reference_id": row.get("id"),
                    "source_label": row.get("source_label", ""),
                    "source_text": row.get("source_text", ""),
                    "usage": row.get("usage", "both"),
                    "image_path": row.get("image_path", ""),
                }
                for row in current.get("figure", {}).get("references", [])
            ]
            prompt_draft["_reference_bundle"] = reference_bundle
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
                if kind == "proposal":
                    yield "event: proposal\ndata: " + json.dumps({"proposal": value}, ensure_ascii=False) + "\n\n"
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
        value = proposal.get("value")
        # Compatibility with already-saved proposals from the metadata-envelope bug.
        if field in {"passage", "ask"} and isinstance(value, dict):
            value = value.get("text") or ""
        if field in {"passage", "ask", "explanation"} and not isinstance(value, str):
            raise HTTPException(400, "문자열 제안만 반영할 수 있습니다.")
        if field in {"title", "qtype", "standard_code", "difficulty", "intent"}:
            if not isinstance(value, str):
                raise HTTPException(400, "문자열 제안만 반영할 수 있습니다.")
            value = value.strip()
        if field == "qtype" and value not in {"정답형", "합답형", "서술형"}:
            raise HTTPException(400, "지원하지 않는 문항 유형입니다.")
        if field == "standard_code" and value and not conn.execute(
            "SELECT 1 FROM standard WHERE code=?", (value,)
        ).fetchone():
            raise HTTPException(400, "등록되지 않은 성취기준입니다.")
        if field == "default_points":
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "배점은 숫자여야 합니다.") from exc
        if field == "answer":
            if not isinstance(value, (str, int, float)):
                raise HTTPException(400, "문자열 제안만 반영할 수 있습니다.")
            value = str(value)
        after[field] = value
        if field in {"passage", "ask"} and proposal.get("frame_id"):
            style_meta = dict(after.get("style_meta") or {})
            style_meta[field] = {
                "frame_id": proposal["frame_id"],
                "sources": proposal.get("style_sources") or [],
            }
            after["style_meta"] = style_meta
        after = enrich_style_metadata(after)
        after["title"] = _auto_title(after, sid)
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


@router.post("/sessions/{sid}/apply-all")
def apply_all_proposals(sid: int, body: ApplyAllIn):
    """Apply one assistant turn atomically so partial clicks cannot mix a draft."""
    with db.transaction() as conn:
        current = _session(conn, sid)
        row = conn.execute(
            "SELECT proposals_json FROM authoring_message WHERE id=? AND session_id=? AND role='assistant'",
            (body.message_id, sid),
        ).fetchone()
        if not row:
            raise HTTPException(404, "제안 메시지를 찾을 수 없습니다.")
        before = current["draft"]
        after = copy.deepcopy(before)
        applied = []
        for proposal in _loads(row["proposals_json"], []):
            field = proposal.get("field")
            if field not in APPLY_FIELDS:
                continue
            value = proposal.get("value")
            if field in {"passage", "ask"} and isinstance(value, dict):
                value = value.get("text") or ""
            if field in {"passage", "ask", "explanation"} and not isinstance(value, str):
                continue
            if field in {"title", "qtype", "standard_code", "difficulty", "intent"}:
                if not isinstance(value, str):
                    continue
                value = value.strip()
            if field == "qtype" and value not in {"정답형", "합답형", "서술형"}:
                continue
            if field == "standard_code" and value and not conn.execute(
                "SELECT 1 FROM standard WHERE code=?", (value,)
            ).fetchone():
                continue
            if field == "default_points":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            if field == "answer":
                if not isinstance(value, (str, int, float)):
                    continue
                value = str(value)
            after[field] = value
            applied.append(field)
        if not applied:
            raise HTTPException(409, "한꺼번에 반영할 수 있는 문항 제안이 없습니다.")
        after = enrich_style_metadata(after)
        after["title"] = _auto_title(after, sid)
        conn.execute(
            "INSERT INTO authoring_revision(session_id,message_id,before_json,after_json) VALUES(?,?,?,?)",
            (sid, body.message_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)),
        )
        status, flags = _transition_after_change(current, after)
        conn.execute(
            "UPDATE authoring_session SET draft_json=?,status=?,review_flags=?,revision=revision+1,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(after, ensure_ascii=False), status, json.dumps(flags, ensure_ascii=False), sid),
        )
        result = _session(conn, sid)
        result["applied_fields"] = applied
        return result


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
        current = _session(conn, sid)
        if current.get("provider") != "mock":
            if current["status"] == "text_drafting" and current.get("draft", {}).get("question_status") == "완성":
                raise HTTPException(409, "완성 문항은 필수 출력 항목을 확인하고 텍스트를 확정한 뒤 저장하세요.")
            figure_status = current.get("figure", {}).get("status", "none")
            needs_figure = bool(current.get("draft", {}).get("figure_plan")) or figure_status != "none"
            if needs_figure and figure_status != "confirmed":
                raise HTTPException(409, "그림을 확정하여 문항 자료에 연결한 뒤 저장하세요.")
        if not conn.execute("SELECT 1 FROM question WHERE id=?", (body.question_id,)).fetchone():
            raise HTTPException(404, "문항을 찾을 수 없습니다.")
        conn.execute(
            "UPDATE authoring_session SET question_id=?,status='saved',"
            "updated_at=datetime('now','localtime') WHERE id=?", (body.question_id, sid))
        return _session(conn, sid)


def _figure_action(sid: int, action: str, progress=None, activation_token: str = ""):
    if action not in {"create", "draw", "edit", "activate", "sync", "revert", "confirm"}:
        raise HTTPException(400, "지원하지 않는 그림 작업입니다.")
    with db.transaction() as conn:
        current = _session(conn, sid)
        if current["figure"].get("provider") == "source_crop_hd":
            raise HTTPException(409, "원본 고해상도 크롭은 생성하거나 편집하지 않습니다.")
        if current["status"] == "text_drafting" and action != "draw":
            raise HTTPException(409, "텍스트를 먼저 확정하세요.")
        provider_name = current["figure"].get("provider") or "stub"
        # 기존 stub 세션도 실제 그림 작업을 처음 시작할 때만 외부 5E adapter로 승격한다.
        if action in {"create", "draw"}:
            requested = current["figure"].get("options", {}).get("provider", "fivee_assets")
            provider_name = "fivee_local" if action == "draw" else ("raster_image" if requested == "raster_image" else "fivee_local")
        figure_provider = get_figure_provider(provider_name)
        try:
            figure_context = _figure_context(conn, current, sid)
            if progress:
                figure_context["progress_callback"] = progress
            if action == "activate" and activation_token:
                figure_context["activation_token"] = activation_token
            figure = getattr(figure_provider, action)(sid, current["draft"], figure_context)
        except FigureProviderError as exc:
            raise HTTPException(503, str(exc)) from exc
        fig_status = figure["status"]
        authoring_status = "figure_confirmed" if action == "confirm" else "figure_drafting"
        previous_image_path = figure.get(
            "previous_image_path", current["figure"].get("previous_image_path", "")
        )
        revision_delta = 1 if action in {"create", "draw", "sync", "revert"} else 0
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
        # A generated asset is already a printable asset.  Link its stable
        # HwpPalette names to the draft immediately so the live/precise preview
        # sees every photo slot before the user presses the separate confirm
        # button.  Confirmation still controls the final workflow state.
        if action in {"create", "sync", "confirm"} and figure.get("material"):
            draft = copy.deepcopy(current["draft"])
            draft["material"] = figure["material"]
            confirmed = copy.deepcopy(current["confirmed"])
            if confirmed and action == "confirm":
                confirmed["material"] = figure["material"]
            conn.execute(
                "UPDATE authoring_session SET draft_json=?,confirmed_json=? WHERE id=?",
                (json.dumps(draft, ensure_ascii=False),
                 json.dumps(confirmed, ensure_ascii=False), sid),
            )
            if current.get("question_id"):
                conn.execute(
                    "UPDATE question SET material=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (figure["material"], current["question_id"]),
                )
        result = _session(conn, sid)
        # 실행 URL과 안내는 영속 데이터가 아니며, 인증 정보도 포함하지 않는다.
        for key in ("launch_url", "instructions"):
            if figure.get(key):
                result["figure"][key] = figure[key]
        return result


@router.post("/sessions/{sid}/figure/create-stream")
def stream_figure_create(sid: int):
    """Stream honest pipeline stages while the blocking local image renderer runs."""
    def events():
        updates: queue.Queue = queue.Queue()

        def report(percent: int, label: str) -> None:
            updates.put(("progress", {
                "percent": max(0, min(100, int(percent))), "label": str(label),
            }))

        def work() -> None:
            try:
                updates.put(("progress", {"percent": 5, "label": "문항과 그림 설정 확인 중"}))
                updates.put(("done", _figure_action(sid, "create", progress=report)))
            except HTTPException as exc:
                updates.put(("error", str(exc.detail)))
            except Exception as exc:  # keep the SSE response parseable
                updates.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        while True:
            try:
                kind, value = updates.get(timeout=1.0)
            except queue.Empty:
                yield "event: heartbeat\ndata: {}\n\n"
                continue
            if kind == "progress":
                yield "event: progress\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"
                continue
            if kind == "done":
                yield "event: done\ndata: " + json.dumps({"session": value}, ensure_ascii=False) + "\n\n"
                return
            yield "event: error\ndata: " + json.dumps({"message": value}, ensure_ascii=False) + "\n\n"
            return

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })


@router.post("/sessions/{sid}/figure/{action}")
def figure_action(sid: int, action: str, activation_token: str = ""):
    return _figure_action(sid, action, activation_token=activation_token)


@router.post("/sessions/{sid}/figure/assets/{asset_id}/{action}")
def figure_asset_action(sid: int, asset_id: int, action: str, activation_token: str = ""):
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
        if asset["provider"] == "source_crop_hd":
            raise HTTPException(409, "원본 고해상도 크롭은 5E에서 편집할 수 없습니다.")
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
        if action == "activate" and activation_token:
            context["activation_token"] = activation_token
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
        if action == "sync":
            synced_assets = [dict(item) for item in conn.execute(
                "SELECT * FROM authoring_figure_asset WHERE session_id=? ORDER BY ord,id", (sid,)
            )]
            ready = bool(synced_assets) and all(
                item.get("status") != "overlay_pending"
                and Path(item.get("rendered_image_path") or "").is_file()
                for item in synced_assets
            )
            if ready:
                material = ",".join(
                    Path(item["rendered_image_path"]).stem for item in synced_assets
                )
                draft = copy.deepcopy(current["draft"])
                draft["material"] = material
                conn.execute(
                    "UPDATE authoring_session SET draft_json=?,updated_at=datetime('now','localtime') "
                    "WHERE id=?",
                    (json.dumps(draft, ensure_ascii=False), sid),
                )
                if current.get("question_id"):
                    conn.execute(
                        "UPDATE question SET material=?,updated_at=datetime('now','localtime') WHERE id=?",
                        (material, current["question_id"]),
                    )
        result = _session(conn, sid)
        for key in ("launch_url", "instructions"):
            if figure.get(key):
                result["figure"][key] = figure[key]
        return result
