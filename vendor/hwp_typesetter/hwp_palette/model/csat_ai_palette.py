# -*- coding: utf-8 -*-
"""2009년 9월 물리 II 원본에서 만든 실제 수능 물감을 설치한다.

학교 시험지용 기존 조각은 사용하지 않는다. 문항 물감은 제공된 원본의 문단·글자
모양, 보기 표, 그림 자리, 탭 선지를 직접 보존한 HWP 파일만 등록한다.
"""

from io import BytesIO
import copy
import pathlib
import shutil
import time
import zipfile

from PIL import Image

from hwp_palette.model import library, palette
from hwp_palette.model.csat_reference_fragments import REFERENCE_FRAGMENT_SPECS


PALETTE_NAME = "수능양식 ai"
SUBCATEGORY = "수능 AI"
TAGS = ["수능", "AI조판", "참고본"]

FORM_NAME = "수능 AI 첫면 기본틀"
FORM_LABEL = "수능AI첫면틀"
REFERENCE_NAME = "수능 AI 물리II 4쪽 비교본"
REFERENCE_LABEL = "수능AI4쪽비교본"

FORM_SLOT_NAMES = ("시험지머리문구", "영역과목명")

# 공개 호환용 이름. 각 사양은 기존 물감의 source가 아니라 원본 문항 범위를 가리킨다.
TEMPLATE_SPECS = REFERENCE_FRAGMENT_SPECS

# 2026-08-08 이전 버전이 학교 시험지 조각에 잘못 붙였던 이름들.
OBSOLETE_SCHOOL_STYLE_TEMPLATES = (
    "수능 AI 합답형 그림1", "수능 AI 합답형 그림2", "수능 AI 실험 합답형",
    "수능 AI 일반형 그림1 소", "수능 AI 일반형 그림2 소", "수능 AI 일반형 그림1 대",
    "수능 AI 보기상자", "수능 AI 선지 1행", "수능 AI 선지 2행",
    "수능 AI 선지 3행", "수능 AI 선지 5행", "수능 AI 표형 선지 3행",
    "수능 AI 표형 선지 5행",
)


REFERENCE_METRICS = {
    "paper_mm": [297.0, 420.0],
    "page_hwpunit": [84188, 119052],
    "margins_hwpunit": {
        "left": 8788, "right": 8788, "top": 15874, "bottom": 8504,
        "header": 0, "footer": 3828,
    },
    "columns": {"count": 2, "gap_hwpunit": 3120, "divider": True},
    "reference_pages": 4,
    "reference_questions": 20,
    "reference_images": 21,
    "question_families": [
        "합답형(보기 상자)", "직접 5지형", "실험·자료 박스형",
        "표형 선택지", "인물 발언형",
    ],
}


def _blank_picture(data, suffix):
    """원본 픽셀 크기와 파일 형식을 유지한 흰 그림을 반환한다."""
    with Image.open(BytesIO(data)) as source:
        blank = Image.new("RGB", source.size, "white")
        output = BytesIO()
        fmt = source.format or {".jpg": "JPEG", ".jpeg": "JPEG", ".pcx": "PCX"}.get(
            suffix.lower(), "PNG")
        options = {"quality": 95, "subsampling": 0} if fmt == "JPEG" else {}
        blank.save(output, format=fmt, **options)
        return output.getvalue()


def build_blank_reference(reference_hwpx, destination):
    """그림만 비운 4쪽 비교본 HWPX를 만든다.

    XML은 한 글자도 바꾸지 않으므로 줄바꿈, 수식, 표, 마스터 페이지와 모든
    그림의 배치 사각형이 기준 파일과 동일하다.
    """
    reference_hwpx = pathlib.Path(reference_hwpx)
    destination = pathlib.Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(reference_hwpx) as source, zipfile.ZipFile(destination, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            path = pathlib.PurePosixPath(info.filename)
            if path.parts[:1] == ("BinData",) and path.suffix.lower() in {".jpg", ".jpeg", ".pcx", ".png"}:
                data = _blank_picture(data, path.suffix)
            target.writestr(copy.copy(info), data)
    return destination


def convert_hwpx_to_hwp(source, destination):
    """한글 자체 변환기로 HWPX를 HWP로 저장한다."""
    from pyhwpx import Hwp

    source = pathlib.Path(source).resolve()
    destination = pathlib.Path(destination).resolve()
    hwp = Hwp(visible=False, register_module=True, on_quit=False)
    try:
        if not hwp.open(str(source)):
            raise RuntimeError(f"HWPX를 열지 못했습니다: {source}")
        if not hwp.save_as(str(destination), format="HWP"):
            raise RuntimeError(f"HWP로 저장하지 못했습니다: {destination}")
    finally:
        hwp.quit()
    return destination


def build_flow_safe_form(source, destination):
    """본문 삽입 때 첫면 유의사항의 세로 간격이 사라지지 않는 양식을 만든다.

    원래 양식은 ``\\본문\\``이 있는 문단 자체에 큰 위 간격이 걸려 있다. 그
    문단을 표 조각으로 치환하면 표의 문단 모양이 간격까지 덮어써 유의사항 위로
    올라간다. 같은 위치에 보이지 않는 닻 문단을 남기고 다음 문단에 본문 표시를
    옮기면 원본의 첫면 흐름을 유지할 수 있다.
    """
    from pyhwpx import Hwp

    source = pathlib.Path(source).resolve()
    destination = pathlib.Path(destination).resolve()
    hwp = Hwp(visible=False, register_module=True, on_quit=False)
    try:
        if not hwp.open(str(source)):
            raise RuntimeError(f"수능 첫면 양식을 열지 못했습니다: {source}")
        hwp.MoveDocBegin()
        if not hwp.find("\\본문\\"):
            raise RuntimeError("수능 첫면 양식에서 본문 표시를 찾지 못했습니다")
        # 선택된 본문 표시의 글자 모양을 1pt로 줄인 뒤 보통 공백으로 바꾼다.
        # U+200B/U+2060은 구형 수능 글꼴에서 네모 기호로 보일 수 있다.
        hwp.set_font(Height=1)
        hwp.insert_text(" ")
        hwp.BreakPara()
        hwp.insert_text("\\본문\\")
        if not hwp.save_as(str(destination), format="HWP"):
            raise RuntimeError(f"흐름 보정 양식을 저장하지 못했습니다: {destination}")
    finally:
        hwp.quit()
    return destination


def _copy_capture(source, text):
    def save_to(destination):
        shutil.copy2(source, destination)
        return text
    return save_to


def _set_item_metadata(category, item_id, *, slot_names, slot_count, label, subcat):
    last_error = None
    for attempt in range(20):
        data = library.load()
        item = next(it for it in data[category] if it.get("id") == item_id)
        item["slot_names"] = list(slot_names)
        item["slot_count"] = int(slot_count)
        item["label"] = label
        item["subcat"] = subcat
        item["tags"] = list(TAGS)
        try:
            library.save(data)
            return
        except PermissionError as error:
            last_error = error
            time.sleep(0.05 * (attempt + 1))
    raise last_error


def _upsert_template(source, name, label, slot_names):
    source = pathlib.Path(source)
    synthetic = "\n".join(f"\\{slot}\\" for slot in slot_names)
    data = library.load()
    existing = next((it for it in data["템플릿"] if it.get("name") == name), None)
    capture = _copy_capture(source, synthetic)
    if existing:
        library.replace_template_fragment(
            existing["id"], capture, slot_count=len(slot_names), category="템플릿")
        item_id = existing["id"]
    else:
        item_id = library.add_template_from_capture(
            name, capture, label=label, tags=TAGS, slot_count=len(slot_names),
            subcat=SUBCATEGORY)
    _set_item_metadata("템플릿", item_id, slot_names=slot_names,
                       slot_count=len(slot_names), label=label, subcat=SUBCATEGORY)
    return item_id


def _upsert_form(source, name, label, slot_names):
    data = library.load()
    existing = next((it for it in data["양식"] if it.get("name") == name), None)
    if existing:
        library.replace_template_fragment(
            existing["id"], _copy_capture(source, ""), slot_count=len(slot_names),
            category="양식")
        item_id = existing["id"]
    else:
        item_id = library.add_form_from_file(
            name, source, label=label, tags=TAGS, slot_count=len(slot_names),
            slot_names=list(slot_names), subcat=SUBCATEGORY)
    _set_item_metadata("양식", item_id, slot_names=slot_names,
                       slot_count=len(slot_names), label=label, subcat=SUBCATEGORY)
    return item_id


def _delete_obsolete_school_templates():
    for item in list(library.load().get("템플릿", [])):
        if item.get("name") not in OBSOLETE_SCHOOL_STYLE_TEMPLATES:
            continue
        last_error = None
        for attempt in range(20):
            try:
                library.delete_item("템플릿", item["id"])
                break
            except PermissionError as error:
                last_error = error
                time.sleep(0.05 * (attempt + 1))
        else:
            raise last_error


def install(blank_reference_hwp=None, first_page_form_hwp=None, template_hwps=None):
    """실제 원본에서 생성한 문항 HWP를 등록 기능으로 팔레트에 설치한다."""
    data = library.load()
    forms = {item.get("name"): item for item in data["양식"]}
    if "수능양식" not in forms:
        raise RuntimeError("기존 수능 첫면 양식 '수능양식'을 찾지 못했습니다")
    template_hwps = template_hwps or {}
    missing = [spec["key"] for spec in TEMPLATE_SPECS
               if spec["key"] not in template_hwps]
    if missing:
        raise RuntimeError("원본에서 만든 수능 문항 HWP가 없습니다: " + ", ".join(missing))

    form_source = first_page_form_hwp or library.template_path(forms["수능양식"])
    form_id = _upsert_form(form_source, FORM_NAME, FORM_LABEL, FORM_SLOT_NAMES)
    template_ids = {}
    for spec in TEMPLATE_SPECS:
        template_ids[spec["name"]] = _upsert_template(
            template_hwps[spec["key"]], spec["name"], spec["label"], spec["slots"])
    # 잘못 붙인 '수능 AI' 이름이 창고 검색에 다시 나타나지 않도록 제거한다.
    _delete_obsolete_school_templates()

    reference_id = None
    if blank_reference_hwp:
        reference_id = _upsert_form(
            pathlib.Path(blank_reference_hwp), REFERENCE_NAME, REFERENCE_LABEL, ())

    blocks = [
        {"type": "form", "ref": form_id, "form": FORM_NAME,
         "name": "① 수능 첫면 틀", "row": 0, "col": 0, "span": 3, "rows": 2,
         "color": "#e8f1fb"},
    ]
    if reference_id:
        blocks.append(
            {"type": "form", "ref": reference_id, "form": REFERENCE_NAME,
             "name": "원본 4쪽 비교본", "row": 0, "col": 3, "span": 3, "rows": 2,
             "color": "#edf3f8"})
    for order, spec in enumerate(TEMPLATE_SPECS):
        blocks.append({
            "type": "template", "ref": template_ids[spec["name"]],
            "template": spec["name"], "name": spec["name"].removeprefix("수능 AI 실제 "),
            "row": 2 + order // 4, "col": (order % 4) * 3,
            "span": 3, "rows": 1, "color": "#edf7ee",
        })
    blocks.extend([
        {"type": "stack", "name": "수능 기호", "row": 4, "col": 9,
         "span": 3, "rows": 1, "color": "#f9f0e7", "items": [
             {"type": "char", "value": value, "name": value}
             for value in ("①", "②", "③", "④", "⑤", "ㄱ.", "ㄴ.", "ㄷ.", "[3점]")
         ]},
        {"type": "function", "name": "수능 본문 조판", "row": 4, "col": 0,
         "span": 3, "rows": 1, "color": "#eef4fa", "actions": [
             {"func": "글씨체", "value": "신명 중명조"},
             {"func": "글씨크기", "value": 10.5},
             {"func": "줄간격", "value": 160},
             {"func": "자간 자동조절", "value": 30},
             {"func": "어절단위 줄바꿈"},
         ]},
        {"type": "builtin", "key": "spacing_fit", "name": "줄끝 자간 보정",
         "row": 4, "col": 3, "span": 3, "rows": 1, "color": "#f2eff9"},
        {"type": "builtin", "key": "convert", "name": "③ AI 원고 조판",
         "row": 4, "col": 6, "span": 3, "rows": 1, "color": "#fff2e5"},
        {"type": "char", "value": "* 확인 사항", "name": "확인 사항",
         "row": 4, "col": 9, "span": 3, "rows": 1, "color": "#fff7e9"},
    ])

    tab = {"name": PALETTE_NAME, "cols": 12, "blocks": blocks}
    tabs = [tab_ for tab_ in palette.load_tabs() if tab_.get("name") != PALETTE_NAME]
    tabs.append(tab)
    palette.save_tabs(tabs)
    return {
        "tab": tab, "form_id": form_id, "reference_id": reference_id,
        "template_ids": template_ids,
    }
