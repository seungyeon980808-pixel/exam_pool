# DocFinder (Electron 판) — 보관용 · 실행하지 말 것

이 폴더는 ExamPool 근거 문서 탭의 **전신**이다. 참고 목적으로만 남겨둔다.

- **원본 위치**: `Desktop/Fable/docfinder`
- **보관일**: 2026-07-22
- **상태**: 기능 전부가 `app/pdf_indexer.py` + `app/routes_doc.py`로 이관 완료. **폐기됨.**
- **이관 보고서**: [`PRD/06_DOCFINDER_MIGRATION.md`](../../PRD/06_DOCFINDER_MIGRATION.md)

## 실행되지 않는다

`node_modules/`(486MB)와 `package-lock.json`은 일부러 가져오지 않았다.
소스 9개 파일(68KB)만 있다. 굳이 돌려보려면 원본 폴더를 쓸 것.

## 이 코드를 참고할 때의 경고

**여기 있는 하이라이트 로직에는 알려진 버그가 두 개 있다. 그대로 옮기지 말 것.**

1. **`--scale-factor` 누락** — `src/renderer/app.js`가 pdf.js 텍스트 레이어를 만들면서
   `--scale-factor` CSS 변수를 설정하지 않아, span 배치용 `calc()`가 통째로 무효가 된다.
   결과적으로 하이라이트가 실제 글자와 어긋난다.

2. **색인 텍스트 ≠ 하이라이트 텍스트** — 색인은 `src/main/indexer.js`의 `assembleText()`가
   조각을 이어붙인 문자열로 만들고, 하이라이트는 `app.js`가 이어붙이지 않은 개별 span에
   정규식을 돌린다. 한글 단어가 두 span에 걸치면 검색은 맞다는데 하이라이트는 안 찍힌다.

Python 판은 서버에서 PNG를 굽고 `page.search_for()`로 좌표를 찾아 두 문제를 모두 피한다.
자세한 설명은 이관 보고서 2장에 있다.

## 그래도 볼 가치가 있는 부분

`src/main/indexer.js`의 `assembleText()` — pdf.js 텍스트 조각을 좌표 기반으로 이어붙이는
휴리스틱(`gap > h * 0.28`). 한글 PDF의 "징 계 기 준" 문제를 다루려 한 시도로,
**실패 사례로서** 참고할 만하다. PyMuPDF를 쓸 수 없는 상황이 오면 출발점이 된다.
