from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes_pdf_hwp


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "conversion.db")
    monkeypatch.setattr(db, "_inited", False)
    monkeypatch.setattr(routes_pdf_hwp, "conversion_root", lambda: tmp_path / "pdf_hwp")
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    return TestClient(app)


def test_locked_hwppalette_resource_is_persisted_as_retryable_conversion_failure(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a ready conversion whose previous output must survive a locked palette registry.
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    previous = tmp_path / "previous.hwp"
    previous.write_bytes(b"previous-output")
    with db.transaction() as connection:
        connection.execute(
            "UPDATE conversion_job SET status='review',source_path='source.pdf' WHERE id=?",
            (job_id,),
        )
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,"
            "draft_json) VALUES(?,1,4,20,'[1,2,3,4]','ready',?)",
            (job_id, '{"palette_markdown":"\\\\direct\\\\n20\\nready"}'),
        ).lastrowid
        output_id = connection.execute(
            "INSERT INTO conversion_output(job_id,kind,status,file_path,sha256,size_bytes) "
            "VALUES(?,'hwp','ready',?,'existing',?)",
            (job_id, str(previous), previous.stat().st_size),
        ).lastrowid

    def locked(*_args, **_kwargs):
        raise PermissionError(13, "palette fragment directory is locked", "fragments")

    monkeypatch.setattr(routes_pdf_hwp.pipeline.HwpPaletteTypesetter, "typeset", locked)

    # When: the real pipeline boundary and production route attempt typesetting.
    response = client.post(f"/api/pdf-hwp/jobs/{job_id}/typeset")

    # Then: no raw 500 escapes and the durable aggregate remains intact and retryable.
    assert response.status_code == 503
    assert "palette fragment directory is locked" in response.json()["detail"]
    persisted = client.get(f"/api/pdf-hwp/jobs/{job_id}").json()
    assert persisted["status"] == "failed"
    assert persisted["error"]["code"] == "typeset_resource_locked"
    assert "palette fragment directory is locked" in persisted["error"]["message"]
    assert persisted["capabilities"]["retry_failed"] is True
    assert [(item["id"], item["status"]) for item in persisted["items"]] == [
        (item_id, "ready"),
    ]
    assert [(output["id"], output["status"]) for output in persisted["outputs"]] == [
        (output_id, "ready"),
    ]
    assert client.get(f"/api/pdf-hwp/jobs/{job_id}/outputs/{output_id}").content == b"previous-output"
