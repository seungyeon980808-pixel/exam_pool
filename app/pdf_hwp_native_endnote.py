"""Native Hanword endnote construction and source-independent QA.

The production path uses Hanword's real ``InsertEndnote`` command and inserts
the selected solution block as HWPML2X.  The pure inspection functions parse a
saved HWPX so unit tests do not need Hanword or copyrighted documents.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping, Sequence
from zipfile import BadZipFile, ZipFile


COMMON_PREFIX = "COMMON"
ELECTIVE_PREFIXES = ("PROBABILITY", "CALCULUS", "GEOMETRY")
DEFAULT_MATH_ITEM_IDS = tuple(
    [f"COMMON-{number:02d}" for number in range(1, 23)]
    + [f"{area}-{number:02d}" for area in ELECTIVE_PREFIXES for number in range(23, 31)]
)
_ITEM_ID_RE = re.compile(r"^(COMMON|PROBABILITY|CALCULUS|GEOMETRY)-(\d{2})$")
_HEADING_RE = re.compile(r"^\s*(\d{1,3})\.\s")
_ENDNOTE_RE = re.compile(r"<hp:endNote\b.*?</hp:endNote>", re.I | re.S)
_AUTO_ENDNOTE_RE = re.compile(r"numType\s*=\s*[\"']ENDNOTE[\"']", re.I)
_TEXT_RE = re.compile(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", re.I | re.S)
_PLAIN_MARKER_RE = re.compile(r"\[\s*미주\s*\d+\s*\]")
_SPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    value = html.unescape(value or "").replace("\x07", " ").replace("\x08", " ")
    return _SPACE_RE.sub(" ", re.sub(r"<[^>]+>", " ", value)).strip()


def parse_item_id(item_id: str) -> tuple[str, int]:
    match = _ITEM_ID_RE.fullmatch(item_id)
    if not match:
        raise ValueError(f"unsupported math item id: {item_id}")
    area, number = match.group(1), int(match.group(2))
    if area == COMMON_PREFIX and not 1 <= number <= 22:
        raise ValueError(f"common item number out of range: {item_id}")
    if area != COMMON_PREFIX and not 23 <= number <= 30:
        raise ValueError(f"elective item number out of range: {item_id}")
    return area, number


def canonical_item_id(number: int, occurrence: int) -> str:
    if 1 <= number <= 22:
        if occurrence != 1:
            raise ValueError(f"common item {number} occurred {occurrence} times")
        return f"COMMON-{number:02d}"
    if 23 <= number <= 30 and 1 <= occurrence <= len(ELECTIVE_PREFIXES):
        return f"{ELECTIVE_PREFIXES[occurrence - 1]}-{number:02d}"
    raise ValueError(f"invalid item number/occurrence: {number}/{occurrence}")


def _expected_number_counts(expected_item_ids: Sequence[str]) -> Counter[int]:
    return Counter(parse_item_id(item_id)[1] for item_id in expected_item_ids)


def canonicalize_heading_numbers(
    numbers: Iterable[int], expected_item_ids: Sequence[str] = DEFAULT_MATH_ITEM_IDS
) -> tuple[str, ...]:
    """Map printed numbers to area IDs while ignoring surplus worked-step headings.

    Worked solutions sometimes contain a line such as ``7. v(t)=...``.  Only
    the source-derived expected occurrence count is admissible; missing counts
    still fail instead of being invented.
    """
    limits = _expected_number_counts(expected_item_ids)
    seen: defaultdict[int, int] = defaultdict(int)
    result: list[str] = []
    for number in numbers:
        if number not in limits or seen[number] >= limits[number]:
            continue
        seen[number] += 1
        result.append(canonical_item_id(number, seen[number]))
    counts = Counter(parse_item_id(item_id)[1] for item_id in result)
    if counts != limits or set(result) != set(expected_item_ids):
        raise ValueError(f"heading occurrence mismatch: expected={dict(limits)}, actual={dict(counts)}")
    return tuple(result)


def heading_numbers_from_text(text: str) -> tuple[int, ...]:
    return tuple(
        int(match.group(1))
        for line in (text or "").replace("\r", "").split("\n")
        if (match := _HEADING_RE.match(line))
    )


def normalized_text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CorrespondenceRecord:
    exam_id: str
    item_id: str
    printed_number: int
    problem_first_sentence: str
    solution_first_sentence: str
    confirmed: bool
    target_problem_hash: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CorrespondenceRecord":
        return cls(
            exam_id=str(value["exam_id"]),
            item_id=str(value["item_id"]),
            printed_number=int(value["printed_number"]),
            problem_first_sentence=str(value.get("problem_first_sentence", "")),
            solution_first_sentence=str(value.get("solution_first_sentence", "")),
            confirmed=bool(value.get("confirmed", False)),
            target_problem_hash=str(value.get("target_problem_hash", "")),
        )


@dataclass(frozen=True, slots=True)
class EndnoteObjectCounts:
    equations: int = 0
    pictures: int = 0
    tables: int = 0


@dataclass(frozen=True, slots=True)
class NativeEndnoteRecord:
    index: int
    item_id: str | None
    native_number: int | None
    anchor_printed_number: int | None
    immediately_after_problem_number: bool
    body_text: str
    body_number_matches: bool
    objects: EndnoteObjectCounts
    has_native_autonum: bool


@dataclass(frozen=True, slots=True)
class NativeEndnoteInventory:
    references: int
    bodies: int
    autonumbers: int
    plain_markers: int
    records: tuple[NativeEndnoteRecord, ...]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EndnoteGate:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class NativeEndnoteQAReport:
    gates: tuple[EndnoteGate, ...]

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(gate.passed for gate in self.gates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.passed else "FAIL",
            "gates": [
                {"code": gate.code, "passed": gate.passed, "detail": gate.detail}
                for gate in self.gates
            ],
        }


def validate_correspondence(
    records: Sequence[CorrespondenceRecord],
    expected_item_ids: Sequence[str] = DEFAULT_MATH_ITEM_IDS,
) -> NativeEndnoteQAReport:
    ids = Counter(record.item_id for record in records)
    expected = Counter(expected_item_ids)
    identity_failures: list[str] = []
    content_failures: list[str] = []
    for record in records:
        try:
            area, number = parse_item_id(record.item_id)
        except ValueError as exc:
            identity_failures.append(str(exc))
            continue
        if number != record.printed_number:
            identity_failures.append(f"{record.item_id}: printed={record.printed_number}")
        if not record.exam_id.strip() or not record.confirmed:
            identity_failures.append(f"{record.item_id}: exam/confirmation missing")
        if not normalize_text(record.problem_first_sentence) or not normalize_text(record.solution_first_sentence):
            content_failures.append(f"{record.item_id}: first sentence missing")
        if record.target_problem_hash and record.target_problem_hash != normalized_text_hash(record.problem_first_sentence):
            content_failures.append(f"{record.item_id}: problem hash mismatch")
        if area not in (COMMON_PREFIX, *ELECTIVE_PREFIXES):
            identity_failures.append(f"{record.item_id}: invalid area")
    gates = (
        EndnoteGate("correspondence_item_ids", ids == expected, f"expected={len(expected)}, actual={len(records)}"),
        EndnoteGate("correspondence_identity", not identity_failures, "; ".join(identity_failures[:12]) or "all identities confirmed"),
        EndnoteGate("correspondence_first_sentences", not content_failures, "; ".join(content_failures[:12]) or "all first sentences/hash evidence present"),
    )
    return NativeEndnoteQAReport(gates)


def _section_xml(path: Path) -> str:
    with ZipFile(path) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"Contents/section\d+\.xml", name, flags=re.I)
        )
        if not names:
            raise ValueError("HWPX has no Contents/section*.xml")
        return "\n".join(archive.read(name).decode("utf-8", errors="ignore") for name in names)


def _anchor_number(before: str) -> int | None:
    direct = re.search(
        r"<hp:t(?:\s[^>]*)?>\s*(\d{1,3})\.\s*</hp:t>\s*<hp:ctrl>\s*$",
        before,
        flags=re.I | re.S,
    )
    if direct:
        return int(direct.group(1))
    nodes = [normalize_text(value) for value in _TEXT_RE.findall(before[-700:])]
    compact = "".join(nodes[-4:])
    match = re.search(r"(\d{1,3})\.\s*$", compact)
    return int(match.group(1)) if match else None


def inspect_hwpx_native_endnotes(
    path: Path,
    expected_item_ids: Sequence[str] = DEFAULT_MATH_ITEM_IDS,
) -> NativeEndnoteInventory:
    try:
        xml = _section_xml(path)
    except (BadZipFile, OSError, KeyError, ValueError) as exc:
        return NativeEndnoteInventory(0, 0, 0, 0, (), (f"{type(exc).__name__}: {exc}",))
    matches = list(_ENDNOTE_RE.finditer(xml))
    occurrences: defaultdict[int, int] = defaultdict(int)
    limits = _expected_number_counts(expected_item_ids)
    records: list[NativeEndnoteRecord] = []
    errors: list[str] = []
    for index, match in enumerate(matches, 1):
        fragment = match.group(0)
        anchor_number = _anchor_number(xml[max(0, match.start() - 900):match.start()])
        item_id: str | None = None
        if anchor_number is not None:
            occurrences[anchor_number] += 1
            if occurrences[anchor_number] <= limits.get(anchor_number, 0):
                try:
                    item_id = canonical_item_id(anchor_number, occurrences[anchor_number])
                except ValueError as exc:
                    errors.append(str(exc))
            else:
                errors.append(f"surplus endnote anchor for printed number {anchor_number}")
        body_text = normalize_text(" ".join(_TEXT_RE.findall(fragment)))
        number_match = re.search(r"\bnumber\s*=\s*[\"'](\d+)[\"']", fragment[:600], flags=re.I)
        has_native = bool(_AUTO_ENDNOTE_RE.search(fragment))
        records.append(
            NativeEndnoteRecord(
                index=index,
                item_id=item_id,
                native_number=int(number_match.group(1)) if number_match else None,
                anchor_printed_number=anchor_number,
                immediately_after_problem_number=anchor_number is not None,
                body_text=body_text,
                body_number_matches=bool(anchor_number is not None and re.match(rf"^{anchor_number}\.\s", body_text)),
                objects=EndnoteObjectCounts(
                    equations=len(re.findall(r"<hp:equation\b", fragment, flags=re.I)),
                    pictures=len(re.findall(r"<hp:pic\b", fragment, flags=re.I)),
                    tables=len(re.findall(r"<hp:tbl\b", fragment, flags=re.I)),
                ),
                has_native_autonum=has_native,
            )
        )
    visible_text = normalize_text(" ".join(_TEXT_RE.findall(xml)))
    return NativeEndnoteInventory(
        references=len(matches),
        bodies=len(records),
        # A reference document may contain standalone ENDNOTE auto-number
        # controls outside endnote bodies.  Count only descendants of each
        # hp:endNote fragment, never the whole section XML.
        autonumbers=sum(len(_AUTO_ENDNOTE_RE.findall(match.group(0))) for match in matches),
        plain_markers=len(_PLAIN_MARKER_RE.findall(visible_text)),
        records=tuple(records),
        errors=tuple(errors),
    )


def audit_native_endnotes(
    inventory: NativeEndnoteInventory,
    expected_item_ids: Sequence[str] = DEFAULT_MATH_ITEM_IDS,
    *,
    expected_objects: Mapping[str, EndnoteObjectCounts] | None = None,
    hwp_reopen: bool,
    hwpx_reopen: bool,
    copy_follows: bool,
    move_follows: bool,
) -> NativeEndnoteQAReport:
    expected_ids = Counter(expected_item_ids)
    actual_ids = Counter(record.item_id for record in inventory.records if record.item_id)
    per_item = all(
        record.item_id is not None
        and record.immediately_after_problem_number
        and record.body_number_matches
        and record.has_native_autonum
        for record in inventory.records
    )
    object_failures: list[str] = []
    if expected_objects is not None:
        for record in inventory.records:
            if record.item_id and expected_objects.get(record.item_id) != record.objects:
                object_failures.append(record.item_id)
        missing_object_evidence = set(expected_ids) - set(expected_objects)
        object_failures.extend(sorted(missing_object_evidence))
    expected_count = len(expected_item_ids)
    gates = (
        EndnoteGate(
            "problem_solution_reference_body_count",
            inventory.references == inventory.bodies == inventory.autonumbers == expected_count,
            f"expected={expected_count}, refs={inventory.references}, bodies={inventory.bodies}, autonum={inventory.autonumbers}",
        ),
        EndnoteGate("one_native_endnote_per_item", actual_ids == expected_ids, f"expected={dict(expected_ids)}, actual={dict(actual_ids)}"),
        EndnoteGate("anchor_immediately_after_problem_number", per_item, "all records native and anchored" if per_item else "missing/non-native/misplaced anchor or mismatched body number"),
        EndnoteGate("no_plain_text_endnote_fallback", inventory.plain_markers == 0, f"plain_markers={inventory.plain_markers}"),
        EndnoteGate("no_inventory_errors", not inventory.errors, "; ".join(inventory.errors) or "none"),
        EndnoteGate("solution_object_counts_preserved", not object_failures, ", ".join(object_failures) or "all equation/picture/table counts match"),
        EndnoteGate("hwp_hwpx_reopen", hwp_reopen and hwpx_reopen, f"hwp={hwp_reopen}, hwpx={hwpx_reopen}"),
        EndnoteGate("copy_carries_endnote", copy_follows, f"copy_follows={copy_follows}"),
        EndnoteGate("move_carries_endnote", move_follows, f"move_follows={move_follows}"),
    )
    return NativeEndnoteQAReport(gates)


@dataclass(frozen=True, slots=True)
class NativeEndnoteBuildJob:
    exam_id: str
    problem_hwp: Path
    solution_hwp: Path
    output_hwp: Path
    output_hwpx: Path
    output_pdf: Path
    expected_item_ids: tuple[str, ...]
    correspondence: tuple[CorrespondenceRecord, ...]
    problem_qa_report: Path
    solution_qa_report: Path

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, base_dir: Path | None = None) -> "NativeEndnoteBuildJob":
        base = (base_dir or Path.cwd()).resolve()

        def resolved(name: str) -> Path:
            path = Path(str(value[name]))
            return path.resolve() if path.is_absolute() else (base / path).resolve()

        return cls(
            exam_id=str(value["exam_id"]),
            problem_hwp=resolved("problem_hwp"),
            solution_hwp=resolved("solution_hwp"),
            output_hwp=resolved("output_hwp"),
            output_hwpx=resolved("output_hwpx"),
            output_pdf=resolved("output_pdf"),
            expected_item_ids=tuple(str(item) for item in value.get("expected_item_ids", DEFAULT_MATH_ITEM_IDS)),
            correspondence=tuple(CorrespondenceRecord.from_mapping(item) for item in value.get("correspondence", ())),
            problem_qa_report=resolved("problem_qa_report"),
            solution_qa_report=resolved("solution_qa_report"),
        )


def load_native_endnote_job(path: Path) -> NativeEndnoteBuildJob:
    return NativeEndnoteBuildJob.from_mapping(json.loads(path.read_text(encoding="utf-8")), base_dir=path.parent)


def require_editable_gate(report_path: Path) -> str:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    status = str(payload.get("status", "FAIL"))
    allowed = {"STRICT_PASS", "PRACTICAL_PASS_WITH_EXCEPTIONS"}
    if status not in allowed:
        raise RuntimeError(f"pre-endnote QA did not pass: {report_path} status={status}")
    return status


@dataclass(frozen=True, slots=True)
class _HwpHeading:
    item_id: str
    printed_number: int
    scan_index: int
    start: tuple[int, int, int]
    number_end: tuple[int, int, int]
    paragraph_text: str


@dataclass(frozen=True, slots=True)
class _SolutionRange:
    end: tuple[int, int, int]
    selected_text: str
    hwpml: str
    objects: EndnoteObjectCounts


def _capture_hwp_headings(hwp: Any, expected_item_ids: Sequence[str], *, label: str) -> list[_HwpHeading]:
    raw_text = hwp.get_text_file("UNICODE", "") or ""
    raw_numbers = heading_numbers_from_text(raw_text)
    item_ids = canonicalize_heading_numbers(raw_numbers, expected_item_ids)
    occurrences: defaultdict[int, int] = defaultdict(int)
    headings: list[_HwpHeading] = []
    hwp.MoveDocBegin()
    for scan_index, item_id in enumerate(item_ids, 1):
        _, number = parse_item_id(item_id)
        occurrences[number] += 1
        if not hwp.find(f"{number}. ", direction="Forward"):
            raise RuntimeError(f"{label}: heading search failed for {item_id}")
        number_end = tuple(int(value) for value in hwp.GetPos())
        hwp.MoveParaBegin()
        start = tuple(int(value) for value in hwp.GetPos())
        hwp.MoveParaEnd()
        para_end = tuple(int(value) for value in hwp.GetPos())
        if not hwp.select_text_by_get_pos(start, para_end):
            raise RuntimeError(f"{label}: paragraph selection failed for {item_id}")
        paragraph = hwp.get_selected_text() or ""
        hwp.Cancel()
        hwp.SetPos(*number_end)
        if not normalize_text(paragraph).startswith(f"{number}. "):
            raise RuntimeError(f"{label}: false heading match for {item_id}")
        headings.append(_HwpHeading(item_id, number, scan_index, start, number_end, paragraph))
    if Counter(heading.item_id for heading in headings) != Counter(expected_item_ids):
        raise RuntimeError(f"{label}: item ID inventory mismatch")
    return headings


def _selection_hwpml(hwp: Any) -> tuple[str, EndnoteObjectCounts]:
    xml = hwp.get_text_file("HWPML2X", "saveblock:true") or ""
    counts = EndnoteObjectCounts(
        equations=len(re.findall(r"<(?:EQUATION|EQEDIT)\b", xml, flags=re.I)),
        pictures=len(re.findall(r"<(?:PICTURE|SHAPEPICTURE|PIC)\b", xml, flags=re.I)),
        tables=len(re.findall(r"<(?:TABLE|TBL)\b", xml, flags=re.I)),
    )
    return xml, counts


def _capture_solution_ranges(hwp: Any, headings: Sequence[_HwpHeading]) -> dict[str, _SolutionRange]:
    ranges: dict[str, _SolutionRange] = {}
    for index, heading in enumerate(headings):
        next_heading = headings[index + 1] if index + 1 < len(headings) else None
        if next_heading is not None and next_heading.start[0] == heading.start[0]:
            end = next_heading.start
        else:
            hwp.SetPos(*heading.start)
            hwp.MoveListEnd()
            end = tuple(int(value) for value in hwp.GetPos())
        if not hwp.select_text_by_get_pos(heading.start, end):
            raise RuntimeError(f"solution selection failed for {heading.item_id}")
        # HWPML must be captured before GetSelectedText; the latter consumes
        # the selection on some Hanword builds.
        hwpml, objects = _selection_hwpml(hwp)
        hwp.Cancel()
        if not hwpml:
            raise RuntimeError(f"empty HWPML selection for {heading.item_id}")
        if not hwp.select_text_by_get_pos(heading.start, end):
            raise RuntimeError(f"solution text reselection failed for {heading.item_id}")
        selected_text = hwp.get_selected_text() or ""
        hwp.Cancel()
        if not normalize_text(selected_text).startswith(f"{heading.printed_number}. "):
            raise RuntimeError(f"solution range starts at wrong heading for {heading.item_id}")
        ranges[heading.item_id] = _SolutionRange(end, selected_text, hwpml, objects)
    return ranges


def build_native_endnote_hwp(
    job: NativeEndnoteBuildJob,
    *,
    visible: bool = False,
    hwp_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Build real endnotes.  Final PASS still requires the separate QA gate."""
    require_editable_gate(job.problem_qa_report)
    require_editable_gate(job.solution_qa_report)
    correspondence = validate_correspondence(job.correspondence, job.expected_item_ids)
    if not correspondence.passed:
        raise RuntimeError("problem/solution correspondence gate failed")
    for path in (job.problem_hwp, job.solution_hwp):
        if not path.is_file():
            raise FileNotFoundError(path)
    job.output_hwp.parent.mkdir(parents=True, exist_ok=True)
    if hwp_factory is None:
        from pyhwpx import Hwp

        hwp_factory = Hwp
    hwp = hwp_factory(new=True, visible=visible, register_module=True, on_quit=False)
    rows: list[dict[str, Any]] = []
    try:
        if not hwp.open(str(job.solution_hwp), format="HWP"):
            raise RuntimeError("solution HWP open failed")
        solution_headings = _capture_hwp_headings(hwp, job.expected_item_ids, label="solution")
        solution_ranges = _capture_solution_ranges(hwp, solution_headings)
        hwp.add_tab()
        problem_document_index = int(hwp.hwp.XHwpDocuments.Count) - 1
        hwp.switch_to(problem_document_index)
        if not hwp.open(str(job.problem_hwp), format="HWP"):
            raise RuntimeError("problem HWP open failed")
        problem_headings = _capture_hwp_headings(hwp, job.expected_item_ids, label="problem")
        # InsertEndnote creates a sub-list and may renumber later HWP list IDs.
        # Reverse document order keeps all pre-captured main-document anchors valid.
        for heading in reversed(problem_headings):
            solution = solution_ranges[heading.item_id]
            hwp.switch_to(problem_document_index)
            hwp.SetPos(*heading.number_end)
            if not hwp.InsertEndnote():
                raise RuntimeError(f"InsertEndnote failed for {heading.item_id}")
            if not hwp.set_text_file(solution.hwpml, format="HWPML2X", option="insertfile"):
                raise RuntimeError(f"HWPML2X insert failed for {heading.item_id}")
            if not hwp.Run("CloseEx"):
                raise RuntimeError(f"CloseEx failed for {heading.item_id}")
            rows.append(
                {
                    "item_id": heading.item_id,
                    "printed_number": heading.printed_number,
                    "scan_index": heading.scan_index,
                    "problem_first_text": normalize_text(heading.paragraph_text)[:160],
                    "solution_first_text": normalize_text(solution.selected_text)[:160],
                    "source_objects": {
                        "equations": solution.objects.equations,
                        "pictures": solution.objects.pictures,
                        "tables": solution.objects.tables,
                    },
                }
            )
        hwp.switch_to(problem_document_index)
        if not hwp.save_as(str(job.output_hwp), format="HWP"):
            raise RuntimeError("final HWP save failed")
        if not hwp.save_as(str(job.output_hwpx), format="HWPX"):
            raise RuntimeError("verification HWPX save failed")
        if not hwp.save_as(str(job.output_pdf), format="PDF"):
            raise RuntimeError("verification PDF save failed")
        page_count = int(hwp.PageCount)
    finally:
        try:
            hwp.quit(save=False)
        except Exception:
            pass
    inventory = inspect_hwpx_native_endnotes(job.output_hwpx, job.expected_item_ids)
    rows.sort(key=lambda row: int(row["scan_index"]))
    report = {
        "status": "BUILT_REQUIRES_RUNTIME_QA",
        "exam_id": job.exam_id,
        "output_hwp": str(job.output_hwp),
        "output_hwpx": str(job.output_hwpx),
        "output_pdf": str(job.output_pdf),
        "page_count": page_count,
        "rows": rows,
        "inventory": {
            "references": inventory.references,
            "bodies": inventory.bodies,
            "autonumbers": inventory.autonumbers,
            "plain_markers": inventory.plain_markers,
            "errors": list(inventory.errors),
        },
    }
    return report
