@echo off
cd /d "%~dp0"
set "PDF_HWP_OCR_RUNTIME=%~dp0data\pdf_hwp_ocr_runtime"
set "PADDLE_PDX_CACHE_HOME=%~dp0data\pdf_hwp_ocr_models"
python -c "import sys; sys.path.insert(0, r'%PDF_HWP_OCR_RUNTIME%'); import paddleocr, paddle" >nul 2>&1
if errorlevel 1 (
  echo [PDF-HWP] 이미지 문항 OCR을 처음 한 번 설치합니다.
  python -m pip install --target "%PDF_HWP_OCR_RUNTIME%" "paddleocr>=3.7,<3.8" "paddlepaddle>=3.3,<3.4"
  if errorlevel 1 (
    echo [PDF-HWP] OCR 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행해 주세요.
    pause
    exit /b 1
  )
)
set "PYTHONPATH=%PDF_HWP_OCR_RUNTIME%;%PYTHONPATH%"
python run_pdf_hwp_webapp.py
