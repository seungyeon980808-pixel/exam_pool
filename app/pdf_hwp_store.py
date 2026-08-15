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


def _error(code: str, message: str) -> ErrorRead | None:
    return ErrorRead(code=code, message=message) if code else None


def _json_map(raw: str) -> dict[str, JsonValue]:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}


def _metadata_map(raw: str) -> dict[str, JsonValue]:
    value = json.loads(raw or "{}")
    return value if isinstance(value, dict) else {}


def _read_job(connection: sqlite3.Connection, job_id: int) -> JobRead | None:
    row = connection.execute("SELECT * FROM conversion_job WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    items = [ItemRead(
        id=item["id"], ord=item["ord"], source_page=item["source_page"],
        source_number=item["source_number"], bbox=tuple(json.loads(item["bbox_json"])),
        status=item["status"], selected=bool(item["selected"]), draft=_json_map(item["draft_json"]),
        error=_error(item["error_code"], item["error_message"]), revision=item["revision"],
    ) for item in connection.execute(
        "SELECT * FROM conversion_item WHERE job_id=? ORDER BY ord,id", (job_id,),
    )]
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
    failed_items = [item for item in items if item.status == "failed"]
    selected_ready = [item for item in ready_items if item.selected]
    capabilities = JobCapabilities(
        review_items=bool(ready_items),
        typeset_selected=bool(selected_ready) and all(
            str(item.draft.get("palette_markdown") or "").strip()
            for item in selected_ready
        ),
        retry_failed=bool(failed_items) or row["status"] == "failed",
    )
    return JobRead(
        id=row["id"], name=row["name"], layout_style=row["layout_style"], status=row["status"],
        source_filename=row["source_filename"], source_path=row["source_path"],
        source_sha256=row["source_sha256"], error=_error(row["error_code"], row["error_message"]),
        revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
        capabilities=capabilities, items=items, assets=assets, outputs=outputs,
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
            "SELECT COUNT(*) FROM conversion_item WHERE job_id=? AND status='failed'", (job_id,),
        ).fetchone()[0]
        if failed == 0:
            status, code, message = "review", "", ""
        elif failed == total:
            status, code = "failed", "crop_all_failed"
            message = f"변환 가능한 문항이 없습니다. 실패한 {failed}개 문항의 상세 원인을 확인하세요."
        else:
            status, code = "partial_failure", "crop_partial_failure"
            message = f"성공 {total - failed}개 · 실패 {failed}개. 성공한 문항은 HWP로 만들 수 있습니다."
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


def begin_typeset(job_id: int) -> JobRead | None:
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='typesetting',error_code='',error_message='',"
            "revision=revision+1,updated_at=datetime('now','localtime') WHERE id=?", (job_id,),
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
            "SELECT COUNT(*) FROM conversion_item WHERE job_id=? AND status='failed'", (job_id,),
        ).fetchone()[0]
        status = "partial_failure" if failed else "completed"
        code = "crop_partial_failure" if failed else ""
        message = (
            f"성공한 문항은 출력했습니다. 실패한 {failed}개 문항은 상세 원인을 확인하세요."
            if failed else ""
        )
        connection.execute(
            "UPDATE conversion_job SET status=?,error_code=?,error_message=?,"
            "updated_at=datetime('now','localtime') WHERE id=?", (status, code, message, job_id),
        )
        return _read_job(connection, job_id)
