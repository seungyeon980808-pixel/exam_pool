"""Observe prepared KICE figure contracts in a generated PDF."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
from itertools import permutations
from typing import Final

import fitz
from PIL import Image

from .pdf_hwp_kice_source_crop import has_drawings, observe_source_crop
from .pdf_hwp_kice_structural_geometry import (
    classify_figure_placement,
    geometric_order,
    is_zero_information_raster,
    scale_is_readable,
)
from .pdf_hwp_kice_structural_models import (
    FigurePlacement,
    KiceFigureDiagnostic,
    KiceFigureExpectation,
    KiceFigureIssue,
    KiceStructuralDiagnostic,
    KiceStructuralIssue,
    KiceStructuralItem,
    KiceStructuralRequest,
    KiceStructuralResult,
    PreparedVisualKind,
)
from .pdf_hwp_pipeline_models import DetectedItem, FigureAsset


BBox = tuple[float, float, float, float]
_OWNER_OVERLAP: Final = 0.50
_SIMILARITY_FLOOR: Final = 0.90
_GEOMETRY_TOLERANCE: Final = 1.0
_SIGNATURE_SIZE: Final = (32, 32)


@dataclass(frozen=True, slots=True)
class _ObservedImage:
    page_number: int
    bbox: BBox
    digest: str
    signature: bytes


def _area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(left: BBox, right: BBox) -> float:
    return _area((max(left[0], right[0]), max(left[1], right[1]),
                  min(left[2], right[2]), min(left[3], right[3])))


def _contains(outer: BBox, inner: BBox) -> bool:
    return (inner[0] >= outer[0] - _GEOMETRY_TOLERANCE
            and inner[1] >= outer[1] - _GEOMETRY_TOLERANCE
            and inner[2] <= outer[2] + _GEOMETRY_TOLERANCE
            and inner[3] <= outer[3] + _GEOMETRY_TOLERANCE)


def _signature(payload: bytes) -> bytes:
    with Image.open(BytesIO(payload)) as opened:
        return opened.convert("L").resize(_SIGNATURE_SIZE, Image.Resampling.LANCZOS).tobytes()


def _similarity(asset: FigureAsset, observed: _ObservedImage) -> float:
    payload = asset.image_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() == observed.digest:
        return 2.0
    expected = _signature(payload)
    distance = sum(abs(left - right) for left, right in zip(expected, observed.signature, strict=True))
    return 1.0 - distance / (255 * len(expected))


def _observed_images(document: fitz.Document) -> tuple[_ObservedImage, ...]:
    found: list[_ObservedImage] = []
    for page in document:
        for raw in page.get_image_info(xrefs=True):
            xref = int(raw["xref"])
            if xref <= 0:
                continue
            extracted = document.extract_image(xref)
            payload = bytes(extracted["image"])
            if is_zero_information_raster(payload, int(extracted.get("smask", 0)) > 0):
                continue
            found.append(_ObservedImage(
                page.number + 1, tuple(float(value) for value in raw["bbox"]),
                hashlib.sha256(payload).hexdigest(), _signature(payload),
            ))
    return tuple(found)


def _owners(image: _ObservedImage, items: tuple[DetectedItem, ...]) -> tuple[int, ...]:
    return tuple(item.item_number for item in items
                 if item.page_number == image.page_number
                 and _intersection(image.bbox, item.bbox) / max(_area(image.bbox), 1.0) >= _OWNER_OVERLAP)


def _best_match(assets: tuple[FigureAsset, ...], images: tuple[_ObservedImage, ...]
                ) -> tuple[tuple[int, ...], float] | None:
    if not assets or len(images) < len(assets):
        return None
    ranked = (
        (sum(_similarity(asset, images[index]) for asset, index in zip(assets, order, strict=True)), order)
        for order in permutations(range(len(images)), len(assets))
    )
    score, order = max(ranked, key=lambda value: (value[0], tuple(-index for index in value[1])))
    minimum = min(_similarity(asset, images[index]) for asset, index in zip(assets, order, strict=True))
    return order, minimum if score >= 0 else minimum


def _issue(code: KiceFigureIssue, item: int | None, detail: str) -> KiceStructuralIssue:
    return KiceStructuralIssue(code, item, detail)


def _diagnostic(
    code: KiceFigureDiagnostic, item: int, detail: str,
) -> KiceStructuralDiagnostic:
    return KiceStructuralDiagnostic(code, item, detail)


def inspect_kice_figure_structure(request: KiceStructuralRequest) -> KiceStructuralResult:
    """Compare prepared visual assets with generated PDF image geometry."""
    source = request.generated_pdf.resolve()
    try:
        document = fitz.open(source)
    except (fitz.FileDataError, FileNotFoundError, OSError) as error:
        issue = _issue(KiceFigureIssue.UNREADABLE_PDF, None, str(error))
        return KiceStructuralResult(source, (), (issue,), ())
    issues: list[KiceStructuralIssue] = []
    diagnostics: list[KiceStructuralDiagnostic] = []
    results: list[KiceStructuralItem] = []
    with document:
        observed = _observed_images(document)
        owners = tuple(_owners(image, request.generated_items) for image in observed)
        expectations = {value.item_number: value for value in request.expectations}
        for item in request.generated_items:
            expected = expectations.get(
                item.item_number,
                KiceFigureExpectation(item.item_number, (), placement=FigurePlacement.NONE),
            )
            if expected.visual_kind is PreparedVisualKind.FIGURE:
                for index, asset in enumerate(expected.assets, 1):
                    source_crop = observe_source_crop(asset)
                    if source_crop.contaminated_bboxes:
                        issues.append(_issue(
                            KiceFigureIssue.SOURCE_CROP_CONTAMINATION, item.item_number,
                            f"asset={index};text_bboxes={source_crop.contaminated_bboxes}",
                        ))
                    elif source_crop.error is not None:
                        diagnostics.append(_diagnostic(
                            KiceFigureDiagnostic.SOURCE_CROP_UNOBSERVED, item.item_number,
                            f"asset={index};error={source_crop.error}",
                        ))
            owned = tuple(
                image for image, owner in zip(observed, owners, strict=True)
                if item.item_number in owner
            )
            if any(len(owner) > 1 and item.item_number in owner for owner in owners):
                issues.append(_issue(
                    KiceFigureIssue.FIGURE_OWNER_AMBIGUOUS, item.item_number,
                    "image has multiple item owners",
                ))
            if any(not _contains(item.bbox, image.bbox) and any(
                other.item_number != item.item_number and _intersection(image.bbox, other.bbox) > 0
                for other in request.generated_items) for image in owned):
                issues.append(_issue(
                    KiceFigureIssue.CROSS_ITEM_SPILL, item.item_number,
                    "image crosses an item boundary",
                ))
            if not expected.assets:
                if owned:
                    diagnostics.append(_diagnostic(
                        KiceFigureDiagnostic.IMAGE_MATCH_UNOBSERVED, item.item_number,
                        f"{len(owned)} unprepared raster image(s) cannot be classified safely",
                    ))
                results.append(KiceStructuralItem(
                    item.item_number, 0, len(owned), FigurePlacement.NONE, None,
                ))
                continue
            table_drawing = (
                not owned
                and expected.visual_kind is PreparedVisualKind.TABLE
                and has_drawings(document[item.page_number - 1], item)
            )
            if table_drawing:
                diagnostics.append(_diagnostic(
                    KiceFigureDiagnostic.VECTOR_OR_TABLE_UNOBSERVED, item.item_number,
                    "vector table has no safely matchable raster",
                ))
                results.append(KiceStructuralItem(item.item_number, len(expected.assets), 0, None, None))
                continue
            if not owned:
                issues.append(_issue(KiceFigureIssue.FIGURE_MISSING, item.item_number, "observed=0"))
            if len(owned) != len(expected.assets):
                detail = f"expected={len(expected.assets)};observed={len(owned)}"
                issues.append(_issue(KiceFigureIssue.PANEL_COUNT_MISMATCH, item.item_number, detail))
            page_images = tuple(image for image in observed if image.page_number == item.page_number)
            page_match = _best_match(expected.assets, page_images)
            if page_match is not None and page_match[1] >= _SIMILARITY_FLOOR and any(
                item.item_number not in owners[observed.index(page_images[index])]
                for index in page_match[0]
            ):
                issues.append(_issue(
                    KiceFigureIssue.FIGURE_OWNER_MISMATCH, item.item_number,
                    "matching prepared asset belongs to another generated item",
                ))
            matched = _best_match(expected.assets, owned)
            placement = classify_figure_placement(
                document[item.page_number - 1], item, tuple(image.bbox for image in owned),
            ) if owned else None
            minimum_scale: float | None = None
            if matched is None or matched[1] < _SIMILARITY_FLOOR:
                diagnostics.append(_diagnostic(
                    KiceFigureDiagnostic.IMAGE_MATCH_UNOBSERVED, item.item_number,
                    "prepared assets could not be matched safely",
                ))
            else:
                indices = matched[0]
                selected = tuple(owned[index] for index in indices)
                geometric = geometric_order(
                    tuple(image.bbox for image in owned), expected.assets,
                )
                if len(selected) == len(owned) and tuple(indices) != geometric:
                    issues.append(_issue(
                        KiceFigureIssue.PANEL_ORDER_MISMATCH, item.item_number,
                        f"matched_order={indices}",
                    ))
                scales = tuple(min(
                    (image.bbox[2] - image.bbox[0])
                    / (asset.metadata.image_bbox[2] - asset.metadata.image_bbox[0]),
                    (image.bbox[3] - image.bbox[1])
                    / (asset.metadata.image_bbox[3] - asset.metadata.image_bbox[1]),
                ) for asset, image in zip(expected.assets, selected, strict=True))
                minimum_scale = min(scales)
                if any(not scale_is_readable(
                    asset.metadata.image_bbox, image.bbox, request.minimum_scale,
                ) for asset, image in zip(expected.assets, selected, strict=True)):
                    issues.append(_issue(
                        KiceFigureIssue.SCALE_UNREADABLE, item.item_number,
                        f"scale={minimum_scale:.4f}",
                    ))
            if placement is None:
                if owned:
                    diagnostics.append(_diagnostic(
                        KiceFigureDiagnostic.PLACEMENT_UNOBSERVED, item.item_number,
                        "question anchor is unavailable",
                    ))
            elif placement is not expected.placement:
                detail = f"expected={expected.placement.value};observed={placement.value}"
                issues.append(_issue(KiceFigureIssue.PLACEMENT_MISMATCH, item.item_number, detail))
            results.append(KiceStructuralItem(
                item.item_number, len(expected.assets), len(owned), placement, minimum_scale,
            ))
    return KiceStructuralResult(source, tuple(results), tuple(issues), tuple(diagnostics))
