"""기출 PDF에서 그림(도해) 영역만 추출해 PNG + 메타데이터로 저장.

평가원 PDF는 연도에 따라 그림 수록 방식이 다르다(2026-07-25 실측):
- 최근 연도: 그림이 고해상도 래스터 이미지 객체 → 이미지 bbox 가 곧 그림 영역
- 일부 연도(2018 등): 그림이 벡터 도형 → 도형 군집 탐지로 영역 추정
두 경로를 모두 태워 페이지 영역을 고해상도로 렌더링(WYSIWYG)한다.

사용: python tools/extract_figures.py
입력: PDF/*.pdf  →  출력: assets/kice_figures/*.png + figures.json
"""
import json
import os
import sys

import fitz  # PyMuPDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(ROOT, "PDF")
OUT_DIR = os.path.join(ROOT, "assets", "kice_figures")
META_PATH = os.path.join(OUT_DIR, "figures.json")

ZOOM = 6.0          # 렌더 배율 (72dpi × 6 = 432dpi)
GAP = 12.0          # 이 거리(pt) 안의 요소는 같은 그림으로 합침
MARGIN = 6.0        # 저장 시 여백(pt)
MIN_SIZE = 25.0     # 그림 최소 변 길이(pt)
MIN_PATHS = 4       # 벡터 군집 최소 도형 수 (보기 상자·괘선 제외용)
MAX_AREA = 0.45     # 페이지 면적 대비 최대 비율 (이 이상은 오탐)


def frame_filter(rects, page_rect):
    """페이지 테두리·단 구분선처럼 지면을 가로지르는 얇은 선과 티끌 제거."""
    out = []
    for r in rects:
        long_h = r.width > page_rect.width * 0.8 and r.height < 3
        long_v = r.height > page_rect.height * 0.8 and r.width < 3
        speck = r.width < 2 and r.height < 2
        if not (long_h or long_v or speck):
            out.append(r)
    return out


def cluster(rects):
    """겹치거나 GAP 이내로 가까운 사각형들을 군집으로 병합. [(box, 개수)]"""
    items = [(fitz.Rect(r), 1) for r in rects]
    merged = True
    while merged:
        merged = False
        out = []
        while items:
            base, n = items.pop()
            grown = fitz.Rect(base.x0 - GAP, base.y0 - GAP, base.x1 + GAP, base.y1 + GAP)
            rest = []
            for r, m in items:
                if grown.intersects(r):
                    base |= r
                    n += m
                    merged = True
                else:
                    rest.append((r, m))
            items = rest
            out.append((base, n))
        items = out
    return items


def page_figures(page):
    """페이지 1장에서 그림 영역 목록을 뽑는다. [(box, 방식)]"""
    page_area = page.rect.get_area()

    # 1) 이미지 객체 = 그림의 씨앗. 서로 가까우면 (가)(나) 세트로 병합.
    img_rects = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    img_rects = [r for r in img_rects if r.width >= 4 and r.height >= 4]
    img_boxes = [box for box, _ in cluster(img_rects)
                 if box.width >= MIN_SIZE and box.height >= MIN_SIZE]

    # 2) 벡터 군집 (이미지가 없는 연도용). 이미지 그림과 겹치면 그쪽에 흡수.
    vec = [d["rect"] for d in page.get_drawings() if not d["rect"].is_empty]
    vec = frame_filter(vec, page.rect)
    vec_boxes = []
    for box, n in cluster(vec):
        if n < MIN_PATHS or box.width < MIN_SIZE or box.height < MIN_SIZE:
            continue
        if box.get_area() > page_area * MAX_AREA:
            continue
        absorbed = False
        for i, ib in enumerate(img_boxes):
            if box.intersects(ib):
                img_boxes[i] = ib | box
                absorbed = True
                break
        if not absorbed:
            vec_boxes.append(box)

    out = [(b, "image") for b in img_boxes if b.get_area() <= page_area * MAX_AREA]
    out += [(b, "vector") for b in vec_boxes]
    # 위→아래, 왼쪽 단→오른쪽 단 순서로 정렬
    mid_x = page.rect.width / 2
    out.sort(key=lambda t: (t[0].x0 > mid_x, t[0].y0))
    return out


def extract_pdf(path, meta):
    name = os.path.splitext(os.path.basename(path))[0]
    doc = fitz.open(path)
    count = 0
    for page in doc:
        fig_no = 0
        for box, how in page_figures(page):
            fig_no += 1
            clip = fitz.Rect(box.x0 - MARGIN, box.y0 - MARGIN,
                             box.x1 + MARGIN, box.y1 + MARGIN) & page.rect
            pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), clip=clip)
            fname = f"{name}_p{page.number + 1:02d}_f{fig_no:02d}.png"
            pix.save(os.path.join(OUT_DIR, fname))
            meta.append({
                "file": fname,
                "source_pdf": os.path.basename(path),
                "page": page.number + 1,
                "bbox_pt": [round(v, 1) for v in (box.x0, box.y0, box.x1, box.y1)],
                "size_mm": [round(box.width * 25.4 / 72, 1), round(box.height * 25.4 / 72, 1)],
                "method": how,
                "type": "",   # 유형 분류 단계에서 채움 (광선도/파면도/회로/그래프/장치도/표…)
            })
            count += 1
    doc.close()
    return count


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    meta = []
    total = 0
    pdfs = sorted(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
    for f in pdfs:
        n = extract_pdf(os.path.join(PDF_DIR, f), meta)
        total += n
        print(f"{f}: {n}")
    with open(META_PATH, "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=1)
    print(f"\ntotal {total} -> {OUT_DIR}")


if __name__ == "__main__":
    sys.exit(main())
