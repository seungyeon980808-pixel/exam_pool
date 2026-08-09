# -*- coding: utf-8 -*-
"""제공된 실제 수능 HWPX에서 삽입용 문항 물감을 만든다.

학교 시험지용 기존 템플릿을 재사용하지 않는다. 원본 문항의 문단 모양, 글자
모양, <보기> 표, 탭 선지, 그림의 자리와 감싸기 속성을 그대로 가져오고 내용만
AI가 채울 수 있는 이름표로 바꾼다.
"""

from __future__ import annotations

import copy
from io import BytesIO
import pathlib
import zipfile
import xml.etree.ElementTree as ET


SECTION_PATH = "Contents/section0.xml"
HEADER_PATH = "Contents/header.xml"


REFERENCE_FRAGMENT_SPECS = (
    {
        "key": "diagram_hapdap",
        "name": "수능 AI 실제 그림합답형",
        "label": "수능AI실제그림합답형",
        "range": (35, 39),
        "slots": ("문항번호", "발문", "질문", "배점", "보기ㄱ", "보기ㄴ", "보기ㄷ",
                  "선지1", "선지2", "선지3", "선지4", "선지5"),
        "numbered": 35,
        "keep_together": True,
        # 원본 6번의 V-T 그래프와 오른쪽 어울림 위치를 그대로 검증하는 그림형이다.
        "use_original_images": True,
        "paragraphs": {35: "발문", 36: "질문"},
        "score": {"source": 37, "after": 36, "slot": "배점"},
        "view": (38, ("보기ㄱ", "보기ㄴ", "보기ㄷ")),
        "choices": (39,),
    },
    {
        "key": "direct",
        "name": "수능 AI 실제 직접형",
        "label": "수능AI실제직접형",
        "range": (19, 21),
        "slots": ("문항번호", "발문", "질문", "배점", "선지1", "선지2", "선지3", "선지4", "선지5"),
        "numbered": 19,
        "keep_together": True,
        # 이 물감에는 그림 입력 슬롯이 없으므로 원본 Q3의 떠 있는 그림 닻을
        # 남기지 않는다. 보이지 않는 그림이 선지 폭을 줄여 ⑤를 밀어냈다.
        "drop_pictures": True,
        "paragraphs": {19: "발문", 20: "질문"},
        # 실제 6번처럼 배점은 질문 끝에 붙이지 않고 별도의 오른쪽 정렬 문단으로 둔다.
        # source 37은 원본 Q6의 배점 문단이며, 질문(20) 다음에 삽입한다.
        "score": {"source": 37, "after": 20, "slot": "배점"},
        "choices": (21,),
    },
    {
        "key": "hapdap",
        "name": "수능 AI 실제 합답형",
        "label": "수능AI실제합답형",
        "range": (42, 46),
        "slots": ("문항번호", "발문", "질문", "보기ㄱ", "보기ㄴ", "보기ㄷ",
                  "선지1", "선지2", "선지3", "선지4", "선지5"),
        "numbered": 42,
        "keep_together": True,
        "paragraphs": {42: "발문", 44: "질문"},
        "view": (45, ("보기ㄱ", "보기ㄴ", "보기ㄷ")),
        "choices": (46,),
    },
    {
        "key": "people",
        "name": "수능 AI 실제 인물합답형",
        "label": "수능AI실제인물합답형",
        "range": (109, 113),
        "slots": ("문항번호", "발문", "질문", "발언1", "발언2", "발언3",
                  "선지1", "선지2", "선지3", "선지4", "선지5"),
        "numbered": 109,
        "keep_together": True,
        "paragraphs": {109: "발문", 110: "질문"},
        "view": (111, ("발언1", "발언2", "발언3")),
        "choices": (112, 113),
    },
    {
        "key": "experiment",
        "name": "수능 AI 실제 실험형",
        "label": "수능AI실제실험형",
        "range": (30, 34),
        "slots": ("문항번호", "실험과정", "질문", "발언1", "발언2", "발언3",
                  "선지1", "선지2", "선지3", "선지4", "선지5"),
        "numbered": 30,
        "keep_together": True,
        "paragraphs": {30: "실험과정", 31: "질문"},
        "view": (32, ("발언1", "발언2", "발언3")),
        "choices": (33, 34),
    },
    {
        "key": "comparison",
        "name": "수능 AI 실제 비교선지형",
        "label": "수능AI실제비교선지형",
        "range": (60, 67),
        "slots": ("문항번호", "발문", "질문", "표머리", "선지1", "선지2", "선지3", "선지4", "선지5"),
        "numbered": 60,
        "keep_together": True,
        "paragraphs": {60: "발문", 61: "질문", 62: "표머리",
                       },
        "choice_rows": (63, 64, 65, 66, 67),
    },
    {
        "key": "data_table",
        "name": "수능 AI 실제 자료표형",
        "label": "수능AI실제자료표형",
        "range": (120, 128),
        "slots": ("문항번호", "자료표", "발문", "질문", "표머리", "선지1", "선지2", "선지3", "선지4", "선지5"),
        "numbered": 121,
        "keep_together": True,
        "paragraphs": {120: "자료표", 121: "발문", 122: "질문", 123: "표머리",
                       },
        "choice_rows": (124, 125, 126, 127, 128),
    },
)


def _local(element):
    return element.tag.rsplit("}", 1)[-1]


def _all_text_nodes(element):
    return [node for node in element.iter() if _local(node) == "t"]


def _marker(_slot):
    """HWP 내부는 안정적인 익명 쌍표시, 의미 이름은 라이브러리 메타데이터에 둔다."""
    return "\\\\"


def _clear_mixed_text(node):
    node.text = ""
    for child in list(node):
        child.tail = ""


def _replace_paragraph(paragraph, slot):
    """문단의 첫 글자 모양은 유지하고 내용/수식만 슬롯 하나로 바꾼다."""
    text_nodes = _all_text_nodes(paragraph)
    if not text_nodes:
        return
    marker_text = text_nodes[0]
    marker_run = next(
        run for run in paragraph.iter()
        if _local(run) == "run" and marker_text in list(run.iter()))
    for text_node in text_nodes:
        _clear_mixed_text(text_node)
    marker_text.text = _marker(slot)
    # 첫 글자가 빈 그림 닻이나 수식용 10pt run인 문항도 있다. 슬롯 문장은
    # 원본 수능의 본문 11.48pt 글자 모양을 명시적으로 받게 한다.
    style_id = paragraph.get("styleIDRef")
    preferred = "103" if slot == "표머리" else ("67" if style_id == "1" else "18")
    marker_run.set("charPrIDRef", preferred)
    # InsertText는 선택 뒤에 남은 수식용 글자 모양을 상속할 수 있다. 그림/표가
    # 없는 나머지 run과 낡은 줄 배치 정보를 없애 슬롯 run의 서식을 확실히 잇는다.
    for child in list(paragraph):
        if _local(child) == "linesegarray":
            paragraph.remove(child)
        elif (_local(child) == "run" and child is not marker_run
              and not any(_local(node) in {"pic", "tbl"} for node in child.iter())):
            paragraph.remove(child)
    # 원문 수식/고정폭 공백이 새 내용과 섞이지 않게 지운다. 그림/표는 보존한다.
    for parent in paragraph.iter():
        for child in list(parent):
            if _local(child) in {"equation", "fwSpace"}:
                parent.remove(child)


def _replace_score_paragraph(paragraph, slot):
    """원본의 오른쪽 정렬 배점 문단을 대괄호 배점 슬롯으로 바꾼다."""
    _replace_paragraph(paragraph, slot)
    text_nodes = _all_text_nodes(paragraph)
    if not text_nodes:
        raise ValueError("수능 배점 문단의 글자를 찾지 못했습니다")
    text_nodes[0].text = f"[{_marker(slot)}]"


def _replace_view_table(paragraph, slots):
    cells = [node for node in paragraph.iter() if _local(node) == "tc"]
    if len(cells) < 6:
        raise ValueError("수능 <보기> 표 구조를 찾지 못했습니다")
    content_paragraphs = [node for node in cells[5].iter() if _local(node) == "p"]
    if len(content_paragraphs) < len(slots):
        raise ValueError("수능 <보기> 항목 수가 부족합니다")
    prefixes = ("ㄱ. ", "ㄴ. ", "ㄷ. ")
    for para, prefix, slot in zip(content_paragraphs, prefixes, slots):
        _replace_paragraph(para, slot)
        first = _all_text_nodes(para)[0]
        first.text = prefix + first.text


def _choice_segments(paragraph):
    """탭으로 정렬된 수능 선지의 혼합 텍스트 조각을 순서대로 돌려준다."""
    for text_node in _all_text_nodes(paragraph):
        yield text_node, "text"
        for child in list(text_node):
            yield child, "tail"


def _replace_choices(paragraphs, slot_names):
    segments = []
    for paragraph in paragraphs:
        segments.extend(_choice_segments(paragraph))
    if len(segments) < len(slot_names):
        raise ValueError("수능 선지 5개를 찾지 못했습니다")
    for number, ((node, field), slot) in enumerate(zip(segments, slot_names), 1):
        old = getattr(node, field) or ""
        # 원문의 특수 글꼴 숫자 글리프를 보존한다. 한글에서 ①~⑤로 렌더링된다.
        prefix = old.split(" ", 1)[0] if old.strip() else ""
        setattr(node, field, f"{prefix} {_marker(slot)}" if prefix else _marker(slot))


def _replace_choice_rows(paragraphs, slot_names):
    """한 행에 하나인 표형 선지의 ①~⑤와 행 정렬을 보존한다."""
    for paragraph, slot in zip(paragraphs, slot_names):
        text_nodes = _all_text_nodes(paragraph)
        if not text_nodes:
            raise ValueError("표형 선지 행의 글자를 찾지 못했습니다")
        old = "".join(text_nodes[0].itertext())
        prefix = old.split(" ", 1)[0] if old.strip() else ""
        for text_node in text_nodes:
            _clear_mixed_text(text_node)
        text_nodes[0].text = f"{prefix} {_marker(slot)}"
        marker_run = next(
            run for run in paragraph.iter()
            if _local(run) == "run" and text_nodes[0] in list(run.iter()))
        for child in list(paragraph):
            if _local(child) == "linesegarray":
                paragraph.remove(child)
            elif _local(child) == "run" and child is not marker_run:
                paragraph.remove(child)
        for parent in paragraph.iter():
            for child in list(parent):
                if _local(child) in {"equation", "fwSpace"}:
                    parent.remove(child)


def _section_prefix(root):
    prefix = copy.deepcopy(list(root)[0])
    runs = [child for child in list(prefix) if _local(child) == "run"]
    if not runs:
        raise ValueError("원본의 구역 설정 문단을 찾지 못했습니다")
    keep_run = runs[0]
    for child in list(prefix):
        if child is not keep_run:
            prefix.remove(child)
    for child in list(keep_run):
        if _local(child) != "secPr":
            keep_run.remove(child)
    prefix.set("id", "0")
    return prefix


def _register_namespaces(xml_bytes):
    for _event, pair in ET.iterparse(BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = pair
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass


def _drop_pictures(element):
    for parent in element.iter():
        for child in list(parent):
            if _local(child) == "pic":
                parent.remove(child)


def _manual_question_number(header_root, paragraph):
    """자동번호 문단 모양을 복제해 수동 `문항번호` 슬롯용으로 바꾼다."""
    source_id = paragraph.get("paraPrIDRef")
    parent = None
    source = None
    all_ids = []
    for candidate_parent in header_root.iter():
        for child in list(candidate_parent):
            if _local(child) != "paraPr":
                continue
            try:
                all_ids.append(int(child.get("id", "0")))
            except ValueError:
                pass
            if child.get("id") == source_id:
                parent, source = candidate_parent, child
    if parent is None or source is None:
        raise ValueError(f"문항 문단 모양 {source_id}를 찾지 못했습니다")
    cloned = copy.deepcopy(source)
    new_id = str(max(all_ids, default=0) + 1)
    cloned.set("id", new_id)
    heading = next((node for node in cloned.iter() if _local(node) == "heading"), None)
    if heading is None:
        raise ValueError("문항 자동번호 속성을 찾지 못했습니다")
    heading.set("type", "NONE")
    heading.set("idRef", "0")
    # 원본 자동번호 문단은 indent=-2260이다. 번호를 텍스트 슬롯으로 바꾸면
    # 한글이 자동번호 폭을 더 이상 보상하지 않으므로, 같은 시각 위치가 되도록
    # 바이너리의 left=2260 / intent=-2260이 되도록 HWPX switch 단위의 절반인
    # 1130/-1130을 쓴다(한글 변환기가 이 두 값을 2배 HWPUNIT로 저장한다).
    cloned.set("tabPrIDRef", "0")
    for margin in (node for node in cloned.iter() if _local(node) == "margin"):
        for child in list(margin):
            if _local(child) == "left":
                child.set("value", "1130")
            elif _local(child) == "intent":
                child.set("value", "-1130")
            elif _local(child) == "lineSpacing":
                child.set("value", "145")
    parent.append(cloned)
    if parent.get("itemCnt") is not None:
        parent.set("itemCnt", str(len([node for node in list(parent)
                                      if _local(node) == "paraPr"])))
    paragraph.set("paraPrIDRef", new_id)
    # 스타일의 160% 기본값이 명시 문단 모양을 덮지 않게 바탕 스타일로 둔다.
    paragraph.set("styleIDRef", "0")
    first = _all_text_nodes(paragraph)[0]
    for child in list(first):
        first.remove(child)
    first.text = _marker("문항번호") + ". " + (first.text or "")


def _keep_with_next(header_root, paragraphs):
    """문항 중간에서 쪽/단이 갈리지 않게 마지막 전 문단을 다음 문단과 묶는다."""
    for paragraph in paragraphs[:-1]:
        source_id = paragraph.get("paraPrIDRef")
        parent = source = None
        all_ids = []
        for candidate_parent in header_root.iter():
            for child in list(candidate_parent):
                if _local(child) != "paraPr":
                    continue
                try:
                    all_ids.append(int(child.get("id", "0")))
                except ValueError:
                    pass
                if child.get("id") == source_id:
                    parent, source = candidate_parent, child
        if parent is None or source is None:
            raise ValueError(f"묶을 문단 모양 {source_id}를 찾지 못했습니다")
        cloned = copy.deepcopy(source)
        new_id = str(max(all_ids, default=0) + 1)
        cloned.set("id", new_id)
        setting = next(
            (node for node in cloned.iter() if _local(node) == "breakSetting"), None)
        if setting is None:
            raise ValueError("문단 나눔 속성을 찾지 못했습니다")
        setting.set("keepWithNext", "1")
        parent.append(cloned)
        if parent.get("itemCnt") is not None:
            parent.set("itemCnt", str(len([node for node in list(parent)
                                          if _local(node) == "paraPr"])))
        paragraph.set("paraPrIDRef", new_id)


def build_reference_fragment(reference_hwpx, destination, spec):
    """원본 HWPX 한 문항을 실제 수능 서식의 슬롯형 HWPX로 만든다."""
    reference_hwpx = pathlib.Path(reference_hwpx)
    destination = pathlib.Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(reference_hwpx) as source:
        section_bytes = source.read(SECTION_PATH)
        header_bytes = source.read(HEADER_PATH)
        _register_namespaces(section_bytes)
        _register_namespaces(header_bytes)
        original_root = ET.fromstring(section_bytes)
        header_root = ET.fromstring(header_bytes)
        children = list(original_root)
        start, end = spec["range"]
        selected = {index: copy.deepcopy(children[index]) for index in range(start, end + 1)}
        score = spec.get("score")
        score_paragraph = None
        score_is_extra = False
        if score:
            if score["source"] in selected:
                score_paragraph = selected[score["source"]]
            else:
                score_paragraph = copy.deepcopy(children[score["source"]])
                score_is_extra = True
        # 원본 페이지 배치용 강제 나눔은 재사용 물감에서는 앞 문항의 흐름을 끊는다.
        for paragraph in selected.values():
            paragraph.set("pageBreak", "0")
            paragraph.set("columnBreak", "0")
            if spec.get("drop_pictures"):
                _drop_pictures(paragraph)
        if score_paragraph is not None:
            score_paragraph.set("pageBreak", "0")
            score_paragraph.set("columnBreak", "0")
            _replace_score_paragraph(score_paragraph, score["slot"])

        for index, slot in spec.get("paragraphs", {}).items():
            _replace_paragraph(selected[index], slot)
        if spec.get("view"):
            index, slots = spec["view"]
            _replace_view_table(selected[index], slots)
        choice_paras = [selected[index] for index in spec.get("choices", ())]
        if choice_paras:
            choice_slots = [slot for slot in spec["slots"] if slot.startswith("선지")]
            _replace_choices(choice_paras, choice_slots)
        choice_rows = [selected[index] for index in spec.get("choice_rows", ())]
        if choice_rows:
            choice_slots = [slot for slot in spec["slots"] if slot.startswith("선지")]
            _replace_choice_rows(choice_rows, choice_slots)
        _manual_question_number(header_root, selected[spec["numbered"]])
        ordered_paragraphs = []
        for index in range(start, end + 1):
            ordered_paragraphs.append(selected[index])
            if (score_paragraph is not None and score_is_extra
                    and index == score["after"]):
                ordered_paragraphs.append(score_paragraph)
        if spec.get("keep_together"):
            _keep_with_next(header_root, ordered_paragraphs)

        output_root = copy.deepcopy(original_root)
        for child in list(output_root):
            output_root.remove(child)
        output_root.append(_section_prefix(original_root))
        for paragraph in ordered_paragraphs:
            output_root.append(paragraph)
        rewritten = ET.tostring(output_root, encoding="utf-8", xml_declaration=True)
        rewritten_header = ET.tostring(
            header_root, encoding="utf-8", xml_declaration=True)

        with zipfile.ZipFile(destination, "w") as target:
            for info in source.infolist():
                if info.filename == SECTION_PATH:
                    data = rewritten
                elif info.filename == HEADER_PATH:
                    data = rewritten_header
                else:
                    data = source.read(info)
                target.writestr(copy.copy(info), data)
    return destination


def build_all_reference_fragments(reference_hwpx, output_dir):
    output_dir = pathlib.Path(output_dir)
    outputs = {}
    for spec in REFERENCE_FRAGMENT_SPECS:
        destination = output_dir / f"수능_AI_실제_{spec['key']}.hwpx"
        outputs[spec["key"]] = build_reference_fragment(reference_hwpx, destination, spec)
    return outputs
