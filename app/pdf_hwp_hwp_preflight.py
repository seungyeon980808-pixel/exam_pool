"""Registered-template compatibility checks before HWP automation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .integrations import palette_registry
from .pdf_hwp_pipeline_models import (
    ConversionUnit,
    FigureAsset,
    GraphicalChoiceAsset,
    LayoutStyle,
    ManualReviewRequiredError,
)


_TEMPLATE_TOKEN = re.compile(r"^\\([^\\\r\n]+)\\$")
_GRAPHICAL_CHOICE_TEMPLATE: Final = "수능정답1대사진그림5선지"
_GRAPHICAL_CHOICE_SLOTS: Final = (
    "문항번호", "문두", "사진1", "발문",
    "선지사진1", "선지사진2", "선지사진3", "선지사진4", "선지사진5",
)
_STATIC_PAIR_CAPTIONS: Final = {
    "수능정답2소사진무캡션5선지": ("(가)", "(나)"),
    "수능정답2대사진5선지": ("(가)", "(나)"),
    "수능합답2소사진무캡션5선지": ("(가)", "(나)"),
    "수능합답2대사진5선지": ("(가)", "(나)"),
}


class _RegisteredTemplate(BaseModel):
    """Parsed active-template metadata at the palette registry boundary."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    label: str = Field(min_length=1)
    slot_count: int = Field(ge=0)
    slot_names: tuple[str, ...]

    @model_validator(mode="after")
    def slot_names_match_count(self) -> _RegisteredTemplate:
        if len(self.slot_names) != self.slot_count:
            msg = "slot_names must match slot_count"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class PreflightedUnit:
    """One HWP-safe unit plus the hashes of its exact inserted PNG assets."""

    item_number: int
    palette_markdown: str
    figure_asset_hashes: tuple[str, ...]


def _manual(unit: ConversionUnit, detail: str) -> ManualReviewRequiredError:
    return ManualReviewRequiredError(item_number=unit.item_number, detail=detail)


def _template_label(unit: ConversionUnit) -> str:
    first = next((line.strip() for line in unit.palette_markdown.splitlines() if line.strip()), "")
    match = _TEMPLATE_TOKEN.fullmatch(first)
    if match is None:
        raise _manual(unit, "registered template label is missing")
    return match.group(1).strip()


def _slot_values(markdown: str, slot_count: int) -> tuple[str, ...]:
    lines = markdown.splitlines()[1:]
    values: list[str] = []
    index = 0
    while index < len(lines) and len(values) < slot_count:
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        if not line.startswith("{"):
            values.append(line)
            continue
        block = [line]
        depth = line.count("{") - line.count("}")
        while depth > 0 and index < len(lines):
            line = lines[index].strip()
            index += 1
            block.append(line)
            depth += line.count("{") - line.count("}")
        values.append("\n".join(block))
    return tuple(values)


def _slot_type(name: str) -> str:
    compact = name.strip().replace(" ", "").lower()
    if compact.startswith(("선지사진", "choicephoto", "choicefigure", "choiceimage")):
        return "choice_figure"
    if compact.startswith(("사진", "photo", "figure", "image")):
        return "figure"
    if compact.startswith(("캡션", "caption")) or re.fullmatch(
        r"\((?:가|나|다|라|마|바|사|아)\)", compact,
    ):
        return "caption"
    return "content"


def _ordered_assets(unit: ConversionUnit) -> tuple[FigureAsset, ...]:
    return tuple(sorted(unit.figure_assets, key=lambda asset: asset.metadata.panel_index))


def _asset_hash(unit: ConversionUnit, asset: FigureAsset | GraphicalChoiceAsset) -> str:
    path = asset.image_path
    if not path.is_file():
        raise _manual(unit, f"figure asset is missing: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != asset.metadata.asset_hash:
        raise _manual(unit, f"figure asset hash mismatch: {path.name}")
    return actual


def preflight_unit(unit: ConversionUnit, layout_style: LayoutStyle) -> PreflightedUnit:
    """Require one registered template to match its ordered captionless assets."""
    if not unit.figure_assets and not unit.graphical_choice_assets:
        first = next(
            (line.strip() for line in unit.palette_markdown.splitlines() if line.strip()), "",
        )
        if first != f"\\{_GRAPHICAL_CHOICE_TEMPLATE}\\":
            return PreflightedUnit(unit.item_number, unit.palette_markdown, ())
    assets = _ordered_assets(unit)
    expected_indices = tuple(range(1, len(assets) + 1))
    if tuple(asset.metadata.panel_index for asset in assets) != expected_indices:
        raise _manual(unit, "figure panel indices are not contiguous")
    if any(asset.metadata.asset_count != len(assets) for asset in assets):
        raise _manual(unit, "figure asset_count metadata is inconsistent")
    if any(asset.metadata.manual_review_required for asset in assets):
        raise _manual(unit, "figure metadata requires manual review")
    choice_assets = unit.graphical_choice_assets
    expected_choice_indices = tuple(range(1, len(choice_assets) + 1))
    if tuple(asset.metadata.choice_index for asset in choice_assets) != expected_choice_indices:
        raise _manual(unit, "graphical choice indices are not contiguous and ordered")
    if any(asset.metadata.asset_count != len(choice_assets) for asset in choice_assets):
        raise _manual(unit, "graphical choice asset_count metadata is inconsistent")
    if any(asset.metadata.manual_review_required for asset in choice_assets):
        raise _manual(unit, "graphical choice metadata requires manual review")

    label = _template_label(unit)
    raw_template = palette_registry.active_template(layout_style.value, label)
    if raw_template is None:
        raise _manual(unit, f"registered template is unavailable: {label}")
    try:
        template = _RegisteredTemplate.model_validate(raw_template)
    except ValidationError as exc:
        raise _manual(unit, f"registered template metadata is invalid: {label}") from exc

    slot_types = tuple(_slot_type(name) for name in template.slot_names)
    figure_slots = tuple(index for index, kind in enumerate(slot_types) if kind == "figure")
    choice_slots = tuple(index for index, kind in enumerate(slot_types) if kind == "choice_figure")
    caption_slots = tuple(index for index, kind in enumerate(slot_types) if kind == "caption")
    if len(figure_slots) != len(assets):
        raise _manual(
            unit,
            f"figure slot count {len(figure_slots)} does not match asset count {len(assets)}",
        )
    if choice_assets and (
        label != _GRAPHICAL_CHOICE_TEMPLATE
        or template.slot_names != _GRAPHICAL_CHOICE_SLOTS
    ):
        raise _manual(unit, "graphical choices require the registered five-choice template")
    if len(choice_slots) != len(choice_assets):
        raise _manual(
            unit,
            f"graphical choice slot count {len(choice_slots)} does not match asset count "
            f"{len(choice_assets)}",
        )
    captions = tuple(
        asset.metadata.caption_text.strip()
        for asset in assets
        if asset.metadata.caption_text.strip()
    )
    static_captions = _STATIC_PAIR_CAPTIONS.get(label)
    if static_captions is not None and caption_slots:
        raise _manual(unit, "static-caption template unexpectedly declares caption slots")
    if static_captions is not None and captions != static_captions:
        raise _manual(unit, "figure captions do not match registered static captions")
    if static_captions is None and len(caption_slots) != len(captions):
        raise _manual(
            unit,
            f"caption slot count {len(caption_slots)} does not match caption count {len(captions)}",
        )

    values = _slot_values(unit.palette_markdown, template.slot_count)
    if len(values) != template.slot_count:
        raise _manual(unit, "template value count does not match registered slot count")
    expected_figures = tuple(f"\\{asset.image_path.stem}\\" for asset in assets)
    if tuple(values[index] for index in figure_slots) != expected_figures:
        raise _manual(unit, "figure slot values do not match ordered captionless assets")
    expected_choices = tuple(f"\\{asset.image_path.stem}\\" for asset in choice_assets)
    if tuple(values[index] for index in choice_slots) != expected_choices:
        raise _manual(unit, "graphical choice slot values do not match ordered assets")
    if static_captions is None and tuple(values[index] for index in caption_slots) != captions:
        raise _manual(unit, "caption slot values do not match separate caption text")

    hashes = tuple(_asset_hash(unit, asset) for asset in (*assets, *choice_assets))
    return PreflightedUnit(unit.item_number, unit.palette_markdown, hashes)


def preflight_units(
    units: tuple[ConversionUnit, ...], layout_style: LayoutStyle,
) -> tuple[PreflightedUnit, ...]:
    """Preflight ordered units without launching or mutating HWP."""
    return tuple(preflight_unit(unit, layout_style) for unit in units)
