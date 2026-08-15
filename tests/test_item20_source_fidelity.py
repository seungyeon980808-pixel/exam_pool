import json
from pathlib import Path

from app.formula_markup import validate_formula_markup


ITEM_PATH = Path(__file__).resolve().parents[1] / "app/seed/reconstructions/p1_2024_11_item20.json"


def test_item20_record_preserves_verified_source_characters_and_answer():
    # Given: the reconstruction record transcribed from the local source PDF.
    item = json.loads(ITEM_PATH.read_text(encoding="utf-8"))

    # When: its source-critical fields are read by the application.
    passage = item["passage"]
    choices = item["choices"]

    # Then: provenance, formula markup, Unicode glyphs, order, and answer stay exact.
    assert item["source"] == {
        "document": "p1_2024_11.pdf",
        "exam": "2024학년도 대학수학능력시험",
        "subject": "물리학Ⅰ",
        "item_number": 20,
        "pdf_page": 4,
        "printed_page": 32,
    }
    assert "마찰 구간 Ⅰ" in passage
    assert "마찰 구간 Ⅱ" in passage
    assert "[[formula:\\frac{7}{2}h]]" in passage
    assert item["ask"].startswith("[[formula:H]]는?")
    assert [choice["text"] for choice in choices] == [
        "[[formula:\\frac{5}{17}h]]",
        "[[formula:\\frac{7}{17}h]]",
        "[[formula:\\frac{9}{17}h]]",
        "[[formula:\\frac{11}{17}h]]",
        "[[formula:\\frac{13}{17}h]]",
    ]
    assert item["answer"] == 2
    assert all(validate_formula_markup(text) == [] for text in [passage, item["ask"], *[c["text"] for c in choices]])
