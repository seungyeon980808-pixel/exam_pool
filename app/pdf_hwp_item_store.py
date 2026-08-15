from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import JsonValue

from . import db, pdf_hwp_store
from .pdf_hwp_models import ErrorRead, JobRead
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
        draft = json.dumps(
            {
                "source_text": item.source_text,
                "source_column": item.column,
                "palette_markdown": palette_markdown,
            },
            ensure_ascii=False,
        )
        values = (
            item.page_number, item.item_number, json.dumps(item.bbox),
            "failed" if error else "ready", draft,
            error.code if error else "", error.message if error else "",
        )
        if existing is None:
            item_id = connection.execute(
                "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
                "selected,draft_json,error_code,error_message) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, ordinal, *values[:4], int(error is None), *values[4:]),
            ).lastrowid
        else:
            item_id = existing["id"]
            selected = 0 if error else existing["selected"]
            connection.execute(
                "UPDATE conversion_item SET source_page=?,source_number=?,bbox_json=?,status=?,"
                "draft_json=?,error_code=?,error_message=?,selected=?,revision=revision+1,"
                "updated_at=datetime('now','localtime') WHERE id=?",
                (*values, selected, item_id),
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
) -> JobRead | None:
    with db.transaction() as connection:
        row = connection.execute(
            "SELECT draft_json,status,selected,error_code,error_message "
            "FROM conversion_item WHERE id=? AND job_id=?",
            (item_id, job_id),
        ).fetchone()
        if row is None:
            return None
        draft = _json_map(row["draft_json"])
        if palette_markdown is not None:
            draft["palette_markdown"] = palette_markdown
        selected_value = int(selected) if selected is not None else row["selected"]
        item_status = "ready" if palette_markdown is not None else row["status"]
        error_code = "" if palette_markdown is not None else row["error_code"]
        error_message = "" if palette_markdown is not None else row["error_message"]
        connection.execute(
            "UPDATE conversion_item SET draft_json=?,selected=?,status=?,"
            "error_code=?,error_message=?,revision=revision+1,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (
                json.dumps(draft, ensure_ascii=False), selected_value, item_status,
                error_code, error_message, item_id,
            ),
        )
        connection.execute(
            "UPDATE conversion_job SET revision=revision+1,"
            "updated_at=datetime('now','localtime') WHERE id=?", (job_id,),
        )
        return pdf_hwp_store.read_job(connection, job_id)
