"""Analyze attested wording patterns in the local KICE Physics I corpus.

The script is intentionally read-only.  It discovers the user's local exam
archive, removes duplicate filenames, splits every paper with ExamPool's
existing item detector, and emits a compact JSON report used by PRD 11.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.exam_items import detect_items  # noqa: E402


CHOICE_MARK_RE = re.compile(r"[①②③④⑤]")
NUMBER_RE = re.compile(r"^\s*\d{1,2}\.\s*")
SPACE_RE = re.compile(r"\s+")

ASK_PATTERNS = (
    ("보기_있는대로", "옳은것만을<보기>에서있는대로고른것은?"),
    ("보기_모두", "옳은것을<보기>에서모두고른것은?"),
    ("학생_있는대로", "옳은학생만을있는대로고른것은?"),
    ("제시내용_있는대로", "제시한내용이옳은학생만을있는대로고른것은?"),
    ("사람_보기", "옳게말한사람을<보기>에서모두고른것은?"),
    ("가장적절", "가장적절한것은?"),
    ("옳게짝지음", "옳게짝지은것은?"),
    ("옳게나타냄", "옳게나타낸것은?"),
    ("옳게비교", "옳게비교한것은?"),
    ("직접_옳은것", "옳은것은?"),
    ("알맞은것", "알맞은것은?"),
)

INTRO_PATTERNS = (
    ("그림_(가)", re.compile(r"^그림\(가\)")),
    ("그림과_같이", re.compile(r"^그림과같이")),
    ("그림은", re.compile(r"^그림은")),
    ("다음_그림은", re.compile(r"^다음그림은")),
    ("다음은", re.compile(r"^다음은")),
    ("표는", re.compile(r"^표는")),
    ("다음과_같이", re.compile(r"^다음과같이")),
)

CHOICE_ENDINGS = (
    "이다.", "한다.", "된다.", "있다.", "없다.", "크다.", "작다.",
    "같다.", "높다.", "낮다.", "증가한다.", "감소한다.", "일정하다.",
)

ATTESTATION_PHRASES = (
    "이에대한설명으로옳은것만을<보기>에서있는대로고른것은?",
    "이에대한설명으로옳은것을<보기>에서모두고른것은?",
    "이에대한설명으로옳은것은?",
    "제시한내용이옳은학생만을있는대로고른것은?",
    "이에대한설명으로옳은것만을고른것은?",
)

STEM_PHRASES = (
    "모습을나타낸것이다.",
    "나타낸것이다.",
    "대화하는모습을나타낸것이다.",
    "설명이다.",
    "실험과정과결과이다.",
    "실험과정이다.",
    "무시한다.)",
)


def compact(text: str) -> str:
    return SPACE_RE.sub("", text).replace("〈", "<").replace("〉", ">") \
        .replace("＜", "<").replace("＞", ">").replace("<`보기`>", "<보기>")


def discover_archive() -> Path:
    desktop = Path.home() / "Desktop"
    anchors = list(desktop.rglob("p1_2007_11.pdf"))
    if not anchors:
        raise FileNotFoundError("p1_2007_11.pdf를 포함한 로컬 기출 폴더를 찾지 못했습니다.")
    anchor = anchors[0]
    for parent in anchor.parents:
        if len({p.name for p in parent.rglob("p1_*.pdf")}) >= 20:
            return parent
    return anchor.parent


def unique_papers(archive: Path) -> dict[str, Path]:
    papers = {}
    for path in archive.rglob("p1_*.pdf"):
        papers.setdefault(path.name, path)
    return dict(sorted(papers.items()))


def question_text(page, item: dict) -> str:
    rect = fitz.Rect(item["x0"], item["y0"], item["x1"], item["y1"])
    raw = " ".join(page.get_text("text", clip=rect, sort=True).split())
    return NUMBER_RE.sub("", raw, count=1).strip()


def classify_ask(stem: str) -> str:
    for label, phrase in ASK_PATTERNS:
        if phrase in stem:
            return label
    if "고른것은?" in stem:
        return "기타_고른것은"
    if "것은?" in stem:
        return "기타_것은"
    if "?" in stem:
        return "기타_의문"
    return "물음표_미추출"


def classify_intro(stem: str) -> str:
    for label, pattern in INTRO_PATTERNS:
        if pattern.match(stem):
            return label
    return "기타"


def classify_choice_ending(choice: str) -> str:
    for ending in CHOICE_ENDINGS:
        if choice.endswith(ending):
            return ending
    if choice.endswith("다."):
        return "기타_다."
    if choice.endswith("."):
        return "기타_마침표"
    return "비문장형"


def analyze(archive: Path) -> dict:
    papers = unique_papers(archive)
    intros = Counter()
    asks = Counter()
    endings = Counter()
    ask_suffixes = Counter()
    phrase_counts = Counter()
    stem_phrase_counts = Counter()
    ask_sources: dict[str, list[dict]] = defaultdict(list)
    intro_sources: dict[str, list[dict]] = defaultdict(list)
    paper_rows = []
    condition_count = 0
    view_count = 0
    negative_count = 0
    question_count = 0
    choice_count = 0
    extraction_failures = []

    for filename, path in papers.items():
        document = fitz.open(path)
        detected = 0
        for page_no, page in enumerate(document, start=1):
            for item in detect_items(page):
                detected += 1
                question_count += 1
                text = question_text(page, item)
                normalized = compact(text)
                choice_match = CHOICE_MARK_RE.search(normalized)
                stem = normalized[:choice_match.start()] if choice_match else normalized
                choices_text = normalized[choice_match.start():] if choice_match else ""
                raw_choice = CHOICE_MARK_RE.search(text)
                stem_sample = (text[:raw_choice.start()] if raw_choice else text).strip()[:700]

                intro = classify_intro(stem)
                ask = classify_ask(stem)
                intros[intro] += 1
                asks[ask] += 1
                if "?" in stem:
                    question_end = stem[:stem.rfind("?") + 1]
                    ask_suffixes[question_end[-36:]] += 1
                for phrase in ATTESTATION_PHRASES:
                    phrase_counts[phrase] += stem.count(phrase)
                for phrase in STEM_PHRASES:
                    stem_phrase_counts[phrase] += stem.count(phrase)
                condition_count += stem.count("(단,")
                view_count += int("<보기>" in stem)
                negative_count += int("옳지않" in stem or "아닌것" in stem)

                if len(ask_sources[ask]) < 5:
                    ask_sources[ask].append({
                        "paper": filename,
                        "page": page_no,
                        "item": item["num"],
                        "sample": stem_sample,
                    })
                if len(intro_sources[intro]) < 5:
                    intro_sources[intro].append({
                        "paper": filename,
                        "page": page_no,
                        "item": item["num"],
                        "sample": stem_sample,
                    })

                if choices_text:
                    pieces = [compact(part) for part in CHOICE_MARK_RE.split(choices_text)[1:]]
                    for choice in (part for part in pieces if part):
                        choice_count += 1
                        endings[classify_choice_ending(choice)] += 1

        document.close()
        paper_rows.append({"paper": filename, "questions": detected})
        if detected != 20:
            extraction_failures.append({"paper": filename, "questions": detected})

    return {
        "corpus": {
            "archive": archive.name,
            "paper_count": len(papers),
            "question_count": question_count,
            "choice_count": choice_count,
            "first_paper": next(iter(papers), ""),
            "last_paper": next(reversed(papers), "") if papers else "",
            "all_papers_have_20_questions": not extraction_failures,
            "extraction_failures": extraction_failures,
        },
        "counts": {
            "intro_frames": dict(intros.most_common()),
            "ask_frames": dict(asks.most_common()),
            "choice_endings": dict(endings.most_common()),
            "ask_suffixes": dict(ask_suffixes.most_common(60)),
            "attestation_phrases": dict(phrase_counts),
            "stem_phrases": dict(stem_phrase_counts),
            "questions_with_view": view_count,
            "questions_with_condition": condition_count,
            "negative_questions": negative_count,
        },
        "ask_sources": dict(ask_sources),
        "intro_sources": dict(intro_sources),
        "papers": paper_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze((args.archive or discover_archive()).resolve())
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
