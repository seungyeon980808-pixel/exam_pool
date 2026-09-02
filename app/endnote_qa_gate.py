"""Strict, manifest-driven QA gates for native HWPX endnotes.

This module deliberately audits the HWPX package instead of trusting a rendered
preview or a converter's counters.  A source manifest describes the expected
solution contract for each problem.  The audit then checks the corresponding
native ``hp:endNote`` subtree, equations, tables, pictures, text, and BinData
references.  A finding in any independent gate makes the document fail.

The module is intentionally dependency-free so it can be used in the converter
and in copyright-free synthetic regression tests.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import html
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET


REPORT_VERSION = 1
_ENDNOTE_RE = re.compile(r"<(?P<prefix>[A-Za-z_][\w.-]*:)?endNote(?=[\s>/])")
_TEXT_RE = re.compile(
    r"<(?P<prefix>[A-Za-z_][\w.-]*:)?t(?:\s[^>]*)?>(?P<text>.*?)</(?P=prefix)t>",
    re.DOTALL,
)
_CONTEXT_TEXT_RE = re.compile(
    r"<(?:[A-Za-z_][\w.-]*:)?(?:t|script)(?:\s[^>]*)?>(?P<text>.*?)"
    r"</(?:[A-Za-z_][\w.-]*:)?(?:t|script)>",
    re.DOTALL,
)
_BODY_IMAGE_WORDS = re.compile(
    r"(?:page|capture|screenshot|full[-_ ]?page|전체\s*(?:페이지|본문)|페이지\s*캡처|본문\s*(?:이미지|캡처)|문항\s*(?:이미지|캡처)|해설\s*(?:이미지|캡처))",
    re.IGNORECASE,
)
_FORMULA_IMAGE_WORDS = re.compile(
    r"(?:formula|equation|math|frac|sqrt|수식|수학식|수식영역)",
    re.IGNORECASE,
)
_PLAIN_FORMULA_PATTERNS = (
    re.compile(r"\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|oint|lim|left|right|overline|underline|vec|begin|end|sin|cos|tan|log|ln|alpha|beta|gamma|theta|pi)\b"),
    re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]|\d)\s*\^\s*(?:\{[^{}]+\}|\([^()]+\)|[A-Za-z0-9])"),
    re.compile(r"\[\[(?:formula|equation|수식)[^\]]*\]\]", re.IGNORECASE),
)


def _local_name(tag: str) -> str:
    """Return the namespace-independent XML element/attribute name."""

    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def _attr(element: ET.Element, name: str, default: str = "") -> str:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value
    return default


def _descendants(element: ET.Element, local_name: str) -> Iterable[ET.Element]:
    return (child for child in element.iter() if _local_name(child.tag) == local_name)


def _normalise_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return " ".join(value.split())


def _normalised_sha256(value: str) -> str:
    return hashlib.sha256(_normalise_text(value).encode("utf-8")).hexdigest()


def _compact_text(value: str) -> str:
    """Canonical text for HWP runs whose control boundaries consume spaces."""

    # NFC preserves semantically significant enclosed numbers/Jamo such as
    # ⑤, ㉠ and ㉡.  NFKC would silently collapse them to 5, ㄱ and ㄴ.
    value = unicodedata.normalize("NFC", value or "")
    return re.sub(r"\s+", "", value)


def _compact_sha256(value: str) -> str:
    return hashlib.sha256(_compact_text(value).encode("utf-8")).hexdigest()


def _fragment_matches_context(fragment: str, context: str) -> bool:
    """Match a COM text fragment even when native equations occupy its gaps."""

    fragment = _normalise_text(fragment)
    context = _normalise_text(context)
    if not fragment:
        return True
    if fragment in context or _compact_text(fragment) in _compact_text(context):
        return True
    fragment_hangul = "".join(re.findall(r"[가-힣]", fragment))
    context_hangul = "".join(re.findall(r"[가-힣]", context))
    if len(fragment_hangul) >= 6 and fragment_hangul in context_hangul:
        return True
    tokens = re.findall(r"[가-힣]+|\d+점|[①②③④⑤]", fragment)
    if len(tokens) < 2:
        return False
    cursor = 0
    compact_context = _compact_text(context)
    for token in tokens:
        index = compact_context.find(_compact_text(token), cursor)
        if index < 0:
            return False
        cursor = index + len(_compact_text(token))
    return True


def _element_text(element: ET.Element) -> str:
    return "".join(element.itertext())


def _number(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _manifest_count(item: dict[str, Any], *names: str) -> int:
    for name in names:
        value = item.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, (list, tuple)):
            return len(value)
    return 0


def _manifest_pictures(item: dict[str, Any]) -> list[str]:
    for name in ("allowed_picture_names", "allowed_picture_ids", "allowed_pictures"):
        value = item.get(name)
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(entry) for entry in value]
    return []


def _manifest_picture_sha256s(item: dict[str, Any]) -> list[str]:
    for name in ("allowed_picture_sha256", "allowed_picture_sha256s", "allowed_image_sha256s"):
        value = item.get(name)
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            hashes = [str(entry).strip().lower() for entry in value if str(entry).strip()]
            if any(not re.fullmatch(r"[0-9a-f]{64}", entry) for entry in hashes):
                raise ValueError(f"invalid allowed picture sha256 in {name}")
            return hashes
    return []


def _manifest_verified_figure_sha256s(item: dict[str, Any]) -> list[str]:
    value = item.get("verified_pure_figure_sha256s", [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    hashes = [str(entry).strip().lower() for entry in value if str(entry).strip()]
    if any(not re.fullmatch(r"[0-9a-f]{64}", entry) for entry in hashes):
        raise ValueError("invalid verified pure figure sha256")
    return hashes


def _picture_aliases(value: str) -> set[str]:
    value = value.replace("\\", "/").strip().lower()
    base = value.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    return {value, base, stem}


def _picture_allowed(name: str, ref: str, patterns: list[str]) -> bool:
    aliases = _picture_aliases(name) | _picture_aliases(ref)
    for pattern in patterns:
        pattern_aliases = _picture_aliases(pattern)
        if aliases & pattern_aliases:
            return True
        if any(fnmatch.fnmatch(alias, pattern.lower()) for alias in aliases):
            return True
    return False


def _manifest_item(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"manifest item {index + 1} is not an object")
    item_id = str(item.get("item_id") or item.get("id") or f"ITEM-{index + 1:03d}")
    printed = item.get("printed_num", item.get("problem_number", item.get("number")))
    printed_num = _number(str(printed)) if printed is not None else None
    min_chars = item.get("solution_text_min_chars", item.get("min_solution_chars"))
    if isinstance(min_chars, str) and min_chars.strip().isdigit():
        min_chars = int(min_chars.strip())
    fragments = item.get("required_text_fragments", item.get("required_fragments", [])) or []
    if isinstance(fragments, str):
        fragments = [fragments]
    formula_scripts = item.get("formula_scripts", item.get("expected_formula_scripts", [])) or []
    if isinstance(formula_scripts, str):
        formula_scripts = [formula_scripts]
    body_formula_scripts = item.get("body_formula_scripts", []) or []
    table_formula_scripts = item.get("table_formula_scripts", []) or []
    if isinstance(body_formula_scripts, str):
        body_formula_scripts = [body_formula_scripts]
    if isinstance(table_formula_scripts, str):
        table_formula_scripts = [table_formula_scripts]
    table_texts = item.get("table_texts", item.get("expected_table_texts", [])) or []
    if isinstance(table_texts, str):
        table_texts = [table_texts]
    return {
        "raw": item,
        "item_id": item_id,
        "printed_num": printed_num,
        "formula_count": _manifest_count(item, "formula_count", "expected_formula_count", "formulas"),
        "formula_scripts": [_normalise_text(str(script)) for script in formula_scripts],
        "body_formula_scripts": [_normalise_text(str(script)) for script in body_formula_scripts],
        "table_formula_scripts": [_normalise_text(str(script)) for script in table_formula_scripts],
        "equation_font": str(item.get("equation_font") or item.get("formula_font") or ""),
        "equation_base_unit": _number(
            str(item.get("equation_base_unit") or item.get("formula_base_unit") or "")
        ),
        "table_count": _manifest_count(item, "table_count", "expected_table_count", "tables"),
        "table_texts": [_normalise_text(str(value)) for value in table_texts],
        "allowed_picture_names": _manifest_pictures(item),
        "allowed_picture_sha256s": _manifest_picture_sha256s(item),
        "verified_pure_figure_sha256s": _manifest_verified_figure_sha256s(item),
        "picture_count": _manifest_count(item, "picture_count", "expected_picture_count", "figures"),
        "problem_text_fragment": _normalise_text(
            str(item.get("problem_text_fragment") or item.get("problem_first_text") or "")
        ),
        "solution_text_sha256": item.get("solution_text_sha256") or item.get("expected_text_sha256"),
        "solution_text_compact_sha256": item.get("solution_text_compact_sha256"),
        "solution_text_compact_min_chars": item.get("solution_text_compact_min_chars"),
        "solution_text_min_chars": min_chars,
        "required_text_fragments": fragments,
    }


def load_source_manifest(path: str | Path) -> list[dict[str, Any]]:
    """Load and normalise the per-item source manifest.

    The public schema is ``{"version": 1, "items": [...]}``.  A bare list is
    accepted for small integrations, but the CLI emits the canonical schema.
    """

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_items = data if isinstance(data, list) else data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("source manifest must contain an items array")
    return [_manifest_item(item, index) for index, item in enumerate(raw_items)]


def _raw_note_starts(raw_xml: str) -> list[int]:
    return [match.start() for match in _ENDNOTE_RE.finditer(raw_xml)]


def _anchor_before(raw_xml: str, start: int) -> int | None:
    """Read the printed number immediately before a native endnote control."""

    prefix = raw_xml[max(0, start - 4096) : start]
    texts = [html.unescape(match.group("text")) for match in _TEXT_RE.finditer(prefix)]
    for text in reversed(texts):
        match = re.search(r"(?:^|\s)(\d{1,3})\.\s*$", text)
        if match:
            return int(match.group(1))
    # Some producers split ``1. `` into more than one <t>; use the final run.
    joined = html.unescape("".join(texts[-3:]))
    match = re.search(r"(\d{1,3})\.\s*$", joined)
    return int(match.group(1)) if match else None


def _problem_context_after(raw_xml: str, start: int) -> str:
    """Return visible main-body text immediately following an endnote ref."""

    close = re.search(
        r"</(?:[A-Za-z_][\w.-]*:)?endNote\s*>",
        raw_xml[start:],
        flags=re.IGNORECASE,
    )
    if close is None:
        return ""
    tail_start = start + close.end()
    tail = raw_xml[tail_start : tail_start + 8192]
    values = [html.unescape(match.group("text")) for match in _CONTEXT_TEXT_RE.finditer(tail)]
    return _normalise_text(" ".join(values))


def _section_xml_members(zf: zipfile.ZipFile) -> list[str]:
    return sorted(
        name
        for name in zf.namelist()
        if re.search(r"(?:^|/)section\d+\.xml$", name, flags=re.IGNORECASE)
    )


def _bin_data_entries(zf: zipfile.ZipFile) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for name in zf.namelist():
        if not re.search(r"(?:^|/)BinData/[^/]+$", name, flags=re.IGNORECASE):
            continue
        data = zf.read(name)
        entries[name] = {
            "name": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "aliases": _picture_aliases(name),
            "referenced": False,
            "used_in_xml": [],
            "used_in_notes": [],
        }
    return entries


def _resolve_bin_data(ref: str, entries: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    ref_aliases = _picture_aliases(ref)
    for entry in entries.values():
        if ref_aliases & entry["aliases"]:
            return entry
    return None


def _picture_info(picture: ET.Element, bins: dict[str, dict[str, Any]]) -> dict[str, Any]:
    image = next(iter(_descendants(picture, "img")), None)
    ref = _attr(image, "binaryItemIDRef") if image is not None else ""
    comment_element = next(iter(_descendants(picture, "shapeComment")), None)
    comment = _element_text(comment_element) if comment_element is not None else ""
    org_size = next(iter(_descendants(picture, "orgSz")), None)
    width = _number(_attr(org_size, "width")) if org_size is not None else None
    height = _number(_attr(org_size, "height")) if org_size is not None else None
    resource = _resolve_bin_data(ref, bins) if ref else None
    resource_name = resource["name"] if resource else ref
    evidence = f"{resource_name} {comment}".strip()
    large = bool(width and height and width >= 7000 and height >= 9000)
    return {
        "resource_ref": ref,
        "resource_name": resource_name,
        "comment": _normalise_text(comment),
        "width": width,
        "height": height,
        "is_large": large,
        "is_body_capture": large or bool(_BODY_IMAGE_WORDS.search(evidence)),
        "is_formula_image": bool(_FORMULA_IMAGE_WORDS.search(evidence)),
        "resource_exists": resource is not None if ref else True,
        "resource": resource,
    }


def _finding(gate: str, code: str, item: dict[str, Any] | None, message: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"gate": gate, "code": code, "message": message}
    if item is not None:
        result["item_id"] = item["item_id"]
    result.update(extra)
    return result


def _audit_item(
    note: ET.Element,
    item: dict[str, Any],
    index: int,
    anchor_num: int | None,
    problem_context: str,
    bins: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # Preserve a boundary between adjacent paragraphs/runs.  Concatenating
    # raw <hp:t> nodes can turn ``미분계수`` + ``출제의도`` into one word and
    # makes a source-derived full-text hash impossible to compare reliably.
    parent_map = {child: parent for parent in note.iter() for child in parent}

    def has_ancestor(element: ET.Element, local_name: str) -> bool:
        parent = parent_map.get(element)
        while parent is not None:
            if _local_name(parent.tag) == local_name:
                return True
            parent = parent_map.get(parent)
        return False

    body_text_nodes = [
        node
        for node in _descendants(note, "t")
        if not has_ancestor(node, "tbl") and not has_ancestor(node, "pic")
    ]
    raw_text = " ".join(_element_text(node) for node in body_text_nodes)
    text = _normalise_text(raw_text)
    equations = list(_descendants(note, "equation"))
    equation_fonts = [_attr(equation, "font") for equation in equations]
    equation_base_units = [_number(_attr(equation, "baseUnit")) for equation in equations]
    formula_scripts = [
        _normalise_text(_element_text(script))
        for equation in equations
        for script in _descendants(equation, "script")
        if _normalise_text(_element_text(script))
    ]
    body_formula_scripts = [
        _normalise_text(_element_text(script))
        for equation in equations
        if not has_ancestor(equation, "tbl")
        for script in _descendants(equation, "script")
        if _normalise_text(_element_text(script))
    ]
    table_formula_scripts = [
        _normalise_text(_element_text(script))
        for equation in equations
        if has_ancestor(equation, "tbl")
        for script in _descendants(equation, "script")
        if _normalise_text(_element_text(script))
    ]
    pictures = [_picture_info(picture, bins) for picture in _descendants(note, "pic")]
    tables = list(_descendants(note, "tbl"))
    table_texts = [
        _normalise_text(" ".join(_element_text(node) for node in _descendants(table, "t")))
        for table in tables
    ]
    expected = item["formula_count"]
    findings: list[dict[str, Any]] = []
    for script_index, script in enumerate(formula_scripts, 1):
        if re.search(r"\{\s*\}\s*over\b|\bover\s*\{\s*\}", script):
            findings.append(
                _finding(
                    "formula_script",
                    "FORMULA_SCRIPT_MALFORMED",
                    item,
                    "native equation contains an empty fraction numerator or denominator",
                    formula_index=script_index,
                    script=script,
                )
            )
        if re.search(r"(?<!\\)\\[A-Za-z]+", script):
            findings.append(
                _finding(
                    "formula_script",
                    "FORMULA_SCRIPT_UNSUPPORTED",
                    item,
                    "native equation script contains an unsupported raw command",
                    formula_index=script_index,
                    script=script,
                )
            )
        if re.search(r"[가-힣]", script):
            findings.append(
                _finding(
                    "formula_script",
                    "FORMULA_SCRIPT_CONTAINS_TEXT",
                    item,
                    "native equation script contains Korean prose that must be a text block",
                    formula_index=script_index,
                    script=script,
                )
            )
    allowed = item["allowed_picture_names"]
    allowed_hashes = set(item["allowed_picture_sha256s"])
    verified_figure_hashes = set(item["verified_pure_figure_sha256s"])
    for picture in pictures:
        resource_hash = (picture.get("resource") or {}).get("sha256", "")
        picture["allowed"] = (
            _picture_allowed(picture["resource_name"], picture["resource_ref"], allowed)
            or resource_hash in allowed_hashes
        )
        verified_pure_figure = bool(
            resource_hash
            and resource_hash in allowed_hashes
            and resource_hash in verified_figure_hashes
        )
        explicit_body_capture = bool(_BODY_IMAGE_WORDS.search(
            f"{picture['resource_name']} {picture['comment']}"
        ))
        picture["verified_pure_figure"] = verified_pure_figure
        effective_body_capture = explicit_body_capture or (
            picture["is_body_capture"] and not verified_pure_figure
        )
        if effective_body_capture or not picture["allowed"]:
            findings.append(
                _finding(
                    "endnote_body_image",
                    "ENDNOTE_BODY_IMAGE",
                    item,
                    "endnote contains an unapproved or body/page capture image",
                    resource=picture["resource_name"],
                    comment=picture["comment"],
                    body_capture=effective_body_capture,
                    allowed=picture["allowed"],
                    verified_pure_figure=verified_pure_figure,
                )
            )
        if expected > len(equations) and picture["is_formula_image"]:
            findings.append(
                _finding(
                    "formula_image",
                    "FORMULA_IMAGE_REPLACEMENT",
                    item,
                    "expected native formula is represented by an image",
                    resource=picture["resource_name"],
                    comment=picture["comment"],
                )
            )
        if not picture["resource_exists"]:
            findings.append(
                _finding(
                    "bindata",
                    "MISSING_BINDATA_REFERENCE",
                    item,
                    "picture references a missing HWPX BinData resource",
                    resource_ref=picture["resource_ref"],
                )
            )
    if item["allowed_picture_sha256s"]:
        actual_picture_hashes = [
            (picture.get("resource") or {}).get("sha256", "") for picture in pictures
        ]
        expected_picture_hashes = item["allowed_picture_sha256s"]
        if len(actual_picture_hashes) != len(expected_picture_hashes):
            findings.append(
                _finding(
                    "picture_count",
                    "PICTURE_COUNT_MISMATCH",
                    item,
                    "endnote picture count differs from the reviewed pure-figure manifest",
                    expected=len(expected_picture_hashes),
                    actual=len(actual_picture_hashes),
                )
            )
        elif actual_picture_hashes != expected_picture_hashes:
            findings.append(
                _finding(
                    "picture_order",
                    "PICTURE_ORDER_MISMATCH",
                    item,
                    "endnote pure figures are attached in a different order",
                    expected=expected_picture_hashes,
                    actual=actual_picture_hashes,
                )
            )
    elif item["picture_count"] != len(pictures):
        findings.append(
            _finding(
                "picture_count",
                "PICTURE_COUNT_MISMATCH",
                item,
                "endnote picture count differs from the source manifest",
                expected=item["picture_count"],
                actual=len(pictures),
            )
        )
    if anchor_num != item["printed_num"]:
        findings.append(
            _finding(
                "item_mapping",
                "ENDNOTE_ITEM_MISMATCH",
                item,
                "endnote anchor number does not match the source manifest",
                expected_printed_num=item["printed_num"],
                actual_printed_num=anchor_num,
                endnote_index=index + 1,
            )
        )
    expected_problem_fragment = item["problem_text_fragment"]
    if expected_problem_fragment and not _fragment_matches_context(expected_problem_fragment, problem_context):
        findings.append(
            _finding(
                "item_mapping",
                "ENDNOTE_PROBLEM_CONTEXT_MISMATCH",
                item,
                "text after the endnote reference does not match the expected problem",
                expected_fragment=expected_problem_fragment,
                actual_context=problem_context[:500],
            )
        )
    if len(equations) < expected:
        findings.append(
            _finding(
                "formula_count",
                "FORMULA_COUNT_SHORTFALL",
                item,
                "native formula count is below the source manifest",
                expected=expected,
                actual=len(equations),
                formula_scripts=len(formula_scripts),
            )
        )
    elif len(equations) > expected:
        findings.append(
            _finding(
                "formula_count",
                "FORMULA_COUNT_MISMATCH",
                item,
                "native formula count exceeds the source manifest",
                expected=expected,
                actual=len(equations),
                formula_scripts=len(formula_scripts),
            )
        )
    if len(tables) != item["table_count"]:
        findings.append(
            _finding(
                "table_count",
                "TABLE_COUNT_MISMATCH",
                item,
                "native table count does not match the source manifest",
                expected=item["table_count"],
                actual=len(tables),
            )
        )
    if item["table_texts"] and table_texts != item["table_texts"]:
        findings.append(
            _finding(
                "table_content",
                "TABLE_CONTENT_MISMATCH",
                item,
                "native table text differs from the ordered source manifest",
                expected=item["table_texts"],
                actual=table_texts,
            )
        )
    if len(formula_scripts) < len(equations):
        findings.append(
            _finding(
                "formula_count",
                "FORMULA_SCRIPT_EMPTY",
                item,
                "one or more native formula objects have no editable script",
                formula_count=len(equations),
                formula_script_count=len(formula_scripts),
            )
        )
    expected_scripts = item["formula_scripts"]
    expected_body_scripts = item["body_formula_scripts"]
    expected_table_scripts = item["table_formula_scripts"]
    if expected_body_scripts or expected_table_scripts:
        expected_scripts = expected_body_scripts + expected_table_scripts
        actual_scripts = body_formula_scripts + table_formula_scripts
    else:
        actual_scripts = formula_scripts
    if expected_scripts and actual_scripts != expected_scripts:
        mismatches = []
        limit = max(len(expected_scripts), len(actual_scripts))
        for formula_index in range(limit):
            expected_script = expected_scripts[formula_index] if formula_index < len(expected_scripts) else None
            actual_script = actual_scripts[formula_index] if formula_index < len(actual_scripts) else None
            if expected_script != actual_script:
                mismatches.append(
                    {
                        "index": formula_index + 1,
                        "expected": expected_script,
                        "actual": actual_script,
                    }
                )
        findings.append(
            _finding(
                "formula_script",
                "FORMULA_SCRIPT_MISMATCH",
                item,
                "native formula scripts differ from the ordered source manifest",
                mismatches=mismatches,
            )
        )
    expected_font = item["equation_font"]
    if expected_font and any(font != expected_font for font in equation_fonts):
        findings.append(
            _finding(
                "formula_format",
                "FORMULA_FONT_MISMATCH",
                item,
                "one or more native equations use a different equation font",
                expected=expected_font,
                actual=equation_fonts,
            )
        )
    expected_base_unit = item["equation_base_unit"]
    if expected_base_unit is not None and any(value != expected_base_unit for value in equation_base_units):
        findings.append(
            _finding(
                "formula_format",
                "FORMULA_BASE_UNIT_MISMATCH",
                item,
                "one or more native equations use a different base unit",
                expected=expected_base_unit,
                actual=equation_base_units,
            )
        )
    if expected > len(equations) and any(pattern.search(text) for pattern in _PLAIN_FORMULA_PATTERNS):
        findings.append(
            _finding(
                "formula_plain_text",
                "FORMULA_PLAIN_TEXT_REPLACEMENT",
                item,
                "formula-like source markup remains in ordinary text while native formulas are missing",
                expected=expected,
                actual=len(equations),
            )
        )
    required = [str(fragment) for fragment in item["required_text_fragments"]]
    min_chars = item["solution_text_min_chars"]
    compact_min_chars = item["solution_text_compact_min_chars"]
    missing_fragments = [
        fragment for fragment in required
        if _compact_text(fragment) not in _compact_text(raw_text)
    ]
    compact_contract = item["solution_text_compact_sha256"]
    if compact_contract:
        too_short = isinstance(compact_min_chars, int) and len(_compact_text(raw_text)) < compact_min_chars
        hash_mismatch = _compact_sha256(raw_text) != str(compact_contract).lower()
    else:
        too_short = isinstance(min_chars, int) and len(text) < min_chars
        hash_mismatch = bool(item["solution_text_sha256"]) and _normalised_sha256(text) != str(item["solution_text_sha256"]).lower()
    has_text_contract = compact_contract is not None or item["solution_text_sha256"] is not None
    if required and missing_fragments or too_short or hash_mismatch or (has_text_contract and not text):
        findings.append(
            _finding(
                "solution_content",
                "SOLUTION_CONTENT_MISSING",
                item,
                "endnote solution text is empty, partial, or does not satisfy its source contract",
                missing_fragments=missing_fragments,
                actual_text_chars=len(text),
                minimum_text_chars=min_chars,
                text_sha256=_normalised_sha256(text),
                expected_text_sha256=item["solution_text_sha256"],
                compact_text_chars=len(_compact_text(raw_text)),
                compact_text_sha256=_compact_sha256(raw_text),
                expected_compact_text_sha256=compact_contract,
            )
        )
    result = {
        "index": index + 1,
        "item_id": item["item_id"],
        "expected_printed_num": item["printed_num"],
        "actual_printed_num": anchor_num,
        "text": text,
        "text_chars": len(text),
        "formula_count": len(equations),
        "formula_script_count": len(formula_scripts),
        "formula_scripts": formula_scripts,
        "body_formula_scripts": body_formula_scripts,
        "table_formula_scripts": table_formula_scripts,
        "equation_fonts": equation_fonts,
        "equation_base_units": equation_base_units,
        "table_count": len(tables),
        "table_texts": table_texts,
        "problem_context": problem_context[:500],
        "pictures": [{key: value for key, value in picture.items() if key not in {"resource"}} for picture in pictures],
        "finding_codes": [finding["code"] for finding in findings],
    }
    return result, findings


def audit_hwpx(hwpx_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    """Audit one HWPX against a per-item source manifest.

    This is a pure read-only operation.  It never opens Hancom, modifies the
    package, or writes source/content data.  ``status`` is PASS only when every
    independent gate has zero findings.
    """

    hwpx_path = Path(hwpx_path)
    manifest_path = Path(manifest_path)
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "path": str(hwpx_path),
        "manifest": str(manifest_path),
        "status": "FAIL",
        "counts": {},
        "items": [],
        "images": [],
        "findings": [],
    }
    try:
        manifest = load_source_manifest(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report["findings"].append(_finding("manifest", "INVALID_SOURCE_MANIFEST", None, str(exc)))
        return report
    try:
        zf = zipfile.ZipFile(hwpx_path)
    except (OSError, zipfile.BadZipFile) as exc:
        report["findings"].append(_finding("package", "INVALID_HWPX", None, str(exc)))
        return report
    with zf:
        bins = _bin_data_entries(zf)
        section_names = _section_xml_members(zf)
        notes: list[tuple[ET.Element, int | None, str, str]] = []
        all_xml_roots: list[ET.Element] = []
        for section_name in section_names:
            raw_xml = zf.read(section_name).decode("utf-8", errors="replace")
            try:
                root = ET.fromstring(raw_xml)
            except ET.ParseError as exc:
                report["findings"].append(_finding("package", "INVALID_SECTION_XML", None, f"{section_name}: {exc}"))
                continue
            all_xml_roots.append(root)
            starts = _raw_note_starts(raw_xml)
            note_elements = [element for element in root.iter() if _local_name(element.tag) == "endNote"]
            if len(starts) != len(note_elements):
                report["findings"].append(
                    _finding(
                        "endnote_count",
                        "ENDNOTE_XML_COUNT_MISMATCH",
                        None,
                        f"{section_name}: raw/native endnote occurrence count differs",
                        raw_count=len(starts),
                        parsed_count=len(note_elements),
                    )
                )
            for idx, note in enumerate(note_elements):
                start = starts[idx] if idx < len(starts) else -1
                notes.append(
                    (
                        note,
                        _anchor_before(raw_xml, start) if start >= 0 else None,
                        _problem_context_after(raw_xml, start) if start >= 0 else "",
                        section_name,
                    )
                )
        endnote_autonum = sum(
            1
            for root in all_xml_roots
            for element in root.iter()
            if _local_name(element.tag) == "autoNum" and _attr(element, "numType").upper() == "ENDNOTE"
        )
        report["counts"].update(
            {
                "source_items": len(manifest),
                "hwpx_sections": len(section_names),
                "hwpx_endnotes": len(notes),
                "native_endnote_autonum": endnote_autonum,
                "bin_data_total": len(bins),
            }
        )
        if len(notes) != len(manifest):
            report["findings"].append(
                _finding(
                    "endnote_count",
                    "ENDNOTE_COUNT_MISMATCH",
                    None,
                    "native endnote count does not match source manifest item count",
                    expected=len(manifest),
                    actual=len(notes),
                )
            )
        if endnote_autonum != len(notes):
            report["findings"].append(
                _finding(
                    "endnote_count",
                    "ENDNOTE_AUTONUM_MISMATCH",
                    None,
                    "ENDNOTE auto-number count does not match native endnote count",
                    expected=len(notes),
                    actual=endnote_autonum,
                )
            )
        for index, (note, anchor, problem_context, section_name) in enumerate(notes):
            if index >= len(manifest):
                report["findings"].append(
                    _finding(
                        "item_mapping",
                        "UNEXPECTED_ENDNOTE",
                        None,
                        "HWPX contains an endnote with no source manifest item",
                        endnote_index=index + 1,
                        section=section_name,
                    )
                )
                continue
            item_report, item_findings = _audit_item(
                note,
                manifest[index],
                index,
                anchor,
                problem_context,
                bins,
            )
            item_report["section"] = section_name
            report["items"].append(item_report)
            report["findings"].extend(item_findings)
            for picture in item_report["pictures"]:
                resource = _resolve_bin_data(picture["resource_ref"], bins) if picture["resource_ref"] else None
                if resource is not None and manifest[index]["item_id"] not in resource["used_in_notes"]:
                    resource["used_in_notes"].append(manifest[index]["item_id"])
        for index, item in enumerate(manifest[len(notes) :], start=len(notes) + 1):
            report["findings"].append(
                _finding(
                    "item_mapping",
                    "MISSING_ENDNOTE",
                    item,
                    "source manifest item has no native endnote",
                    endnote_index=index,
                )
            )
        # Audit every image relationship in the package, not just notes.  This
        # catches broken references introduced while editing a document.
        all_refs: list[tuple[str, str]] = []
        xml_names = [name for name in zf.namelist() if name.lower().endswith(".xml")]
        for xml_name in xml_names:
            # Parse each XML member so header/footer/master-page image
            # relationships are audited as well as section content.
            try:
                section_root = ET.fromstring(zf.read(xml_name))
            except ET.ParseError:
                continue
            for element in section_root.iter():
                if _local_name(element.tag) == "img":
                    ref = _attr(element, "binaryItemIDRef")
                    if ref:
                        all_refs.append((xml_name, ref))
        for xml_name, ref in all_refs:
            resource = _resolve_bin_data(ref, bins)
            if resource is None:
                report["findings"].append(
                    _finding(
                        "bindata",
                        "MISSING_BINDATA_REFERENCE",
                        None,
                        "XML image relationship points to a missing BinData resource",
                        xml_member=xml_name,
                        resource_ref=ref,
                    )
                )
            else:
                resource["referenced"] = True
                if xml_name not in resource["used_in_xml"]:
                    resource["used_in_xml"].append(xml_name)
        report["counts"]["bin_data_referenced"] = sum(1 for entry in bins.values() if entry["referenced"])
        report["images"] = [
            {
                "name": entry["name"],
                "size": entry["size"],
                "referenced": entry["referenced"],
                "used_in_xml": entry["used_in_xml"],
                "used_in_notes": entry["used_in_notes"],
            }
            for entry in bins.values()
        ]
    report["status"] = "PASS" if not report["findings"] else "FAIL"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strict source-manifest QA gates for native HWPX endnotes")
    parser.add_argument("hwpx", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path, help="write the full report to this path")
    args = parser.parse_args(argv)
    report = audit_hwpx(args.hwpx, args.manifest)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_path:
        args.json_path.write_text(output + "\n", encoding="utf-8")
    try:
        print(output)
    except UnicodeEncodeError:
        # Windows consoles may still run in cp949; keep the CLI's gate/exit
        # semantics instead of crashing after the UTF-8 report was written.
        print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke tests
    sys.exit(main())
