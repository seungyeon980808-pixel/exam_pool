"""Reference-style contracts for editable mathematical HWP/HWPX documents.

The module is intentionally independent of Hanword/pyhwpx.  It provides a
validated JSON profile, a small adapter protocol for applying Page/Column/
Char/Para/Table/Figure/Endnote styles, and content-hash helpers used by the
strict QA module.  A real Hanword adapter can implement the protocol without
changing the profile or QA rules; synthetic tests use a recording adapter.

The style operation is content-preserving by contract.  It may change
presentation properties, but it must not change item IDs, native equation
scripts, native table-cell text, or image SHA-256 values.  The caller must
capture a native inventory before and after applying the profile and use
``preserved_content_gate`` before accepting a result.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Protocol, Sequence


SCHEMA_VERSION = "math-hwp-reference-style-v1"
STYLE_KINDS = ("page", "column", "char", "para", "table", "figure", "endnote")
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")


class StyleProfileError(ValueError):
    """Raised when a style profile is structurally unsafe."""


class StyleApplicationError(RuntimeError):
    """Raised when a style adapter cannot apply a required style family."""


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StyleProfileError("non-finite numeric profile value")
        return round(value, 6)
    return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _as_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StyleProfileError(f"{name} must be an object")
    return {str(key): value[key] for key in value}


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise StyleProfileError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StyleProfileError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise StyleProfileError(f"invalid {name}: {value!r}")
    return number


@dataclass(frozen=True)
class StyleProfile:
    """Validated profile plus the original normalized settings."""

    profile_id: str
    status: str
    measured: bool
    settings: Mapping[str, Mapping[str, Any]]
    tolerances: Mapping[str, float]
    density: Mapping[str, float]
    overlap_threshold: float
    large_gap_mm: float
    raw: Mapping[str, Any]

    @property
    def profile_sha256(self) -> str:
        """Return the immutable hash of the canonical profile document.

        The hash is deliberately calculated from the normalized profile data,
        rather than from the JSON file's whitespace or key order.  Production
        orchestration records this value before applying styles and requires
        the same value in the application and QA reports.
        """

        payload = {key: value for key, value in self.raw.items() if key != "profile_sha256"}
        return canonical_sha256(payload)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StyleProfile":
        data = _as_mapping(value, "profile")
        schema = str(data.get("schema_version", ""))
        if schema != SCHEMA_VERSION:
            raise StyleProfileError(f"schema_version must be {SCHEMA_VERSION!r}")
        profile_id = str(data.get("profile_id") or data.get("name") or "").strip()
        if not profile_id:
            raise StyleProfileError("profile_id is required")
        status = str(data.get("status", "DRAFT"))
        measured_raw = data.get("measured", False)
        if not isinstance(measured_raw, bool):
            raise StyleProfileError("measured must be a boolean")
        measured = measured_raw
        styles = data.get("styles", data.get("style", {}))
        styles_map = _as_mapping(styles, "styles")
        missing = [kind for kind in STYLE_KINDS if kind not in styles_map]
        if missing:
            raise StyleProfileError(f"styles missing required families: {', '.join(missing)}")
        normalized_styles: dict[str, Mapping[str, Any]] = {}
        for kind in STYLE_KINDS:
            normalized_styles[kind] = _as_mapping(styles_map[kind], f"styles.{kind}")
        tolerance_data = _as_mapping(data.get("tolerances", {}), "tolerances")
        tolerances = {
            "geometry_mm": _number(tolerance_data.get("geometry_mm", 0.5), "tolerances.geometry_mm", minimum=0),
            "typography_pt": _number(tolerance_data.get("typography_pt", 0.1), "tolerances.typography_pt", minimum=0),
        }
        if tolerances["geometry_mm"] > 0.5:
            raise StyleProfileError("geometry_mm tolerance may not exceed 0.5")
        density_data = _as_mapping(data.get("density", {}), "density")
        density: dict[str, float] = {}
        for key in ("min", "median_min", "median_max", "max"):
            density[key] = _number(density_data.get(key, 0.0 if key in {"min", "median_min"} else 1.0), f"density.{key}", minimum=0)
        if not (0 <= density["min"] <= density["median_min"] <= density["median_max"] <= density["max"] <= 1):
            raise StyleProfileError("density thresholds must satisfy 0 <= min <= median_min <= median_max <= max <= 1")
        overlap_threshold = _number(data.get("overlap_threshold", 0.0), "overlap_threshold", minimum=0)
        if overlap_threshold > 1:
            raise StyleProfileError("overlap_threshold must be between 0 and 1")
        large_gap_mm = _number(data.get("large_gap_mm", 18.0), "large_gap_mm", minimum=0)
        return cls(
            profile_id=profile_id,
            status=status,
            measured=measured,
            settings=normalized_styles,
            tolerances=tolerances,
            density=density,
            overlap_threshold=overlap_threshold,
            large_gap_mm=large_gap_mm,
            raw=_canonical(data),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "StyleProfile":
        profile_path = Path(path)
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StyleProfileError(f"cannot read profile {profile_path}: {exc}") from exc
        return cls.from_mapping(data)


def profile_sha256(value: "StyleProfile | Mapping[str, Any] | str | Path") -> str:
    """Return the canonical SHA-256 for a style profile.

    This helper is intentionally public so an orchestrator can pin the exact
    profile it preflighted without importing implementation details from the
    QA module.
    """

    profile = value if isinstance(value, StyleProfile) else (
        StyleProfile.from_mapping(value) if isinstance(value, Mapping) else StyleProfile.from_file(value)
    )
    return profile.profile_sha256


def preflight_reference_style(
    profile: "StyleProfile | Mapping[str, Any] | str | Path",
    *,
    require_measured: bool = True,
) -> dict[str, Any]:
    """Validate a profile before a builder or style adapter is invoked.

    Draft profiles are useful for analysis and schema work, but a production
    document must not be built from one.  The result uses literal boolean
    gates so callers can fail closed; no callback or builder should run when
    any gate is false.  ``require_measured=False`` is provided only for tools
    that inspect a draft and still report its provenance honestly.
    """

    try:
        profile_obj = profile if isinstance(profile, StyleProfile) else (
            StyleProfile.from_mapping(profile) if isinstance(profile, Mapping) else StyleProfile.from_file(profile)
        )
    except (OSError, StyleProfileError, TypeError, ValueError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "FAIL",
            "passed": False,
            "profile_sha256": None,
            "gates": {"profile_valid": False, "measured_reference_profile": False},
            "errors": [f"profile validation failed: {exc}"],
        }

    raw_status = str(profile_obj.raw.get("reference_measurement_status", "unknown")).lower()
    reference_source = profile_obj.raw.get("reference_source")
    provenance_declared = (
        isinstance(reference_source, str)
        and bool(reference_source.strip())
        and raw_status in {"measured", "verified", "complete"}
        and profile_obj.status.strip().upper() in {"VERIFIED", "COMPLETE"}
    )
    declared_hash = profile_obj.raw.get("profile_sha256")
    declared_hash_matches = declared_hash in (None, profile_obj.profile_sha256)
    gates = {
        "profile_valid": True,
        "schema_version": profile_obj.raw.get("schema_version") == SCHEMA_VERSION,
        "style_families_complete": all(
            kind in profile_obj.settings
            and isinstance(profile_obj.settings[kind], Mapping)
            and bool(profile_obj.settings[kind])
            for kind in STYLE_KINDS
        ),
        "geometry_tolerance_within_0_5mm": profile_obj.tolerances["geometry_mm"] <= 0.5,
        "profile_hash_present": bool(re.fullmatch(r"[0-9a-f]{64}", profile_obj.profile_sha256)),
        "declared_profile_hash_matches": declared_hash_matches,
        "measured_reference_profile": (profile_obj.measured and provenance_declared) if require_measured else True,
    }
    errors: list[str] = []
    if require_measured and not gates["measured_reference_profile"]:
        errors.append(
            "reference profile is not measured/verified; keep this profile in analysis only "
            f"(measured={profile_obj.measured!r}, reference_measurement_status={raw_status!r})"
        )
    if not declared_hash_matches:
        errors.append("declared profile_sha256 does not match the canonical profile payload")
    passed = all(value is True for value in gates.values()) and not errors
    return {
        "schema_version": SCHEMA_VERSION,
        "profile_id": profile_obj.profile_id,
        "profile_sha256": profile_obj.profile_sha256,
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "gates": {key: value is True for key, value in gates.items()},
        "errors": errors,
    }


class StyleDocumentAdapter(Protocol):
    """Minimal adapter used by :func:`apply_reference_style`.

    ``apply_style`` must mutate presentation only.  ``capture_content`` is
    optional at runtime but required for a PASS content-preservation gate.
    """

    def apply_style(self, kind: str, settings: Mapping[str, Any]) -> Any: ...

    def capture_content(self) -> Mapping[str, Any]: ...


def _item_id(value: Mapping[str, Any]) -> str:
    return str(value.get("item_id") or value.get("id") or "").strip()


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _inventory_sequence(value: Any, name: str, errors: list[str]) -> list[Any]:
    """Read an optional collection without silently dropping malformed data."""

    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    errors.append(f"{name} must be a list")
    return []


def content_manifest(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical hashes for content that formatting must preserve.

    The inventory is intentionally adapter-neutral.  Each item may expose
    ``equation_scripts``/``equations``, ``tables`` with ``cells``, and
    ``images``/``pictures`` with ``sha256``.  Missing collections are treated
    as empty, while malformed rows are retained in the error list rather than
    silently disappearing.
    """

    errors: list[str] = []
    items = inventory.get("items", [])
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise StyleProfileError("content inventory.items must be a list")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(items, 1):
        if not isinstance(raw, Mapping):
            errors.append(f"item[{index}] is not an object")
            continue
        item_id = _item_id(raw)
        if not item_id:
            errors.append(f"item[{index}] has no item_id")
        equations = _inventory_sequence(
            raw.get("equation_scripts", raw.get("equations", [])),
            f"{item_id}: equations",
            errors,
        )
        equation_scripts: list[str] = []
        for equation in equations:
            if isinstance(equation, Mapping):
                raw_script = equation.get("script", equation.get("source", ""))
            else:
                raw_script = equation
            script = "" if raw_script is None else str(raw_script)
            equation_scripts.append(script)
            if not script:
                errors.append(f"{item_id}: equation has empty script")
        tables_out: list[list[str]] = []
        for table in _inventory_sequence(raw.get("tables", []), f"{item_id}: tables", errors):
            if not isinstance(table, Mapping):
                errors.append(f"{item_id}: malformed table")
                continue
            cells = _inventory_sequence(table.get("cells", []), f"{item_id}: table cells", errors)
            cell_texts: list[str] = []
            for cell in cells:
                if isinstance(cell, Mapping):
                    cell_texts.append(str(cell.get("text", cell.get("value", ""))))
                else:
                    cell_texts.append(str(cell))
            tables_out.append(cell_texts)
        images_out: list[dict[str, str]] = []
        for picture in _inventory_sequence(
            raw.get("images", raw.get("pictures", [])),
            f"{item_id}: images",
            errors,
        ):
            if not isinstance(picture, Mapping):
                errors.append(f"{item_id}: malformed image")
                continue
            digest = str(picture.get("sha256", "")).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append(f"{item_id}: image missing valid sha256")
            images_out.append({"sha256": digest, "id": str(picture.get("id", picture.get("name", "")))})
        rows.append({"item_id": item_id, "equation_scripts": equation_scripts, "table_cells": tables_out, "images": images_out})
    item_ids = [row["item_id"] for row in rows]
    duplicates = sorted({item_id for item_id in item_ids if item_ids.count(item_id) > 1 and item_id})
    if duplicates:
        errors.append("duplicate item_id values: " + ", ".join(duplicates))
    # Images can live in headers, the main problem body, or other XML
    # members rather than inside an item/endnote.  Preserve a document-level
    # list as well as item-owned pictures so a style pass cannot silently
    # replace an unowned figure.  These rows are deliberately included in the
    # image hash but never inferred into an item ID.
    document_images: list[dict[str, str]] = []
    for index, picture in enumerate(_inventory_sequence(inventory.get("images", []), "document images", errors), 1):
        if not isinstance(picture, Mapping):
            errors.append(f"document image[{index}] is not an object")
            continue
        digest = str(picture.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"document image[{index}] missing valid sha256")
        document_images.append({"sha256": digest, "id": str(picture.get("id", picture.get("name", ""))), "owner_item_id": str(picture.get("owner_item_id", ""))})
    # Keep source order.  Sorting here would make the item-ID hash invariant
    # to a reorder, which would let a style pass silently change the order in
    # which problems/endnotes are presented.  Canonicalisation is still stable
    # for the values within each row, while the row sequence remains semantic.
    document_images.sort(key=lambda row: (row["owner_item_id"], row["id"], row["sha256"]))
    guards = _inventory_sequence(
        inventory.get("zero_width_guards", inventory.get("equation_offset_guards", [])),
        "equation offset guards",
        errors,
    )
    canonical_guards: list[dict[str, Any]] = []
    for index, guard in enumerate(guards, 1):
        if not isinstance(guard, Mapping):
            errors.append(f"equation offset guard[{index}] is not an object")
            continue
        canonical_guards.append({
            "member": str(guard.get("member", guard.get("xml_member", ""))),
            "xml_location": str(guard.get("xml_location", guard.get("location", ""))),
            "equation_index": int(guard.get("equation_index", index)) if str(guard.get("equation_index", index)).lstrip("-").isdigit() else str(guard.get("equation_index", index)),
            "length": int(guard.get("length", 0)) if str(guard.get("length", 0)).lstrip("-").isdigit() else 0,
            "adjacent": guard.get("adjacent") is True,
            "visible_text": guard.get("visible_text", False) is True,
            "character": str(guard.get("character", "U+200B")),
            "equation_script_sha256": str(guard.get("equation_script_sha256", "")),
        })
    # Guard order is the equation order within the XML member.  Preserve it
    # instead of sorting by metadata so a guard cannot be reassigned to a
    # different equation without changing the hash.
    document_equations: list[str] = []
    document_equation_source = inventory.get(
        "document_equation_scripts",
        inventory.get("equation_scripts", []),
    )
    for index, equation in enumerate(_inventory_sequence(document_equation_source, "document equations", errors), 1):
        if isinstance(equation, Mapping):
            raw_script = equation.get("script", equation.get("source", ""))
        else:
            raw_script = equation
        script = "" if raw_script is None else str(raw_script)
        document_equations.append(script)
        if not script:
            errors.append(f"document equation[{index}] has empty script")
    document_tables: list[list[str]] = []
    for index, table in enumerate(_inventory_sequence(inventory.get("tables", []), "document tables", errors), 1):
        if not isinstance(table, Mapping):
            errors.append(f"document table[{index}] is not an object")
            continue
        cells = _inventory_sequence(table.get("cells", []), f"document table[{index}] cells", errors)
        cell_texts = [str(cell.get("text", cell.get("value", ""))) if isinstance(cell, Mapping) else str(cell) for cell in cells]
        document_tables.append(cell_texts)
    resources: list[dict[str, str]] = []
    for index, resource in enumerate(_inventory_sequence(inventory.get("image_resources", []), "image resources", errors), 1):
        if not isinstance(resource, Mapping):
            errors.append(f"image resource[{index}] is not an object")
            continue
        digest = str(resource.get("sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"image resource[{index}] missing valid sha256")
        resources.append({"resource": str(resource.get("resource", resource.get("name", ""))), "sha256": digest})
    resources.sort(key=lambda row: (row["resource"], row["sha256"]))
    return {
        "item_ids": [row["item_id"] for row in rows],
        "items": rows,
        "item_ids_sha256": canonical_sha256([row["item_id"] for row in rows]),
        "equation_scripts_sha256": canonical_sha256({
            "items": [(row["item_id"], row["equation_scripts"]) for row in rows],
            "document": document_equations,
        }),
        "table_cells_sha256": canonical_sha256({
            "items": [(row["item_id"], row["table_cells"]) for row in rows],
            "document": document_tables,
        }),
        "image_sha256": canonical_sha256({
            "items": [(row["item_id"], row["images"]) for row in rows],
            "document": document_images,
            "resources": resources,
        }),
        "equation_offset_guards_sha256": canonical_sha256(canonical_guards),
        "content_sha256": canonical_sha256({
            "items": rows,
            "document_images": document_images,
            "image_resources": resources,
            "document_equation_scripts": document_equations,
            "document_table_cells": document_tables,
            "equation_offset_guards": canonical_guards,
        }),
        "equation_offset_guards": canonical_guards,
        "document_equation_scripts": document_equations,
        "document_table_cells": document_tables,
        "image_resources": resources,
        "document_images": document_images,
        "errors": errors,
    }


def preserved_content_gate(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Compare all content hashes and return literal-boolean gates."""

    fields = (
        "item_ids_sha256",
        "equation_scripts_sha256",
        "table_cells_sha256",
        "image_sha256",
        "equation_offset_guards_sha256",
        "content_sha256",
    )
    mismatches = [field for field in fields if before.get(field) != after.get(field)]
    errors = list(before.get("errors", [])) + list(after.get("errors", []))
    gates = {
        "item_ids_preserved": before.get("item_ids_sha256") == after.get("item_ids_sha256"),
        "equation_scripts_preserved": before.get("equation_scripts_sha256") == after.get("equation_scripts_sha256"),
        "table_cells_preserved": before.get("table_cells_sha256") == after.get("table_cells_sha256"),
        "image_sha256_preserved": before.get("image_sha256") == after.get("image_sha256"),
        "equation_offset_guards_preserved": before.get("equation_offset_guards_sha256") == after.get("equation_offset_guards_sha256"),
        "content_hash_preserved": not mismatches and not errors,
    }
    return {"status": "PASS" if all(value is True for value in gates.values()) else "FAIL", "passed": all(value is True for value in gates.values()), "gates": gates, "mismatches": mismatches, "errors": errors}


def _zero_width_values(value: Any, path: str = "root") -> list[str]:
    findings: list[str] = []
    if isinstance(value, str) and ZERO_WIDTH_RE.search(value):
        findings.append(path)
    elif isinstance(value, Mapping):
        for key, child in value.items():
            findings.extend(_zero_width_values(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            findings.extend(_zero_width_values(child, f"{path}[{index}]"))
    return findings


def _equation_offset_guards(value: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    """Validate the narrowly allowed HWPX equation offset guards.

    Hanword may require invisible U+200B characters immediately after an
    inline equation because its cached line-segment offsets count the source
    placeholder rather than the one-slot equation control.  Such guards are
    structural, not spacing: they must be adjacent to an equation, contain
    only U+200B, and carry a positive length.  Any other zero-width data is a
    real spacing/content defect.
    """

    raw_guards = value.get("zero_width_guards", value.get("equation_offset_guards", []))
    guards: list[Mapping[str, Any]] = []
    errors: list[str] = []
    if raw_guards is None:
        raw_guards = []
    if not isinstance(raw_guards, Sequence) or isinstance(raw_guards, (str, bytes)):
        return [], ["zero_width_guards must be a list"]
    for index, row in enumerate(raw_guards, 1):
        if not isinstance(row, Mapping):
            errors.append(f"zero_width_guards[{index}] is not an object")
            continue
        try:
            length = int(row.get("length", 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            errors.append(f"zero_width_guards[{index}] has non-positive length")
        if row.get("adjacent") is not True:
            errors.append(f"zero_width_guards[{index}] is not adjacent to an equation")
        if row.get("visible_text", False) is not False:
            errors.append(f"zero_width_guards[{index}] contains visible text")
        character = str(row.get("character", "U+200B"))
        if character not in {"U+200B", "\\u200b", "\u200b"}:
            errors.append(f"zero_width_guards[{index}] is not a U+200B equation guard")
        raw_text = row.get("text", row.get("characters", ""))
        if raw_text and set(str(raw_text)) != {"\u200b"}:
            errors.append(f"zero_width_guards[{index}] contains a non-U+200B character")
        guards.append(row)
    stray = value.get("zero_width_strays", [])
    if stray:
        errors.append("non-equation zero-width data found")
    # A generic adapter that reports zero-width locations without classifying
    # each one must fail closed rather than treating the data as a guard.
    locations = value.get("zero_width_locations", [])
    if locations and not guards:
        errors.append("zero-width locations were not classified as equation guards")
    return guards, errors


def apply_reference_style(document: StyleDocumentAdapter, profile: StyleProfile) -> dict[str, Any]:
    """Apply all seven style families and prove formatting did not alter content."""

    preflight = preflight_reference_style(profile)
    if preflight.get("passed") is not True:
        raise StyleApplicationError(
            "reference style preflight failed: "
            + "; ".join(str(error) for error in preflight.get("errors", []))
        )

    try:
        before_raw = document.capture_content()
    except Exception as exc:
        raise StyleApplicationError(f"content capture before styling failed: {exc}") from exc
    before = content_manifest(before_raw)
    if before["errors"]:
        raise StyleApplicationError("invalid pre-style content inventory: " + "; ".join(before["errors"]))
    calls: list[str] = []
    errors: list[str] = []
    for kind in STYLE_KINDS:
        try:
            result = document.apply_style(kind, profile.settings[kind])
            if result is False:
                errors.append(f"adapter rejected {kind} style")
            else:
                calls.append(kind)
        except Exception as exc:
            errors.append(f"{kind}: {type(exc).__name__}: {exc}")
    try:
        after_raw = document.capture_content()
        after = content_manifest(after_raw)
    except Exception as exc:
        errors.append(f"content capture after styling failed: {type(exc).__name__}: {exc}")
        after = {"errors": [str(exc)]}
    preserve = preserved_content_gate(before, after)
    after_inventory: Mapping[str, Any] = after_raw if "after_raw" in locals() and isinstance(after_raw, Mapping) else {}
    zero_width = _zero_width_values(after_inventory)
    guards, guard_errors = _equation_offset_guards(after_inventory)
    before_inventory: Mapping[str, Any] = before_raw if isinstance(before_raw, Mapping) else {}
    before_blank_paragraphs = list(before_inventory.get("blank_paragraphs", []))
    after_blank_paragraphs = list(after_inventory.get("blank_paragraphs", []))
    blank_paragraphs_preserved = before_blank_paragraphs == after_blank_paragraphs
    # Only classified equation guards are allowed; the recursive scan is kept
    # for visible/unknown zero-width data in adapter inventories.
    zero_width = [path for path in zero_width if "zero_width_guards" not in path and "equation_offset_guards" not in path]
    zero_width.extend(guard_errors)
    gates = {
        "all_style_families_applied": calls == list(STYLE_KINDS),
        # A previously approved layout paragraph may remain, but a style
        # operation must never add, remove, or move one to manufacture space.
        "no_blank_or_zero_width_spacing": not zero_width and blank_paragraphs_preserved,
        **{key: value is True for key, value in preserve["gates"].items()},
    }
    errors.extend(preserve.get("errors", []))
    if zero_width:
        errors.append("zero-width spacing found at: " + ", ".join(zero_width))
    if not blank_paragraphs_preserved:
        errors.append(
            "blank paragraphs changed during styling: "
            f"before={before_blank_paragraphs!r}, after={after_blank_paragraphs!r}"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "profile_id": profile.profile_id,
        "profile_sha256": profile.profile_sha256,
        "preflight": preflight,
        "status": "PASS" if all(value is True for value in gates.values()) and not errors else "FAIL",
        "passed": all(value is True for value in gates.values()) and not errors,
        "applied": calls == list(STYLE_KINDS),
        "applied_styles": calls,
        "gates": {key: value is True for key, value in gates.items()},
        "errors": errors,
        "before_content": before,
        "after_content": after,
        "preservation": preserve,
    }


__all__ = [
    "SCHEMA_VERSION",
    "STYLE_KINDS",
    "StyleProfile",
    "StyleProfileError",
    "StyleApplicationError",
    "StyleDocumentAdapter",
    "apply_reference_style",
    "canonical_sha256",
    "content_manifest",
    "preflight_reference_style",
    "profile_sha256",
    "preserved_content_gate",
]
