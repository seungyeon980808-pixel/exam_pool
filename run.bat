@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo [ExamPool] Python was not found in PATH.
  pause
  exit /b 1
)

echo [ExamPool] Checking HWP automation security module...
python -m app.integrations.hwp_security
if errorlevel 1 (
  echo.
  echo [ExamPool] HWP security module setup failed.
  echo Run this file again as the current Windows user.
  pause
  exit /b 2
)

set "EXAMPOOL_OLD_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:8632 .*LISTENING"') do set "EXAMPOOL_OLD_PID=%%P"
if defined EXAMPOOL_OLD_PID (
  powershell.exe -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId = %EXAMPOOL_OLD_PID%'; if (-not $p -or $p.CommandLine -notmatch 'uvicorn app\.main:app.*--port 8632') { exit 5 }"
  if errorlevel 1 (
    echo [ExamPool] Port 8632 is occupied by a different program.
    echo Close that program or change the ExamPool port before retrying.
    pause
    exit /b 5
  )
  echo [ExamPool] Stopping old server PID %EXAMPOOL_OLD_PID%...
  taskkill /PID %EXAMPOOL_OLD_PID% /F >nul 2>&1
  if errorlevel 1 (
    echo [ExamPool] Could not stop PID %EXAMPOOL_OLD_PID%.
    echo Close the previous ExamPool window and try again.
    pause
    exit /b 3
  )
  timeout /t 1 /nobreak >nul
)

echo [ExamPool] Starting server at http://127.0.0.1:8632 ...
if not exist "data\logs" mkdir "data\logs"
powershell.exe -NoProfile -Command "$p=Start-Process -FilePath 'python' -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8632') -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardOutput '%CD%\data\logs\server.stdout.log' -RedirectStandardError '%CD%\data\logs\server.stderr.log' -PassThru; if (-not $p) { exit 1 }"
if errorlevel 1 (
  echo [ExamPool] Could not launch the Python server process.
  pause
  exit /b 4
)
timeout /t 3 /nobreak >nul

netstat -ano | findstr /R /C:"127.0.0.1:8632 .*LISTENING" >nul
if errorlevel 1 (
  echo [ExamPool] Server did not start. Review the message above.
  pause
  exit /b 4
)

powershell.exe -NoProfile -Command "try { Start-Process 'http://127.0.0.1:8632' } catch { }" >nul 2>&1
echo [ExamPool] Ready.
endlocal
exit /b 0
