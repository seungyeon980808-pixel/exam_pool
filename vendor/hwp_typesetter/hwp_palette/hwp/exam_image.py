# -*- coding: utf-8 -*-
r"""이미지를 평가원 시험지 삽화 스타일로 변환.

흑백 선화 — 얇은 검은 선, 순백 바탕, 계조 없음.
사진·일러스트·손그림을 시험지에 들어가는 깔끔한 삽화로 만든다.

사용:
    from hwp_palette.hwp import exam_image
    exam_image.convert("input.png", "output.png", style="contour")

스타일:
    contour   가장자리 검출 (Canny) — 사진·복잡한 그림에 적합
    sketch    필기감 (Laplacian) — 손그림·필기 느낌
    threshold 단순 이진화 (Otsu) — 이미 깔끔한 도식에 적합
    adaptive  적응형 이진화 — 얼룩이 있는 스캔본에 적합
"""

import pathlib
from dataclasses import dataclass

import cv2
import numpy as np


# ── 스타일 설정 ─────────────────────────────────────────
@dataclass
class ExamStyle:
    blur_ksize: int = 5          # 가우시안 블러 커널 (홀수)
    canny_low: int = 50          # Canny 하위 임계값
    canny_high: int = 150        # Canny 상위 임계값
    morph_ksize: int = 2         # 모폴로지 커널 (선 굵기 조절)
    close_iter: int = 1          # 닫기 반복 (끊긴 선 잇기)
    open_iter: int = 0           # 열기 반복 (잡점 제거)
    dilate_iter: int = 1         # 팽창 반복 (선 굵게)
    erode_iter: int = 0          # 침식 반복 (선 가늘게)
    threshold_value: int = 127   # 이진화 임계값 (threshold 스타일)
    adaptive_block: int = 31     # 적응형 블록 크기 (adaptive 스타일)
    adaptive_c: int = 5          # 적응형 상수
    invert: bool = False         # 흑백 반전 여부
    dpi_hint: int = 300          # 저장 시 DPI 힌트 (pHYs 청크에 기록)


STYLES = {
    "contour": ExamStyle(
        blur_ksize=5, canny_low=50, canny_high=150,
        morph_ksize=2, close_iter=1, dilate_iter=1,
    ),
    "contour-thin": ExamStyle(
        blur_ksize=5, canny_low=60, canny_high=180,
        morph_ksize=2, close_iter=1, dilate_iter=0,
    ),
    "contour-bold": ExamStyle(
        blur_ksize=7, canny_low=30, canny_high=120,
        morph_ksize=3, close_iter=2, dilate_iter=2,
    ),
    "sketch": ExamStyle(
        blur_ksize=3, canny_low=80, canny_high=200,
        morph_ksize=2, close_iter=0, dilate_iter=0,
    ),
    "threshold": ExamStyle(
        blur_ksize=3, threshold_value=127, invert=False,
        morph_ksize=2, close_iter=1, dilate_iter=1,
    ),
    "adaptive": ExamStyle(
        blur_ksize=5, adaptive_block=31, adaptive_c=5,
        morph_ksize=2, close_iter=1, dilate_iter=1,
    ),
    # ── 평가원 실전 preset ──
    "exam-clean": ExamStyle(
        # 가장 평가원 답게 — 깔끔한 윤곽선, 얇은 선
        blur_ksize=5, canny_low=40, canny_high=160,
        morph_ksize=2, close_iter=2, dilate_iter=1, erode_iter=1,
    ),
    "exam-diagram": ExamStyle(
        # 도표/그래프 — 선명한 선, 잡점 최소
        blur_ksize=3, canny_low=60, canny_high=200,
        morph_ksize=2, close_iter=2, dilate_iter=1, erode_iter=1, open_iter=1,
    ),
}


# ── 코어 변환 ───────────────────────────────────────────
def _load_image(path):
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"이미지를 열 수 없습니다: {path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img, gray


def _denoise(gray, style):
    if style.blur_ksize >= 1:
        ksize = style.blur_ksize | 1  # odd
        return cv2.GaussianBlur(gray, (ksize, ksize), 0)
    return gray


def _to_binary(gray, style):
    if style.canny_low < style.canny_high:
        edges = cv2.Canny(gray, style.canny_low, style.canny_high)
        return edges
    return _threshold_or_adaptive(gray, style)


def _threshold_or_adaptive(gray, style):
    if style.adaptive_block > 0:
        return cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            style.adaptive_block,
            style.c)
    _, binary = cv2.threshold(gray, style.threshold_value,
                              255, cv2.THRESH_BINARY)
    return binary


def _morph_cleanup(binary, style):
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                   (style.morph_ksize, style.morph_ksize))
    img = binary
    for _ in range(style.open_iter):
        img = cv2.morphologyEx(img, cv2.MORPH_OPEN, k)
    for _ in range(style.close_iter):
        img = cv2.morphologyEx(img, cv2.MORPH_CLOSE, k)
    for _ in range(style.dilate_iter):
        img = cv2.dilate(img, k)
    for _ in range(style.erode_iter):
        img = cv2.erode(img, k)
    return img


def _pad_white(binary, margin=20):
    """평가원식 여백 — 본문 가장자리를 보호한다."""
    h, w = binary.shape
    padded = np.full((h + margin * 2, w + margin * 2), 255, dtype=np.uint8)
    padded[margin:margin + h, margin:margin + w] = binary
    return padded


def _save_with_dpi(binary, output_path, dpi=300):
    path = pathlib.Path(output_path)
    cv2.imwrite(str(path), binary)
    _write_png_dpi(str(path), dpi)


def _write_png_dpi(path_str, dpi):
    """PNG pHYs 청크에 DPI 기록 — HWP 삽입 시 실제 크기로 들어간다."""
    ppm = int(dpi / 0.0254)  # pixels per metre
    with open(path_str, "r+b") as f:
        f.seek(0)
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            return
        f.seek(8)
        inserted = False
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            ln = int.from_bytes(head[:4], "big")
            typ = head[4:8]
            pos = f.tell()
            f.seek(pos + ln + 4)
            if typ == b"IDAT":
                break
            if typ == b"pHYs":
                # 덮어쓰기
                f.seek(pos)
                f.write(ppm.to_bytes(4, "big"))
                f.write(ppm.to_bytes(4, "big"))
                f.write(b"\x01")
                inserted = True
                break
        if not inserted:
            from hwp_palette.core import applog
            applog.warn("PNG pHYs 청크가 없어 DPI를 기록하지 못했습니다 — 이미지 크기가 잘못될 수 있습니다")


def convert(path, output_path=None, style="contour", margin=20, dpi=300):
    """이미지를 평가원 시험지 삽화 스타일로 변환한다.

    Args:
        path: 입력 이미지 경로
        output_path: 출력 경로 (None이면 자동: 원본명_exam.png)
        style: 변환 스타일 이름 또는 ExamStyle 객체
        margin: 하얀 여백 (px)
        dpi: 저장 DPI

    Returns:
        출력 경로 (pathlib.Path)
    """
    cfg = STYLES.get(style, style) if isinstance(style, str) else style
    if isinstance(cfg, str):
        raise ValueError(f"알 수 없는 스타일: {style} (사용 가능: {list(STYLES)})")

    in_path = pathlib.Path(path)
    if output_path is None:
        output_path = in_path.parent / f"{in_path.stem}_exam.png"
    out_path = pathlib.Path(output_path)

    _, gray = _load_image(in_path)
    gray = _denoise(gray, cfg)
    binary = _to_binary(gray, cfg)
    binary = _morph_cleanup(binary, cfg)

    if cfg.invert:
        binary = cv2.bitwise_not(binary)

    binary = _pad_white(binary, margin)
    _save_with_dpi(binary, out_path, dpi)
    return out_path


def convert_batch(input_dir, output_dir, style="contour",
                  patterns=("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.webp")):
    """디렉터리 안의 모든 이미지를 일괄 변환한다."""
    input_dir = pathlib.Path(input_dir)
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for pat in patterns:
        for p in input_dir.glob(pat):
            out = output_dir / f"{p.stem}_exam.png"
            try:
                convert(p, out, style=style)
                results.append((p.name, True, str(out)))
            except Exception as e:
                results.append((p.name, False, str(e)))
    return results
