# ExamPool 조판 런타임 동기화

ExamPool은 HwpPalette 전체 앱에 의존하지 않고, 검증된 조판 런타임과 시험지 팩만
`vendor/hwp_typesetter`에 포함한다.

## 사용자가 양식을 수정하는 흐름

1. HwpPalette에서 시험지 팔레트의 양식·템플릿을 수정한다.
2. 팔레트 탭을 `.hwpal`로 내보낸다. 파일에는 `exam.json` 슬롯 계약과 실제 HWP
   조각이 함께 들어간다.
3. ExamPool의 `환경설정 > 시험지 팔레트`에서 학교 또는 수능 양식으로 등록한다.
4. ExamPool은 같은 호출 라벨의 조각만 새 버전으로 교체한다. 팔레트에 들어 있지
   않은 내장 양식은 유지된다.
5. 문제가 있으면 목록의 이전 팔레트를 다시 적용하거나 `기본값`을 눌러 내장본으로
   즉시 되돌린다.

등록 파일은 개인 데이터 폴더의 `typesetting_palettes` 아래에 보존한다. 원본을
덮어쓰지 않고 SHA-256별 버전으로 저장하므로 같은 파일을 다시 등록해도 중복되지 않는다.

## 개발과 배포

- 소스 체크아웃에서 실행할 때는 기본적으로 형제 저장소 `31_hwp_palette`를 참조한다.
  따라서 HwpPalette의 조판 수정이 ExamPool 미리보기에 즉시 반영된다.
- 독립 배포본에서는 `vendor/hwp_typesetter`를 사용한다. 사용자는 HwpPalette를 별도로
  설치하지 않아도 된다.
- `EXAMPOOL_HWPPAL_ROOT`를 지정하면 두 기본값보다 우선한다.

## 검증된 스냅샷 갱신

HwpPalette에서 조판 팩 테스트와 실제 출력 검수를 마친 뒤 ExamPool 저장소에서 실행한다.

```powershell
python tools/sync_hwp_typesetter.py
```

이 명령은 `csat_science`, `school_exam` 팩과 실행에 필요한 최소 Python 모듈만 복사하고,
`UPSTREAM.json`에 원본 커밋·브랜치·dirty 상태·팩 버전을 기록한다. 동기화 결과를
ExamPool 변경과 함께 커밋하면 두 프로젝트의 발전 속도가 달라도 언제든 해당 스냅샷을
재현하거나 이후 HwpPalette 변경을 다시 동기화할 수 있다.

권장 흐름은 HwpPalette의 `feat/...` 브랜치에서 수정·검수한 뒤 팩 버전을 올리고,
ExamPool의 별도 기능 브랜치에서 동기화 결과를 커밋한 후 병합하는 방식이다.
