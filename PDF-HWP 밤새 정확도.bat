@echo off
cd /d "%~dp0"
echo [PDF-HWP] Overnight multi-subject audit until tomorrow 08:00.
python tools\pdf_hwp_overnight_loop.py --interval-min 30 --subjects p1,c1,c2,b1,b2,e1,e2
echo.
echo Status: data\pdf_hwp\overnight_status.md
pause
