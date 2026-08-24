# -*- coding: utf-8 -*-
"""참고 HWPX에서 대왕중 시험지 물감·팔레트를 재현한다.

참고본의 첫 문항 표를 그대로 사용하되 그림 개체만 제거하고 의미 있는 위치를
이름표 슬롯으로 바꾼다. 표의 셀 폭·높이·병합·테두리·셀 안쪽 여백, 문단의
들여쓰기·줄간격·자동 자간, 글자의 장평·자간은 XML을 새로 추정하지 않고
원본 값을 그대로 보존한다.
"""

from io import BytesIO
import copy
import json
import pathlib
import shutil
import zipfile
import xml.etree.ElementTree as ET

from hwp_palette.model import library, palette


PALETTE_NAME = "대왕중 시험지 재현"
FORM_NAME = "대왕중 2단 시험지 틀"
FORM_LABEL = "대왕중2단틀"
TEMPLATE_NAME = "대왕중 합답형 그림빈칸"
TEMPLATE_LABEL = "대왕중그림합답"
SCORE_NAME = "대왕중 배점 현황"
SCORE_LABEL = "대왕중배점현황"

SLOT_NAMES = (
    "번호", "발문", "질문", "배점",
    "보기1", "보기2", "보기3",
    "선지1", "선지2", "선지3", "선지4", "선지5",
)
FORM_SLOT_NAMES = ("과목", "학년", "고사구분", "과목코드", "시행일시")

# 암호화된 문항 모음에서 예전에 수작업으로 떼어 둔 조각을 한 번만 찾아,
# AI가 의미를 알 수 있는 이름표 메타데이터를 가진 새 물감으로 승격한다.
# 실제 HWP는 그대로 복제하므로 셀 폭·여백·문단/글자 모양은 손실되지 않는다.
REFERENCE_TEMPLATE_SPECS = (
    {
        "source": "학교정답0사진1선지",
        "name": "대왕중 일반선택형 그림없음",
        "label": "대왕중일반선택",
        "slots": ("번호", "발문", "배점", "선지1", "선지2", "선지3", "선지4", "선지5"),
    },
    {
        "source": "학교합답0사진5선지",
        "name": "대왕중 합답형 그림없음",
        "label": "대왕중합답0그림",
        "slots": ("번호", "발문", "배점", "보기1", "보기2", "보기3",
                  "선지1", "선지2", "선지3", "선지4", "선지5"),
    },
    {
        "source": "학교합답2사진5선지",
        "name": "대왕중 합답형 그림2칸",
        "label": "대왕중합답2그림",
        "slots": ("번호", "발문", "그림1비움", "그림2비움", "질문", "배점",
                  "보기1", "보기2", "보기3",
                  "선지1", "선지2", "선지3", "선지4", "선지5"),
    },
)

REFERENCE_METRICS = {
    "paper_mm": [210.0, 297.0],
    # 문항 조각 자체의 구역 설정. 전체 원안지 양식은 같은 93mm 단 폭을
    # 10mm 여백 + 4mm 단 간격으로 만든다.
    "fragment_page_margin_mm": {
        "left": 7.0, "right": 7.0, "top": 20.0, "bottom": 20.0,
    },
    "fragment_columns": {"count": 2, "gap_mm": 8.0},
    "page_form_margin_mm": {
        "left": 10.0, "right": 10.0, "top": 10.0, "bottom": 10.0,
    },
    "page_form_columns": {"count": 2, "gap_mm": 4.0, "divider": True},
    "table": {"rows": 9, "cols": 16, "width_mm": 92.3, "height_mm": 123.47},
    # 원본 그림 개체 46.25mm + 셀 위/아래 여백 1.5/3.0mm. 그림을 지워도
    # 이 점유 높이를 유지해야 표가 줄지 않고 다음 문항이 오른쪽 단으로 흐른다.
    "image_blank": {"height_mm": 50.75},
    "cell_margin_mm": {
        "stem": {"left": 0.0, "right": 3.0, "top": 1.5, "bottom": 3.0},
        "question": {"left": 0.0, "right": 0.0, "top": 1.5, "bottom": 3.0},
        "statements": {"left": 2.0, "right": 2.0, "top": 1.5, "bottom": 3.0},
        "choices": {"left": 0.0, "right": 0.0, "top": 3.0, "bottom": 3.0},
    },
    "text": {
        "number": {"font": "한컴산뜻돋움", "size_pt": 12.0, "bold": True,
                   "ratio_pct": 95, "spacing_pct": 33},
        "stem": {"font": "한컴산뜻돋움", "size_pt": 11.0,
                 "ratio_pct": 95, "spacing_pct": -15, "line_spacing_pct": 145,
                 "indent_hwpunit": 500, "right_margin_hwpunit": 200},
        "question_and_statements": {"font": "한컴산뜻돋움", "size_pt": 11.0,
                                    "ratio_pct": 95, "spacing_pct": -5,
                                    "line_spacing_pct": 155},
        "statements": {"hanging_indent_hwpunit": -2240,
                       "left_margin_hwpunit": 200, "condense_pct": 30},
        "choices": {"font": "한컴산뜻돋움", "size_pt": 11.48,
                    "ratio_pct": 95, "spacing_pct_by_choice": [-5, -5, -47, -47, -41],
                    "line_spacing_pct": 155},
    },
}


def _local(elem):
    return elem.tag.rsplit("}", 1)[-1]


def _find_all(elem, name):
    return [node for node in elem.iter() if _local(node) == name]


def _register_namespaces(xml_bytes):
    for _event, pair in ET.iterparse(BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = pair
        try:
            ET.register_namespace(prefix or "", uri)
        except ValueError:
            pass


def _cells(table):
    out = []
    for tr in [e for e in list(table) if _local(e) == "tr"]:
        out.extend(e for e in list(tr) if _local(e) == "tc")
    return out


def _set_cell_text(cell, values):
    """셀의 문단별 첫 텍스트를 바꾸고 나머지 run 텍스트를 비운다."""
    paragraphs = _find_all(cell, "p")
    if len(paragraphs) < len(values):
        raise ValueError("참고 문항의 셀 문단 수가 예상보다 적습니다")
    for index, paragraph in enumerate(paragraphs):
        texts = _find_all(paragraph, "t")
        if not texts:
            raise ValueError("텍스트 노드가 없는 문단입니다")
        texts[0].text = values[index] if index < len(values) else ""
        for extra in texts[1:]:
            extra.text = ""


def _remove_descendants(elem, name):
    for parent in elem.iter():
        for child in list(parent):
            if _local(child) == name:
                parent.remove(child)


def _rewrite_section(xml_bytes, kind):
    _register_namespaces(xml_bytes)
    root = ET.fromstring(xml_bytes)
    if not list(root):
        raise ValueError("비어 있는 HWPX section입니다")

    if kind == "template":
        # 첫 문항 + 뒤의 빈 문단만 남긴다. 두 번째 문항은 팔레트에서 같은 물감을
        # 한 번 더 호출해 오른쪽 단으로 흘려보낸다.
        for child in list(root)[2:]:
            root.remove(child)
        tables = _find_all(root, "tbl")
        if len(tables) != 1:
            raise ValueError(f"첫 문항 표 1개를 기대했지만 {len(tables)}개입니다")
        table = tables[0]
        cells = _cells(table)
        if len(cells) != 30 or table.get("rowCnt") != "9" or table.get("colCnt") != "16":
            raise ValueError("참고 문항 표 구조가 예상한 9행×16열/30셀과 다릅니다")

        _set_cell_text(cells[0], ["\\번호\\"])
        _set_cell_text(cells[1], ["\\발문\\"])
        _remove_descendants(cells[3], "pic")
        _set_cell_text(cells[3], [""])
        image_size = next((e for e in cells[3].iter() if _local(e) == "cellSz"), None)
        if image_size is None:
            raise ValueError("그림 셀의 크기 정보를 찾지 못했습니다")
        image_size.set("height", "14386")
        _set_cell_text(cells[4], ["\\질문\\ (\\배점\\점)"])
        # 제목 괄호도 원본의 ASCII < >를 그대로 둔다. 비슷하게 보이는
        # 전각 괄호(〈 〉)로 바꾸면 같은 글꼴에서도 폭과 중심 좌표가 달라진다.
        _set_cell_text(cells[9], ["<보 기>"])
        _set_cell_text(cells[19], [
            "ㄱ. \\보기1\\", "ㄴ. \\보기2\\", "ㄷ. \\보기3\\",
        ])
        # 원본은 긴 선택지 셀의 미세 정렬을 앞/뒤 공백으로 맞췄다. 슬롯의
        # 값과 분리해 템플릿에 고정해야 AI가 어떤 답안을 넣어도 같은 기준선에 놓인다.
        choice_patterns = ("\\선지1\\", "\\선지2\\", " \\선지3\\",
                           "\\선지4\\", "\\선지5\\ ")
        for cell_index, pattern in zip((21, 23, 25, 27, 29), choice_patterns):
            _set_cell_text(cells[cell_index], [pattern])

    elif kind == "form":
        first = list(root)[0]
        for child in list(root)[1:]:
            root.remove(child)
        table_runs = [run for run in _find_all(first, "run")
                      if _find_all(run, "tbl")]
        if len(table_runs) != 1:
            raise ValueError("양식 틀에서 제거할 문항 표를 찾지 못했습니다")
        run = table_runs[0]
        _remove_descendants(run, "tbl")
        texts = _find_all(run, "t")
        if texts:
            texts[0].text = "\\본문\\"
        else:
            namespace = run.tag.rsplit("}", 1)[0].lstrip("{")
            ET.SubElement(run, f"{{{namespace}}}t").text = "\\본문\\"
    else:
        raise ValueError(f"알 수 없는 산출물 종류: {kind}")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _rewrite_full_page_form(xml_bytes):
    """학교 원안지 원본에서 첫머리·쪽 테두리·꼬리말만 남긴 AI 양식을 만든다."""
    _register_namespaces(xml_bytes)
    root = ET.fromstring(xml_bytes)
    children = list(root)
    if len(children) < 2:
        raise ValueError("원안지 양식에 머리표와 본문 문단이 없습니다")

    # 첫 문단에는 secPr(쪽/단/테두리), 꼬리말, 시험 정보표가 함께 있다.
    first = children[0]
    tables = _find_all(first, "tbl")
    if len(tables) != 1:
        raise ValueError(f"원안지 첫머리 표 1개를 기대했지만 {len(tables)}개입니다")
    cells = _cells(tables[0])
    if len(cells) != 9:
        raise ValueError(f"원안지 첫머리 표 셀 9개를 기대했지만 {len(cells)}개입니다")
    _set_cell_text(cells[2], ["\\과목\\"])
    _set_cell_text(cells[5], ["\\학년\\"])
    _set_cell_text(cells[6], ["\\고사구분\\"])
    _set_cell_text(cells[7], ["\\과목코드\\"])
    _set_cell_text(cells[8], ["\\시행일시\\"])

    # 최종 기준 PDF의 꼬리말을 고정 보존한다. 쪽 번호는 원본의 자동 필드라
    # 텍스트로 다시 만들지 않는다.
    footer = next((e for e in first.iter() if _local(e) == "footer"), None)
    if footer is None:
        raise ValueError("원안지 꼬리말을 찾지 못했습니다")
    footer_texts = [e for e in footer.iter() if _local(e) == "t" and e.text]
    if not footer_texts:
        raise ValueError("원안지 꼬리말 텍스트를 찾지 못했습니다")
    footer_texts[0].text = "대왕중학교 ( 2 )학년 ( 과학 )과      총( 8 )쪽 중 - "

    # 두 번째 빈 문단을 본문 삽입점으로 사용하고 예시 문항은 모두 제거한다.
    body = children[1]
    runs = _find_all(body, "run")
    if not runs:
        raise ValueError("원안지 본문 삽입 문단에 run이 없습니다")
    texts = _find_all(runs[0], "t")
    if texts:
        texts[0].text = "\\본문\\"
    else:
        namespace = runs[0].tag.rsplit("}", 1)[0].lstrip("{")
        ET.SubElement(runs[0], f"{{{namespace}}}t").text = "\\본문\\"
    for run in runs[1:]:
        for text in _find_all(run, "t"):
            text.text = ""
    for child in children[2:]:
        root.remove(child)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _rewrite_score_summary(xml_bytes):
    """원안지 예시의 배점 현황 상자를 세 이름표짜리 독립 물감으로 만든다."""
    _register_namespaces(xml_bytes)
    root = ET.fromstring(xml_bytes)
    children = list(root)
    if len(children) < 3:
        raise ValueError("원안지 양식에서 배점 현황 문단을 찾지 못했습니다")
    score = children[2]
    tables = _find_all(score, "tbl")
    if len(tables) != 1:
        raise ValueError(f"배점 현황 표 1개를 기대했지만 {len(tables)}개입니다")
    cells = _cells(tables[0])
    if len(cells) != 1:
        raise ValueError("배점 현황 표가 1셀 구조가 아닙니다")
    _set_cell_text(cells[0], ["\\배점제목\\", "\\배점1\\", "\\배점2\\"])
    for child in children:
        root.remove(child)
    root.append(score)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_hwpx_variants(reference_hwpx, output_dir, page_form_hwpx=None):
    """참고 HWPX로부터 그림 없는 문항 템플릿과 2단 양식을 만든다."""
    reference_hwpx = pathlib.Path(reference_hwpx)
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "template": output_dir / "대왕중_합답형_그림빈칸.hwpx",
        "form": output_dir / "대왕중_원안지_전체_틀.hwpx",
    }
    with zipfile.ZipFile(reference_hwpx) as source:
        section = source.read("Contents/section0.xml")
        rewritten = {kind: _rewrite_section(section, kind) for kind in out}
        for kind, destination in out.items():
            with zipfile.ZipFile(destination, "w") as target:
                for info in source.infolist():
                    data = rewritten[kind] if info.filename == "Contents/section0.xml" else source.read(info)
                    clone = copy.copy(info)
                    target.writestr(clone, data)
    if page_form_hwpx is not None:
        page_form_hwpx = pathlib.Path(page_form_hwpx)
        with zipfile.ZipFile(page_form_hwpx) as source:
            page_xml = source.read("Contents/section0.xml")
            page_outputs = {
                out["form"]: _rewrite_full_page_form(page_xml),
                output_dir / "대왕중_배점_현황.hwpx": _rewrite_score_summary(page_xml),
            }
            out["score"] = output_dir / "대왕중_배점_현황.hwpx"
            for destination, rewritten in page_outputs.items():
                with zipfile.ZipFile(destination, "w") as target:
                    for info in source.infolist():
                        data = (rewritten if info.filename == "Contents/section0.xml"
                                else source.read(info))
                        target.writestr(copy.copy(info), data)
    (output_dir / "대왕중_참고본_측정값.json").write_text(
        json.dumps(REFERENCE_METRICS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def convert_hwpx_to_hwp(src, dest):
    """한글 자체 변환기로 HWPX를 삽입 가능한 HWP 물감으로 만든다."""
    from pyhwpx import Hwp

    hwp = Hwp(new=True, visible=False, register_module=True, on_quit=False)
    try:
        if not hwp.open(str(pathlib.Path(src).resolve())):
            raise RuntimeError(f"HWPX를 열지 못했습니다: {src}")
        if not hwp.save_as(str(pathlib.Path(dest).resolve()), format="HWP"):
            raise RuntimeError(f"HWP로 저장하지 못했습니다: {dest}")
    finally:
        hwp.quit()
    return pathlib.Path(dest)


def _copy_with_text(source, text):
    def save_to(destination):
        shutil.copy2(source, destination)
        return text
    return save_to


def _upsert_file_paint(category, name, label, source, text, subcat):
    data = library.load()
    existing = next((item for item in data.get(category, []) if item.get("name") == name), None)
    save_to = _copy_with_text(source, text)
    if existing:
        library.replace_template_fragment(existing["id"], save_to, category=category)
        library.update_item(category, existing["id"], label=label, subcat=subcat,
                            tags=["대왕중", "참고본재현", "그림빈칸"])
        return existing["id"]
    if category == "템플릿":
        return library.add_template_from_capture(
            name, save_to, label=label, subcat=subcat,
            tags=["대왕중", "참고본재현", "그림빈칸"],
            slot_count=library.count_slots(text))
    return library.add_form_from_file(
        name, source, label=label, subcat=subcat,
        tags=["대왕중", "참고본재현"],
        slot_count=library.count_slots(text), slot_names=list(FORM_SLOT_NAMES))


def install(template_hwp, form_hwp, score_hwp=None):
    """만든 HWP 물감을 창고에 등록하고 전용 팔레트를 설치한다."""
    template_text = "\n".join(f"\\{name}\\" for name in SLOT_NAMES)
    template_id = _upsert_file_paint(
        "템플릿", TEMPLATE_NAME, TEMPLATE_LABEL, template_hwp, template_text, "참고본 재현")
    form_id = _upsert_file_paint(
        "양식", FORM_NAME, FORM_LABEL, form_hwp,
        "\n".join(f"\\{name}\\" for name in FORM_SLOT_NAMES) + "\n\\본문\\",
        "참고본 재현")
    # 개발 중 잠시 사용했던 이름이 같은 라벨로 남아 있으면 마크다운 라벨
    # 조회가 어느 양식을 고를지 모호해진다. 기존 참조를 정리한 뒤 하나만 둔다.
    for item in list(library.load().get("양식", [])):
        if (item.get("id") != form_id
                and item.get("name") == "대왕중 원안지 전체 틀"):
            library.delete_item("양식", item["id"])

    # 이미 창고에 있는 정확한 학교 문항 조각을 AI용 이름표가 달린 물감으로
    # 자동 승격한다. 원본 조각이 없는 새 설치에서도 핵심 1그림 물감은 동작한다.
    extra_ids = {}
    source_items = {item.get("name"): item
                    for item in library.load().get("템플릿", [])}
    for spec in REFERENCE_TEMPLATE_SPECS:
        source_item = source_items.get(spec["source"])
        if source_item is None:
            continue
        source_path = library.template_path(source_item)
        synthetic_text = "\n".join(f"\\{name}\\" for name in spec["slots"])
        extra_ids[spec["label"]] = _upsert_file_paint(
            "템플릿", spec["name"], spec["label"], source_path,
            synthetic_text, "참고본 재현")
    score_id = None
    if score_hwp is not None:
        score_text = "\n".join(
            f"\\{name}\\" for name in ("배점제목", "배점1", "배점2"))
        score_id = _upsert_file_paint(
            "템플릿", SCORE_NAME, SCORE_LABEL, score_hwp,
            score_text, "참고본 재현")

    blocks = [
        {"type": "form", "ref": form_id, "form": FORM_NAME,
         "name": "① 원안지 전체 틀", "row": 0, "col": 0, "span": 3, "rows": 2,
         "color": "#eaf2fb"},
        {"type": "template", "ref": template_id, "template": TEMPLATE_NAME,
         "name": "② 합답 그림 1칸", "row": 0, "col": 3, "span": 3, "rows": 2,
         "color": "#eef7ef"},
        {"type": "builtin", "key": "convert", "name": "③ 원고 조판",
         "row": 0, "col": 6, "span": 3, "rows": 2, "color": "#fff4e8"},
        {"type": "builtin", "key": "spacing_fit", "name": "줄끝 자간 보정",
         "row": 0, "col": 9, "span": 3, "rows": 2, "color": "#f4effa"},
        {"type": "function", "name": "발문 11pt/-15", "row": 2, "col": 0,
         "span": 3, "rows": 1, "color": "#eff8f6", "actions": [
             {"func": "글씨체", "value": "한컴산뜻돋움"},
             {"func": "글씨크기", "value": 11}, {"func": "자간", "value": -15},
             {"func": "줄간격", "value": 145}, {"func": "자간 자동조절", "value": 30},
             {"func": "어절단위 줄바꿈"},
         ]},
        {"type": "function", "name": "질문·보기 11pt/-5", "row": 2, "col": 3,
         "span": 3, "rows": 1, "color": "#eff8f6", "actions": [
             {"func": "글씨체", "value": "한컴산뜻돋움"},
             {"func": "글씨크기", "value": 11}, {"func": "자간", "value": -5},
             {"func": "줄간격", "value": 155}, {"func": "자간 자동조절", "value": 30},
             {"func": "어절단위 줄바꿈"},
         ]},
        {"type": "char", "value": "<보 기>", "name": "<보 기>",
         "row": 2, "col": 6, "span": 2, "rows": 1, "color": "#f8f1e9"},
        {"type": "stack", "name": "선지 기호", "row": 2, "col": 8,
         "span": 4, "rows": 1, "color": "#f8f1e9",
         "items": [{"type": "char", "value": value} for value in "①②③④⑤"]},
    ]
    extra_blocks = (
        ("대왕중일반선택", "일반 5지·그림 없음", 3, 0),
        ("대왕중합답0그림", "합답·그림 없음", 3, 3),
        ("대왕중합답2그림", "합답·그림 2칸", 3, 6),
    )
    for label, name, row, col in extra_blocks:
        ref = extra_ids.get(label)
        if ref:
            blocks.append({"type": "template", "ref": ref,
                           "template": next(s["name"] for s in REFERENCE_TEMPLATE_SPECS
                                            if s["label"] == label),
                           "name": name, "row": row, "col": col,
                           "span": 3, "rows": 1, "color": "#eef7ef"})
    if score_id:
        blocks.append({"type": "template", "ref": score_id,
                       "template": SCORE_NAME, "name": "배점 현황 상자",
                       "row": 3, "col": 9, "span": 3, "rows": 1,
                       "color": "#eef4fb"})
    blocks.extend([
        {"type": "char", "value": "☞ 다음 면에 계속",
         "name": "다음 면에 계속", "row": 4, "col": 0,
         "span": 3, "rows": 1, "color": "#fdf3e7"},
        {"type": "char", "value": "◇ 끝 ◇", "name": "끝 표시",
         "row": 4, "col": 3, "span": 3, "rows": 1, "color": "#fdf3e7"},
    ])
    tab = {"name": PALETTE_NAME, "cols": 12, "blocks": blocks}
    tabs = [t for t in palette.load_tabs()
            if t.get("name") not in (PALETTE_NAME, "수능 완전조판 AI")]
    tabs.append(tab)
    palette.save_tabs(tabs)
    return {"form_id": form_id, "template_id": template_id,
            "extra_template_ids": extra_ids, "score_id": score_id, "tab": tab}
