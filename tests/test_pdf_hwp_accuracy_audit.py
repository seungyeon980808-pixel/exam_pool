from pathlib import Path

from app import pdf_hwp_pipeline as pipeline
from app.pdf_hwp_pipeline_models import DetectedItem, DetectionResult
from tools.pdf_hwp_accuracy_audit import audit_pdf


def test_accuracy_audit_records_unexpected_item_crash_and_continues(
    tmp_path: Path, monkeypatch,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    items = (
        DetectedItem(1, 1, 0, (0, 0, 100, 100), "first"),
        DetectedItem(1, 2, 0, (0, 100, 100, 200), "second"),
    )
    monkeypatch.setattr(
        pipeline, "detect_items",
        lambda path: DetectionResult(path, "hash", 1, items),
    )
    monkeypatch.setattr(
        pipeline, "build_editable_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(StopIteration()),
    )

    report = audit_pdf(source, tmp_path / "audit")

    assert report["total"] == 2
    assert report["failed"] == 2
    assert [row["item"] for row in report["failures"]] == [1, 2]
    assert all(row["stage"] == "crash" for row in report["failures"])
    assert all("StopIteration" in row["error"] for row in report["failures"])
