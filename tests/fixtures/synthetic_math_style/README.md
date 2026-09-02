# Synthetic math-style fixture

이 디렉터리는 기준 서식 엔진의 구조 계약을 설명하는 저작권 없는 fixture
공간이다. 실제 HWP/HWPX, 시험지, OCR, 캡처 이미지는 저장하지 않는다.

테스트는 실행 중 임시 디렉터리에 최소 HWPX ZIP(XML section, 합성 BinData)을
생성하여 다음을 검증한다.

- 두 문항의 native endnote와 ENDNOTE autonum
- 합성 equation script와 표 셀
- XML에서 참조되는 그림의 SHA-256
- 7개 서식군, profile hash/application record
- 좌표, 밀도, orphan/overlap/clipping/large-gap gate
- zero-width·내용 변경 자동 FAIL
