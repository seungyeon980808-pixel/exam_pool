@echo off
REM ===== ExamPool 실행 =====
REM 로컬 서버를 띄우고 기본 브라우저로 연다. 한글은 필요 없다(이 프로그램만 단독 동작).
cd /d "%~dp0"
start "" http://127.0.0.1:8632
python -m uvicorn app.main:app --host 127.0.0.1 --port 8632
