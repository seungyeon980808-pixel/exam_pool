@echo off
REM ===== ExamMaker launcher =====
REM Starts: ExamPool server (8632) + 5E local server (8611, MCP bridge on)
REM Then: open both in browser. Start Claude Code from THIS folder (32_exam_pool)
REM so the exampool MCP attaches. See docs/PIPELINE.md for the workflow.

start "ExamPool 8632" cmd /k "cd /d %~dp0 && python -m uvicorn app.main:app --host 127.0.0.1 --port 8632"
start "5E 8611" cmd /k "cd /d %~dp0..\51_5E\5E_main && python -m http.server 8611"
timeout /t 2 >nul
start "" "http://127.0.0.1:8632"
start "" "http://localhost:8611/?mcp=1"
echo.
echo ExamPool:  http://127.0.0.1:8632
echo 5E (MCP):  http://localhost:8611/?mcp=1
echo.
echo Next: open Claude Code in this folder (32_exam_pool) and paste the
echo       prompt copied from the blueprint screen. Workflow: docs/PIPELINE.md