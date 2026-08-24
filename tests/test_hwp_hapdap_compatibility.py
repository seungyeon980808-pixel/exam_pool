from __future__ import annotations

from pathlib import Path

import pytest

from app import export_palette
from app.integrations import palette_registry


def test_one_large_hapdap_style_metadata_avoids_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: item 1 requests the exact one-photo hapdap label under an active user palette.
    monkeypatch.setattr(palette_registry, "data_dir", lambda: tmp_path / "data")
    palette_registry._save_registry({
        "schema_version": palette_registry.REGISTRY_SCHEMA,
        "active": {"suneung": "isolated-user-palette"},
        "packages": [],
    })
    question = {
        "qtype": "합답형",
        "is_negative": False,
        "passage": "prompt",
        "ask": "ask",
        "material": "item1-prompt.png",
        "default_points": 3,
        "style_meta": {"palette_template": "수능합답1대사진5선지"},
        "bogi_items": (
            '[{"label":"ㄱ","text":"claim-a"},'
            '{"label":"ㄴ","text":"claim-b"},'
            '{"label":"ㄷ","text":"claim-c"}]'
        ),
    }
    choices = [
        {"ord": 1, "combo": '["ㄱ"]'},
        {"ord": 2, "combo": '["ㄴ"]'},
        {"ord": 3, "combo": '["ㄷ"]'},
        {"ord": 4, "combo": '["ㄱ","ㄴ"]'},
        {"ord": 5, "combo": '["ㄱ","ㄴ","ㄷ"]'},
    ]

    # When: production palette markdown is selected.
    lines = export_palette.question_to_palette(
        question, choices, num=1, layout_style="suneung",
    ).splitlines()

    # Then: it emits the compatible 12-slot label instead of the 11-slot legacy fallback.
    assert lines[0] == "\\수능합답1대사진5선지\\"
    assert len(lines[1:]) == 12
    assert lines[3] == "\\item1-prompt\\"
    assert lines[5:8] == ["claim-a", "claim-b", "claim-c"]
