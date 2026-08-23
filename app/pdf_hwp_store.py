from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from pydantic import JsonValue

from . import db
from .pdf_hwp_models import (
    AssetRead,
    ErrorRead,
    ItemRead,
    JobCapabilities,
    JobCreate,
    JobRead,
    OutputRead,
)
from .pdf_hwp_serializer import serialize_draft


def _error(code: str, message: str) -> ErrorRead | None:
    return ErrorRead(code=code, message=message) if code else None


def _json_map(raw: str) -> dict[str, JsonValue]:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}


def _metadata_map(raw: str) -> dict[str, JsonValue]:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}


def _json_list(raw: str) -> list[JsonValue]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _read_job(connection: sqlite3.Connection, job_id: int) -> JobRead | None:
    row = connection.execute("SELECT * FROM conversion_job WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    items = []
    for item in connection.execute(
        "SELECT * FROM conversion_item WHERE job_id=? ORDER BY ord,id", (job_id,),
    ):
        draft = _json_map(item["draft_json"])
        draft.setdefault("source_text", str(draft.get("source_text") or ""))
        draft.setdefault("source_bbox", json.loads(item["bbox_json"]))
        draft.setdefault("materials", [])
        draft.setdefault("bogi", [])
        draft.setdefault("choices", [])
        items.append(ItemRead(
            id=item["id"], ord=item["ord"], source_page=item["source_page"],
            source_number=item["source_number"], bbox=tuple(json.loads(item["bbox_json"])),
            status=item["status"], selected=bool(item["selected"]), draft=draft,
            error=_error(item["error_code"], item["error_message"]), revision=item["revision"],
            question_number=item["question_number"] or item["source_number"],
            domain=item["domain"] or str(draft.get("domain") or ""),
            type_id=item["type_id"] or str(draft.get("type_id") or ""),
            type_version=item["type_version"] or str(draft.get("type_version") or "1.0"),
            response_type=item["response_type"] or str(draft.get("response_type") or "matching"),
            asset_count=int(item["asset_count"] or len(draft.get("materials") or [])),
            detection_status=item["detection_status"] or str(draft.get("detection_status") or item["status"]),
            conversion_status=item["conversion_status"] or str(draft.get("conversion_status") or "pending"),
            confirmed=bool(item["confirmed"]),
            confirmed_at=item["confirmed_at"] or None,
            source_text=str(draft.get("source_text") or ""),
            manual_blocks=_json_list(item["manual_blocks_json"]),
            unplaced_materials=_json_list(item["unplaced_materials_json"]),
        ))
    assets = [AssetRead(
        id=asset["id"], item_id=asset["item_id"], role=asset["role"],
        file_path=asset["file_path"], sha256=asset["sha256"], media_type=asset["media_type"],
        metadata=_metadata_map(asset["metadata_json"]),
    ) for asset in connection.execute(
        "SELECT * FROM conversion_asset WHERE job_id=? ORDER BY id", (job_id,),
    )]
    outputs = [OutputRead(
        id=output["id"], kind=output["kind"], status=output["status"],
        file_path=output["file_path"], sha256=output["sha256"], size_bytes=output["size_bytes"],
        error=_error(output["error_code"], output["error_message"]),
    ) for output in connection.execute(
        "SELECT * FROM conversion_output WHERE job_id=? ORDER BY id", (job_id,),
    )]
    ready_items = [item for item in items if item.status == "ready"]
    failed_items = [item for item in items if item.status in {"failed", "manual_required", "conversion_failed"}]
    selected_ready = [item for item in ready_items if item.selected]
    selected_confirmed = [
        item for item in selected_ready
        if item.confirmed or (not item.domain and not item.type_id)
    ]
    capabilities = JobCapabilities(
        review_items=bool(ready_items),
        typeset_selected=bool(selected_confirmed) and all(
            (serialize_draft(item) if item.confirmed else str(item.draft.get("palette_markdown") or "")).strip()
            for item in selected_confirmed
        ),
        retry_failed=bool(failed_items) or row["status"] == "failed",
    )
    return JobRead(
        id=row["id"], name=row["name"], layout_style=row["layout_style"], status=row["status"],
        source_filename=row["source_filename"], source_path=row["source_path"],
        source_sha256=row["source_sha256"], error=_error(row["error_code"], row["error_message"]),
        revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
        capabilities=capabilities, items=items, assets=assets, outputs=outputs,
        detection_progress=int(row["detection_progress"] or 0),
        generation_progress=int(row["generation_progress"] or 0),
        current_item_number=row["current_item_number"],
        selection_snapshot=_json_list(row["selection_snapshot_json"]),
        selection_snapshot_at=row["selection_snapshot_at"] or None,
        warnings=[
            f"{item.question_number or item.ord}번 문항에 미배치 자료가 {len(item.unplaced_materials)}개 있습니다."
            for item in items if item.unplaced_materials
        ],
        async_typeset=True,
        async_detection=True,
    )


def read_job(connection: sqlite3.Connection, job_id: int) -> JobRead | None:
    return _read_job(connection, job_id)


def create_job(payload: JobCreate) -> JobRead:
    with db.transaction() as connection:
        job_id = connection.execute(
            "INSERT INTO conversion_job(name,layout_style) VALUES(?,?)",
            (payload.name.strip(), payload.layout_style),
        ).lastrowid
        job = _read_job(connection, job_id)
        assert job is not None
        return job


def get_job(job_id: int) -> JobRead | None:
    with db.connect() as connection:
        return _read_job(connection, job_id)


def list_jobs() -> list[JobRead]:
    with db.connect() as connection:
        ids = [row["id"] for row in connection.execute(
            "SELECT id FROM conversion_job ORDER BY id DESC"
        )]
        return [job for job_id in ids if (job := _read_job(connection, job_id)) is not None]


def cancel_job(job_id: int) -> JobRead | None:
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='cancelled',error_code='cancelled',"
            "error_message='사용자가 작업을 취소했습니다.',updated_at=datetime('now','localtime') WHERE id=?",
            (job_id,),
        )
        connection.execute(
            "UPDATE conversion_operation SET status='cancelled',updated_at=datetime('now','localtime') "
            "WHERE job_id=? AND status IN ('queued','running')", (job_id,),
        )
        return _read_job(connection, job_id)


def delete_job(job_id: int) -> bool:
    with db.transaction() as connection:
        row = connection.execute("SELECT id FROM conversion_job WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return False
        connection.execute("DELETE FROM conversion_job WHERE id=?", (job_id,))
        return True


def attach_source(job_id: int, filename: str, path: Path, sha256: str) -> JobRead | None:
    with db.transaction() as connection:
        if _read_job(connection, job_id) is None:
            return None
        connection.execute("DELETE FROM conversion_item WHERE job_id=?", (job_id,))
        connection.execute("DELETE FROM conversion_asset WHERE job_id=?", (job_id,))
        connection.execute("DELETE FROM conversion_output WHERE job_id=?", (job_id,))
        connection.execute(
            "UPDATE conversion_job SET source_filename=?,source_path=?,source_sha256=?,"
            "status='uploaded',error_code='',error_message='',revision=revision+1,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (filename, str(path), sha256, job_id),
        )
        return _read_job(connection, job_id)


def output_path(job_id: int, output_id: int) -> Path | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT file_path FROM conversion_output WHERE id=? AND job_id=? AND status='ready'",
            (output_id, job_id),
        ).fetchone()
    if row is None:
        return None
    path = Path(row["file_path"])
    return path if path.is_file() else None


def begin_detection(job_id: int, preserve_success: bool = False) -> JobRead | None:
    with db.transaction() as connection:
        if _read_job(connection, job_id) is None:
            return None
        if not preserve_success:
            connection.execute("DELETE FROM conversion_item WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM conversion_asset WHERE job_id=?", (job_id,))
        connection.execute(
            "UPDATE conversion_job SET status='detecting',error_code='',error_message='',"
            "revision=revision+1,updated_at=datetime('now','localtime') WHERE id=?", (job_id,),
        )
        return _read_job(connection, job_id)


def finish_detection(job_id: int) -> JobRead | None:
    with db.transaction() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM conversion_item WHERE job_id=?", (job_id,),
        ).fetchone()[0]
        failed = connection.execute(
            "SELECT COUNT(*) FROM conversion_item WHERE job_id=? AND status IN ('failed','manual_required','conversion_failed')",
            (job_id,),
        ).fetchone()[0]
        if failed == 0:
            status, code, message = "review", "", ""
        elif failed == total:
            status, code = "failed", "crop_all_failed"
            message = f"실패한 {failed}개 문항도 자동 추출 원문으로 HWP에 보존합니다."
        else:
            status, code = "partial_failure", "crop_partial_failure"
            message = f"성공 {total - failed}개 · 실패 {failed}개. 실패 문항도 자동 추출 원문으로 함께 보존합니다."
        connection.execute(
            "UPDATE conversion_job SET status=?,error_code=?,error_message=?,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (status, code, message, job_id),
        )
        return _read_job(connection, job_id)


def fail_job(job_id: int, code: str, message: str) -> JobRead | None:
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='failed',error_code=?,error_message=?,"
            "updated_at=datetime('now','localtime') WHERE id=?", (code, message, job_id),
        )
        return _read_job(connection, job_id)


def begin_typeset(job_id: int, selection: list[int] | None = None) -> JobRead | None:
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='typesetting',error_code='',error_message='',"
            "generation_progress=0,current_item_number=NULL,selection_snapshot_json=?,"
            "selection_snapshot_at=datetime('now','localtime'),revision=revision+1,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(selection or [], ensure_ascii=False), job_id),
        )
        return _read_job(connection, job_id)


def finish_typeset(job_id: int, paths: tuple[Path, Path]) -> JobRead | None:
    with db.transaction() as connection:
        for kind, path in zip(("hwp", "pdf"), paths, strict=True):
            payload = path.read_bytes()
            connection.execute(
                "INSERT INTO conversion_output(job_id,kind,status,file_path,sha256,size_bytes) "
                "VALUES(?,?,'ready',?,?,?) ON CONFLICT(job_id,kind) DO UPDATE SET "
                "status='ready',file_path=excluded.file_path,sha256=excluded.sha256,"
                "size_bytes=excluded.size_bytes,error_code='',error_message='',"
                "updated_at=datetime('now','localtime')",
                (job_id, kind, str(path), hashlib.sha256(payload).hexdigest(), len(payload)),
            )
        failed = connection.execute(
            "SELECT COUNT(*) FROM conversion_item WHERE job_id=? AND status IN ('failed','manual_required','conversion_failed')",
            (job_id,),
        ).fetchone()[0]
        status = "partial_failure" if failed else "completed"
        code = "crop_partial_failure" if failed else ""
        message = (
            f"성공한 문항은 편집 가능하게 출력했습니다. 실패한 {failed}개 문항은 자동 추출 원문으로 보존했습니다."
            if failed else ""
        )
        connection.execute(
            "UPDATE conversion_job SET status=?,error_code=?,error_message=?,"
            "generation_progress=100,current_item_number=NULL,updated_at=datetime('now','localtime') WHERE id=?",
            (status, code, message, job_id),
        )
        return _read_job(connection, job_id)


def mark_item_conversion_failed(job_id: int, item_number: int, detail: str) -> JobRead | None:
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_item SET status='conversion_failed',selected=0,confirmed=0,"
            "conversion_status='failed',error_code='item_conversion_failed',error_message=?,"
            "revision=revision+1,updated_at=datetime('now','localtime') "
            "WHERE job_id=? AND (source_number=? OR question_number=?)",
            (detail, job_id, item_number, item_number),
        )
        remaining = connection.execute(
            "SELECT COUNT(*) FROM conversion_item WHERE job_id=? AND status='ready' AND selected=1",
            (job_id,),
        ).fetchone()[0]
        status = "partial_failure" if remaining else "failed"
        connection.execute(
            "UPDATE conversion_job SET status=?,error_code='item_conversion_failed',error_message=?,"
            "updated_at=datetime('now','localtime') WHERE id=?", (status, detail, job_id),
        )
        return _read_job(connection, job_id)
