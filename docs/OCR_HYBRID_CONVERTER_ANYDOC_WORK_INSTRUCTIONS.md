# 수학 PDF OCR·hwp-converter·anydoc 하이브리드 작업지시서

이 문서는 `hwp-converter-v0.1.1`과 Firecrawl `anydoc`를 기존 수학 PDF→HWP/HWPX
작업에 추가할 때의 역할·증거·실패 조건을 정한다. PDF 원문과 검수된
`math-source-manifest-v1`만 수학 내용의 권위이며 OCR 후보를 그대로 조판하지 않는다.

## 도구 역할

| 구성 요소 | 역할 | 최종 정답 여부 |
| --- | --- | --- |
| PaddleOCR + PyMuPDF | 주 OCR, polygon·PDF 좌표·confidence 후보 | 아님 |
| ExamPool `hwp-converter-v0.1.1` | 보조 OCR 후보와 HwpPalette 네이티브 조판 | manifest 이후의 writer |
| Firecrawl `anydoc` | 선택적 일반 문장·섹션·표 구조 비교 | 아님 |
| PDF 원문 + reviewed manifest | 수식·문장·문항·좌표의 단일 권위 | 유일한 권위 |

`hwp-converter-v0.1.1`은 다음 버전으로 고정한다.

```text
release: hwp-converter-v0.1.1
commit: be1893f
```

실행에 사용하는 정책 파일은 `config/ocr_hybrid_policy_v1.json`이다.

## anydoc 사용 제한

anydoc는 텍스트 PDF를 로컬에서 읽지만 스캔 PDF 자체를 로컬 OCR하지 않는다.
`ocr=hosted`를 선택하면 OCR 대상 문서 전체가 Firecrawl Parse로 전송되며 페이지
선택을 지원하지 않는다. 저작권·개인정보 PDF는 외부 전송 승인이 없으면 hosted
경로를 사용하지 않는다.

anydoc의 PDF 출력은 Markdown이며, PDF-point bbox·MathIR·수식 crop hash를
권위 있게 제공하는 계약이 없다. 따라서 anydoc Markdown을 직접 HWP writer에
전달하거나 수식 정답으로 채택하지 않는다.

참고:

- <https://github.com/firecrawl/anydoc/blob/main/README.md>
- <https://github.com/firecrawl/anydoc/blob/main/src/formats/pdf.rs>

## hwp-converter 사용 제한

converter의 OCR은 로컬 후보 생성에만 사용한다. 현재 릴리스의 raster 경로에는
다음과 같은 자동 fallback 가능성이 있으므로 strict wrapper에서 차단한다.

- 텍스트·sidecar가 없는 스캔 PDF를 페이지 전체 문항으로 처리
- 복잡한 수식을 제한된 정규식으로 복원
- 수식 생성 실패 시 평문으로 대체
- 복잡한 도형을 이미지로 보존
- crop 기본값 300dpi

최종 작업에서는 전체 페이지 이미지·본문 이미지·수식 평문 fallback·수식 이미지를
허용하지 않는다. 순수 그래프·기하·삽화 이미지만 문항 ID·bbox·SHA-256과 함께
예외 등록할 수 있다.

## 처리 순서

1. PDF SHA-256, 페이지 수, 텍스트/벡터/스캔 유형을 기록한다.
2. PyMuPDF 600dpi 전 페이지 렌더와 PaddleOCR 주 후보를 만든다.
3. hwp-converter OCR은 별도 후보로 실행할 수 있으며 모델·설정·출력 해시를 기록한다.
4. anydoc는 텍스트 PDF 또는 허가된 비민감 자료에서만 구조 비교용으로 실행한다.
5. OCR 후보를 자동 병합하지 않는다. 숫자·부호·수식·문항 순서가 다르면 PDF
   600/900dpi 직접 확인 대상으로 등록한다.
6. 수식별 MathIR, source crop SHA-256, PDF bbox, 문항 ID, ordinal,
   `review_status: VERIFIED`를 확정한다.
7. 검수된 `math-source-manifest-v1`만 converter의 네이티브 writer에 전달한다.
8. HWP/HWPX를 저장·닫기·재열기하고 HWPX XML 및 COM `eqed`를 감사한다.
9. 결과 PDF를 다시 만들고 300dpi overlay/diff와 문항·그림 소유권을 전수검사한다.
10. 모든 게이트가 통과한 경우에만 최종 폴더로 승격한다.

## OCR 불일치 정책

다음은 다수결로 결정하지 않는다.

- 시그마·곱셈의 상·하한
- 리미트 접근 변수·접근값·좌우 방향
- 적분 상·하한과 미분기호
- 위·아래첨자 소유 범위
- 분수·근호·절댓값·괄호 범위
- 구간별 함수·행렬·벡터 구조
- 숫자·부호·그리스 문자·선지·정답

불일치 레코드에는 `page`, `item_id`, `formula_ordinal`, 두 후보, 원본 확인 결과,
검수자, 검수 시각을 기록한다. `disagreement_count > 0`인데 모든 레코드가
`resolved: true`가 아니면 자동 FAIL이다.

## converter strict wrapper 계약

converter 후보를 조판에 사용할 때 다음 값을 manifest에 기록하고 검사한다.

```text
strict_wrapper = true
fallback_policy = fail_closed
full_page_fallback = forbidden
formula_image_fallback = forbidden
formula_plain_text_fallback = forbidden
equation_font = HancomEQN
equation_base_unit = 1100
```

EquationCreate 실패는 예외로 승격한다. HWPX의 모든 section·미주·표 셀에서
`hp:equation`의 script/font/baseUnit을 확인하고, HWP 재열기에서 `CtrlID ==
"eqed"`와 BaseUnit을 비교한다.

## 필수 provenance

OCR provenance manifest는 `app.ocr_hybrid_policy.validate_ocr_provenance()`로
검사한다. 다음 필드를 빠뜨리지 않는다.

- `source_pdf_sha256`
- `source_manifest_sha256`
- `engine_versions`
- `engine_config_hashes`
- `candidate_output_sha256`
- `disagreement_count`, `disagreement_records`
- `transfer_approval`
- anydoc hosted 사용 시 `whole_document_sent`, `page_selection`

실행:

```powershell
python tools/ocr_hybrid_preflight.py ocr-provenance.json `
  --source-is-copyrighted `
  --json ocr-provenance-qa.json
```

## PASS 조건

- reviewed PDF manifest가 유일한 수학 권위
- 주 OCR 후보와 버전·설정·좌표 기록 완료
- converter release/commit 고정
- converter strict wrapper와 fallback 차단 확인
- anydoc hosted 전송 승인 또는 hosted 미사용
- OCR 불일치 0건 또는 전부 원본 대조로 해결
- 600/900dpi 수식 검수 및 MathIR 완료
- source manifest = HWP = HWPX = COM 수식 수
- 수식 script·상하한·첨자·지수·분수·근호·cases·행렬·벡터 일치
- 평문·이미지 수식 fallback 0건
- HWP/HWPX 재열기·실제 편집성·PDF 왕복 QA 통과

anydoc 또는 converter의 자체 성공 메시지·벤치마크·문항 수만으로 PASS하지 않는다.

## 저장소 반영 및 라이선스

이 문서·정책 파일·검사 코드·합성 fixture만 저장소에 반영한다. 실제 PDF,
OCR 전사, HWP/HWPX, 원문 그림은 저장소에 넣지 않는다. converter 릴리스의
소스 라이선스와 현재 저장소의 AGPL 조건을 유지하고, 외부 anydoc 패키지를
자동 번들하지 않는다.
