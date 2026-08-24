# -*- coding: utf-8 -*-
r"""AI 이미지 생성 + 평가원 스타일 변환 파이프라인.

텍스트 프롬프트로 시험지용 삽화를 생성하고, 바로 평가원 흑백 스타일로 변환한다.

사용:
    from hwp_palette.hwp import exam_image_ai
    exam_image_ai.generate("화학 실험 장치 도식", "output.png")

백엔드:
    local   diffusers 로컬 추론 (CPU도 가능, 느림)
    hf-api  HuggingFace Inference API (빠름, 무료 토큰 필요)

기본값은 local. 환경변수 HF_TOKEN 이 있으면 hf-api 로 자동 전환.
"""

import os
import pathlib
import tempfile

from hwp_palette.hwp import exam_image


# ── 모델 설정 ───────────────────────────────────────────
LOCAL_MODEL = "nota-ai/bk-sdm-small"       # 1-스텝 distilled SD (0.9B, ~1.2GB)
LOCAL_STEPS = 1
LOCAL_WIDTH = 512
LOCAL_HEIGHT = 512

_pipe = None


def _get_local_pipe():
    global _pipe
    if _pipe is not None:
        return _pipe
    import torch
    from diffusers import StableDiffusionPipeline

    _pipe = StableDiffusionPipeline.from_pretrained(
        LOCAL_MODEL,
        torch_dtype=torch.float32,
        safety_checker=None,
    )
    return _pipe


def _generate_local(prompt, negative_prompt=None, width=None, height=None):
    pipe = _get_local_pipe()
    w = width or LOCAL_WIDTH
    h = height or LOCAL_HEIGHT
    neg = negative_prompt or "blurry, low quality, watermark, text, signature, nsfw"
    result = pipe(
        prompt=prompt,
        negative_prompt=neg,
        num_inference_steps=LOCAL_STEPS,
        width=w,
        height=h,
        guidance_scale=0.0,      # distilled model: cfg=0 for fewer artifacts
    )
    return result.images[0]


def _generate_hf_api(prompt, negative_prompt=None, width=None, height=None):
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        raise RuntimeError(
            "HF_TOKEN 환경변수가 없습니다. HuggingFace 에서 무료 토큰을 발급받으세요.\n"
            "https://huggingface.co/settings/tokens")
    import requests
    import io
    from PIL import Image

    model = "stabilityai/stable-diffusion-xl-base-1.0"
    url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "inputs": prompt,
        "parameters": {
            "width": width or 1024,
            "height": height or 1024,
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
        },
    }
    if negative_prompt:
        payload["parameters"]["negative_prompt"] = negative_prompt

    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        # 모델 로드 중이면 대기
        error = resp.json().get("error", "")
        if "loading" in error.lower():
            estimated = resp.json().get("estimated_time", 30)
            import time
            time.sleep(min(estimated + 5, 60))
            return _generate_hf_api(prompt, negative_prompt, width, height)
        raise RuntimeError(f"HuggingFace API 오류 ({resp.status_code}): {error}")

    return Image.open(io.BytesIO(resp.content))


def _prep_exam_prompt(prompt, style="science"):
    """시험지에 어울리는 흑백 선화 이미지를 만들도록 프롬프트를 보강한다."""
    prefixes = {
        "science": "scientific diagram, clean black and white line art, "
                   "no shading, no color, simple outlines, textbook illustration of ",
        "graph": "black and white chart, clean graph, simple lines, "
                 "no shading, no gridlines, scientific chart of ",
        "map": "black and white map outline, simple cartography, "
               "clean lines, no labels, minimalist map of ",
        "general": "black and white line drawing, clean outlines, "
                   "no shading, simple illustration, educational diagram of ",
    }
    prefix = prefixes.get(style, prefixes["science"])
    neg = ("color, shaded, grayscale gradient, realistic, photographic, "
           "blurry, complex, text, numbers, watermark, messy, sketchy")
    return prefix + prompt, neg


def generate(prompt, output_path=None, style="science",
             negative_prompt=None, width=None, height=None,
             backend="auto", convert_to_exam=True):
    """AI로 이미지를 생성하고 평가원 스타일로 변환한다.

    Args:
        prompt: 생성할 이미지에 대한 설명 (한글 가능)
        output_path: 출력 경로 (None이면 현재 디렉터리)
        style: 프롬프트 스타일 보강 ("science", "graph", "map", "general")
        negative_prompt: 제외할 요소 (None이면 자동)
        width/height: 이미지 크기 (None이면 모델 기본값)
        backend: "auto" (HF_TOKEN 있으면 hf-api), "local", "hf-api"
        convert_to_exam: 생성 후 평가원 스타일(contour)로 자동 변환 여부

    Returns:
        출력 경로 (pathlib.Path)
    """
    import time

    if output_path is None:
        output_path = f"{prompt[:30].strip().replace(' ', '_')}.png"
    out = pathlib.Path(output_path)

    # 프롬프트 보강
    full_prompt, neg_auto = _prep_exam_prompt(prompt, style)
    neg = negative_prompt or neg_auto

    # 백엔드 결정
    if backend == "auto":
        backend = "hf-api" if os.environ.get("HF_TOKEN") else "local"

    # 생성
    t0 = time.time()
    if backend == "hf-api":
        pil_img = _generate_hf_api(full_prompt, neg, width, height)
        raw_path = out.parent / f"{out.stem}_raw.png"
        pil_img.save(str(raw_path))
    else:
        pil_img = _generate_local(full_prompt, neg, width, height)
        raw_path = out.parent / f"{out.stem}_raw.png"
        pil_img.save(str(raw_path))
    elapsed = time.time() - t0

    if convert_to_exam:
        exam_image.convert(raw_path, out, style="exam-clean")
        try:
            os.unlink(raw_path)
        except Exception:
            pass
        print(f"[{elapsed:.1f}s, {backend}] {out}")
    else:
        if out != raw_path:
            import shutil
            shutil.move(str(raw_path), str(out))
        print(f"[{elapsed:.1f}s, {backend}] {out}")
    return out


def is_ready():
    """로컬 생성 준비 상태."""
    has_gpu = False
    try:
        import torch
        has_gpu = torch.cuda.is_available()
    except ImportError:
        pass
    has_hf = bool(os.environ.get("HF_TOKEN"))
    return {"gpu": has_gpu, "hf_api": has_hf,
            "recommended": "hf-api" if has_hf else "local"}
