"""Strict style/content QA for native mathematical HWP/HWPX documents.

The checker is manifest-driven and intentionally conservative.  It verifies
that applying a reference style changed presentation only: native endnote
item IDs, equation scripts, table-cell text, and referenced image SHA-256
values must remain identical.  It also checks style families, typography and
geometry tolerances, page density, blank/zero-width spacing, orphan objects,
overlap, clipping, and large gaps.

The HWPX reader is dependency-free and is used in CI and synthetic tests.
Binary HWP files require an adapter-provided inventory because the HWP binary
format is not a stable public XML contract; the HWP and HWPX inventories can
then be compared by the same gates.  No PDF, screenshot, OCR, or copyrighted
content is embedded by this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import xml.etree.ElementTree as ET

try:
    from .hwp_reference_style import (
        SCHEMA_VERSION,
        ZERO_WIDTH_RE,
        StyleProfile,
        StyleProfileError,
        _equation_offset_guards,
        content_manifest,
        canonical_sha256,
        preflight_reference_style,
        preserved_content_gate,
        profile_sha256,
    )
except ImportError:  # direct CLI execution
    from hwp_reference_style import (  # type: ignore
        SCHEMA_VERSION,
        ZERO_WIDTH_RE,
        StyleProfile,
        StyleProfileError,
        _equation_offset_guards,
        content_manifest,
        canonical_sha256,
        preflight_reference_style,
        preserved_content_gate,
        profile_sha256,
    )


QA_SCHEMA_VERSION = "math-hwp-reference-style-qa-v1"
TEXT_TAGS = {"t", "text"}
EQUATION_TAG = "equation"
ENDNOTE_TAG = "endnote"
TABLE_TAGS = {"tbl", "table"}
CELL_TAGS = {"tc", "cell", "tablecell"}
PICTURE_TAGS = {"pic", "picture"}
IMAGE_TAGS = {"img", "image"}
ITEM_ID_RE = re.compile(r"(?:ENDNOTE|ITEM)\s*[:=]\s*([A-Za-z0-9_.:-]+)", re.I)


class StyleQAError(ValueError):
    """Raised for invalid QA input rather than returning a false PASS."""


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1].split(":", 1)[-1].lower()


def _attr(element: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for key, value in element.attrib.items():
        if _local_name(key) in wanted or str(key).split(":")[-1].lower() in wanted:
            return str(value)
    return ""


def _iter_name(element: ET.Element, names: set[str]) -> Iterable[ET.Element]:
    return (node for node in element.iter() if _local_name(node.tag) in names)


def _element_text(element: ET.Element, *, include_scripts: bool = False) -> str:
    chunks: list[str] = []
    for node in element.iter():
        name = _local_name(node.tag)
        if name in TEXT_TAGS or (include_scripts and name == "script"):
            chunks.append("".join(node.itertext()))
    return "".join(chunks)


def _normal_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u200b", "").split())


def _image_key(value: str) -> str:
    value = value.replace("\\", "/").strip().lower()
    return value.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _image_sha(resources: Mapping[str, bytes], reference: str) -> tuple[str, str]:
    key = _image_key(reference)
    for name, payload in resources.items():
        if _image_key(name) == key or key in _image_key(name):
            return name, hashlib.sha256(payload).hexdigest()
    return reference, ""


def _equation_script(element: ET.Element) -> str:
    scripts = list(_iter_name(element, {"script"}))
    if scripts:
        return "".join(scripts[0].itertext())
    return _attr(element, "script")


def _hwpx_inventory(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".hwpx":
        raise StyleQAError(f"HWPX reader requires .hwpx: {path}")
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise StyleQAError(f"invalid HWPX: {path}: {exc}") from exc
    with archive:
        resources = {name: archive.read(name) for name in archive.namelist() if name.lower().startswith("bindata/")}
        xml_members = [(name, archive.read(name)) for name in archive.namelist() if name.lower().endswith(".xml")]
    items: list[dict[str, Any]] = []
    all_item_ids: list[str] = []
    equation_scripts: list[str] = []
    document_equation_scripts: list[str] = []
    equation_style: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    image_rows: list[dict[str, Any]] = []
    blank_paragraphs: list[str] = []
    zero_width_locations: list[str] = []
    zero_width_guards: list[dict[str, Any]] = []
    zero_width_strays: list[str] = []
    page_styles: list[dict[str, Any]] = []
    column_styles: list[dict[str, Any]] = []
    endnote_count = 0
    autonum_count = 0
    page_count = 0
    for member, payload in xml_members:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise StyleQAError(f"invalid XML member {member}: {exc}") from exc
        paragraph_positions = {
            id(node): index
            for index, node in enumerate(_iter_name(root, {"p", "paragraph"}), 1)
        }
        text_positions = {
            id(node): index
            for index, node in enumerate(_iter_name(root, TEXT_TAGS), 1)
        }
        if _local_name(root.tag) in {"sec", "section"}:
            page_count += 1
        for page_pr in _iter_name(root, {"pagepr"}):
            margin = next((child for child in page_pr if _local_name(child.tag) == "margin"), None)
            page_styles.append(
                {
                    "member": member,
                    "width_hwpunits": _attr(page_pr, "width"),
                    "height_hwpunits": _attr(page_pr, "height"),
                    "orientation": _attr(page_pr, "landscape"),
                    "margin_left_hwpunits": _attr(margin, "left") if margin is not None else "",
                    "margin_right_hwpunits": _attr(margin, "right") if margin is not None else "",
                    "margin_top_hwpunits": _attr(margin, "top") if margin is not None else "",
                    "margin_bottom_hwpunits": _attr(margin, "bottom") if margin is not None else "",
                    "header_hwpunits": _attr(margin, "header") if margin is not None else "",
                    "footer_hwpunits": _attr(margin, "footer") if margin is not None else "",
                }
            )
        for column_pr in _iter_name(root, {"colpr"}):
            column_styles.append(
                {
                    "member": member,
                    "count": _attr(column_pr, "colCount", "count"),
                    "gutter_hwpunits": _attr(column_pr, "sameGap", "gap"),
                    "equal_width": _attr(column_pr, "sameSz", "equalWidth"),
                }
            )
        for field in _iter_name(root, {"fieldbegin", "field_begin"}):
            name = _attr(field, "name", "fieldName")
            match = ITEM_ID_RE.search(name)
            if match:
                all_item_ids.append(match.group(1))
        for node in root.iter():
            lname = _local_name(node.tag)
            if lname in {"endnote", "end_note"}:
                endnote_count += 1
                note_text = _element_text(node)
                match = ITEM_ID_RE.search(note_text)
                item_id = match.group(1) if match else ""
                if item_id:
                    all_item_ids.append(item_id)
                scripts: list[str] = []
                for equation in _iter_name(node, {EQUATION_TAG}):
                    script = ""
                    scripts_nodes = list(_iter_name(equation, {"script"}))
                    if scripts_nodes:
                        script = "".join(scripts_nodes[0].itertext())
                    elif _attr(equation, "script"):
                        script = _attr(equation, "script")
                    scripts.append(script)
                    equation_scripts.append(script)
                table_rows: list[list[str]] = []
                for table in _iter_name(node, TABLE_TAGS):
                    cells: list[str] = []
                    for cell in _iter_name(table, CELL_TAGS):
                        cells.append(_normal_text(_element_text(cell, include_scripts=False)))
                    if cells:
                        table_rows.append(cells)
                        tables.append({"owner_item_id": item_id, "cells": cells})
                pictures: list[dict[str, Any]] = []
                for picture in _iter_name(node, PICTURE_TAGS):
                    for image in _iter_name(picture, IMAGE_TAGS):
                        ref = _attr(image, "binaryItemIDRef", "binaryItemIdRef", "ref", "src", "name")
                        if ref:
                            resource_name, digest = _image_sha(resources, ref)
                            picture_row = {"id": ref, "resource": resource_name, "sha256": digest}
                            pictures.append(picture_row)
                            image_rows.append({"owner_item_id": item_id, **picture_row})
                if item_id or scripts or table_rows or pictures:
                    items.append({"item_id": item_id, "equation_scripts": scripts, "tables": [{"cells": row} for row in table_rows], "images": pictures})
            elif lname in {"autonum", "auto_num"} and _attr(node, "numType", "numtype").upper() == "ENDNOTE":
                autonum_count += 1
            elif lname in {"p", "paragraph"}:
                # Endnotes, tables and text boxes contain nested paragraphs.
                # Only leaf paragraphs represent actual flow positions; an
                # enclosing paragraph would otherwise duplicate both blank
                # and zero-width observations from all descendants.
                if any(
                    descendant is not node
                    and _local_name(descendant.tag) in {"p", "paragraph"}
                    for descendant in node.iter()
                ):
                    continue
                text = _normal_text(_element_text(node, include_scripts=False))
                semantic_children = [
                    child for child in node.iter()
                    if child is not node and _local_name(child.tag) in ({EQUATION_TAG} | TABLE_TAGS | PICTURE_TAGS | IMAGE_TAGS)
                ]
                if not text and not semantic_children:
                    blank_paragraphs.append(
                        f"{member}:paragraph[{paragraph_positions.get(id(node), 0)}]"
                    )
                raw = "".join(node.itertext())
                if ZERO_WIDTH_RE.search(raw):
                    zero_width_locations.append(
                        f"{member}:paragraph[{paragraph_positions.get(id(node), 0)}]"
                    )
        for equation in _iter_name(root, {EQUATION_TAG}):
            pos = next((child for child in equation if _local_name(child.tag) == "pos"), None)
            size = next((child for child in equation if _local_name(child.tag) == "sz"), None)
            script = _equation_script(equation)
            document_equation_scripts.append(script)
            equation_style.append({
                "member": member,
                "font": _attr(equation, "font"),
                "base_unit": _attr(equation, "baseUnit", "base_unit"),
                "line_mode": _attr(equation, "lineMode", "line_mode"),
                "treat_as_char": _attr(pos, "treatAsChar", "treat_as_char") if pos is not None else "",
                "flow_with_text": _attr(pos, "flowWithText", "flow_with_text") if pos is not None else "",
                "allow_overlap": _attr(pos, "allowOverlap", "allow_overlap") if pos is not None else "",
                "width": _attr(size, "width") if size is not None else "",
                "height": _attr(size, "height") if size is not None else "",
                "script": script,
            })
        # The equation injector may add a run-local U+200B carrier directly
        # after an equation.  It is valid only in that exact relationship and
        # only when the carrier has no visible characters.  All other
        # zero-width text is spacing corruption and remains a FAIL finding.
        for parent in root.iter():
            children = list(parent)
            for child_index, child in enumerate(children):
                if _local_name(child.tag) not in TEXT_TAGS:
                    continue
                raw = "".join(child.itertext())
                if not raw or not ZERO_WIDTH_RE.search(raw):
                    continue
                # The injector's structural carrier can share an hp:t with
                # the ordinary continuation text.  Only a leading, contiguous
                # U+200B prefix immediately following the equation is the
                # carrier; embedded/trailing zero-width characters remain a
                # spacing defect.  The visible tail is semantic text, not part
                # of the guard.
                match = re.match(r"^(\u200b+)(.*)$", raw, flags=re.DOTALL)
                previous = children[child_index - 1] if child_index else None
                if (
                    match is None
                    or ZERO_WIDTH_RE.search(match.group(2))
                    or previous is None
                    or _local_name(previous.tag) != EQUATION_TAG
                ):
                    zero_width_strays.append(
                        f"{member}:text[{text_positions.get(id(child), 0)}]"
                    )
                    continue
                guard_text = match.group(1)
                zero_width_guards.append({
                    "member": member,
                    "xml_location": f"{member}:text[{text_positions.get(id(child), 0)}]",
                    "equation_index": len(zero_width_guards) + 1,
                    "length": len(guard_text),
                    "adjacent": True,
                    "visible_text": False,
                    "host_visible_text": bool(match.group(2)),
                    "equation_script_sha256": hashlib.sha256(_equation_script(previous).encode("utf-8")).hexdigest(),
                })
        for node in root.iter():
            if _local_name(node.tag) == "t":
                raw = "".join(node.itertext())
                if ZERO_WIDTH_RE.search(raw):
                    zero_width_locations.append(
                        f"{member}:text[{text_positions.get(id(node), 0)}]"
                    )
        # Pictures may be placed in the main problem body, headers, or
        # endnotes.  The item loop above records note-owned pictures; this
        # second pass records every remaining XML reference so an image SHA
        # change cannot hide outside an endnote.
        known_picture_keys = {(str(row.get("owner_item_id", "")), str(row.get("id", ""))) for row in image_rows}
        known_picture_refs = {str(row.get("id", "")) for row in image_rows}
        for picture in _iter_name(root, PICTURE_TAGS):
            for image in _iter_name(picture, IMAGE_TAGS):
                ref = _attr(image, "binaryItemIDRef", "binaryItemIdRef", "ref", "src", "name")
                if not ref:
                    continue
                key = ("", ref)
                # The same BinData can be referenced by a note-owned picture
                # and by an enclosing XML wrapper.  It is one content asset,
                # so do not hash it twice at document scope.
                if ref in known_picture_refs:
                    continue
                resource_name, digest = _image_sha(resources, ref)
                image_rows.append({"owner_item_id": "", "id": ref, "resource": resource_name, "sha256": digest, "xml_member": member})
                known_picture_keys.add(key)
                known_picture_refs.add(ref)
    # A document can have item-ID fields in its problem anchors/endnote
    # headers while the hp:endNote element itself carries no ID attribute.
    # When the counts match, bind unlabelled endnotes to those unique IDs in
    # document order.  This is fail-closed: a count mismatch leaves the rows
    # unlabelled so content_manifest reports an invalid inventory.
    unique_ids = list(dict.fromkeys(all_item_ids))
    seen = {str(item.get("item_id", "")) for item in items if item.get("item_id")}
    missing_ids = [item_id for item_id in unique_ids if item_id not in seen]
    unlabelled = [item for item in items if not item.get("item_id")]
    if unlabelled and len(unlabelled) == len(missing_ids):
        for item, item_id in zip(unlabelled, missing_ids):
            item["item_id"] = item_id
        for table in tables:
            if not table.get("owner_item_id"):
                # Tables are already emitted in endnote order.  Ownership is
                # restored below from each item's table rows.
                continue
        for item in items:
            item_id = str(item.get("item_id", ""))
            for picture in item.get("images", []):
                ref = str(picture.get("id", ""))
                for row in image_rows:
                    if not row.get("owner_item_id") and str(row.get("id", "")) == ref:
                        row["owner_item_id"] = item_id
                        break
    # Rebuild table ownership from the canonical item rows.  It is not used
    # to infer IDs, only to prevent an otherwise-correct positional mapping
    # from producing orphan table records.
    table_cursor = 0
    for item in items:
        for _ in item.get("tables", []):
            if table_cursor < len(tables) and not tables[table_cursor].get("owner_item_id"):
                tables[table_cursor]["owner_item_id"] = str(item.get("item_id", ""))
            table_cursor += 1
    return {
        "path": str(path),
        "format": "HWPX",
        "page_count": page_count,
        "endnote_count": endnote_count,
        "autonum_count": autonum_count,
        "item_ids": unique_ids,
        "items": items,
        "tables": tables,
        "images": image_rows,
        "equation_scripts": equation_scripts,
        "document_equation_scripts": document_equation_scripts,
        "equation_style": equation_style,
        "image_resources": [{"resource": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(resources.items())],
        "blank_paragraphs": blank_paragraphs,
        "zero_width_locations": sorted(set(zero_width_locations)),
        "zero_width_guards": zero_width_guards,
        "zero_width_strays": sorted(set(zero_width_strays)),
        "page_styles": page_styles,
        "column_styles": column_styles,
    }


def load_inventory(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    path = Path(value)
    if path.suffix.lower() == ".hwpx":
        return _hwpx_inventory(path)
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StyleQAError(f"invalid inventory JSON: {path}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise StyleQAError("inventory JSON must be an object")
        return dict(data)
    raise StyleQAError("binary HWP requires an adapter-provided inventory mapping or JSON sidecar")


def _as_profile(value: StyleProfile | str | Path | Mapping[str, Any]) -> StyleProfile:
    if isinstance(value, StyleProfile):
        return value
    if isinstance(value, Mapping):
        return StyleProfile.from_mapping(value)
    return StyleProfile.from_file(value)


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _compare_style_values(expected: Any, actual: Any, path: str, profile: StyleProfile, findings: list[dict[str, Any]]) -> None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            findings.append({"code": "STYLE_MISSING", "path": path})
            return
        for key, value in expected.items():
            if key not in actual:
                findings.append({"code": "STYLE_PROPERTY_MISSING", "path": f"{path}.{key}"})
            else:
                _compare_style_values(value, actual[key], f"{path}.{key}", profile, findings)
        return
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            actual_number = float(actual)
            if path.endswith("_mm") or "geometry" in path:
                tolerance = profile.tolerances["geometry_mm"]
            elif any(token in path.lower() for token in ("font", "size", "leading", "spacing")):
                # Typography is an exact reference contract.  The profile's
                # typography tolerance remains available for non-typographic
                # scalar metadata, but font/size/leading values do not drift.
                tolerance = 0.0
            else:
                tolerance = profile.tolerances["typography_pt"]
            if abs(float(expected) - actual_number) > tolerance:
                findings.append({"code": "STYLE_VALUE_MISMATCH", "path": path, "expected": expected, "actual": actual, "tolerance": tolerance})
        except (TypeError, ValueError):
            findings.append({"code": "STYLE_VALUE_MISMATCH", "path": path, "expected": expected, "actual": actual})
        return
    if expected != actual:
        findings.append({"code": "STYLE_VALUE_MISMATCH", "path": path, "expected": expected, "actual": actual})


def _equation_style_findings(inventory: Mapping[str, Any], profile: StyleProfile) -> list[dict[str, Any]]:
    """Check native equation presentation directly from HWPX XML.

    ``style_snapshot`` is still required for the seven broad style families,
    but it must not be possible to hide a wrong equation font or inline flag
    by supplying a forged snapshot.  Profiles may choose either HYhwpEQ or
    HancomEQN; the value is read from the profile and is never hard-coded.
    """

    findings: list[dict[str, Any]] = []
    rows = inventory.get("equation_style", [])
    if not isinstance(rows, list):
        return [{"code": "EQUATION_STYLE_INVENTORY_INVALID"}]
    expected_scripts = inventory.get(
        "document_equation_scripts",
        inventory.get("equation_scripts", []),
    )
    if not isinstance(expected_scripts, list):
        expected_scripts = []
    if not expected_scripts:
        expected_scripts = [
            script
            for item in _rows(inventory.get("items"))
            for script in item.get("equation_scripts", [])
            if isinstance(item.get("equation_scripts", []), list)
        ]
    if len(rows) != len(expected_scripts):
        findings.append({"code": "EQUATION_STYLE_COUNT_MISMATCH", "expected": len(expected_scripts), "actual": len(rows)})
    equation_settings = profile.settings.get("char", {}).get("equation", {})
    expected_font = equation_settings.get("font_family")
    expected_base = equation_settings.get("base_unit")
    expected_flags = {
        "treat_as_char": str(equation_settings.get("treat_as_char", "1")),
        "flow_with_text": str(equation_settings.get("flow_with_text", "1")),
        "allow_overlap": str(equation_settings.get("allow_overlap", "0")),
    }
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            findings.append({"code": "EQUATION_STYLE_INVENTORY_INVALID", "index": index})
            continue
        script = str(row.get("script", ""))
        if not script:
            findings.append({"code": "EQUATION_SCRIPT_EMPTY", "index": index, "member": row.get("member")})
        if expected_font is not None and row.get("font") != expected_font:
            findings.append({"code": "EQUATION_FONT_MISMATCH", "index": index, "expected": expected_font, "actual": row.get("font")})
        if expected_base is not None:
            try:
                base_matches = int(row.get("base_unit", "")) == int(expected_base)
            except (TypeError, ValueError):
                base_matches = False
            if not base_matches:
                findings.append({"code": "EQUATION_BASE_UNIT_MISMATCH", "index": index, "expected": expected_base, "actual": row.get("base_unit")})
        for key, expected in expected_flags.items():
            if row.get(key) != expected:
                findings.append({"code": "EQUATION_INLINE_FLAG_MISMATCH", "index": index, "property": key, "expected": expected, "actual": row.get(key)})
    return findings


def _native_page_column_findings(
    inventory: Mapping[str, Any],
    profile: StyleProfile,
    application: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Read back physical page/column geometry from the generated HWPX.

    The measured default is two columns, while the same reference document
    also contains a one-column long-form variant.  A variant is accepted only
    when it is explicitly named in the application record and its count/gap
    pair exactly matches ``styles.column.verified_variants``.
    """

    findings: list[dict[str, Any]] = []
    page_rows = _rows(inventory.get("page_styles"))
    column_rows = _rows(inventory.get("column_styles"))
    if not page_rows:
        findings.append({"code": "PAGE_STYLE_INVENTORY_MISSING"})
    if not column_rows:
        findings.append({"code": "COLUMN_STYLE_INVENTORY_MISSING"})
    page = profile.settings.get("page", {})
    expected_page = {
        "width_hwpunits": page.get("width_hwpunits", round(float(page.get("width_mm", 0)) * 7200 / 25.4)),
        "height_hwpunits": page.get("height_hwpunits", round(float(page.get("height_mm", 0)) * 7200 / 25.4)),
        "margin_left_hwpunits": page.get("margin_left_hwpunits", round(float(page.get("margin_left_mm", 0)) * 7200 / 25.4)),
        "margin_right_hwpunits": page.get("margin_right_hwpunits", round(float(page.get("margin_right_mm", 0)) * 7200 / 25.4)),
        "margin_top_hwpunits": page.get("margin_top_hwpunits", round(float(page.get("margin_top_mm", 0)) * 7200 / 25.4)),
        "margin_bottom_hwpunits": page.get("margin_bottom_hwpunits", round(float(page.get("margin_bottom_mm", 0)) * 7200 / 25.4)),
        "header_hwpunits": page.get("header_hwpunits", round(float(page.get("header_distance_mm", 0)) * 7200 / 25.4)),
        "footer_hwpunits": page.get("footer_hwpunits", round(float(page.get("footer_distance_mm", 0)) * 7200 / 25.4)),
    }
    for index, row in enumerate(page_rows, 1):
        for key, expected in expected_page.items():
            try:
                actual = int(row.get(key, ""))
                matches = actual == int(expected)
            except (TypeError, ValueError):
                actual = row.get(key)
                matches = False
            if not matches:
                findings.append({"code": "NATIVE_PAGE_STYLE_MISMATCH", "index": index, "property": key, "expected": expected, "actual": actual})
    column = profile.settings.get("column", {})
    variants = column.get("verified_variants", {})
    if not isinstance(variants, Mapping):
        variants = {}
    selected = str(application.get("column_variant", "reference_default_two_column"))
    selected_style = variants.get(selected)
    if not isinstance(selected_style, Mapping):
        if selected == "reference_default_two_column":
            selected_style = {
                "count": column.get("count"),
                "gutter_hwpunits": column.get(
                    "gutter_hwpunits",
                    round(float(column.get("gutter_mm", 0)) * 7200 / 25.4),
                ),
            }
        else:
            findings.append({"code": "COLUMN_VARIANT_NOT_VERIFIED", "variant": selected})
            selected_style = {}
    for index, row in enumerate(column_rows, 1):
        for key in ("count", "gutter_hwpunits"):
            try:
                actual = int(row.get(key, ""))
                expected = int(selected_style.get(key, ""))
                matches = actual == expected
            except (TypeError, ValueError):
                actual = row.get(key)
                expected = selected_style.get(key)
                matches = False
            if not matches:
                findings.append({"code": "NATIVE_COLUMN_STYLE_MISMATCH", "index": index, "variant": selected, "property": key, "expected": expected, "actual": actual})
    return findings


def _rect(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, Mapping):
        value = value.get("rect_mm", value.get("bbox_mm", value.get("rect")))
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(number) for number in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    return _area((max(first[0], second[0]), max(first[1], second[1]), min(first[2], second[2]), min(first[3], second[3])))


def _objects(inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page_index, page in enumerate(_rows(inventory.get("pages")), 1):
        for index, row in enumerate(_rows(page.get("objects")), 1):
            value = dict(row)
            value.setdefault("page", page.get("page", page_index))
            value.setdefault("object_id", f"page-{value['page']}-object-{index}")
            result.append(value)
    for index, row in enumerate(_rows(inventory.get("objects")), 1):
        value = dict(row)
        value.setdefault("object_id", f"object-{index}")
        result.append(value)
    return result


def _layout_audit(expected: Mapping[str, Any], actual: Mapping[str, Any], profile: StyleProfile) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    expected_objects = {str(row.get("object_id", row.get("id", ""))): row for row in _objects(expected)}
    actual_objects = {str(row.get("object_id", row.get("id", ""))): row for row in _objects(actual)}
    if expected_objects and set(expected_objects) != set(actual_objects):
        findings.append({"code": "OBJECT_ID_SET_MISMATCH", "missing": sorted(set(expected_objects) - set(actual_objects)), "unexpected": sorted(set(actual_objects) - set(expected_objects))})
    for object_id in sorted(set(expected_objects) & set(actual_objects)):
        before = _rect(expected_objects[object_id])
        after = _rect(actual_objects[object_id])
        if before is None or after is None:
            findings.append({"code": "OBJECT_GEOMETRY_MISSING", "object_id": object_id})
            continue
        if any(abs(left - right) > profile.tolerances["geometry_mm"] for left, right in zip(before, after)):
            findings.append({"code": "OBJECT_GEOMETRY_MISMATCH", "object_id": object_id, "expected": before, "actual": after, "tolerance_mm": profile.tolerances["geometry_mm"]})
    pages = _rows(actual.get("pages"))
    known_items = {str(row.get("item_id")) for row in _rows(actual.get("items")) if row.get("item_id")}
    actual_objects_list = _objects(actual)
    for row in actual_objects_list:
        owner = str(row.get("owner_item_id", ""))
        if row.get("kind") in {"item", "paragraph", "equation", "table", "figure", "endnote"} and (not owner or owner not in known_items):
            findings.append({"code": "ORPHAN_OBJECT", "object_id": row.get("object_id"), "owner_item_id": owner})
    for page in pages:
        page_number = page.get("page", pages.index(page) + 1)
        page_width = float(page.get("width_mm", page.get("page_width_mm", 210.0)))
        page_height = float(page.get("height_mm", page.get("page_height_mm", 297.0)))
        page_rect = (0.0, 0.0, page_width, page_height)
        page_objects = [row for row in actual_objects_list if row.get("page") == page_number]
        for row in page_objects:
            rect = _rect(row)
            if rect is None:
                continue
            if rect[0] < -profile.tolerances["geometry_mm"] or rect[1] < -profile.tolerances["geometry_mm"] or rect[2] > page_width + profile.tolerances["geometry_mm"] or rect[3] > page_height + profile.tolerances["geometry_mm"]:
                findings.append({"code": "OBJECT_CLIPPED", "page": page_number, "object_id": row.get("object_id"), "rect_mm": rect})
        for index, first in enumerate(page_objects):
            first_rect = _rect(first)
            if first_rect is None:
                continue
            for second in page_objects[index + 1:]:
                second_rect = _rect(second)
                if second_rect is None:
                    continue
                overlap = _intersection(first_rect, second_rect)
                smaller = min(_area(first_rect), _area(second_rect))
                pair = {str(first.get("kind", "")), str(second.get("kind", ""))}
                if smaller > 0 and overlap / smaller > profile.overlap_threshold and pair not in ({"table", "table_cell"}, {"figure", "figure_label"}):
                    findings.append({"code": "OBJECT_OVERLAP", "page": page_number, "first": first.get("object_id"), "second": second.get("object_id"), "ratio": round(overlap / smaller, 6)})
        # Large gaps are evaluated only within an explicit owner/flow group;
        # unrelated headers and figures are not treated as accidental gaps.
        groups: dict[str, list[tuple[float, float, str]]] = {}
        for row in page_objects:
            rect = _rect(row)
            owner = str(row.get("owner_item_id", ""))
            if rect is not None and owner:
                groups.setdefault(owner, []).append((rect[1], rect[3], str(row.get("object_id"))))
        for owner, group in groups.items():
            group.sort()
            for (_, bottom, first_id), (top, _, second_id) in zip(group, group[1:]):
                if top - bottom > profile.large_gap_mm:
                    findings.append({"code": "LARGE_VERTICAL_GAP", "page": page_number, "owner_item_id": owner, "first": first_id, "second": second_id, "gap_mm": round(top - bottom, 4)})
    density_values: list[float] = []
    for page in pages:
        if page.get("density") is not None:
            density_values.append(float(page["density"]))
            continue
        width = float(page.get("width_mm", page.get("page_width_mm", 210.0)))
        height = float(page.get("height_mm", page.get("page_height_mm", 297.0)))
        total = sum(_area(rect) for row in _rows(page.get("objects")) if (rect := _rect(row)) is not None)
        density_values.append(min(1.0, total / max(width * height, 1.0)))
    if not density_values:
        density_values = [0.0]
    density = {"min": min(density_values), "median": statistics.median(density_values), "max": max(density_values), "count": len(density_values)}
    density_gates = {
        "density_min": density["min"] >= profile.density["min"],
        "density_median": profile.density["median_min"] <= density["median"] <= profile.density["median_max"],
        "density_max": density["max"] <= profile.density["max"],
    }
    for key, passed in density_gates.items():
        if not passed:
            findings.append({"code": "DENSITY_OUT_OF_RANGE", "gate": key, "density": density, "thresholds": dict(profile.density)})
    return {"status": "PASS" if not findings and all(value is True for value in density_gates.values()) else "FAIL", "passed": not findings and all(value is True for value in density_gates.values()), "density": density, "density_gates": {key: value is True for key, value in density_gates.items()}, "findings": findings}


def audit_style(
    source: str | Path | Mapping[str, Any],
    generated: str | Path | Mapping[str, Any],
    profile: StyleProfile | str | Path | Mapping[str, Any],
    *,
    style_snapshot: Mapping[str, Any] | None = None,
    layout_snapshot: Mapping[str, Any] | None = None,
    style_application: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed report for one source/generated pair."""

    try:
        profile_obj = _as_profile(profile)
        expected = load_inventory(source)
        actual = load_inventory(generated)
    except (StyleQAError, StyleProfileError, OSError, ValueError) as exc:
        return {"schema_version": QA_SCHEMA_VERSION, "status": "FAIL", "passed": False, "gates": {"inputs_valid": False}, "findings": [{"code": "INVALID_INPUT", "message": str(exc)}]}
    findings: list[dict[str, Any]] = []
    profile_preflight = preflight_reference_style(profile_obj)
    if profile_preflight.get("passed") is not True:
        findings.extend(
            {"code": "PROFILE_PREFLIGHT_FAILED", "message": message}
            for message in profile_preflight.get("errors", [])
        )
    before_content = content_manifest(expected)
    after_content = content_manifest(actual)
    preservation = preserved_content_gate(before_content, after_content)
    if before_content.get("errors") or after_content.get("errors"):
        findings.extend({"code": "CONTENT_INVENTORY_INVALID", "message": message} for message in before_content.get("errors", []) + after_content.get("errors", []))
    if preservation.get("passed") is not True:
        findings.extend({"code": "CONTENT_HASH_MISMATCH", "field": field} for field in preservation.get("mismatches", []))
    expected_ids = [str(value) for value in expected.get("item_ids", before_content.get("item_ids", []))]
    actual_ids = [str(value) for value in actual.get("item_ids", after_content.get("item_ids", []))]
    if expected_ids != actual_ids:
        findings.append({"code": "ITEM_ID_SET_OR_ORDER_MISMATCH", "expected": expected_ids, "actual": actual_ids})
    if int(expected.get("endnote_count", 0)) != int(actual.get("endnote_count", 0)):
        findings.append({"code": "ENDNOTE_COUNT_MISMATCH", "expected": expected.get("endnote_count", 0), "actual": actual.get("endnote_count", 0)})
    expected_blank_paragraphs = list(expected.get("blank_paragraphs", []))
    actual_blank_paragraphs = list(actual.get("blank_paragraphs", []))
    blank_paragraphs_preserved = expected_blank_paragraphs == actual_blank_paragraphs
    if not blank_paragraphs_preserved:
        findings.append(
            {
                "code": "BLANK_PARAGRAPH_CHANGED",
                "expected": expected_blank_paragraphs,
                "actual": actual_blank_paragraphs,
            }
        )
    _, zero_width_errors = _equation_offset_guards(actual)
    for message in zero_width_errors:
        findings.append({"code": "ZERO_WIDTH_SPACING", "message": message, "locations": actual.get("zero_width_locations", [])})
    raw_snapshot = style_snapshot or actual.get("style_snapshot", actual.get("styles", {}))
    snapshot = dict(raw_snapshot) if isinstance(raw_snapshot, Mapping) else {}
    application = style_application or actual.get("style_application", {})
    if not application and isinstance(snapshot.get("style_application"), Mapping):
        application = snapshot.get("style_application", {})
    if isinstance(snapshot.get("styles"), Mapping):
        snapshot = dict(snapshot["styles"])
    if not isinstance(application, Mapping):
        application = {}
    # A style snapshot alone proves only that values were supplied to QA; it
    # does not prove that the builder actually applied them.  Production must
    # carry the signed application record produced by apply_reference_style.
    application_passed = application.get("status") == "PASS" and application.get("passed") is True and application.get("applied") is True
    application_profile_hash = str(application.get("profile_sha256", ""))
    if not application_passed:
        findings.append({"code": "STYLE_APPLICATION_NOT_PROVEN"})
    if application_profile_hash != profile_obj.profile_sha256:
        findings.append({"code": "STYLE_PROFILE_HASH_MISMATCH", "expected": profile_obj.profile_sha256, "actual": application_profile_hash or None})
    style_findings: list[dict[str, Any]] = []
    for kind in ("page", "column", "char", "para", "table", "figure", "endnote"):
        expected_style: Any = profile_obj.settings[kind]
        if kind == "column":
            selected_variant = str(application.get("column_variant", "reference_default_two_column"))
            variants = profile_obj.settings["column"].get("verified_variants", {})
            variant = variants.get(selected_variant) if isinstance(variants, Mapping) else None
            if isinstance(variant, Mapping):
                expected_style = dict(profile_obj.settings["column"])
                expected_style.update(dict(variant))
        _compare_style_values(expected_style, snapshot.get(kind), f"styles.{kind}", profile_obj, style_findings)
    findings.extend(style_findings)
    equation_findings = _equation_style_findings(actual, profile_obj)
    findings.extend(equation_findings)
    native_geometry_findings = _native_page_column_findings(actual, profile_obj, application)
    findings.extend(native_geometry_findings)
    layout_actual = dict(actual)
    if layout_snapshot is not None:
        layout_actual.update(dict(layout_snapshot))
    layout = _layout_audit(expected, layout_actual, profile_obj)
    findings.extend(layout.get("findings", []))
    gates = {
        "inputs_valid": True,
        "profile_preflight_passed": profile_preflight.get("passed") is True,
        "style_application_recorded": application_passed,
        "profile_hash_match": application_profile_hash == profile_obj.profile_sha256,
        "item_ids_preserved": preservation["gates"].get("item_ids_preserved") is True and expected_ids == actual_ids,
        "native_endnotes_preserved": (
            expected.get("endnote_count", 0) == actual.get("endnote_count", 0)
            and int(expected.get("autonum_count", expected.get("endnote_count", 0))) == int(actual.get("autonum_count", actual.get("endnote_count", 0)))
            and int(actual.get("autonum_count", actual.get("endnote_count", 0))) == int(actual.get("endnote_count", 0))
        ),
        "equation_scripts_preserved": preservation["gates"].get("equation_scripts_preserved") is True,
        "table_cells_preserved": preservation["gates"].get("table_cells_preserved") is True,
        "image_sha256_preserved": preservation["gates"].get("image_sha256_preserved") is True,
        "styles_exact_or_within_tolerance": not style_findings,
        "native_equation_style_exact": not equation_findings,
        "native_page_column_style_exact": not native_geometry_findings,
        "no_blank_or_zero_width_spacing": blank_paragraphs_preserved and not zero_width_errors,
        "orphan_objects_absent": not any(row.get("code") == "ORPHAN_OBJECT" for row in layout.get("findings", [])),
        "overlap_absent": not any(row.get("code") == "OBJECT_OVERLAP" for row in layout.get("findings", [])),
        "clipping_absent": not any(row.get("code") == "OBJECT_CLIPPED" for row in layout.get("findings", [])),
        "large_gaps_absent": not any(row.get("code") == "LARGE_VERTICAL_GAP" for row in layout.get("findings", [])),
        "density_in_range": all(value is True for value in layout.get("density_gates", {}).values()),
    }
    # A profile marked as a draft may be used for analysis, but never receives
    # a misleading PASS from incomplete style snapshots.
    if not profile_obj.measured:
        gates["measured_reference_profile"] = False
        findings.append({"code": "REFERENCE_PROFILE_NOT_MEASURED", "status": profile_obj.status})
    else:
        gates["measured_reference_profile"] = True
    gates = {key: value is True for key, value in gates.items()}
    passed = all(value is True for value in gates.values()) and not findings
    return {
        "schema_version": QA_SCHEMA_VERSION,
        "profile_id": profile_obj.profile_id,
        "profile_sha256": profile_obj.profile_sha256,
        "profile_preflight": profile_preflight,
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "gates": gates,
        "findings": findings,
        "preservation": preservation,
        "layout": layout,
        "counts": {
            "source_items": len(expected_ids),
            "generated_items": len(actual_ids),
            "source_endnotes": expected.get("endnote_count", 0),
            "generated_endnotes": actual.get("endnote_count", 0),
            "source_equations": len(before_content.get("document_equation_scripts", [])) or sum(len(row.get("equation_scripts", [])) for row in before_content.get("items", [])),
            "generated_equations": len(after_content.get("document_equation_scripts", [])) or sum(len(row.get("equation_scripts", [])) for row in after_content.get("items", [])),
        },
        "content_hashes": {"source": before_content.get("content_sha256"), "generated": after_content.get("content_sha256")},
        "style_application": dict(application),
    }


def assert_style_pass(*args: Any, **kwargs: Any) -> dict[str, Any]:
    report = audit_style(*args, **kwargs)
    if report.get("status") != "PASS":
        raise StyleQAError(json.dumps(report, ensure_ascii=False))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strict reference-style QA for mathematical HWP/HWPX")
    parser.add_argument("source", type=Path)
    parser.add_argument("generated", type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--style-snapshot", type=Path)
    parser.add_argument("--layout-snapshot", type=Path)
    parser.add_argument("--style-application", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    snapshot = None
    if args.style_snapshot:
        snapshot = load_inventory(args.style_snapshot)
    layout_snapshot = None
    if args.layout_snapshot:
        layout_snapshot = load_inventory(args.layout_snapshot)
    style_application = None
    if args.style_application:
        style_application = load_inventory(args.style_application)
    report = audit_style(args.source, args.generated, args.profile, style_snapshot=snapshot, layout_snapshot=layout_snapshot, style_application=style_application)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report.get("status") == "PASS" else 1


__all__ = ["QA_SCHEMA_VERSION", "StyleQAError", "audit_style", "assert_style_pass", "load_inventory"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
