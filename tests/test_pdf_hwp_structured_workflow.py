from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes_pdf_hwp


def _client(tmp_path: Path) -> TestClient:
    db.DB_PATH = tmp_path / "structured.db"
    db._inited = False
    routes_pdf_hwp.conversion_root = lambda: tmp_path / "pdf_hwp"
    db.init_db()
    app = FastAPI()
    app.include_router(routes_pdf_hwp.router)
    return TestClient(app)


def test_catalog_is_configured_and_domain_filters_types(tmp_path: Path) -> None:
    client = _client(tmp_path)
    catalog = client.get("/api/pdf-hwp/catalog").json()
    assert {entry["domain"] for entry in catalog["domains"]} >= {"science", "common"}
    science = next(entry for entry in catalog["domains"] if entry["domain"] == "science")
    assert {entry["response_type"] for entry in science["types"]} == {"matching", "combined"}


def test_structured_edit_preserves_original_and_unplaced_materials(tmp_path: Path) -> None:
    client = _client(tmp_path)
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status,draft_json) "
            "VALUES(?,1,1,1,'[0,0,1,1]','ready',?)",
            (job_id, json.dumps({"materials": [{"caption": "A"}, {"caption": "B"}]})),
        ).lastrowid
    response = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}",
        json={"domain": "science", "type_id": "science_matching", "asset_count": 1, "passage": "그림은 대상을 나타낸 것이다."},
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["unplaced_materials"] == [{"caption": "B"}]
    assert item["draft"]["original_draft"]["materials"][1]["caption"] == "B"
    assert response.json()["warnings"]


def test_confirm_requires_type_and_required_fields_then_enables_typeset(tmp_path: Path) -> None:
    client = _client(tmp_path)
    job_id = client.post("/api/pdf-hwp/jobs", json={}).json()["id"]
    with db.transaction() as connection:
        item_id = connection.execute(
            "INSERT INTO conversion_item(job_id,ord,source_page,source_number,bbox_json,status) "
            "VALUES(?,1,1,1,'[0,0,1,1]','ready')", (job_id,),
        ).lastrowid
    blocked = client.post(f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}/confirm")
    assert blocked.status_code == 422
    saved = client.patch(
        f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}",
        json={"domain": "science", "type_id": "science_combined", "prompt": "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?", "bogi": ["ㄱ", "ㄴ"], "choices": ["ㄱ", "ㄴ", "ㄱ, ㄴ"]},
    )
    assert saved.status_code == 200
    confirmed = client.post(f"/api/pdf-hwp/jobs/{job_id}/items/{item_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["items"][0]["confirmed"] is True
    assert confirmed.json()["capabilities"]["typeset_selected"] is True
