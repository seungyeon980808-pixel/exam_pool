# 수학 PDF→HWP 수식 보존 작업지시서

이 문서는 저작권 자료를 포함하지 않는 범용 작업 규칙이다. 시험 PDF의
내용은 각 작업의 외부 원본으로만 취급하고 저장소나 테스트 fixture에 복사하지
않는다. 목표는 수학 내용을 읽기 쉬운 모양으로 흉내 내는 것이 아니라, HWP/HWPX
안에 실제로 수정할 수 있는 네이티브 수식 개체를 만들고 원문 구조를 보존하는
것이다.

## 적용 범위와 우선순위

문제지·해설지·미주 본문에 있는 모든 수식에 적용한다. 사용자가 지정한 기준
HWP의 페이지·문단·수식 스타일이 있으면 그 기준을 우선하고, 내용 정확성 및
네이티브 편집성 gate는 어떤 스타일에서도 완화하지 않는다.

작업 순서는 다음과 같다.

1. PDF에서 페이지·문항·수식·표·그림의 원본 좌표와 순서를 manifest로 만든다.
2. PDF 텍스트/벡터 추출과 600 dpi 확대 검수를 통해 수식 source를 확정한다.
   OCR은 초벌 후보일 뿐이며 수식의 숫자나 범위를 수학적으로 추측하지 않는다.
3. 각 source를 MathIR 또는 동등한 구조 인벤토리로 분석한다.
4. HWP `EquationCreate`로 네이티브 수식을 삽입한다. 실패하면 평문·이미지로
   대체하지 말고 즉시 FAIL로 올린다.
5. HWPX의 모든 `Contents/section*.xml`과 HWP 재열기 COM `eqed` 목록을 읽어
   source 순서, script, 글꼴, 크기 및 수를 비교한다.
6. 하나를 수정하면 해당 문서 전체를 처음부터 다시 검증한다. PASS가 아닌
   문서는 최종 폴더로 승격하거나 “완성본”으로 표시하지 않는다.

## 네이티브 입력 계약

수식은 텍스트처럼 보이는 유니코드 조합이 아니라 실제 수식 개체여야 한다.
생성 시 다음을 고정한다.

- EquationCreate → `HEqEdit.string`(COM wrapper가 노출하는 소문자 속성)
- HWPX equation `font="HancomEQN"`, `baseUnit="1100"`(11 pt)
- `treatAsChar="1"`, `flowWithText="1"`, `allowOverlap="0"`
- 수식 삽입 직후 커서를 개체 뒤로 이동하여 다음 텍스트가 수식 앞에 끼지
  않도록 한다.
- 생성기 예외를 `_formula_plain_fallback`, 일반 텍스트, 수식 이미지로 처리하지
  않는다. 예외·빈 script·미지원 명령은 모두 hard FAIL이다.

문서 전체 캡처, 문항/해설 영역 캡처, 수식과 문장이 함께 있는 캡처는 금지한다.
순수 그래프·기하 도형·삽화만 별도 그림 개체로 허용할 수 있으며, 해당 문항 ID,
PDF 좌표, 이미지 SHA-256, 편집 불가능 범위를 manifest에 기록한다.

## 구조 보존 규칙

다음 구조를 source와 결과에서 순서대로 확인한다.

| 구조 | 필수 보존 값 |
| --- | --- |
| Σ/Π | 연산자, 아래·위 한계, 인덱스 식, 바깥 피연산자 |
| lim | 접근 변수, 목표값, `+/-` 일측 방향, 아래첨자 전체 |
| ∫/∮ | 하한·상한, 미분 변수, 적분구간 |
| 첨자·지수 | 부착된 소유자, 피연산자 범위, 중첩 순서 |
| 분수 | 분자·분모 범위, 중첩 분수 순서 |
| 근호 | 근호 범위와 n제곱근 지수 |
| cases | 왼쪽 중괄호, 행 수, 각 식·조건 및 행 순서 |
| 행렬·벡터 | 행·열 수, 셀 순서, 벡터/화살표가 덮는 범위 |

`a_i` 같은 source shorthand는 출력에서 `a_{i}`로 명시적으로 묶을 수 있다.
이는 의미 변경이 아니라 HancomEQN의 script 경계를 고정하기 위한 표현 정규화다.
반대로 `x_{n+1}`의 1을 `x_n+1`로 바꾸거나, `\lim_{h\to0^+}`를
`\lim_{x\to0}`로 바꾸는 것은 구조·수학 의미 변경이므로 FAIL이다.

## 수식 정규화 허용 목록

입력 source의 허용 명령은 `frac/dfrac/tfrac`, `sqrt`, `vec`, `bar/overline`,
`sum`, `prod`, `int/oint`, `lim`, `begin/end`(cases 및 matrix 계열), 기본
그리스 문자·관계·연산 기호다. `left/right`, `bigl/bigr`는 괄호 크기 표시에
한해 제거할 수 있다. 지원 목록 밖의 raw `\\foo`, OCR 융합 명령(예:
`\\prodr`, `\\intbf`)은 출력 전에 복구하거나 원문에서 직접 판독한다. 확정하지
못하면 추측하지 말고 해당 문서·쪽·문항·수식 위치를 FAIL 목록으로 남긴다.

중괄호 균형, `begin/end` 짝, 분수의 두 피연산자, 수식 delimiter를 사전 검사한다.
한 줄/여러 줄 display 수식은 하나의 formula unit으로 합친 뒤 변환하며,
cases/matrix의 행 구분자를 잃지 않는다. 수식 source 또는 생성 script에 한글
풀이 문장이 들어가면 텍스트 block으로 분리하고 수식 script는 FAIL 처리한다.

## QA gate와 실패 코드

`app.math_formula_semantic_qa.validate_native_equation_document()` 또는 동일한
게이트를 사용한다. 아래 조건은 모두 독립적으로 평가한다.

- `formula_count_exact`: source 수식 수 = HWPX `hp:equation` 수 = COM `eqed` 수
- `native_scripts_nonempty`: 모든 script가 비어 있지 않음
- `no_formula_fallback`: fallback/placeholder/수식 이미지/평문 대체 0
- `semantic_scope_exact`: Σ/Π bounds, lim 접근조건, 적분, 첨자·지수, 분수·근호,
  cases, 행렬·벡터의 signature가 source와 동일
- `unsupported_command`: 미지원 OCR 명령 0
- `equation_presentation`: font/baseUnit/treatAsChar/flowWithText/allowOverlap
  속성 일치
- `equation_order`: 문항 ID 및 원본 수식 순서 일치

대표 실패 코드는 `FORMULA_COUNT_MISMATCH`, `FORMULA_SCRIPT_EMPTY`,
`FORMULA_FALLBACK`, `FORMULA_PROSE_IN_SCRIPT`, `OPERATOR_BOUNDS_MISSING`,
`OPERATOR_BOUNDS_MISMATCH`, `LIMIT_APPROACH_MISMATCH`,
`INTEGRAL_SCOPE_MISMATCH`, `SCRIPT_OWNER_MISMATCH`, `SCRIPT_UNSCOPED_ATOM`,
`FRACTION_SCOPE_MISMATCH`, `RADICAL_SCOPE_MISMATCH`,
`CASES_STRUCTURE_MISMATCH`, `MATRIX_STRUCTURE_MISMATCH`,
`VECTOR_SCOPE_MISMATCH`, `GENERATED_FORMULA_INVALID`이다. 하나라도 있으면
전체 파일 FAIL이다.

## HWP/HWPX readback

HWPX는 ZIP으로 열어 모든 section XML을 순회하고, 미주·표 셀을 포함해
`hp:equation`의 `font`, `baseUnit`, `hp:script`를 기록한다. `baseUnit=1100`만
11 pt로 인정하고, script에 미지원 raw backslash나 빈 내용이 있으면 실패한다.
BinData 이미지 감사는 수식 감사와 별도로 수행한다. figure-only 예외 이외의
이미지는 본문/수식 대체로 판정한다.

HWP는 저장 후 닫고 `Hwp(visible=False, register_module=True, on_quit=False)`로
다시 연다. `ctrl_list`의 `CtrlID == "eqed"`를 전수 수집해 `Properties.Item("String")`
및 `BaseUnit == PointToHwpUnit(11.0)`을 확인한다. XML/COM 구조만으로 편집성을
단정하지 말고 대표 유형별로 EquationModify 진입, 문자 변경, 취소를 수행하는
별도 editability gate를 기록한다.

## 회귀 fixture와 실행

저장소의 `tests/test_math_formula_semantic_qa.py`는 합성 문자열만 사용하며
다음 실패를 고정한다.

- Σ/Π 하한·상한 누락 또는 변경
- lim 접근 변수·목표·일측 방향 변경
- 첨자·지수 소유자와 그룹 범위 변경
- cases 행/조건 변경
- 중첩 분수·근호 범위 변경
- 적분 경계 변경
- 벡터·행렬 행 구조 변경
- 미지원 OCR command, 빈 script, formula count shortfall
- 평문/image fallback marker

실행:

```powershell
pytest -q tests/test_math_formula_semantic_qa.py
python -m py_compile app/math_formula_semantic_qa.py
```

실제 PDF·HWP/HWPX·캡처·전사 본문을 fixture에 넣지 않는다. 원격 저장소에는 이
문서, 검사 코드, 합성 fixture 및 회귀 테스트만 커밋한다.

## OCR 후보와 writer 연계 규칙

`hwp-converter-v0.1.1`(release commit `be1893f`)은 후보 추출과 네이티브 HwpPalette
writer의 보조 경로로만 사용한다. 검수된 `math-source-manifest-v1` 없이는
EquationCreate를 실행하지 않으며, strict wrapper는 페이지 전체·수식 평문·수식
이미지 fallback을 허용하지 않고 실패를 예외로 올린다. 최종 수식은 반드시
`HancomEQN`, `baseUnit=1100`과 HWPX/COM readback을 통과해야 한다.

Firecrawl anydoc는 필요할 때 일반 문장·섹션 비교용으로만 사용한다. hosted OCR은
저작권 자료의 명시적 전송 승인과 전체 문서 전송 조건을 모두 만족할 때만 허용하며,
anydoc 출력은 수식 script·MathIR·좌표의 권위나 조판 입력이 아니다. 후보 불일치는
원본 PDF 600/900 dpi 재확인으로 해결하고 provenance를 기록한다. 자동 다수결이나
미검수 OCR을 수식 writer에 전달하면 `OCR_DISAGREEMENT_UNRESOLVED` 또는
`FORMULA_FALLBACK`으로 FAIL한다. 구현·검사 명세는
[`OCR_HYBRID_CONVERTER_ANYDOC_WORK_INSTRUCTIONS.md`](OCR_HYBRID_CONVERTER_ANYDOC_WORK_INSTRUCTIONS.md),
`app/ocr_hybrid_policy.py`, `tools/ocr_hybrid_preflight.py`를 따른다.
