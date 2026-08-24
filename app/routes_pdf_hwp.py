from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

import fitz
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .paths import data_dir
from . import db
from .integrations import palette_registry
from .integrations.hwppalette import hwppalette_provider
from . import pdf_hwp_graphical_choices
from .pdf_hwp_final_figure_contract import FinalFigureContract, FinalFigureReview
from . import pdf_hwp_final_figure_contract
from .pdf_hwp_figure_layout import display_size_for_width
from .pdf_hwp_models import AssetRead, ErrorRead, ItemPatch, ItemRead, JobCreate, JobList, JobRead
from .pdf_hwp_catalog import compatible_type, load_catalog
from .pdf_hwp_pipeline_models import (
    FigureArrangement,
    FigureAsset,
    FigureAssetMetadata,
    ManualReviewRequiredError,
    PanelMode,
    SourceKind,
)
from . import pdf_hwp_item_store, pdf_hwp_store
from . import pdf_hwp_raster_ocr
from .pdf_hwp_serializer import serialize_draft
from . import pdf_hwp_pipeline as pipeline
from .export_palette import question_to_palette


router = APIRouter(prefix="/api/pdf-hwp", tags=["pdf-hwp"])


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    contract_version: int = 2
    raster_editable_ocr: bool = True


@router.get("/runtime")
def get_runtime_contract() -> RuntimeContract:
    return RuntimeContract()


@router.get("/palette")
def get_active_palette() -> dict:
    active = palette_registry.active_palette_package("suneung")
    if active is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "등록된 수능 양식이 없습니다.")
    record, _ = active
    return record


@router.get("/palette/download")
def download_active_palette() -> FileResponse:
    active = palette_registry.active_palette_package("suneung")
    if active is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "내려받을 수능 양식이 없습니다.")
    record, archive = active
    filename = Path(str(record.get("filename") or "수능양식.hwpal")).name
    return FileResponse(
        archive,
        media_type="application/octet-stream",
        filename=filename,
    )


@router.post("/palette", status_code=status.HTTP_201_CREATED)
async def replace_active_palette(file: UploadFile = File(...)) -> dict:
    filename = Path(file.filename or "palette.hwpal").name
    content = await file.read(palette_registry.MAX_ARCHIVE_BYTES + 1)
    if len(content) > palette_registry.MAX_ARCHIVE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "팔레트 파일은 32MB를 넘을 수 없습니다.")
    try:
        result = palette_registry.install_hwpal(content, filename, "suneung")
        hwppalette_provider._data_root()
        return result
    except palette_registry.PalettePackageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/catalog")
def get_type_catalog() -> dict:
    """Return the configured domain/type catalog used by the structured editor."""
    return load_catalog()


@router.get("/types")
def get_type_catalog_alias() -> dict:
    """Short alias kept for settings panels and older clients."""
    return load_catalog()


def conversion_root() -> Path:
    return data_dir() / "pdf_hwp"


def _job_or_404(job_id: int) -> JobRead:
    job = pdf_hwp_store.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF-HWP conversion job not found")
    return job


def _operation_read(operation_id: str) -> dict | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM conversion_operation WHERE id=?", (operation_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "operation_id": row["id"], "job_id": row["job_id"], "kind": row["kind"],
            "status": row["status"], "progress": row["progress"],
            "current_item_number": row["current_item_number"],
            "selection_snapshot": json.loads(row["selection_snapshot_json"] or "[]"),
            "error": {"code": row["error_code"], "message": row["error_message"]}
            if row["error_code"] else None,
            "updated_at": row["updated_at"],
        }


def _typeset_with_fallback_retry(job_id: int, selection: list[int]) -> None:
    """Retry once after preflight marks an unsafe item for text fallback."""
    try:
        _typeset(job_id, selection)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
        _typeset(job_id, selection)


def _run_typeset_operation(operation_id: str, job_id: int) -> None:
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_operation SET status='running',progress=10,"
            "updated_at=datetime('now','localtime') WHERE id=? AND status='queued'", (operation_id,),
        )
        cancelled = connection.execute(
            "SELECT status,selection_snapshot_json FROM conversion_operation WHERE id=?", (operation_id,),
        ).fetchone()
    if not cancelled or cancelled["status"] == "cancelled":
        return
    selection = json.loads(cancelled["selection_snapshot_json"] or "[]")
    if selection:
        with db.transaction() as connection:
            connection.execute(
                "UPDATE conversion_operation SET progress=15,current_item_number=?,"
                "updated_at=datetime('now','localtime') WHERE id=?",
                (int(selection[0]), operation_id),
            )
    try:
        _typeset_with_fallback_retry(job_id, selection)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
        with db.transaction() as connection:
            connection.execute(
                "UPDATE conversion_operation SET status='failed',error_code='typeset_failed',"
                "error_message=?,updated_at=datetime('now','localtime') WHERE id=?",
                (detail, operation_id),
            )
        return
    except Exception as exc:  # provider failures must remain observable in operation history
        with db.transaction() as connection:
            connection.execute(
                "UPDATE conversion_operation SET status='failed',error_code='typeset_exception',"
                "error_message=?,updated_at=datetime('now','localtime') WHERE id=?",
                (str(exc), operation_id),
            )
        return
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_operation SET status='completed',progress=100,"
            "current_item_number=NULL,updated_at=datetime('now','localtime') WHERE id=?", (operation_id,),
        )


def _run_detect_operation(operation_id: str, job_id: int) -> None:
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_operation SET status='running',progress=10,"
            "updated_at=datetime('now','localtime') WHERE id=? AND status='queued'", (operation_id,),
        )
    try:
        _detect(job_id, False)
    except Exception as exc:
        with db.transaction() as connection:
            connection.execute(
                "UPDATE conversion_operation SET status='failed',error_code='detection_failed',"
                "error_message=?,updated_at=datetime('now','localtime') WHERE id=?",
                (str(exc), operation_id),
            )
        return
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_operation SET status='completed',progress=100,"
            "updated_at=datetime('now','localtime') WHERE id=?", (operation_id,),
        )


@router.get("/operations/{operation_id}")
def get_operation(operation_id: str) -> dict:
    operation = _operation_read(operation_id)
    if operation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion operation not found")
    return operation


@router.post("/operations/{operation_id}/cancel")
def cancel_operation(operation_id: str) -> dict:
    operation = _operation_read(operation_id)
    if operation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion operation not found")
    pdf_hwp_store.cancel_job(int(operation["job_id"]))
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_operation SET status='cancelled',updated_at=datetime('now','localtime') WHERE id=?",
            (operation_id,),
        )
    return _operation_read(operation_id) or operation


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate) -> JobRead:
    return pdf_hwp_store.create_job(payload)


@router.get("/jobs", response_model=JobList)
def list_jobs() -> JobList:
    return JobList(items=pdf_hwp_store.list_jobs())


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: int) -> JobRead:
    return _job_or_404(job_id)


@router.get("/jobs/{job_id}/source")
def preview_source(job_id: int) -> FileResponse:
    job = _job_or_404(job_id)
    path = Path(job.source_path)
    if not job.source_path or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source PDF not found")
    return FileResponse(path, media_type="application/pdf", filename=job.source_filename)


@router.get("/jobs/{job_id}/assets/{asset_id}")
def preview_asset(job_id: int, asset_id: int) -> FileResponse:
    _job_or_404(job_id)
    asset = pdf_hwp_item_store.asset_path(job_id, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion asset not found")
    path, media_type = asset
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.post("/jobs/{job_id}/upload", response_model=JobRead)
async def upload_source(job_id: int, file: UploadFile = File(...)) -> JobRead:
    _job_or_404(job_id)
    filename = Path(file.filename or "source.pdf").name
    payload = await file.read(100 * 1024 * 1024 + 1)
    if len(payload) > 100 * 1024 * 1024:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A valid PDF or image up to 100 MB is required")
    folder = conversion_root() / f"job_{job_id}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "source.pdf"
    normalized = payload
    ocr_words = ()
    if not payload.startswith(b"%PDF-"):
        extension = Path(filename).suffix.lower().lstrip(".")
        if extension not in {"png", "jpg", "jpeg", "webp"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A valid PDF, PNG, JPEG, or WebP file is required")
        try:
            recognized = pdf_hwp_raster_ocr.recognize_raster_document(
                payload,
                extension,
                image_path=folder / f"ocr-input.{extension}",
            )
            normalized = recognized.pdf_bytes
            ocr_words = recognized.words
        except (fitz.FileDataError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                str(exc) or "A valid PDF, PNG, JPEG, or WebP file is required",
            ) from exc
    target.write_bytes(normalized)
    if ocr_words:
        pdf_hwp_raster_ocr.write_sidecar(target, ocr_words)
    else:
        pdf_hwp_raster_ocr.sidecar_path(target).unlink(missing_ok=True)
    job = pdf_hwp_store.attach_source(
        job_id, filename, target, hashlib.sha256(normalized).hexdigest(),
    )
    if job is None:
        target.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF-HWP conversion job not found")
    return job


@router.get("/jobs/{job_id}/outputs/{output_id}")
def download_output(job_id: int, output_id: int) -> FileResponse:
    job = _job_or_404(job_id)
    path = pdf_hwp_store.output_path(job_id, output_id)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion output not found")
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "application/x-hwp"
    source_stem = Path(job.source_filename or job.name or "output").stem.strip() or "output"
    return FileResponse(
        path, media_type=media_type,
        filename=f"{source_stem}_converted{path.suffix.lower()}",
    )


def _detect(job_id: int, preserve_success: bool) -> JobRead:
    job = _job_or_404(job_id)
    if not job.source_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload a source PDF before detection")
    pdf_hwp_store.begin_detection(job_id, preserve_success)
    if preserve_success:
        detected_items = tuple(
            pipeline.DetectedItem(
                item.source_page or 1, item.source_number or item.ord,
                int(item.draft.get("source_column") or 0), item.bbox,
                str(item.draft.get("source_text") or ""),
            )
            for item in job.items
        )
    else:
        try:
            detected_items = pipeline.detect_items(Path(job.source_path)).items
        except pipeline.InvalidSourcePdfError as exc:
            pdf_hwp_store.fail_job(job_id, "invalid_source_pdf", str(exc))
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    crop_dir = conversion_root() / f"job_{job_id}" / "crops"
    for ordinal, item in enumerate(detected_items, 1):
        prior = next((existing for existing in job.items if existing.ord == ordinal), None)
        if preserve_success and prior is not None and prior.status == "ready":
            continue
        try:
            draft = pipeline.build_editable_draft(
                Path(job.source_path), item, crop_dir,
                layout_style=pipeline.LayoutStyle(job.layout_style),
            )
        except pipeline.UnsupportedDraftLayoutError as exc:
            pdf_hwp_item_store.add_detected_item(
                job_id, ordinal, item, exc.source_image,
                routes_error("manual_review_required", str(exc)),
            )
        except (pipeline.DraftExtractionError, pipeline.InvalidCropError) as exc:
            pdf_hwp_item_store.add_detected_item(
                job_id, ordinal, item, None,
                routes_error("draft_extraction_failed", str(exc)),
            )
        else:
            choice_detail = pdf_hwp_graphical_choices.draft_review_detail(
                item.item_number, draft.palette_markdown, draft.graphical_choice_assets,
            )
            final_contract = pdf_hwp_final_figure_contract.reconcile_final_figure_contract(
                item.item_number, draft.palette_markdown, draft.figure_assets,
            )
            match final_contract:
                case FinalFigureContract(palette_markdown=final_markdown):
                    figure_detail = None
                case FinalFigureReview(detail=figure_detail):
                    final_markdown = draft.palette_markdown
                case unreachable:
                    assert_never(unreachable)
            review_error = (
                routes_error("manual_review_required", choice_detail)
                if choice_detail is not None else
                routes_error("manual_review_required", figure_detail)
                if figure_detail is not None else _figure_review_error(draft.figure_assets)
            )
            pdf_hwp_item_store.add_detected_item(
                job_id, ordinal, item, draft.source_image, review_error,
                final_markdown, draft.figure_asset, draft.figure_assets,
                draft.graphical_choice_assets,
            )
    if not detected_items:
        failed = pdf_hwp_store.fail_job(job_id, "no_items_detected", "No question items detected")
        assert failed is not None
        return failed
    completed = pdf_hwp_store.finish_detection(job_id)
    assert completed is not None
    return completed


@router.post("/jobs/{job_id}/detect", response_model=JobRead)
def detect_source(job_id: int) -> JobRead:
    return _detect(job_id, False)


def routes_error(code: str, message: str) -> ErrorRead:
    return ErrorRead(code=code, message=message)


def _figure_review_error(assets: tuple[pipeline.CropArtifact, ...]) -> ErrorRead | None:
    try:
        metadata = tuple(
            FigureAssetMetadata.model_validate_json(asset.provenance_path.read_text(encoding="utf-8"))
            for asset in assets
        )
    except ValidationError:
        return routes_error("manual_review_required", "invalid figure asset metadata")
    if not pdf_hwp_final_figure_contract.final_figure_metadata_requires_review(metadata):
        return None
    unsafe = tuple(asset for asset in metadata if asset.manual_review_required)
    reasons = tuple(dict.fromkeys(
        reason for asset in unsafe for reason in asset.review_reasons if reason.strip()
    ))
    detail = "; ".join(reasons) or "figure separation requires manual review"
    return routes_error("manual_review_required", detail)


def _figure_assets(item: ItemRead, assets: list[AssetRead]) -> tuple[FigureAsset, ...]:
    candidates = [
        asset for asset in assets
        if asset.item_id == item.id
        and (asset.role in {"figure", "figure_panel"} or asset.role.startswith("figure_panel_"))
    ]
    try:
        parsed = tuple(
            FigureAsset(Path(asset.file_path), FigureAssetMetadata.model_validate(asset.metadata))
            for asset in candidates
        )
    except ValidationError as exc:
        raise ManualReviewRequiredError(
            item.source_number or item.ord, "invalid persisted figure asset metadata",
        ) from exc
    return tuple(sorted(parsed, key=lambda asset: asset.metadata.panel_index))


def _failed_source_figure(
    item: ItemRead, assets: list[AssetRead],
) -> FigureAsset | None:
    """Promote the exact item crop to a safe one-panel fallback asset."""
    source = next((
        asset for asset in assets
        if asset.item_id == item.id and asset.role == "source_crop"
    ), None)
    if source is None:
        return None
    path = Path(source.file_path)
    metadata = source.metadata
    bbox_value = metadata.get("bbox") or metadata.get("source_bbox")
    if not path.is_file() or not isinstance(bbox_value, (list, tuple)) or len(bbox_value) != 4:
        return None
    bbox = tuple(float(value) for value in bbox_value)
    width_px = int(metadata.get("width_px") or 0)
    height_px = int(metadata.get("height_px") or 0)
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1] or width_px <= 0 or height_px <= 0:
        return None
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if source.sha256 and source.sha256 != actual_hash:
        return None
    number = item.source_number or item.question_number or item.ord
    return FigureAsset(path, FigureAssetMetadata(
        source_pdf=Path(str(metadata.get("source_pdf") or "source.pdf")),
        page_number=int(metadata.get("page_number") or item.source_page or 1),
        item_number=number,
        image_bbox=bbox,
        caption_text="",
        caption_bbox=None,
        asset_count=1,
        panel_index=1,
        panel_mode=PanelMode.SINGLE,
        arrangement=FigureArrangement.COMPOSITE,
        source_kind=SourceKind.RASTER,
        display_size=display_size_for_width(bbox[2] - bbox[0]),
        dpi=int(metadata.get("dpi") or 300),
        width_px=width_px,
        height_px=height_px,
        asset_hash=actual_hash,
        confidence=1.0,
        manual_review_required=False,
        review_reasons=(),
    ))


def _failed_item_fallback(
    item: ItemRead, layout_style: str, assets: list[AssetRead],
) -> tuple[str, tuple[FigureAsset, ...]]:
    """Keep an unparsed item inside a real exam-question template.

    The extraction may be too uncertain to split into passage, prompt, and
    choices, but the source text can still occupy one protected passage slot.
    Empty structured slots are deliberately skipped so the HWP keeps the
    selected exam layout instead of degrading to loose paragraphs.
    """
    number = item.source_number or item.question_number or item.ord
    source_figure = _failed_source_figure(item, assets)
    if source_figure is not None:
        markdown = "\n".join((
            "\\수능원문1대사진\\",
            f"\\{source_figure.image_path.stem}\\",
        ))
        return markdown, (source_figure,)
    draft = item.draft or {}
    text = str(item.source_text or draft.get("source_text") or draft.get("passage") or "").strip()
    if not text:
        text = "PDF 원문을 텍스트로 읽지 못했습니다. 변환 상세 보기에서 원문을 확인하세요."
    number_marker = re.search(
        rf"(?m)^\s*{re.escape(str(number))}\s*[.)]\s*", text,
    )
    if number_marker is not None:
        # PDF text layers often prepend footer/page fragments (for example
        # ``31\n3\n13.``).  Everything before the printed item number is page
        # furniture, not question content.
        text = text[number_marker.end():].strip()
    question = {
        "qtype": "정답형",
        "passage": f"[자동 추출 원문]\n{text}",
        "ask": "",
        "material": "",
        "default_points": None,
        "is_negative": False,
    }
    return question_to_palette(question, [], num=number, layout_style=layout_style), ()


def _typeset(job_id: int, selection: list[int] | None = None) -> JobRead:
    job = _job_or_404(job_id)
    selection_set = {int(number) for number in selection} if selection is not None else None
    failed_statuses = {"failed", "manual_required", "conversion_failed"}
    if (
        Path(job.source_filename or "").suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        and any(item.status in failed_statuses for item in job.items)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "이미지 문항은 전체 사진으로 대체하지 않습니다. OCR 결과를 검토한 뒤 다시 변환해 주세요.",
        )
    try:
        units_list: list[pipeline.ConversionUnit] = []
        for item in job.items:
            item_number = item.source_number or item.ord
            if selection_set is not None and item_number not in selection_set:
                continue
            if item.status in failed_statuses:
                fallback_markdown, fallback_assets = _failed_item_fallback(
                    item, job.layout_style, job.assets,
                )
                units_list.append(pipeline.ConversionUnit(
                    item_number=item_number,
                    palette_markdown=fallback_markdown,
                    figure_assets=fallback_assets,
                ))
                continue
            if not (item.selected and item.status == "ready"):
                continue
            if not (item.confirmed or (not item.domain and not item.type_id)):
                continue
            units_list.append(pipeline.ConversionUnit(
                item_number=item_number,
                palette_markdown=serialize_draft(item),
                figure_assets=_figure_assets(item, job.assets),
                graphical_choice_assets=pdf_hwp_graphical_choices.selected_assets(
                    item_number,
                    str(item.draft.get("palette_markdown") or ""),
                    [asset for asset in job.assets if asset.item_id == item.id],
                ),
            ))
        units = tuple(units_list)
    except ManualReviewRequiredError as exc:
        pdf_hwp_item_store.mark_item_manual_review(job_id, exc.item_number, exc.detail)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not units or any(not unit.palette_markdown.strip() for unit in units):
        raise HTTPException(status.HTTP_409_CONFLICT, "Every ready item needs palette_markdown")
    pdf_hwp_store.begin_typeset(job_id, [unit.item_number for unit in units])
    from .integrations.hwppalette_runner import subject_header_from_source
    request = pipeline.ConversionRequest(
        job_key=str(job_id), units=units,
        output_dir=conversion_root() / f"job_{job_id}" / "outputs",
        layout_style=pipeline.LayoutStyle(job.layout_style),
        asset_dirs=tuple(sorted({Path(asset.file_path).parent for asset in job.assets})),
        header_subject=subject_header_from_source(job.source_filename or job.source_path) or "",
    )
    try:
        result = pipeline.typeset_conversion(request)
    except ManualReviewRequiredError as exc:
        pdf_hwp_item_store.mark_item_manual_review(job_id, exc.item_number, exc.detail)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except pipeline.ConversionResourceLockedError as exc:
        pdf_hwp_store.fail_job(job_id, "typeset_resource_locked", str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except pipeline.ConversionTypesetError as exc:
        match = re.search(r"(?:item|문항)\s*([0-9]+)", str(exc), re.IGNORECASE)
        if match:
            pdf_hwp_store.mark_item_conversion_failed(job_id, int(match.group(1)), str(exc))
        else:
            pdf_hwp_store.fail_job(job_id, "typeset_failed", str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    completed = pdf_hwp_store.finish_typeset(job_id, (result.hwp_path, result.pdf_path))
    assert completed is not None
    return completed


@router.post("/jobs/{job_id}/typeset", response_model=JobRead)
def typeset_job(job_id: int) -> JobRead:
    return _typeset(job_id)


@router.post("/jobs/{job_id}/typeset/start")
def start_typeset_job(job_id: int) -> dict:
    """Queue a non-blocking HWP build using a durable selection snapshot."""
    job = _job_or_404(job_id)
    fallback_items = [item for item in job.items if item.status in {"failed", "manual_required", "conversion_failed"}]
    if not job.capabilities.typeset_selected and not fallback_items:
        raise HTTPException(status.HTTP_409_CONFLICT, "확정된 문항만 HWP 변환 대상으로 선택할 수 있습니다.")
    snapshot = [item.source_number or item.ord for item in job.items
                if (item.selected and item.status == "ready") or item.status in {"failed", "manual_required", "conversion_failed"}]
    operation_id = uuid.uuid4().hex
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO conversion_operation(id,job_id,kind,status,progress,selection_snapshot_json) "
            "VALUES(?,?,?,'queued',0,?)",
            (operation_id, job_id, "typeset", json.dumps(snapshot, ensure_ascii=False)),
        )
    thread = threading.Thread(target=_run_typeset_operation, args=(operation_id, job_id), daemon=True)
    thread.start()
    return {
        "operation_id": operation_id, "job_id": job_id, "status": "queued", "progress": 0,
        "selection_snapshot": snapshot,
    }


@router.post("/jobs/{job_id}/detect/start")
def start_detection_job(job_id: int) -> dict:
    """Queue detection so the review shell can stay interactive while pages are read."""
    job = _job_or_404(job_id)
    if not job.source_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload a source PDF before detection")
    operation_id = uuid.uuid4().hex
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO conversion_operation(id,job_id,kind,status,progress) VALUES(?,?,?,'queued',0)",
            (operation_id, job_id, "detect"),
        )
    threading.Thread(target=_run_detect_operation, args=(operation_id, job_id), daemon=True).start()
    return {"operation_id": operation_id, "job_id": job_id, "status": "queued", "progress": 0}


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: int) -> JobRead:
    job = _job_or_404(job_id)
    if job.status == "partial_failure":
        return _detect(job_id, True)
    if job.status == "failed" and any(item.status == "failed" for item in job.items):
        return _detect(job_id, True)
    if job.status == "failed" and job.items:
        return _typeset(job_id)
    if job.status == "failed":
        return _detect(job_id, False)
    raise HTTPException(status.HTTP_409_CONFLICT, "Only failed jobs can be retried")


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel_job(job_id: int) -> JobRead:
    job = _job_or_404(job_id)
    if job.status not in {"detecting", "typesetting"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "진행 중인 작업만 취소할 수 있습니다.")
    cancelled = pdf_hwp_store.cancel_job(job_id)
    assert cancelled is not None
    return cancelled


@router.delete("/jobs/{job_id}", response_model=JobRead)
def delete_job(job_id: int) -> JobRead:
    """Delete one history card and its job folder; active work must be cancelled first."""
    job = _job_or_404(job_id)
    if job.status in {"detecting", "typesetting"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "진행 중인 작업은 먼저 취소해야 삭제할 수 있습니다.")
    folder = conversion_root() / f"job_{job_id}"
    try:
        if folder.exists():
            shutil.rmtree(folder)
    except OSError as exc:
        with db.transaction() as connection:
            connection.execute(
                "UPDATE conversion_job SET status='cleanup_pending',error_code='cleanup_pending',"
                "error_message=?,updated_at=datetime('now','localtime') WHERE id=?",
                (str(exc), job_id),
            )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "작업 폴더 정리가 지연되고 있습니다.") from exc
    if not pdf_hwp_store.delete_job(job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion job not found")
    return job


@router.patch("/jobs/{job_id}/items/{item_id}", response_model=JobRead)
def update_item(job_id: int, item_id: int, payload: ItemPatch) -> JobRead:
    _job_or_404(job_id)
    values = payload.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An item change is required")
    domain = values.get("domain")
    type_id = values.get("type_id")
    if domain is not None or type_id is not None:
        current = pdf_hwp_store.get_job(job_id)
        current_item = next((item for item in (current.items if current else []) if item.id == item_id), None)
        effective_domain = domain if domain is not None else (current_item.domain if current_item else "")
        effective_type = type_id if type_id is not None else (current_item.type_id if current_item else "")
        spec = compatible_type(effective_domain, effective_type)
        if effective_type and spec is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "선택한 분야에서 사용할 수 없는 문항 유형입니다.")
        if spec and "response_type" not in values:
            values["response_type"] = spec.get("response_type", "matching")
    job = pdf_hwp_item_store.update_item(
        job_id, item_id, values.pop("palette_markdown", None), values.pop("selected", None), **values,
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion item not found")
    return job


@router.post("/jobs/{job_id}/items/{item_id}/confirm", response_model=JobRead)
def confirm_item(job_id: int, item_id: int) -> JobRead:
    _job_or_404(job_id)
    job, errors = pdf_hwp_item_store.confirm_item(job_id, item_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion item not found")
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "code": "question_not_ready", "message": "문항을 확정할 수 없습니다.", "errors": errors,
        })
    return job


@router.post("/jobs/{job_id}/items/{item_id}/manual-blocks", response_model=JobRead)
def save_manual_blocks(job_id: int, item_id: int, payload: dict) -> JobRead:
    _job_or_404(job_id)
    blocks = payload.get("manual_blocks")
    if not isinstance(blocks, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "manual_blocks must be an array")
    job = pdf_hwp_item_store.save_manual_blocks(
        job_id, item_id, blocks, whole_source_text=bool(payload.get("whole_source_text", False)),
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion item not found")
    return job
