from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue

from . import db, pdf_hwp_store
from .pdf_hwp_models import ErrorRead, JobRead
from .pdf_hwp_catalog import compatible_type
from .pdf_hwp_pipeline_models import (
    CropArtifact,
    DetectedItem,
    FigureAssetMetadata,
    GraphicalChoiceAssetMetadata,
)


def _json_map(raw: str) -> dict[str, JsonValue]:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}


def asset_path(job_id: int, asset_id: int) -> tuple[Path, str] | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT file_path,media_type FROM conversion_asset WHERE id=? AND job_id=?",
            (asset_id, job_id),
        ).fetchone()
    if row is None:
        return None
    path = Path(row["file_path"])
    return (path, row["media_type"]) if path.is_file() else None


def _asset_metadata(artifact: CropArtifact, is_figure: bool) -> str:
    raw = artifact.provenance_path.read_text(encoding="utf-8")
    if not is_figure:
        return raw
    metadata = FigureAssetMetadata.model_validate_json(raw)
    return metadata.model_dump_json()


def _graphical_choice_metadata(artifact: CropArtifact) -> str:
    raw = artifact.provenance_path.read_text(encoding="utf-8")
    return GraphicalChoiceAssetMetadata.model_validate_json(raw).model_dump_json()


def _structured_draft(item: DetectedItem, palette_markdown: str) -> dict:
    """Create a safe structured projection for both successful and failed items."""
    return {
        "source_text": item.source_text or "",
        "source_bbox": list(item.bbox),
        "source_column": item.column,
        "palette_markdown": palette_markdown,
        "question_number": item.item_number,
        "domain": "",
        "type_id": "",
        "type_version": "1.0",
        "response_type": "matching",
        "asset_count": 0,
        "passage": item.source_text or "",
        "materials": [],
        "prompt": "",
        "bogi": [],
        "choices": [],
        "manual_blocks": [],
        "unplaced_materials": [],
        "detection_status": "detected",
        "conversion_status": "pending",
        "confirmed": False,
    }


def add_detected_item(
    job_id: int, ordinal: int, item: DetectedItem, crop: CropArtifact | None,
    error: ErrorRead | None, palette_markdown: str = "",
    figure: CropArtifact | None = None,
    figure_assets: tuple[CropArtifact, ...] = (),
    graphical_choice_assets: tuple[CropArtifact, ...] = (),
) -> None:
    with db.transaction() as connection:
        existing = connection.execute(
            "SELECT id,selected FROM conversion_item WHERE job_id=? AND ord=?", (job_id, ordinal),
        ).fetchone()
        draft_payload = _structured_draft(item, palette_markdown)
        draft_payload["detection_status"] = "manual_required" if error else "detected"
        draft_payload["asset_count"] = len(figure_assets or ((figure,) if figure is not None else ()))
        draft = json.dumps(draft_payload, ensure_ascii=False)
        status = "failed" if error else "ready"
        detection_status = "manual_required" if error else "detected"
        conversion_status = "pending"
        values = (
            item.page_number, item.item_number, json.dumps(item.bbox),
            status, draft,
            error.code if error else "", error.message if error else "",
        )
        if existing is None:
            item_id = connection.execute(
                "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
                "selected,draft_json,error_code,error_message,question_number,domain,type_id,type_version,"
                "response_type,asset_count,detection_status,conversion_status,manual_blocks_json,"
                "unplaced_materials_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, ordinal, *values[:4], int(error is None), *values[4:], item.item_number, "", "", "1.0",
                 "matching", draft_payload["asset_count"], detection_status, conversion_status, "[]", "[]"),
            ).lastrowid
        else:
            item_id = existing["id"]
            selected = 0 if error else existing["selected"]
            connection.execute(
                "UPDATE conversion_item SET source_page=?,source_number=?,bbox_json=?,status=?,"
                "draft_json=?,error_code=?,error_message=?,selected=?,question_number=?,domain=?,type_id=?,"
                "type_version=?,response_type=?,asset_count=?,detection_status=?,conversion_status=?,"
                "confirmed=0,confirmed_at='',manual_blocks_json='[]',unplaced_materials_json='[]',"
                "revision=revision+1,"
                "updated_at=datetime('now','localtime') WHERE id=?",
                (*values, selected, item.item_number, "", "", "1.0", "matching", draft_payload["asset_count"],
                 detection_status, conversion_status, item_id),
            )
            connection.execute("DELETE FROM conversion_asset WHERE item_id=?", (item_id,))
        final_assets = figure_assets or ((figure,) if figure is not None else ())
        final_role = "figure" if len(final_assets) == 1 else "figure_panel"
        assets = [("source_crop", crop, False)]
        assets.extend((final_role, artifact, True) for artifact in final_assets)
        assets.extend(("graphical_choice", artifact, False) for artifact in graphical_choice_assets)
        for role, artifact, is_figure in assets:
            if artifact is None:
                continue
            payload = artifact.image_path.read_bytes()
            connection.execute(
                "INSERT INTO conversion_asset(job_id,item_id,role,file_path,sha256,media_type,metadata_json) "
                "VALUES(?,?,?,?,?,'image/png',?)",
                (job_id, item_id, role, str(artifact.image_path),
                 hashlib.sha256(payload).hexdigest(),
                 _graphical_choice_metadata(artifact)
                 if role == "graphical_choice" else _asset_metadata(artifact, is_figure)),
            )


def mark_item_manual_review(job_id: int, item_number: int, detail: str) -> JobRead | None:
    """Persist a downstream safety rejection on its source item."""
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_item SET status='failed',selected=0,error_code='manual_review_required',"
            "error_message=?,revision=revision+1,updated_at=datetime('now','localtime') "
            "WHERE job_id=? AND source_number=?",
            (detail, job_id, item_number),
        )
        ready = connection.execute(
            "SELECT COUNT(*) FROM conversion_item WHERE job_id=? AND status='ready'", (job_id,),
        ).fetchone()[0]
        status = "partial_failure" if ready else "failed"
        connection.execute(
            "UPDATE conversion_job SET status=?,error_code='manual_review_required',error_message=?,"
            "revision=revision+1,updated_at=datetime('now','localtime') WHERE id=?",
            (status, detail, job_id),
        )
        return pdf_hwp_store.read_job(connection, job_id)


def update_item(
    job_id: int, item_id: int, palette_markdown: str | None, selected: bool | None,
    *, question_number: int | None = None, domain: str | None = None,
    type_id: str | None = None, response_type: str | None = None,
    asset_count: int | None = None, passage: str | None = None,
    prompt: str | None = None, materials: list | None = None,
    bogi: list | None = None, choices: list | None = None,
    source_text: str | None = None, manual_blocks: list | None = None,
    whole_source_text: bool | None = None,
) -> JobRead | None:
    with db.transaction() as connection:
        row = connection.execute(
            "SELECT draft_json,status,selected,error_code,error_message,question_number,domain,type_id,"
            "response_type,asset_count,confirmed,confirmed_at,detection_status "
            "FROM conversion_item WHERE id=? AND job_id=?",
            (item_id, job_id),
        ).fetchone()
        if row is None:
            return None
        draft = _json_map(row["draft_json"])
        changed_structured = any(value is not None for value in (
            question_number, domain, type_id, response_type, asset_count, passage,
            prompt, materials, bogi, choices, source_text, manual_blocks, whole_source_text,
        ))
        if changed_structured and "original_draft" not in draft:
            draft["original_draft"] = dict(draft)
        if palette_markdown is not None:
            draft["palette_markdown"] = palette_markdown
        if question_number is not None:
            draft["question_number"] = question_number
        if domain is not None:
            draft["domain"] = domain
        if type_id is not None:
            draft["type_id"] = type_id
        if response_type is not None:
            draft["response_type"] = response_type
        if passage is not None:
            draft["passage"] = passage
        if prompt is not None:
            draft["prompt"] = prompt
        if materials is not None:
            draft["materials"] = list(materials)
        if bogi is not None:
            draft["bogi"] = list(bogi)
        if choices is not None:
            draft["choices"] = list(choices)
        if source_text is not None:
            draft["source_text"] = source_text
        if manual_blocks is not None:
            draft["manual_blocks"] = list(manual_blocks)
        if whole_source_text is not None:
            draft["whole_source_text"] = whole_source_text
        unplaced = list(draft.get("unplaced_materials") or [])
        if asset_count is not None:
            current_materials = list(draft.get("materials") or [])
            if asset_count < len(current_materials):
                unplaced.extend(current_materials[asset_count:])
                current_materials = current_materials[:asset_count]
            else:
                current_materials.extend({"asset_id": None, "caption": ""} for _ in range(asset_count - len(current_materials)))
            draft["materials"] = current_materials
            draft["asset_count"] = asset_count
            draft["unplaced_materials"] = unplaced
        elif materials is not None:
            draft["asset_count"] = len(draft.get("materials") or [])
        content_changed = palette_markdown is not None or changed_structured
        if content_changed:
            draft["confirmed"] = False
        # A failed item that receives manual text is now a real conversion
        # candidate. Select it by default so the next HWP build keeps the
        # repaired question in the paper without a second hidden step.
        selected_value = (
            int(selected) if selected is not None else
            1 if content_changed and row["status"] in {"failed", "manual_required", "conversion_failed"}
            else row["selected"]
        )
        item_status = "ready" if content_changed else row["status"]
        error_code = "" if content_changed else row["error_code"]
        error_message = "" if content_changed else row["error_message"]
        current_domain = domain if domain is not None else row["domain"]
        current_type = type_id if type_id is not None else row["type_id"]
        current_response = response_type if response_type is not None else row["response_type"]
        current_number = question_number if question_number is not None else row["question_number"]
        current_assets = int(draft.get("asset_count") or row["asset_count"] or 0)
        current_detection = (
            "manual_editing"
            if manual_blocks is not None or whole_source_text is not None or row["detection_status"] == "manual_required"
            else row["detection_status"]
        )
        current_manual_blocks = json.dumps(draft.get("manual_blocks") or [], ensure_ascii=False)
        current_unplaced = json.dumps(draft.get("unplaced_materials") or [], ensure_ascii=False)
        confirmed_value = 0 if content_changed else int(row["confirmed"] or 0)
        confirmed_at = "" if content_changed else row["confirmed_at"]
        connection.execute(
            "UPDATE conversion_item SET draft_json=?,selected=?,status=?,"
            "error_code=?,error_message=?,question_number=?,domain=?,type_id=?,response_type=?,"
            "asset_count=?,manual_blocks_json=?,unplaced_materials_json=?,confirmed=?,confirmed_at=?,"
            "detection_status=?,conversion_status='pending',revision=revision+1,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (
                json.dumps(draft, ensure_ascii=False), selected_value, item_status,
                error_code, error_message, current_number, current_domain, current_type,
                current_response, current_assets, current_manual_blocks, current_unplaced,
                confirmed_value, confirmed_at, current_detection, item_id,
            ),
        )
        connection.execute(
            "UPDATE conversion_job SET revision=revision+1,"
            "updated_at=datetime('now','localtime') WHERE id=?", (job_id,),
        )
        if content_changed:
            # Any content edit invalidates the previous HWP/PDF pair. The
            # next explicit or automatic typeset creates a fresh pair from
            # the saved review state instead of leaving a stale download link.
            connection.execute("DELETE FROM conversion_output WHERE job_id=?", (job_id,))
            counts = connection.execute(
                "SELECT "
                "SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) AS ready_count, "
                "SUM(CASE WHEN status IN ('failed','manual_required','conversion_failed') THEN 1 ELSE 0 END) AS failed_count "
                "FROM conversion_item WHERE job_id=?", (job_id,),
            ).fetchone()
            ready_count = int(counts["ready_count"] or 0)
            failed_count = int(counts["failed_count"] or 0)
            if ready_count:
                next_status = "partial_failure" if failed_count else "review"
                next_code = "crop_partial_failure" if failed_count else ""
                next_message = (
                    f"성공 {ready_count}개 · 실패 {failed_count}개. "
                    "실패 문항도 자동 추출 원문으로 함께 보존합니다."
                    if failed_count else ""
                )
                connection.execute(
                    "UPDATE conversion_job SET status=?,error_code=?,error_message=?,"
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (next_status, next_code, next_message, job_id),
                )
        return pdf_hwp_store.read_job(connection, job_id)


def confirm_item(job_id: int, item_id: int) -> tuple[JobRead | None, list[str]]:
    """Validate and confirm one item; the returned list is safe to show in the UI."""
    with db.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM conversion_item WHERE id=? AND job_id=?", (item_id, job_id),
        ).fetchone()
        if row is None:
            return None, ["문항을 찾을 수 없습니다."]
        draft = _json_map(row["draft_json"])
        type_spec = compatible_type(row["domain"], row["type_id"])
        errors: list[str] = []
        if not row["type_id"] or type_spec is None:
            errors.append("문항 유형을 지정하세요.")
        if not (row["question_number"] or row["source_number"]):
            errors.append("문항번호를 입력하세요.")
        if not str(draft.get("prompt") or "").strip() and not str(draft.get("passage") or "").strip():
            errors.append("제시문 또는 발문을 입력하세요.")
        if type_spec:
            required = set(type_spec.get("required_fields") or [])
            for field in required:
                value = draft.get(field)
                if field == "choices" and not isinstance(value, list):
                    value = []
                if not value:
                    errors.append(f"필수 항목을 입력하세요: {field}")
            slots = type_spec.get("asset_slots") or {}
            count = int(row["asset_count"] or 0)
            if count < int(slots.get("min", 0)) or count > int(slots.get("max", 20)):
                errors.append("자료 이미지 개수가 유형 허용 범위를 벗어났습니다.")
            validation = type_spec.get("validation") or {}
            choices_count = len(draft.get("choices") or [])
            if choices_count < int(validation.get("min_choices", 0)):
                errors.append("선지를 두 개 이상 입력하세요.")
            if row["response_type"] == "combined" and len(draft.get("bogi") or []) < int(validation.get("min_bogi", 0)):
                errors.append("합답형 보기를 입력하세요.")
        if errors:
            return pdf_hwp_store.read_job(connection, job_id), errors
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        draft["confirmed"] = True
        draft["updated_at"] = now
        connection.execute(
            "UPDATE conversion_item SET confirmed=1,confirmed_at=?,status='ready',"
            "detection_status='manual_ready',conversion_status='pending',draft_json=?,"
            "error_code='',error_message='',revision=revision+1,updated_at=datetime('now','localtime') "
            "WHERE id=? AND job_id=?",
            (now, json.dumps(draft, ensure_ascii=False), item_id, job_id),
        )
        connection.execute(
            "UPDATE conversion_job SET revision=revision+1,updated_at=datetime('now','localtime') WHERE id=?",
            (job_id,),
        )
        return pdf_hwp_store.read_job(connection, job_id), []


def save_manual_blocks(job_id: int, item_id: int, blocks: list, *, whole_source_text: bool = False) -> JobRead | None:
    return update_item(job_id, item_id, None, None, manual_blocks=blocks, whole_source_text=whole_source_text)
