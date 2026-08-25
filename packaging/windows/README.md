# ExamPool HWP 변환기 Windows 설치본

이 패키지는 PDF-to-HWP 앱, Python 런타임, 로컬 OCR 엔진을 하나의 Windows 설치
파일로 묶는다. 실행하면 시스템 브라우저가 아니라 Edge WebView2 기반 독립 창이
열리고, 내부 로컬 서버는 창을 닫을 때 함께 종료된다. 한글 프로그램은 재배포하지
않으며 사용자 PC에 별도로 설치되어 있어야 한다.

## 로컬 빌드

1. Python 3.12 x64, `uv`, Inno Setup 6을 준비한다.
2. 프로젝트 루트에서 의존성을 설치한다.

   ```powershell
   uv sync --extra installer --no-install-project
   ```

3. 설치 파일을 만든다.

   ```powershell
   .\packaging\windows\build_installer.ps1 -Version 0.1.1
   ```

빌드 스크립트는 기존 `data/pdf_hwp_ocr_runtime`을 재사용하고, 없으면 격리된 빌드
폴더에 OCR 런타임을 자동으로 준비한다. 완성 파일은
`dist/installer/ExamPool-HWP-Converter-Setup-0.1.1.exe`에 생성된다.
설치 프로그램은 관리자 권한 없이 `%LOCALAPPDATA%\Programs` 아래에 앱을 설치한다.
OCR 패키지는 필요한 모듈·메타데이터·수치 연산 DLL만 동결하며 개발용 패키지 폴더를
그대로 복사하지 않는다. 이 방식은 설치 파일 수와 설치 시간을 크게 줄인다.
빌드 시 `collect_licenses.ps1`이 실제 Python 및 OCR 런타임의 패키지 목록과
라이선스 파일을 수집한다. 프로젝트 라이선스, 제3자 고지, 한글 오토메이션 안내는
설치 폴더에도 함께 들어간다.

## 라이선스

- ExamPool 자체 소스: `AGPL-3.0-only`
- 저작권: `Copyright © 2026 박승연 (SOMC)`
- 한글 프로그램: 설치본에 포함하지 않으며 사용자가 별도로 준비한다.
- 상업적 이용: 한글과컴퓨터의 오토메이션 라이선스 조건을 별도로 확인한다.

## 배포 전 확인

- Windows 10/11 x64에서 설치·실행·제거
- Edge WebView2 Runtime이 설치된 환경에서 독립 창 실행
- 지원할 한글 버전별 HWP 생성
- PDF 업로드, 이미지 업로드, Ctrl+V 이미지 붙여넣기
- 최초 이미지 변환 시 OCR 모델 다운로드 안내
- 한글이 설치되지 않은 PC의 오류 안내
- 설치 파일 코드 서명과 Windows SmartScreen 결과
