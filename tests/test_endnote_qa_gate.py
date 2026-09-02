"""Copyright-free regression fixtures for the native endnote QA gates."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from app.endnote_qa_gate import audit_hwpx


HP = "http://www.hancom.co.kr/schema/2011/hpf"
HC = "http://www.hancom.co.kr/schema/2011/hcf"


def _fixture_hwpx(tmp_path: Path, notes: list[dict], *, missing_bindata: bool = False) -> Path:
    body: list[str] = [
        f'<hs:sec xmlns:hs="urn:synthetic:section" xmlns:hp="{HP}" xmlns:hc="{HC}">'
    ]
    for note in notes:
        printed = note["printed"]
        number = note.get("note_number", printed)
        equation_font = note.get("equation_font", "HancomEQN")
        equation_base_unit = note.get("equation_base_unit", 1100)
        equation_xml = "".join(
            f'<hp:equation font="{equation_font}" baseUnit="{equation_base_unit}">'
            f'<hp:script>{script}</hp:script></hp:equation>'
            for script in note.get("equations", [])
        )
        table_equation_xml = "".join(
            f'<hp:equation font="{equation_font}" baseUnit="{equation_base_unit}">'
            f'<hp:script>{script}</hp:script></hp:equation>'
            for script in note.get("table_equations", [])
        )
        picture_xml = "".join(
            '<hp:pic>'
            f'<hp:orgSz width="{picture.get("width", 1000)}" height="{picture.get("height", 1000)}"/>'
            f'<hc:img binaryItemIDRef="{picture["ref"]}"/>'
            f'<hp:shapeComment>{picture.get("comment", "")}</hp:shapeComment>'
            "</hp:pic>"
            for picture in note.get("pictures", [])
        )
        if table_equation_xml or note.get("table_text"):
            table_xml = (
                '<hp:tbl><hp:tr><hp:tc><hp:p><hp:run>'
                f'<hp:t>{note.get("table_text", "")}</hp:t>{table_equation_xml}'
                '</hp:run></hp:p></hp:tc></hp:tr></hp:tbl>'
            )
        else:
            table_xml = "<hp:tbl/>" * note.get("tables", 0)
        if "text_before" in note or "text_after" in note:
            text_xml = f'<hp:t>{note.get("text_before", "")}</hp:t>'
            text_xml += equation_xml
            text_xml += f'<hp:t>{note.get("text_after", "")}</hp:t>'
        else:
            text_xml = f'<hp:t>{note.get("text", "")}</hp:t>{equation_xml}'
        body.append(
            f'<hp:p><hp:run><hp:t>{printed}. </hp:t><hp:ctrl>'
            f'<hp:endNote number="{number}"><hp:subList>'
            f'<hp:p><hp:run>{text_xml}{picture_xml}{table_xml}</hp:run></hp:p>'
            "</hp:subList></hp:endNote>"
            f'</hp:ctrl><hp:t>{note.get("problem_text", "")}</hp:t></hp:run></hp:p>'
        )
        body.append('<hp:autoNum numType="ENDNOTE"/>')
    body.append("</hs:sec>")
    path = tmp_path / "synthetic.hwpx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("Contents/section0.xml", "".join(body))
        refs = {picture["ref"] for note in notes for picture in note.get("pictures", [])}
        if not missing_bindata:
            for ref in refs:
                package.writestr(f"BinData/{ref}.png", b"synthetic")
    return path


def _manifest(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "source-manifest.json"
    path.write_text(json.dumps({"version": 1, "items": items}), encoding="utf-8")
    return path


def _base_item(**overrides: object) -> dict:
    item = {
        "item_id": "SYNTH-01",
        "printed_num": 1,
        "formula_count": 1,
        "table_count": 0,
        "solution_text_min_chars": 8,
        "required_text_fragments": ["정답", "풀이"],
    }
    item.update(overrides)
    return item


def test_clean_native_endnote_passes(tmp_path: Path) -> None:
    text = "정답 4 풀이 완료"
    result = audit_hwpx(
        _fixture_hwpx(tmp_path, [{"printed": 1, "text": text, "equations": ["x+1"]}]),
        _manifest(tmp_path, [_base_item()]),
    )
    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert result["items"][0]["formula_count"] == 1


def test_endnote_body_image_is_an_independent_failure(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{"printed": 1, "text": "정답 풀이 완료", "equations": [], "pictures": [{"ref": "page-1", "comment": "전체 페이지 캡처"}]}],
        ),
        _manifest(tmp_path, [_base_item(formula_count=0)]),
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert "ENDNOTE_BODY_IMAGE" in codes


def test_formula_count_shortfall_is_an_independent_failure(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(tmp_path, [{"printed": 1, "text": "정답 풀이 완료", "equations": ["x"]}]),
        _manifest(tmp_path, [_base_item(formula_count=2)]),
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert "FORMULA_COUNT_SHORTFALL" in codes
    assert "FORMULA_IMAGE_REPLACEMENT" not in codes


def test_formula_image_replacement_is_detected_separately(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{"printed": 1, "text": "정답 풀이 완료", "equations": [], "pictures": [{"ref": "formula-1", "comment": "수식 이미지"}]}],
        ),
        _manifest(tmp_path, [_base_item(formula_count=1, allowed_picture_names=["formula-1"])]),
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert "FORMULA_IMAGE_REPLACEMENT" in codes
    assert "ENDNOTE_BODY_IMAGE" not in codes


def test_formula_plain_text_replacement_is_detected_separately(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(tmp_path, [{"printed": 1, "text": r"정답 풀이: \frac{1}{2}", "equations": []}]),
        _manifest(tmp_path, [_base_item(formula_count=1)]),
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert "FORMULA_PLAIN_TEXT_REPLACEMENT" in codes
    assert "FORMULA_COUNT_SHORTFALL" in codes


def test_partial_solution_content_is_detected(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(tmp_path, [{"printed": 1, "text": "정답" , "equations": ["x+1"]}]),
        _manifest(tmp_path, [_base_item()]),
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert "SOLUTION_CONTENT_MISSING" in codes


def test_solution_hash_mismatch_is_detected(tmp_path: Path) -> None:
    expected = hashlib.sha256("정답 4 풀이 완료".encode("utf-8")).hexdigest()
    result = audit_hwpx(
        _fixture_hwpx(tmp_path, [{"printed": 1, "text": "정답 4 풀이 변경", "equations": ["x+1"]}]),
        _manifest(tmp_path, [_base_item(solution_text_sha256=expected)]),
    )
    assert result["status"] == "FAIL"
    assert any(finding["code"] == "SOLUTION_CONTENT_MISSING" for finding in result["findings"])


def test_missing_bindata_reference_is_detected(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{"printed": 1, "text": "정답 풀이 완료", "equations": ["x+1"], "pictures": [{"ref": "diagram-1", "comment": "도형"}]}],
            missing_bindata=True,
        ),
        _manifest(tmp_path, [_base_item(allowed_picture_names=["diagram-1"])]),
    )
    assert result["status"] == "FAIL"
    assert any(finding["code"] == "MISSING_BINDATA_REFERENCE" for finding in result["findings"])


def test_formula_script_order_or_symbol_change_is_fail(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{"printed": 1, "text": "정답 4 풀이 완료", "equations": ["x^{2}-1", "x+1"]}],
        ),
        _manifest(
            tmp_path,
            [
                _base_item(
                    formula_count=2,
                    formula_scripts=["x^{2}+1", "x+1"],
                )
            ],
        ),
    )
    assert result["status"] == "FAIL"
    mismatch = next(finding for finding in result["findings"] if finding["code"] == "FORMULA_SCRIPT_MISMATCH")
    assert mismatch["mismatches"] == [{"index": 1, "expected": "x^{2}+1", "actual": "x^{2}-1"}]


def test_empty_fraction_operand_is_fail_even_when_manifest_matches(tmp_path: Path) -> None:
    malformed = "{} over {b}a"
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{"printed": 1, "text": "정답 4 풀이 완료", "equations": [malformed]}],
        ),
        _manifest(
            tmp_path,
            [_base_item(formula_scripts=[malformed])],
        ),
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert "FORMULA_SCRIPT_MALFORMED" in codes


def test_repeated_number_problem_context_mismatch_is_fail(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{"printed": 1, "text": "정답 4 풀이 완료", "equations": ["x+1"], "problem_text": "다른 문제 본문"}],
        ),
        _manifest(tmp_path, [_base_item(problem_text_fragment="기준 문제 첫 문장")]),
    )
    assert result["status"] == "FAIL"
    assert any(finding["code"] == "ENDNOTE_PROBLEM_CONTEXT_MISMATCH" for finding in result["findings"])


def test_equation_font_and_base_unit_mismatch_are_fail(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{
                "printed": 1,
                "text": "정답 4 풀이 완료",
                "equations": ["x+1"],
                "equation_font": "HYhwpEQ",
                "equation_base_unit": 1200,
            }],
        ),
        _manifest(
            tmp_path,
            [_base_item(equation_font="HancomEQN", equation_base_unit=1100)],
        ),
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert "FORMULA_FONT_MISMATCH" in codes
    assert "FORMULA_BASE_UNIT_MISMATCH" in codes


def test_pure_figure_can_be_allowed_by_exact_bindata_hash(tmp_path: Path) -> None:
    exact_hash = hashlib.sha256(b"synthetic").hexdigest()
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{
                "printed": 1,
                "text": "정답 4 풀이 완료",
                "equations": ["x+1"],
                "pictures": [{"ref": "generated-name-1", "comment": "기하 도형"}],
            }],
        ),
        _manifest(tmp_path, [_base_item(allowed_picture_sha256s=[exact_hash])]),
    )
    assert result["status"] == "PASS"
    assert result["items"][0]["pictures"][0]["allowed"] is True


def test_korean_prose_inside_equation_script_is_fail(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{"printed": 1, "text": "정답 4 풀이 완료", "equations": ["x=1 이므로"]}],
        ),
        _manifest(tmp_path, [_base_item()]),
    )
    assert result["status"] == "FAIL"
    assert any(finding["code"] == "FORMULA_SCRIPT_CONTAINS_TEXT" for finding in result["findings"])


def test_body_and_table_formula_scripts_are_compared_separately(tmp_path: Path) -> None:
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{
                "printed": 1,
                "text": "정답 4 풀이 완료",
                "equations": ["x+1"],
                "table_text": "표",
                "table_equations": ["y=2"],
            }],
        ),
        _manifest(
            tmp_path,
            [_base_item(
                formula_count=2,
                body_formula_scripts=["x+1"],
                table_formula_scripts=["y=2"],
                table_count=1,
                table_texts=["표"],
            )],
        ),
    )
    assert result["status"] == "PASS"
    assert result["findings"] == []
    item = result["items"][0]
    assert item["body_formula_scripts"] == ["x+1"]
    assert item["table_formula_scripts"] == ["y=2"]
    assert item["formula_scripts"] == ["x+1", "y=2"]


def test_verified_pure_figure_hash_bypasses_size_only_but_not_capture_evidence(tmp_path: Path) -> None:
    exact_hash = hashlib.sha256(b"synthetic").hexdigest()
    pure_result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{
                "printed": 1,
                "text": "정답 4 풀이 완료",
                "equations": ["x+1"],
                "pictures": [{
                    "ref": "approved-figure",
                    "width": 8000,
                    "height": 10000,
                    "comment": "기하 도형",
                }],
            }],
        ),
        _manifest(
            tmp_path,
            [_base_item(
                allowed_picture_sha256s=[exact_hash],
                verified_pure_figure_sha256s=[exact_hash],
            )],
        ),
    )
    assert pure_result["status"] == "PASS"
    pure_picture = pure_result["items"][0]["pictures"][0]
    assert pure_picture["is_body_capture"] is True
    assert pure_picture["verified_pure_figure"] is True
    assert pure_picture["allowed"] is True

    capture_result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{
                "printed": 1,
                "text": "정답 4 풀이 완료",
                "equations": ["x+1"],
                "pictures": [{
                    "ref": "approved-solution-capture",
                    "width": 8000,
                    "height": 10000,
                    "comment": "기하 도형",
                }],
            }],
        ),
        _manifest(
            tmp_path,
            [_base_item(
                allowed_picture_sha256s=[exact_hash],
                verified_pure_figure_sha256s=[exact_hash],
            )],
        ),
    )
    assert capture_result["status"] == "FAIL"
    assert any(
        finding["code"] == "ENDNOTE_BODY_IMAGE"
        for finding in capture_result["findings"]
    )
    assert capture_result["items"][0]["pictures"][0]["verified_pure_figure"] is True


def test_compact_solution_hash_accepts_spaces_lost_around_native_equation(tmp_path: Path) -> None:
    compact_source = "정답풀이완료"
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{
                "printed": 1,
                "text_before": "정답",
                "text_after": "풀이완료",
                "equations": ["x+1"],
            }],
        ),
        _manifest(
            tmp_path,
            [_base_item(
                solution_text_compact_sha256=hashlib.sha256(compact_source.encode("utf-8")).hexdigest(),
                solution_text_compact_min_chars=len(compact_source),
            )],
        ),
    )
    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert result["items"][0]["text"] == "정답 풀이완료"


def test_problem_context_formula_spacing_uses_korean_token_order(tmp_path: Path) -> None:
    problem_context = "정적분 을 이용하여 함수 f ( x ) 의 값을 구한다"
    result = audit_hwpx(
        _fixture_hwpx(
            tmp_path,
            [{
                "printed": 1,
                "text": "정답 4 풀이 완료",
                "equations": ["x+1"],
                "problem_text": problem_context,
            }],
        ),
        _manifest(
            tmp_path,
            [_base_item(problem_text_fragment="정적분 이용하여 함수 값을 구한다")],
        ),
    )
    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert result["items"][0]["problem_context"] == problem_context
