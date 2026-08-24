from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace

import fitz
import pytest

import app.pdf_hwp_roundtrip_backend as subject
from app.pdf_hwp_pipeline_models import ConversionUnit, DetectedItem, GeneratedDocument, LayoutStyle
from app.pdf_hwp_roundtrip_backend import BackendSourcePlan, RealRoundTripBackend
from app.pdf_hwp_roundtrip_backend_store import load_conversion_paths, load_verification
from app.pdf_hwp_roundtrip_checkpoint import CheckpointStore, artifact_hash
from app.pdf_hwp_roundtrip_item_alignment import AlignedItemPair, AlignmentIssue, ItemAlignmentRequest, ItemAlignmentResult, ItemVisualComparison
from app.pdf_hwp_roundtrip_readback import HwpReadbackReport, HwpSnapshot, IssueCode
from app.pdf_hwp_roundtrip_models import SourceFacts, SourceIntegrity, SourceProfile, WorkflowStage
from app.pdf_hwp_roundtrip_profile import (
    ImageOwnershipResult, ObservedProfileIssue, load_profile_verification,
)
from app.pdf_hwp_roundtrip_structure import parse_prepared_structure
from app.pdf_hwp_roundtrip_runner import (
    BackendStageError, RoundTripRunner, RunPolicy, RunStatus, SourceInput,
)
from app.pdf_hwp_roundtrip_source import (
    DetectedItemSelection,
    SelectionIssue,
    SelectionIssueKind,
)
from app.pdf_hwp_roundtrip_unit_store import (
    FailureCode, ItemFailure, PreparationPayload, PreparationResult,
    PreparedUnitRecord, load_prepared_units, write_prepared_units,
)


class _FileTypesetter:
    """Fast file-backed typesetter using a parseable real HWP seed."""

    def __init__(self) -> None:
        self.markdown_calls: list[str] = []

    def typeset(
        self,
        markdown: str,
        output_dir: Path,
        layout_style: LayoutStyle,
        asset_dirs: tuple[Path, ...],
    ) -> GeneratedDocument:
        self.markdown_calls.append(markdown)
        output_dir.mkdir(parents=True, exist_ok=True)
        hwp = output_dir / "generated.hwp"
        shutil.copyfile(
            Path("vendor/hwp_typesetter/seed_data/fragments/csat_science_direct.hwp"), hwp,
        )
        pdf = output_dir / "generated.pdf"
        with fitz.open() as document:
            page = document.new_page(width=300, height=220)
            page.insert_text((32, 42), "34. generated item", fontsize=14)
            document.save(pdf)
        return GeneratedDocument(hwp, pdf, ())


def _plan(tmp_path: Path) -> tuple[BackendSourcePlan, SourceInput]:
    original = tmp_path / "original.pdf"
    pipeline = tmp_path / "pipeline.pdf"
    original.write_bytes(b"original identity bytes")
    with fitz.open() as document:
        page = document.new_page(width=300, height=220)
        page.insert_text((32, 42), "34. source item", fontsize=14)
        document.save(pipeline)
    plan = BackendSourcePlan(
        original,
        pipeline,
        (35, 37, 234),
        tmp_path / "roundtrip",
        "물리학Ⅰ", SourceProfile.EBS_EDITABLE_REFLOW,
        LayoutStyle.SUNEUNG,
    )
    source = SourceInput(original, SourceFacts(
        "p1_2024_11.pdf",
        "2024학년도 대학수학능력시험 문제지",
        "1. 문항",
        1,
        0,
        SourceIntegrity.VALID,
    ))
    return plan, source


def _install_preparation(
    monkeypatch: pytest.MonkeyPatch, *, hapdap: bool = False,
) -> None:
    items = tuple(
        DetectedItem(1, number, 0, (0, 0, 100, 100), str(number))
        for number in (35, 37, 234)
    )
    monkeypatch.setattr(
        subject, "select_detected_items",
        lambda source, numbers: DetectedItemSelection(items, ()),
    )

    def prepare(source: Path, selected: tuple[DetectedItem, ...], output: Path,
                style: LayoutStyle, profile: SourceProfile) -> PreparationResult:
        values = (("\\수능합답1대사진5선지\\", "234", "지문", "-", "발문", "첫째", "둘째", "셋째", "ㄱ", "ㄴ", "ㄷ", "ㄱ, ㄴ", "ㄱ, ㄴ, ㄷ")
                  if hapdap else ("\\수능정답1대사진5선지\\", "234", "지문", "-", "발문", "①", "②", "③", "④", "⑤"))
        unit = ConversionUnit(234, "\n".join(values))
        structure = parse_prepared_structure(unit, 1, (0.0, 0.0, 100.0, 100.0), style)
        failures: tuple[ItemFailure, ...] = ()
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        return write_prepared_units(
            output / "prepared-units.json",
            PreparationPayload(source, source_hash, style,
                               (PreparedUnitRecord(unit, "a" * 64, structure),), failures, profile),
        )

    monkeypatch.setattr(subject, "prepare_units", prepare)


def _install_alignment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generated_text: str = "<보기> ㄱ. ㄴ. ㄷ. ① ② ③ ④ ⑤",
) -> None:
    (tmp_path / "contact.png").write_bytes(b"contact sheet")
    def compare(request: ItemAlignmentRequest) -> ItemAlignmentResult:
        source = DetectedItem(1, 234, 0, (0, 0, 100, 100), "source")
        generated = DetectedItem(1, 234, 0, (0, 0, 100, 100), generated_text)
        comparison = ItemVisualComparison(
            234,
            tmp_path / "source-234.png",
            tmp_path / "generated-234.png",
            0.12,
            0.08,
            (1, 2, 3, 4),
            (AlignmentIssue.VISUAL_MISMATCH,),
        )
        return ItemAlignmentResult(
            (AlignedItemPair(234, source, generated),), (comparison,), (), (), (), (),
            (AlignmentIssue.VISUAL_MISMATCH,), tmp_path / "contact.png",
        )

    monkeypatch.setattr(subject, "align_and_compare_items", compare)
    mapped = {"generated without final choice": "missing_choice",
              "ㄱ ㄴ ㄷ ⑤": "missing_bogi_marker", "<보기> ㄱ ㄴ ⑤": "missing_bogi_claim"}
    code = mapped.get(generated_text)
    issues = () if code is None else (SimpleNamespace(code=SimpleNamespace(value=code)),)
    item = SimpleNamespace(item_number=234, issues=issues)
    monkeypatch.setattr(subject, "inspect_generated_pdf_contract",
                        lambda request: SimpleNamespace(items=(item,), issues=issues))


def _install_hwp_report(
    monkeypatch: pytest.MonkeyPatch, table_count: int, complete_bogi_tables: int | None = None,
) -> None:
    complete = table_count if complete_bogi_tables is None else complete_bogi_tables
    cells = (("<보기>", "ㄱ ㄴ ㄷ"),) * complete + (("unrelated header",),) * (table_count - complete)
    snapshot = HwpSnapshot(
        "hwp", "5.1", "<보기> ㄱ ㄴ ㄷ ⑤", (), 1, 1, 1, 10, 5, table_count, cells,
    )
    monkeypatch.setattr(
        subject, "inspect_hwp", lambda path, expected: HwpReadbackReport(snapshot, ()),
    )


def test_real_backend_runs_prepared_unit_and_preserves_original_runner_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one q234-style prepared unit whose visual comparison exceeds the threshold.
    plan, source = _plan(tmp_path)
    _install_preparation(monkeypatch)
    _install_alignment(monkeypatch, tmp_path)
    typesetter = _FileTypesetter()

    # When: the generic runner drives the real adapter through all four stages.
    result = RoundTripRunner(
        RealRoundTripBackend((plan,), typesetter),
        CheckpointStore(tmp_path / "state"),
        RunPolicy(),
    ).run((source,))[0]

    # Then: source identity remains the original bytes and visual mismatch is classified.
    assert result.status is RunStatus.SUCCEEDED
    assert result.artifact_hash == artifact_hash(plan.original_path)
    assert tuple(artifact.stage for artifact in result.artifacts) == (
        WorkflowStage.EXTRACT,
        WorkflowStage.HWP, WorkflowStage.HWP, WorkflowStage.HWP,
        WorkflowStage.PDF, WorkflowStage.PDF, WorkflowStage.PDF, WorkflowStage.PDF,
    )
    verification = load_verification(plan.output_dir / "verification.json")
    assert load_prepared_units(plan.output_dir / "prepared-units.json").profile is plan.profile
    assert verification.preparation_failures == ()
    assert verification.items[0].item_number == 234
    assert verification.items[0].issues == (AlignmentIssue.VISUAL_MISMATCH.value,)
    assert load_profile_verification(plan.output_dir / "profile-verification.json").blocking_issues == ()
    assert typesetter.markdown_calls and "234" in typesetter.markdown_calls[0]


def test_kice_backend_serializes_structural_image_issue_from_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, source = _plan(tmp_path)
    plan = replace(plan, profile=SourceProfile.KICE_STRUCTURAL)
    _install_preparation(monkeypatch)
    _install_alignment(monkeypatch, tmp_path)
    _install_hwp_report(monkeypatch, 0)

    class BlockingVerifier:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def verify(self, records: tuple[PreparedUnitRecord, ...]) -> ImageOwnershipResult:
            issue = ObservedProfileIssue(
                "kice_structural_scale_unreadable", 234, "scale=0.5000",
            )
            return ImageOwnershipResult(False, (issue,), "kice_structural_generated_pdf")

    monkeypatch.setattr(subject, "KiceStructuralImageOwnershipVerifier", BlockingVerifier)
    backend = RealRoundTripBackend((plan,), _FileTypesetter())
    route = backend.route(source).route
    backend.extract(source, route)
    backend.typeset(source, route)

    with pytest.raises(BackendStageError) as captured:
        backend.verify(source, route)

    assert captured.value.code == "kice_structural_scale_unreadable"
    profile = load_profile_verification(plan.output_dir / "profile-verification.json")
    assert tuple(issue.code for issue in profile.blocking_issues) == (
        "kice_structural_scale_unreadable",
    )


def test_ebs_backend_does_not_invoke_kice_geometry_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, source = _plan(tmp_path)
    _install_preparation(monkeypatch)
    _install_alignment(monkeypatch, tmp_path)
    _install_hwp_report(monkeypatch, 0)

    class ForbiddenVerifier:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("EBS editable reflow must not invoke KICE geometry")

    monkeypatch.setattr(subject, "KiceStructuralImageOwnershipVerifier", ForbiddenVerifier)
    backend = RealRoundTripBackend((plan,), _FileTypesetter())
    route = backend.route(source).route
    backend.extract(source, route)
    backend.typeset(source, route)

    assert backend.verify(source, route).pdf_path.is_file()


def test_real_backend_reload_typesets_from_persisted_prepared_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: extract completed under one adapter instance.
    plan, source = _plan(tmp_path)
    _install_preparation(monkeypatch)
    first = RealRoundTripBackend((plan,), _FileTypesetter())
    route = first.route(source).route
    extracted = first.extract(source, route)
    typesetter = _FileTypesetter()

    # When: a fresh adapter typesets using only the deterministic manifest path.
    outcome = RealRoundTripBackend((plan,), typesetter).typeset(source, route)

    # Then: prepared state reloads and produces a structurally readable HWP.
    assert extracted.manifest_path == plan.output_dir / "prepared-units.json"
    assert outcome.hwp_path.is_file()
    assert {path.name for path in outcome.auxiliary_paths} == {
        "generated.pdf", "backend-conversion.json",
    }
    assert len(typesetter.markdown_calls) == 1


def test_real_backend_selection_issue_is_typed_extract_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: source selection cannot find requested item 234.
    plan, source = _plan(tmp_path)
    monkeypatch.setattr(
        subject,
        "select_detected_items",
        lambda path, numbers: DetectedItemSelection((), (
            SelectionIssue(SelectionIssueKind.MISSING, 234, 0),
        )),
    )
    backend = RealRoundTripBackend((plan,), _FileTypesetter())

    # When/Then: extraction stops with a stable typed failure before preparation.
    with pytest.raises(BackendStageError) as captured:
        backend.extract(source, backend.route(source).route)
    assert (captured.value.stage, captured.value.code) == (WorkflowStage.EXTRACT, "selection_failed")


def test_pdf_gate_uses_actual_generated_page_count_not_prepared_item_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one prepared item produces a readable two-page PDF.
    plan, source = _plan(tmp_path)
    _install_preparation(monkeypatch)
    _install_alignment(monkeypatch, tmp_path)
    backend = RealRoundTripBackend((plan,), _FileTypesetter())
    route = backend.route(source).route
    backend.extract(source, route)
    backend.typeset(source, route)
    pdf = load_conversion_paths(plan.output_dir / "backend-conversion.json").pdf_path
    with fitz.open(pdf) as document:
        document.new_page(width=300, height=220)
        document.saveIncr()

    # When/Then: readability passes; item alignment owns semantic completeness.
    assert backend.verify(source, route).pdf_path == pdf


@pytest.mark.parametrize(("hapdap", "generated_text", "expected_code"), (
    (False, "generated without final choice", "missing_choice"),
    (True, "ㄱ ㄴ ㄷ ⑤", "missing_bogi_marker"),
    (True, "<보기> ㄱ ㄴ ⑤", "missing_bogi_claim")))
def test_generated_item_text_requires_choice_and_hapdap_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hapdap: bool,
    generated_text: str,
    expected_code: str,
) -> None:
    # Given: HWP structure passes but aligned generated item text is incomplete.
    plan, source = _plan(tmp_path)
    _install_preparation(monkeypatch, hapdap=hapdap)
    _install_alignment(monkeypatch, tmp_path, generated_text)
    _install_hwp_report(monkeypatch, 1)
    backend = RealRoundTripBackend((plan,), _FileTypesetter())
    route = backend.route(source).route
    backend.extract(source, route)
    backend.typeset(source, route)

    # When/Then: the PDF stage emits the stable per-item semantic issue.
    with pytest.raises(BackendStageError) as captured:
        backend.verify(source, route)
    assert captured.value.code == expected_code
    assert load_verification(plan.output_dir / "verification.json").items[0].issues[-1] == expected_code


def test_hwp_table_count_must_cover_prepared_hapdap_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one prepared 합답 unit but rhwp observes only an unrelated header table.
    plan, source = _plan(tmp_path)
    _install_preparation(monkeypatch, hapdap=True)
    _install_hwp_report(monkeypatch, 1, complete_bogi_tables=0)
    backend = RealRoundTripBackend((plan,), _FileTypesetter())
    route = backend.route(source).route
    backend.extract(source, route)

    # When/Then: HWP stage cannot pass an under-counted table snapshot.
    with pytest.raises(BackendStageError) as captured:
        backend.typeset(source, route)
    assert captured.value.code == IssueCode.MISSING_BOGI_BOX.value
