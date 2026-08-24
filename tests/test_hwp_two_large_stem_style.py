from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest


TEMPLATES = Path(__file__).resolve().parents[1] / "assets/hwp_templates"


@pytest.mark.parametrize(
    "name",
    [
        "csat_direct_two_large_caption_free.hwp",
        "csat_hapdap_two_large_caption_free.hwp",
    ],
)
def test_two_large_stem_starts_after_normal_style_separator(name: str) -> None:
    completed = subprocess.run(
        ["rhwp", "dump", str(TEMPLATES / name), "--section", "0"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    first_paragraph = re.split(r"--- 문단 0\.\d+ ---", completed.stdout)[1]

    assert '텍스트: "\\문항번호\\. \\자간맞춤{\\문두\\}"' in first_paragraph
    assert re.search(
        r'\[CS\] pos=\d+ id=\d+ bold=false .* base=1148 .* char=" "',
        first_paragraph,
    )
