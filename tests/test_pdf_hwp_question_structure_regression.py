from app.pdf_hwp_pipeline_models import FigureLayout
from app.pdf_hwp_question_structure import palette_question


def test_palette_question_preserves_direct_question() -> None:
    question = palette_question("자료", "가장 적절한 것은?", "figure.png", FigureLayout.ONE_LARGE)

    assert question == {
        "qtype": "정답형",
        "passage": "자료",
        "ask": "가장 적절한 것은?",
        "material": "figure.png",
        "default_points": 3,
        "is_negative": False,
        "style_meta": {"palette_template": "수능정답1대사진5선지"},
    }


def test_palette_question_preserves_canonical_bogi_block() -> None:
    question = palette_question(
        "자료",
        "옳은 것은?\n<보기>\nㄱ. 첫째\nㄴ. 둘째\nㄷ. 셋째",
        "figure.png",
        FigureLayout.ONE_LARGE,
    )

    assert question == {
        "qtype": "합답형",
        "passage": "자료",
        "ask": "옳은 것은?",
        "material": "figure.png",
        "default_points": 3,
        "is_negative": False,
        "bogi_items": [
            {"label": "ㄱ", "text": "첫째"},
            {"label": "ㄴ", "text": "둘째"},
            {"label": "ㄷ", "text": "셋째"},
        ],
        "style_meta": {"palette_template": "수능합답1대사진5선지"},
    }


def test_palette_question_parses_ebs_ask_embedded_bogi_marker() -> None:
    question = palette_question(
        "다음은 빛의 성질을 알아보는 자료이다.",
        (
            "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은? "
            "ㄱ. 굴절률은 A가 B보다 크다. "
            "ㄴ. P의 속력은 B에서가 C에서보다 작다. "
            "ㄷ. 입사각을 크게 하면 전반사가 일어난다."
        ),
        "figure.png",
        FigureLayout.ONE_LARGE,
    )

    assert question["qtype"] == "합답형"
    assert question["style_meta"]["palette_template"] == "수능합답1대사진5선지"
    assert question["ask"] == "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?"
    assert question["bogi_items"] == [
        {"label": "ㄱ", "text": "굴절률은 A가 B보다 크다."},
        {"label": "ㄴ", "text": "P의 속력은 B에서가 C에서보다 작다."},
        {"label": "ㄷ", "text": "입사각을 크게 하면 전반사가 일어난다."},
    ]
    assert all(item["text"] not in question["ask"] for item in question["bogi_items"])
