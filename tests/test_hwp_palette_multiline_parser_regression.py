from pathlib import Path
import importlib
import sys
from unittest.mock import patch


def test_image_token_can_close_multiline_slot_before_the_next_template() -> None:
    runtime = Path(__file__).resolve().parents[1] / "vendor" / "hwp_typesetter"
    with patch.object(sys, "path", [str(runtime), *sys.path]):
        parser = importlib.import_module("hwp_palette.model.parser")
    comparison = {
        "slot_count": 3,
        "slot_names": ["문항번호", "발문", "질문"],
    }
    direct = {"slot_count": 2, "slot_names": ["문항번호", "발문"]}
    lookup = {
        "비교형": ("템플릿", comparison),
        "직접형": ("템플릿", direct),
        "문항그림": ("사진", {"path": "figure.png"}),
    }
    markdown = "\n".join((
        "\\비교형\\", "1", "{표 설명", "\\표1*1\\", "자료",
        "\\문항그림\\}", "옳은 것은?", "\\직접형\\", "2", "다음 문항",
    ))

    operations, warnings = parser.build_library_plan(markdown, lookup)

    assert warnings == []
    assert len(operations) == 2
    assert operations[0][2][2] == "옳은 것은?"
    assert operations[1][2] == ["2", "다음 문항"]
