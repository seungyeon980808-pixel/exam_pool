"""문항 제작 대화 provider.

UI와 저장 로직은 이 계약만 안다. 실제 Codex App Server 연결은 별도 adapter로
추가하고, 개발·회귀 테스트는 네트워크가 필요 없는 MockProvider로 완주한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Iterator, Protocol
from uuid import uuid4

from .item_rules import FRAME_SOURCES, infer_ask_frame, infer_intro_frame, style_prompt_contract
from ..formula_markup import normalize_reconstruction_draft_formulas

from .exam_graph_spec import GRAPH_PROMPT_RULES, GraphSpecError, normalize_figure_objects


@dataclass
class AuthoringReply:
    content: str
    proposals: list[dict]


class AuthoringProvider(Protocol):
    name: str

    def connection_state(self) -> dict: ...

    def stream(self, message: str, draft: dict,
               thread_id: str | None = None, model: str | None = None,
               reasoning_effort: str | None = None) -> tuple[str | None, Iterator[tuple[str, object]]]: ...


def _proposal(field: str, label: str, value) -> dict:
    return {"id": uuid4().hex, "field": field, "label": label, "value": value}


def _normalize_artboard(value) -> dict:
    """Return the canonical 5E artboard shape, accepting common model aliases."""
    source = value if isinstance(value, dict) else {}

    def dimension(key: str, alias: str, fallback: float) -> float:
        raw = source.get(key, source.get(alias, fallback))
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return fallback
        return number if math.isfinite(number) and number > 0 else fallback

    return {"w": dimension("w", "width", 90), "h": dimension("h", "height", 60)}


class MockProvider:
    """프론트 세로 흐름을 검증하는 결정적 로컬 provider."""

    name = "mock"

    def connection_state(self) -> dict:
        return {
            "provider": self.name,
            "connected": True,
            "account": "프론트 확인용",
            "plan": "Mock",
            "usage": None,
            "message": "MockProvider 연결됨 — 실제 Codex 호출 없이 화면을 시험합니다.",
        }

    def generate(self, message: str, draft: dict) -> AuthoringReply:
        text = message.strip()
        proposals: list[dict] = []
        lower = text.lower()

        if "그림" in text or "도판" in text or "5e" in lower:
            options = {
                "provider": "fivee_assets", "include_text": False, "composition": "combined",
                **(draft.get("_figure_options") or {}),
            }
            if "검전기" in text:
                objects = [
                        {"type": "apparatus", "kind": "electroscope", "x": -32, "y": -17,
                         "w": 22, "h": 34, "leafSpread": 0.2},
                        {"type": "apparatus", "kind": "electroscope", "x": 10, "y": -17,
                         "w": 22, "h": 34, "leafSpread": 0.8},
                ]
                if options["include_text"]:
                    objects += [
                        {"type": "text", "x": -21, "y": 22, "text": "(가)"},
                        {"type": "text", "x": 21, "y": 22, "text": "(나)"},
                    ]
                summary = "금속박 벌어짐이 다른 두 검전기를 비교하는 평가원식 흑백 도판"
            else:
                summary = "두 지점에서 자기장의 방향을 비교하는 평가원식 흑백 도판"
                objects = [
                        {"type": "apparatus", "kind": "bar_magnet", "x": -28, "y": -5,
                         "w": 26, "h": 9, "northSide": "right"},
                        {"type": "apparatus", "kind": "compass", "x": 10, "y": -9,
                         "w": 18, "h": 18, "needleAngle": 0},
                ]
            if options["provider"] == "raster_image":
                panels = [{"id": "main", "summary": summary,
                           "image_prompt": summary + ", 글자 없는 평가원식 흑백 도판"}]
            elif options["composition"] in {"auto", "separate"} and len(objects) > 1:
                panels = [
                    {"id": f"scene-{i + 1}", "summary": summary,
                     "artboard": {"w": 60, "h": 50}, "objects": [obj]}
                    for i, obj in enumerate(objects) if obj.get("type") != "text"
                ]
            else:
                panels = [{"id": "main", "summary": summary,
                           "artboard": {"w": 90, "h": 60}, "objects": objects}]
            plan = {"version": 2, "summary": summary, "options": options, "panels": panels}
            if len(panels) == 1 and options["provider"] == "fivee_assets":
                plan["artboard"] = panels[0]["artboard"]
                plan["objects"] = panels[0]["objects"]
            proposals.append(_proposal("figure_plan", "그림 설계안", plan))
        elif "해설" in text:
            proposals.append(_proposal(
                "explanation", "해설",
                "정답이 성립하는 핵심 개념을 먼저 밝히고, 나머지 선택지가 성립하지 않는 이유를 각각 확인한다.",
            ))
        elif "보기" in text and draft.get("qtype") == "합답형":
            proposals.append(_proposal("bogi_items", "보기 전체", [
                {"label": "ㄱ", "text": "제시된 조건에서 확인되는 과학 개념이다.",
                 "evidence": "선택한 참고 자료의 핵심 개념", "explanation": "근거가 진술을 직접 뒷받침하므로 옳다."},
                {"label": "ㄴ", "text": "원인과 결과를 반대로 설명한 진술이다.",
                 "evidence": "선택한 참고 자료에 제시된 인과 관계", "explanation": "근거의 인과 방향과 반대이므로 옳지 않다."},
                {"label": "ㄷ", "text": "자료의 적용 범위를 벗어난 진술이다.",
                 "evidence": "선택한 참고 자료가 한정한 조건과 적용 범위", "explanation": "근거가 성립하는 조건을 벗어나 일반화했으므로 옳지 않다."},
            ]))
        elif "선지" in text or "보기" in text:
            proposals.append(_proposal("choices", "선지 전체", [
                {"ord": 1, "text": "제시된 조건을 모두 만족한다.", "is_answer": True},
                {"ord": 2, "text": "일부 조건만 만족하므로 옳지 않다.", "is_answer": False},
                {"ord": 3, "text": "조건의 인과 관계를 반대로 해석했다.", "is_answer": False},
                {"ord": 4, "text": "주어진 범위를 벗어난 설명이다.", "is_answer": False},
                {"ord": 5, "text": "자료에서 확인할 수 없는 설명이다.", "is_answer": False},
            ]))
            proposals.append(_proposal("answer", "정답", "①"))
        else:
            passage = draft.get("passage") or "다음은 과학 수업에서 다룬 현상에 대한 설명이다."
            ask = draft.get("ask") or "이에 대한 설명으로 옳은 것은?"
            proposals.extend([
                _proposal("passage", "문제 본문", passage),
                _proposal("ask", "발문", ask),
                _proposal(
                    "explanation", "해설",
                    "제시된 조건과 성취기준을 차례로 대조하여 정답을 판단한다. 실제 문항에서는 교과서 근거를 확인해 구체화한다.",
                ),
            ])

        summary = "요청을 바탕으로 문항 수정안을 만들었습니다. 아래 제안은 아직 문항에 들어가지 않았습니다. 필요한 항목만 ‘반영’을 누르세요."
        if "mock" in lower:
            summary += " 현재 응답은 MockProvider의 시연 데이터입니다."
        return AuthoringReply(content=summary, proposals=proposals)

    def stream(self, message: str, draft: dict,
               thread_id: str | None = None, model: str | None = None,
               reasoning_effort: str | None = None) -> tuple[str | None, Iterator[tuple[str, object]]]:
        reply = self.generate(message, draft)

        def events():
            words = reply.content.split(" ")
            for i, word in enumerate(words):
                yield "delta", word + (" " if i < len(words) - 1 else "")
            yield "done", reply

        return thread_id, events()


PROPOSAL_MARKER = "<EXAMPOOL_PROPOSALS>\n"
PROPOSAL_EVENT_MARKER = "<EXAMPOOL_PROPOSAL>\n"
DEVELOPER_INSTRUCTIONS = """당신은 한국 중학교 과학 문항 제작을 돕는 대화형 조력자다.
파일이나 도구를 사용하지 말고 사용자의 요청과 현재 문항 데이터만으로 답한다.
문항 내용을 직접 저장하거나 반영했다고 말하지 않는다. 제안은 사용자가 버튼으로 선택 반영한다.
먼저 사용자에게 보여 줄 자연스러운 한국어 답변을 간결하게 작성한다.
자연스러운 답변 뒤에는 제안 시작을 알리는 <EXAMPOOL_PROPOSAL>을 새 줄에 단 한 번만 쓴다.
그 다음 줄부터 제안 JSON 객체를 한 줄에 하나씩 연속 출력한다. 배열로 묶지 말고 마커도 반복하지 않는다.
필요한 모든 제안 JSON을 출력할 때까지 첫 객체에서 응답을 끝내지 않는다.
각 제안은 field, label, value를 가진다. field는 title, qtype, standard_code, difficulty,
default_points, intent, passage, ask, bogi_items, choices, answer, explanation, figure_plan 중 하나다.
사용자가 주제나 상황을 제시하며 문항·문제의 제작, 생성, 작성, 설계 또는 출제를 요청하면
"전체", "한 번에"라는 표현이 없어도 기본적으로 완성된 문항 전체를 제안한다. 일부 항목에서 멈추지 않는다.
"발문만", "선지만", "해설만"처럼 범위를 명시한 경우에만 해당 항목만 제안한다. 정답형·합답형은
passage(불필요하면 빈 문자열), ask, choices 5개, answer, explanation을 한 응답에서 모두 제안한다.
그림이 필요한 요청이면 figure_plan도 같은 응답에 포함한다. 기존 필드가 채워져 있어도 전체 제작 요청이면
완성된 문항 전체를 다시 제안한다.
현재 문항 데이터의 _references는 사용자가 선택한 참고 자료다.
_purpose_mode가 reconstruct이면 정확히 하나의 선택 자료가 복원 원본이다. 이 경우 source_text의 문장,
수식, 보기·선지 순서와 원본 이미지의 장면 수, 상대 배치, 표지 문자·기호를 임의로 바꾸거나 의역하지
말고 충실히 재현한다. 등록 frame_id 제한은 새 문항 생성용이므로 복원 원문에는 적용하지 않는다.
복원 원문의 물리량과 수식은 반드시 [[formula:...]]로 보존한다. 예를 들어 질량 m은
[[formula:m]], 높이 9h는 [[formula:9h]], 세로 분수 7/2h는 [[formula:\frac{7}{2}h]]로 쓴다.
_purpose_mode가 create이면 usage가 content 또는 both인 source_text는 근거·맥락으로만 활용하고 그대로
베끼지 않는다. usage가 image 또는 both인 자료도 의미 관계만 참고하고 원본 구도나 문자를 복제하지 않는다.
현재 문항 데이터의 _workflow_mode가 auto이면 막연한 요청도 주제·난이도·문항 형식을 합리적으로
보완해 완성안까지 제안한다. dialogue이면 결과를 크게 바꾸는 조건이 빠졌을 때 한 번에 2~4개의
짧고 구체적인 질문을 먼저 하고, 사용자가 답하기 전에는 임의로 확정하지 않는다.
_standard_candidates가 있으면 그중 가장 적합한 성취기준을 standard_code로 제안하고, 문항 유형을
qtype으로 함께 제안한다. 기출 문장 골격과 근거 연결은 품질 권고 사항이며 초안 생성을 거부할 이유가 아니다.
bogi_items 값은 label, text, evidence, explanation을 가진 객체 배열이다. evidence에는 선택된 _references의
출처와 근거 내용을 구체적으로 요약하고, explanation에는 그 근거로 해당 보기를 참·거짓으로 판단하는 이유를 쓴다.
합답형 보기를 자동 생성하거나 수정할 때는 모든 보기 객체에 evidence와 explanation을 빠짐없이 함께 생성한다.
제시문은 passage에만, 〈보기〉 문장은 bogi_items에만 넣고
passage나 ask 문자열 안에 〈보기〉와 ㄱ·ㄴ·ㄷ 문장을 합쳐 넣지 않는다.
choices 값은 ord, text, is_answer, custom_evidence를 가진 객체 배열이다. custom_evidence에는
정오 판단에 사용한 reference_id와 출처 내용을 적는다. 근거 없이 상식이나 기억만으로 선지를 만들지 않는다.
그림이나 도판 요청에는 "이미지 도구가 없다"고 답하지 말고 figure_plan을 제안한다.
figure_plan은 version 2 형식이며 {"summary":"한국어 설명","options":{...},"panels":[...]}로 제안한다.
options는 현재 문항 데이터의 _figure_options를 그대로 따른다. 기본값은 provider=fivee_assets,
include_text=false, composition=auto이다. include_text=false이면 text·formula 객체, 글자·숫자·전하
기호·패널명·범례·치수 문자를 절대 넣지 않는다. 사용자가 명시적으로 요구해도 현재 옵션이 false이면
옵션 변경이 필요하다고 답하고 글자를 생성하지 않는다.
panels의 각 항목은 id, summary를 가지며 fivee_assets 방식이면 artboard와 objects를 가진다.
raster_image 방식이면 image_prompt와 artboard를 가지며, 생성 바탕에는 글자를 절대 넣지 않는다.
최종 그림에 글자가 필요하고 include_text=true이면 overlay_objects에 5E text/formula 객체로 따로 제안한다.
물리량과 분수는 text가 아니라 반드시 formula의 source로 쓴다. composition=combined이면 panels는 한 개이며 여러 장면을
한 아트보드 안에 배치하고, separate이면 장면마다 패널을 하나씩 만든다. auto이면 독립된 상태·시점·
사례·실험이 둘 이상일 때만 패널을 나누고, 한 장이면 한 패널만 만든다.
현재 문항 데이터의 _required_figure_count가 2 이상이면 조판 물감에 그 수만큼 독립 그림 슬롯이 있다는
뜻이다. 이때 composition은 반드시 separate로 하고 panels를 정확히 그 개수만큼 제안한다. 하나의 큰
도판으로 합치거나 같은 그림을 복제하지 말고 구조·상태·과정·결과·비교 장면으로 의미 있게 나눈다.
objects는 5E의 의미 객체만 사용한다. 현재 안전하게 쓸 수 있는 예시는 text, formula, line,
rect, ellipse, triangle, apparatus이다. apparatus kind는 wire, compass, pulley, clamp, scale,
transistor, device_box, speaker, phototube, slit, thermometer, bar_magnet, fringe_pattern,
electroscope만 허용한다. 검전기는 {"type":"apparatus","kind":"electroscope","x":-11,
"y":-17,"w":22,"h":34,"leafSpread":0.55}처럼 쓴다.
그래프 명세는 다음을 반드시 따른다:
5E endpoint objects must use canonical point objects. A line must be written as
{"type":"line","p1":{"x":-20,"y":0},"p2":{"x":20,"y":0}}.
Never use x1/y1/x2/y2 or from/to for a line.
""" + GRAPH_PROMPT_RULES + """
지원되지 않는 실험 기구가 핵심이면 objects를 비우고 blocked_reason에 필요한 전용 부품명을 적는다.
선·도형을 여러 개 조합해 지원되지 않는 기구를 흉내 내지 않는다. 좌표 단위는 mm이며 원점은 중앙이다.
첫 마커 뒤에는 마커와 JSON 객체 외의 문장을 쓰지 않는다.

""" + style_prompt_contract()


class CodexLocalProvider:
    name = "codex_local"
    # 이 값이 바뀌면 기존 App Server 스레드는 최신 developerInstructions를 모르므로
    # ExamPool이 다음 요청에서 새 스레드를 시작한다. 저장된 대화·문항 초안은 유지된다.
    protocol_version = "authoring-v18-raster-overlay-formulas"

    def connection_state(self) -> dict:
        from .codex_app_server import CodexAppServerError, codex_app_server
        try:
            state = codex_app_server.account_state()
        except CodexAppServerError as exc:
            return {
                "provider": self.name, "connected": False, "service_available": False,
                "signed_in": False, "account": None, "plan": None,
                "rate_limits": None, "usage": None, "message": str(exc),
            }
        account = state.get("account") or {}
        signed_in = bool(account)
        return {
            "provider": self.name, "connected": signed_in,
            "service_available": True, "signed_in": signed_in,
            "account": account.get("email"), "account_type": account.get("type"),
            "plan": account.get("planType"), "rate_limits": state.get("rate_limits"),
            "usage": state.get("usage"), "model": state.get("model"),
            "models": state.get("models") or [],
            "capabilities": state.get("capabilities") or {},
            "message": "ChatGPT 계정으로 연결되었습니다." if signed_in else "ChatGPT 로그인이 필요합니다.",
        }

    @staticmethod
    def _prompt(message: str, draft: dict) -> str:
        current = {key: draft.get(key) for key in (
            "title", "qtype", "is_negative", "standard_code", "passage", "material",
            "ask", "bogi_items", "default_points", "behavior", "origin",
            "choices", "answer", "explanation", "difficulty", "intent",
            "figure_plan",
            "_figure_options",
            "_references",
            "_reference_bundle",
            "_purpose_mode",
            "style_meta",
        )}
        compact = re.sub(r"\s+", "", message)
        field_words = ("제시문", "발문", "보기", "선지", "정답", "해설", "그림", "도판")
        explicit_partial = any(f"{field}만" in compact for field in field_words)
        creation_request = (
            "출제" in compact
            or (any(noun in compact for noun in ("문항", "문제"))
                and any(verb in compact for verb in ("만들", "제작", "생성", "작성", "설계", "내줘")))
        )
        if explicit_partial:
            scope = "명시적으로 지정된 항목만 제안한다."
        elif creation_request:
            scope = (
                "전체 문항 제작 요청이다. '전체'라는 단어가 없어도 제시문(없으면 빈 값), 발문, "
                "선지 5개, 정답, 해설을 모두 제안하고 필요한 경우 그림 설계도 포함한다."
            )
        else:
            scope = "새 문항 제작 맥락이면 전체 문항을, 특정 항목 수정 맥락이면 해당 항목을 제안한다."
        return (f"현재 문항 데이터:\n{json.dumps(current, ensure_ascii=False)}\n\n"
                f"요청 범위 판정:\n{scope}\n\n사용자 요청:\n{message.strip()}")

    @staticmethod
    def _validated_proposals(value, figure_options: dict | None = None,
                             reconstruction: bool = False) -> list[dict]:
        allowed = {
            "title", "qtype", "standard_code", "difficulty", "default_points", "intent",
            "passage", "ask", "bogi_items", "choices", "answer", "explanation", "figure_plan",
        }
        if not isinstance(value, list):
            return []
        out = []
        for item in value:
            if not isinstance(item, dict) or item.get("field") not in allowed or "value" not in item:
                continue
            proposal = dict(item)
            proposal["id"] = uuid4().hex
            proposal["label"] = str(proposal.get("label") or proposal["field"])
            if proposal["field"] in {"title", "qtype", "standard_code", "difficulty", "intent"}:
                if not isinstance(proposal["value"], str):
                    continue
                proposal["value"] = proposal["value"].strip()
            if proposal["field"] == "qtype" and proposal["value"] not in {"정답형", "합답형", "서술형"}:
                continue
            if proposal["field"] == "default_points":
                try:
                    proposal["value"] = float(proposal["value"])
                except (TypeError, ValueError):
                    continue
            if proposal["field"] in {"passage", "ask"}:
                raw_value = proposal["value"]
                # Models sometimes return the documented metadata envelope as the
                # value itself. Only its text belongs in the user-visible draft.
                if isinstance(raw_value, dict):
                    text_value = str(raw_value.get("text") or "").strip()
                    proposal["frame_id"] = proposal.get("frame_id") or raw_value.get("frame_id")
                    proposal["style_sources"] = (
                        proposal.get("style_sources") or raw_value.get("style_sources") or []
                    )
                elif isinstance(raw_value, str):
                    text_value = raw_value.strip()
                else:
                    continue
                if not text_value:
                    continue
                proposal["value"] = text_value
                frame = (infer_intro_frame(text_value) if proposal["field"] == "passage"
                         else infer_ask_frame(text_value))
                if frame:
                    proposal["frame_id"] = frame
                    proposal["style_sources"] = proposal.get("style_sources") or FRAME_SOURCES[frame]
                if reconstruction:
                    proposal["value"] = normalize_reconstruction_draft_formulas(
                        {proposal["field"]: proposal["value"]}
                    )[proposal["field"]]
            if proposal["field"] == "answer":
                if not isinstance(proposal["value"], (str, int, float)):
                    continue
                raw_answer = str(proposal["value"]).strip()
                proposal["value"] = raw_answer
                if raw_answer in {"1", "2", "3", "4", "5"}:
                    proposal["value"] = "①②③④⑤"[int(raw_answer) - 1]
            if proposal["field"] == "explanation":
                if not isinstance(proposal["value"], str) or not proposal["value"].strip():
                    continue
                proposal["value"] = proposal["value"].strip()
            if proposal["field"] == "bogi_items":
                items = proposal["value"]
                if not isinstance(items, list) or any(
                    not isinstance(row, dict)
                    or not isinstance(row.get("text"), str) or not row["text"].strip()
                    or not isinstance(row.get("evidence"), str) or not row["evidence"].strip()
                    or not isinstance(row.get("explanation"), str) or not row["explanation"].strip()
                    for row in items
                ):
                    continue
                labels = "ㄱㄴㄷㄹㅁ"
                proposal["value"] = [
                    {"label": str(row.get("label") or (labels[i] if i < len(labels) else i + 1)),
                     "text": str(row["text"]).strip(),
                     "evidence": str(row["evidence"]).strip(),
                     "explanation": str(row["explanation"]).strip(),
                     "proposition_id": None, "variant_id": None}
                    for i, row in enumerate(items)
                ]
            if proposal["field"] == "choices":
                choices = proposal["value"]
                if not isinstance(choices, list) or any(
                    not isinstance(row, dict)
                    or not isinstance(row.get("text"), str)
                    or not isinstance(row.get("custom_evidence", row.get("evidence", "")), str)
                    for row in choices
                ):
                    continue
                normalized_choices = []
                for index, row in enumerate(choices):
                    evidence = str(row.get("custom_evidence") or row.get("evidence") or "").strip()
                    normalized_choices.append({
                        "ord": int(row.get("ord") or index + 1),
                        "text": str(row.get("text") or "").strip(),
                        "is_answer": bool(row.get("is_answer")),
                        "custom_evidence": evidence,
                    })
                if any(not row["text"] for row in normalized_choices):
                    continue
                proposal["value"] = normalized_choices
                if reconstruction:
                    proposal["value"] = normalize_reconstruction_draft_formulas(
                        {"choices": proposal["value"]}
                    )["choices"]
            if proposal["field"] == "figure_plan":
                plan = proposal["value"]
                if not isinstance(plan, dict) or not isinstance(plan.get("summary"), str):
                    continue
                options = {
                    "provider": "fivee_assets", "include_text": False,
                    "composition": "auto", **(figure_options or {}),
                }
                panels = plan.get("panels")
                if not isinstance(panels, list):
                    panels = [{
                        "id": "main", "summary": plan.get("summary", ""),
                        "artboard": plan.get("artboard") or {"w": 90, "h": 60},
                        "objects": plan.get("objects") or [],
                    }]
                normalized_panels = []
                for index, panel in enumerate(panels):
                    if not isinstance(panel, dict):
                        continue
                    row = dict(panel)
                    row["id"] = str(row.get("id") or f"panel-{index + 1}")
                    if options["provider"] == "fivee_assets":
                        objects = row.get("objects") or []
                        if not isinstance(objects, list) or any(not isinstance(o, dict) for o in objects):
                            continue
                        if not options["include_text"]:
                            cleaned = []
                            for raw in objects:
                                if raw.get("type") in {"text", "formula"}:
                                    continue
                                obj = dict(raw)
                                for key in ("text", "label", "dimensionLabel", "labelX", "labelY"):
                                    obj.pop(key, None)
                                obj["showTickLabels"] = False
                                obj["showAxisLabels"] = False
                                cleaned.append(obj)
                            objects = cleaned
                        try:
                            row["objects"] = normalize_figure_objects(
                                objects, include_text=bool(options["include_text"]),
                                path=f"figure_plan.panels[{index}].objects",
                            )
                        except GraphSpecError:
                            continue
                        row["artboard"] = _normalize_artboard(row.get("artboard"))
                    else:
                        row["image_prompt"] = str(row.get("image_prompt") or row.get("summary") or plan["summary"])
                        row["artboard"] = _normalize_artboard(row.get("artboard"))
                        overlay_objects = row.get("overlay_objects") or []
                        if options["include_text"] and isinstance(overlay_objects, list):
                            try:
                                row["overlay_objects"] = normalize_figure_objects(
                                    overlay_objects, include_text=True,
                                    path=f"figure_plan.panels[{index}].overlay_objects",
                                )
                            except GraphSpecError:
                                continue
                        else:
                            row["overlay_objects"] = []
                    normalized_panels.append(row)
                if not normalized_panels:
                    continue
                normalized_plan = {
                    "version": 2, "summary": plan["summary"], "options": options,
                    "panels": normalized_panels,
                }
                if plan.get("blocked_reason"):
                    normalized_plan["blocked_reason"] = plan["blocked_reason"]
                if len(normalized_panels) == 1 and options["provider"] == "fivee_assets":
                    normalized_plan["artboard"] = normalized_panels[0]["artboard"]
                    normalized_plan["objects"] = normalized_panels[0]["objects"]
                proposal["value"] = normalized_plan
            out.append(proposal)
        return out

    def stream(self, message: str, draft: dict,
               thread_id: str | None = None, model: str | None = None,
               reasoning_effort: str | None = None) -> tuple[str | None, Iterator[tuple[str, object]]]:
        from .codex_app_server import codex_app_server
        image_paths = [
            str(row.get("image_path") or "") for row in draft.get("_references") or []
            if row.get("usage") in {"content", "image", "both"} and row.get("image_path")
        ]
        active_id, raw_deltas = codex_app_server.stream_turn(
            thread_id, self._prompt(message, draft), DEVELOPER_INSTRUCTIONS,
            model=model, reasoning_effort=reasoning_effort,
            local_image_paths=image_paths)

        def events():
            full = ""
            emitted = 0
            marker_found = ""
            proposal_cursor = 0
            proposals = []
            decoder = json.JSONDecoder()
            for delta in raw_deltas:
                full += delta
                event_at = full.find(PROPOSAL_EVENT_MARKER)
                batch_at = full.find(PROPOSAL_MARKER)
                candidates = [(event_at, "events", PROPOSAL_EVENT_MARKER),
                              (batch_at, "batch", PROPOSAL_MARKER)]
                candidates = [item for item in candidates if item[0] >= 0]
                marker_at, marker_mode, marker_token = min(candidates, default=(-1, "", ""))
                if marker_at >= 0:
                    if marker_at > emitted:
                        yield "delta", full[emitted:marker_at]
                    if not marker_found:
                        marker_found = marker_mode
                        proposal_cursor = marker_at + len(marker_token)
                    emitted = marker_at
                    if marker_found == "batch":
                        continue
                    while True:
                        while (proposal_cursor < len(full) and full[proposal_cursor].isspace()
                               and not full.startswith(PROPOSAL_EVENT_MARKER, proposal_cursor)):
                            proposal_cursor += 1
                        if full.startswith(PROPOSAL_EVENT_MARKER, proposal_cursor):
                            proposal_cursor += len(PROPOSAL_EVENT_MARKER)
                            continue
                        try:
                            raw_proposal, end = decoder.raw_decode(full, proposal_cursor)
                        except json.JSONDecodeError:
                            break
                        proposal_cursor = end
                        validated = self._validated_proposals(
                            [raw_proposal], draft.get("_figure_options") or {},
                            draft.get("_purpose_mode") == "reconstruct",
                        )
                        if validated:
                            proposals.extend(validated)
                            yield "proposal", validated[0]
                    continue
                if not marker_found:
                    marker_guard = max(len(PROPOSAL_MARKER), len(PROPOSAL_EVENT_MARKER))
                    safe_end = max(emitted, len(full) - marker_guard)
                    if safe_end > emitted:
                        yield "delta", full[emitted:safe_end]
                        emitted = safe_end
            if marker_found == "events":
                visible = full.split(PROPOSAL_EVENT_MARKER, 1)[0]
            elif marker_found == "batch":
                visible, raw_json = full.split(PROPOSAL_MARKER, 1)
                try:
                    proposals = self._validated_proposals(
                        json.loads(raw_json.strip()), draft.get("_figure_options") or {},
                        draft.get("_purpose_mode") == "reconstruct",
                    )
                except json.JSONDecodeError:
                    proposals = []
            else:
                visible = full
                if len(full) > emitted:
                    yield "delta", full[emitted:]
            yield "done", AuthoringReply(content=visible.strip(), proposals=proposals)

        return active_id, events()


_PROVIDERS: dict[str, AuthoringProvider] = {
    "codex_local": CodexLocalProvider(), "mock": MockProvider(),
}


def get_provider(name: str) -> AuthoringProvider:
    return _PROVIDERS.get(name) or _PROVIDERS["codex_local"]
