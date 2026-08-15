from __future__ import annotations

import hashlib
from pathlib import Path
from typing import assert_never

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .paths import data_dir
from . import pdf_hwp_graphical_choices
from .pdf_hwp_final_figure_contract import FinalFigureContract, FinalFigureReview
from . import pdf_hwp_final_figure_contract
from .pdf_hwp_models import AssetRead, ErrorRead, ItemPatch, ItemRead, JobCreate, JobList, JobRead
from .pdf_hwp_pipeline_models import FigureAsset, FigureAssetMetadata, ManualReviewRequiredError
from . import pdf_hwp_item_store, pdf_hwp_store
from . import pdf_hwp_pipeline as pipeline


router = APIRouter(prefix="/api/pdf-hwp", tags=["pdf-hwp"])


def conversion_root() -> Path:
    return data_dir() / "pdf_hwp"


def _job_or_404(job_id: int) -> JobRead:
    job = pdf_hwp_store.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF-HWP conversion job not found")
    return job


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
    if len(payload) > 100 * 1024 * 1024 or not payload.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A valid PDF up to 100 MB is required")
    folder = conversion_root() / f"job_{job_id}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "source.pdf"
    target.write_bytes(payload)
    job = pdf_hwp_store.attach_source(
        job_id, filename, target, hashlib.sha256(payload).hexdigest(),
    )
    if job is None:
        target.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF-HWP conversion job not found")
    return job


@router.get("/jobs/{job_id}/outputs/{output_id}")
def download_output(job_id: int, output_id: int) -> FileResponse:
    _job_or_404(job_id)
    path = pdf_hwp_store.output_path(job_id, output_id)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion output not found")
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "application/x-hwp"
    return FileResponse(path, media_type=media_type, filename=path.name)


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
    unsafe = tuple(asset for asset in metadata if asset.manual_review_required)
    if not unsafe:
        return None
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


def _typeset(job_id: int) -> JobRead:
    job = _job_or_404(job_id)
    try:
        units = tuple(
            pipeline.ConversionUnit(
                item_number=item.source_number or item.ord,
                palette_markdown=str(item.draft.get("palette_markdown") or ""),
                figure_assets=_figure_assets(item, job.assets),
                graphical_choice_assets=pdf_hwp_graphical_choices.selected_assets(
                    item.source_number or item.ord,
                    str(item.draft.get("palette_markdown") or ""),
                    [asset for asset in job.assets if asset.item_id == item.id],
                ),
            )
            for item in job.items if item.selected and item.status == "ready"
        )
    except ManualReviewRequiredError as exc:
        pdf_hwp_item_store.mark_item_manual_review(job_id, exc.item_number, exc.detail)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not units or any(not unit.palette_markdown.strip() for unit in units):
        raise HTTPException(status.HTTP_409_CONFLICT, "Every ready item needs palette_markdown")
    pdf_hwp_store.begin_typeset(job_id)
    request = pipeline.ConversionRequest(
        job_key=str(job_id), units=units,
        output_dir=conversion_root() / f"job_{job_id}" / "outputs",
        layout_style=pipeline.LayoutStyle(job.layout_style),
        asset_dirs=tuple(sorted({Path(asset.file_path).parent for asset in job.assets})),
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
        pdf_hwp_store.fail_job(job_id, "typeset_failed", str(exc))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    completed = pdf_hwp_store.finish_typeset(job_id, (result.hwp_path, result.pdf_path))
    assert completed is not None
    return completed


@router.post("/jobs/{job_id}/typeset", response_model=JobRead)
def typeset_job(job_id: int) -> JobRead:
    return _typeset(job_id)


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


@router.patch("/jobs/{job_id}/items/{item_id}", response_model=JobRead)
def update_item(job_id: int, item_id: int, payload: ItemPatch) -> JobRead:
    _job_or_404(job_id)
    if payload.palette_markdown is None and payload.selected is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An item change is required")
    job = pdf_hwp_item_store.update_item(
        job_id, item_id, payload.palette_markdown, payload.selected,
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversion item not found")
    return job
