"""Typed structural view of one prepared HwpPalette conversion unit."""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .integrations import palette_registry
from .pdf_hwp_pipeline_models import ConversionUnit, LayoutStyle


FieldOrder = tuple[
    Literal["number"], Literal["stem"], Literal["materials"],
    Literal["ask"], Literal["bogi"], Literal["choices"],
]
_FIELD_ORDER: Final[FieldOrder] = (
    "number", "stem", "materials", "ask", "bogi", "choices",
)
_TEMPLATE_TOKEN: Final = re.compile(r"^\\([^\\\r\n]+)\\$")
_CHOICE_SLOT: Final = re.compile(
    r"^(?:선지|선지사진|choice|choicephoto|choicefigure|choiceimage)?([1-5])$",
)
_BOGI_INDEX: Final = {
    "ㄱ": 1, "보기ㄱ": 1, "보기1": 1, "발언1": 1,
    "ㄴ": 2, "보기ㄴ": 2, "보기2": 2, "발언2": 2,
    "ㄷ": 3, "보기ㄷ": 3, "보기3": 3, "발언3": 3,
}
_STEM_SLOTS: Final = {
    "문두", "지문", "passage", "실험과정", "실험내용", "실험본문",
}


class AssetRole(StrEnum):
    MATERIAL = "material"
    GRAPHICAL_CHOICE = "graphical_choice"


class PreparedStructureIssue(StrEnum):
    INVALID_STRUCTURE = "invalid_structure"
    MERGED_FIELDS = "merged_fields"
    MISSING_STEM = "missing_stem"
    MISSING_BOGI = "missing_bogi"
    MISSING_CHOICES = "missing_choices"
    ASK_CHOICE_OVERLAP = "ask_choice_overlap"


class PreparedMaterialField(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    order: int = Field(ge=1)


class PreparedBogiClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: Literal["ㄱ", "ㄴ", "ㄷ"]
    text: str = Field(min_length=1)


class PreparedAssetRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: AssetRole
    asset_path: Path
    slot_name: str = Field(min_length=1)
    owner_item_number: int = Field(ge=1)
    source_page: int = Field(ge=1)
    asset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    order: int = Field(ge=1)


class PreparedItemStructure(BaseModel):
    """Frozen, ordered fields required for structural round-trip acceptance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    source_page: int = Field(ge=1)
    item_bbox: tuple[float, float, float, float]
    stem: str = Field(min_length=1)
    materials: tuple[PreparedMaterialField, ...]
    ask: str = Field(min_length=1)
    bogi: tuple[PreparedBogiClaim, ...]
    choices: tuple[str, str, str, str, str]
    asset_refs: tuple[PreparedAssetRef, ...]
    field_order: FieldOrder = _FIELD_ORDER

    @model_validator(mode="after")
    def complete_bogi(self) -> PreparedItemStructure:
        labels = tuple(claim.label for claim in self.bogi)
        if labels not in {(), ("ㄱ", "ㄴ", "ㄷ")}:
            msg = "bogi must be absent or contain ordered ㄱ/ㄴ/ㄷ claims"
            raise ValueError(msg)
        return self


class _ActiveTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    slot_count: int = Field(ge=1)
    slot_names: tuple[str, ...]

    @model_validator(mode="after")
    def matching_slot_count(self) -> _ActiveTemplate:
        if len(self.slot_names) != self.slot_count:
            msg = "active template slot names do not match slot count"
            raise ValueError(msg)
        return self


class PreparedStructureError(ValueError):
    """Raised when palette slots cannot form the required typed structure."""

    def __init__(
        self,
        item_number: int,
        detail: str,
        code: PreparedStructureIssue = PreparedStructureIssue.INVALID_STRUCTURE,
    ) -> None:
        self.item_number = item_number
        self.detail = detail
        self.code = code
        super().__init__(item_number, code, detail)

    def __str__(self) -> str:
        return f"item {self.item_number} structure {self.code.value}: {self.detail}"


def _template_label(unit: ConversionUnit) -> str:
    first = next((line.strip() for line in unit.palette_markdown.splitlines() if line.strip()), "")
    match = _TEMPLATE_TOKEN.fullmatch(first)
    if match is None:
        raise PreparedStructureError(unit.item_number, "registered template label is missing")
    return match.group(1).strip()


def _slot_values(markdown: str, slot_count: int) -> tuple[str, ...]:
    lines = markdown.splitlines()[1:]
    values: list[str] = []
    index = 0
    while index < len(lines) and len(values) < slot_count:
        value = lines[index].strip()
        index += 1
        if not value:
            continue
        if not value.startswith("{"):
            values.append(value)
            continue
        block = [value[1:]]
        depth = value.count("{") - value.count("}")
        while depth > 0 and index < len(lines):
            value = lines[index].strip()
            index += 1
            block.append(value)
            depth += value.count("{") - value.count("}")
        if depth != 0:
            return ()
        block[-1] = block[-1][:-1]
        values.append("\n".join(part for part in block if part))
    return tuple(values)


def _compact(slot_name: str) -> str:
    return slot_name.strip().replace(" ", "").lower()


def _asset_refs(
    unit: ConversionUnit, slot_names: tuple[str, ...], values: tuple[str, ...],
) -> tuple[PreparedAssetRef, ...]:
    refs: list[PreparedAssetRef] = []
    for asset in unit.figure_assets:
        token = f"\\{asset.image_path.stem}\\"
        slot_name = next((name for name, value in zip(slot_names, values, strict=True) if token in value), "자료")
        refs.append(PreparedAssetRef(
            role=AssetRole.MATERIAL, asset_path=asset.image_path, slot_name=slot_name,
            owner_item_number=asset.metadata.item_number,
            source_page=asset.metadata.page_number, asset_hash=asset.metadata.asset_hash,
            order=asset.metadata.panel_index,
        ))
    for asset in unit.graphical_choice_assets:
        token = f"\\{asset.image_path.stem}\\"
        slot_name = next((name for name, value in zip(slot_names, values, strict=True) if value == token), f"선지사진{asset.metadata.choice_index}")
        refs.append(PreparedAssetRef(
            role=AssetRole.GRAPHICAL_CHOICE, asset_path=asset.image_path,
            slot_name=slot_name, owner_item_number=asset.metadata.item_number,
            source_page=asset.metadata.page_number, asset_hash=asset.metadata.asset_hash,
            order=asset.metadata.choice_index,
        ))
    return tuple(refs)


def parse_prepared_structure(
    unit: ConversionUnit,
    source_page: int,
    item_bbox: tuple[float, float, float, float],
    layout_style: LayoutStyle,
) -> PreparedItemStructure:
    """Parse active-template slot values into a frozen structural contract."""
    label = _template_label(unit)
    raw_template = palette_registry.active_template(layout_style.value, label)
    if raw_template is None:
        raise PreparedStructureError(unit.item_number, f"active template is unavailable: {label}")
    template = _ActiveTemplate.model_validate(raw_template)
    values = _slot_values(unit.palette_markdown, template.slot_count)
    if len(values) != template.slot_count:
        raise PreparedStructureError(unit.item_number, "slot value count does not match active template")
    pairs = tuple(zip(template.slot_names, values, strict=True))
    compact_names = tuple(_compact(name) for name in template.slot_names)
    has_stem_anchor = any(name in _STEM_SLOTS for name in compact_names)
    number_values = tuple(value for name, value in pairs if _compact(name) in {"문항번호", "번호", "num"})
    stem_values = tuple(
        value for name, value in pairs
        if _compact(name) in _STEM_SLOTS
        or (_compact(name) == "발문" and not has_stem_anchor)
    )
    stem_parts = tuple(value for value in stem_values if "".join(value.split()) != "-")
    stem_value = "\n".join(stem_parts)
    ask_values = tuple(value for name, value in pairs if _compact(name) in {"질문", "ask", "질문배점"} or (_compact(name) == "발문" and has_stem_anchor))
    bogi_by_index = {_BOGI_INDEX[_compact(name)]: value for name, value in pairs if _compact(name) in _BOGI_INDEX}
    choices_by_index = {
        int(match.group(1)): value
        for name, value in pairs
        if (match := _CHOICE_SLOT.fullmatch(_compact(name))) is not None
    }
    try:
        parsed_number = int(number_values[0]) if len(number_values) == 1 else None
    except ValueError as error:
        raise PreparedStructureError(unit.item_number, "typed number slot is not an integer") from error
    if parsed_number != unit.item_number:
        raise PreparedStructureError(unit.item_number, "typed number slot is missing or inconsistent")
    if not stem_parts:
        raise PreparedStructureError(
            unit.item_number, "stem is empty or an obvious sentence fragment",
            PreparedStructureIssue.MISSING_STEM,
        )
    if tuple(sorted(choices_by_index)) != (1, 2, 3, 4, 5):
        raise PreparedStructureError(
            unit.item_number, "exactly five ordered choice slots are required",
            PreparedStructureIssue.MISSING_CHOICES,
        )
    if tuple(sorted(bogi_by_index)) not in {(), (1, 2, 3)}:
        raise PreparedStructureError(
            unit.item_number, "bogi slots must be absent or ordered ㄱ/ㄴ/ㄷ",
            PreparedStructureIssue.MISSING_BOGI,
        )
    normalized_stem = "".join(stem_value.split())
    fragment_stem = (
        normalized_stem == "-"
        or (len(normalized_stem) <= 4 and normalized_stem.endswith(("은", "는", "이", "가", "을", "를", "의")))
    )
    if fragment_stem:
        raise PreparedStructureError(
            unit.item_number, "stem is empty or an obvious sentence fragment",
            PreparedStructureIssue.MISSING_STEM,
        )
    if len(ask_values) != 1:
        raise PreparedStructureError(
            unit.item_number, "stem and ask must occupy separate slots",
            PreparedStructureIssue.MERGED_FIELDS,
        )
    ask_tail = "".join(ask_values[0].partition("?")[2].split())
    choice_overlap = tuple(value for value in choices_by_index.values()
                           if len("".join(value.split())) >= 3
                           and "".join(value.split()) in ask_tail)
    if len(choice_overlap) >= 2:
        raise PreparedStructureError(
            unit.item_number, "ask repeats trailing prepared choice values",
            PreparedStructureIssue.ASK_CHOICE_OVERLAP,
        )
    claimed = {
        name for name in compact_names
        if name in {"문항번호", "번호", "num", "발문", "질문", "ask", "질문배점"}
        or name in _STEM_SLOTS or name in _BOGI_INDEX or _CHOICE_SLOT.fullmatch(name)
    }
    materials = tuple(
        PreparedMaterialField(slot_name=name, value=value, order=index)
        for index, (name, value) in enumerate(pairs, 1) if _compact(name) not in claimed
    )
    return PreparedItemStructure(
        number=unit.item_number, source_page=source_page, item_bbox=item_bbox,
        stem=stem_value, materials=materials, ask=ask_values[0],
        bogi=tuple(PreparedBogiClaim(label=label, text=bogi_by_index[index]) for index, label in enumerate(("ㄱ", "ㄴ", "ㄷ"), 1) if index in bogi_by_index),
        choices=tuple(choices_by_index[index] for index in range(1, 6)),
        asset_refs=_asset_refs(unit, template.slot_names, values),
    )


__all__ = [
    "AssetRole", "PreparedAssetRef", "PreparedBogiClaim", "PreparedItemStructure",
    "PreparedMaterialField", "PreparedStructureError", "PreparedStructureIssue",
    "parse_prepared_structure",
]
