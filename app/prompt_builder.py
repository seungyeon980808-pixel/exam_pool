"""청사진 → 출제 지시문 (Claude Code 에 붙여넣는 텍스트).

한 장의 지시문에 ①슬롯별 주문 ②MCP 사용 규약 ③파일명 규약을 전부 담는다 —
붙여넣는 순간 다른 문서를 뒤지지 않아도 작업이 시작되게. 루트 CLAUDE.md 의
프롬프트 규칙(한국어 + 영어 병기, 마지막 고정 문구)을 따른다.
"""


def _slot_line(s: dict) -> str:
    parts = [f"{s['ord']}번 [{s['plan_qtype']}]"]
    if s["plan_standard_code"]:
        parts.append(f"성취기준 {s['plan_standard_code']}")
    if s["plan_topic"]:
        parts.append(f"주제: {s['plan_topic']}")
    if s["plan_is_negative"]:
        parts.append("부정발문(옳지 않은)")
    if s["points"] is not None:
        parts.append(f"{s['points']}점")
    if s["plan_needs_figure"]:
        fig = f"그림 필요 → 파일명 {s['figure_name']}"
        if s["plan_figure_hint"]:
            fig += f" ({s['plan_figure_hint']})"
        parts.append(fig)
    line = " · ".join(parts)
    if s["plan_situation"]:
        line += f"\n   상황: {s['plan_situation']}"
    return line


def build(bp: dict) -> str:
    st = bp["set"]
    slots = [s for s in bp["slots"] if not s["question_id"]]
    figure_slots = [s for s in slots if s["plan_needs_figure"]]

    lines = [
        f"## 출제 지시 — {st['name']} (세트 id {st['id']}, 약칭 {st['short_code']})",
        "",
        "아래 슬롯을 순서대로 출제하라. 진행 전 docs/PIPELINE.md 를 읽는다.",
        "",
        "### 슬롯",
        *[_slot_line(s) for s in slots],
        "",
        "### 절차 (슬롯마다)",
        "1. get_blueprint 로 슬롯 확인 → get_standard 로 성취기준·해설 읽기",
        "2. search_evidence 로 교과서 근거 검색 — 근거에 실제로 있는 내용만 쓴다",
        "3. 참 명제는 create_pool_item + add_evidence, 오답 재료는 add_false_variant",
        "4. create_question 으로 문항 조립 (선지는 proposition_id/variant_id 연결) →",
        "   attach_to_set 으로 슬롯에 연결 → check_question_rules 로 자가 점검",
    ]
    if figure_slots:
        names = ", ".join(s["figure_name"] for s in figure_slots)
        lines += [
            "5. 그림 슬롯은 5E 로 그린다 (프로젝트 CLAUDE.md 의 그림 규칙 문서를 먼저 읽을 것):",
            f"   페이지 이름 = 파일명 = {names}",
            "   그리기 → export_image 눈 확인 → save_image 로 세트 사진 폴더에 저장(300dpi)",
            "   → create_question/update_question 의 material 에 파일명 기록",
        ]
    lines += [
        "",
        "### 규칙",
        "- 근거 없는 참 명제 금지. 모든 선다형 문항에 근거 1개 이상.",
        "- 부정발문은 발문의 '옳지 않은'이 자동 강조되므로 문구를 그대로 쓴다.",
        "- 이미 문항이 있는 슬롯은 건드리지 않는다.",
        "- 완료 후: 슬롯별 문항 id·근거 수·그림 파일 경로를 표로 보고한다.",
        "",
        "---",
        f"Create exam questions for every empty slot of exam set {st['id']} "
        f"('{st['name']}', short code {st['short_code']}) following the slot plan above. "
        "For each slot: read the achievement standard, search textbook evidence, "
        "create propositions with evidence, assemble the question via create_question, "
        "attach it with attach_to_set, and self-check with check_question_rules. "
        + ("Draw required figures in 5E (page name = file name as listed), verify with "
           "export_image, then save with save_image at 300dpi and record the file name "
           "in the question's material field. " if figure_slots else "")
        + "Do not ask clarifying questions. Make reasonable assumptions and proceed.",
    ]
    return "\n".join(lines)
