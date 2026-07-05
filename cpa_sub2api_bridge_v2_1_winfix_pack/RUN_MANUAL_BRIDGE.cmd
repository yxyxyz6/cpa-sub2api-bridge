@echo off
setlocal
cd /d "%~dp0"

set "SCRIPT=%~dp0cpa_sub2api_bridge.py"
set "PYTHON_CMD="

echo ============================================
echo CPA sub2api bridge - manual mode v2.1
echo ============================================
echo.

echo Checking Python 3...

rem IMPORTANT: do not use %%ERRORLEVEL%% inside parenthesized blocks.
rem CMD expands it too early and may choose a bad command such as python3.
py -3 -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD python -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD python3 -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul && set "PYTHON_CMD=python3"

if not defined PYTHON_CMD (
    echo Python 3 was not found or cannot run scripts.
    echo Install Python 3 from python.org, then run again.
    echo Recommended: install Python and enable "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

echo Found Python command: %PYTHON_CMD%
echo.

if not exist "%SCRIPT%" (
    echo Missing script: %SCRIPT%
    echo Please keep this CMD file and cpa_sub2api_bridge.py in the same folder.
    echo.
    pause
    exit /b 1
)

echo Paste input path or URL, then press Enter.
echo Examples:
echo   E:\Downloads\cpa-auths.zip
echo   E:\Downloads\sub2api.json
echo   https://example.com/sub2api.json
echo.
set /p "INPUT=Input path or URL: "
set "INPUT=%INPUT:"=%"

if "%INPUT%"=="" (
    echo Empty input.
    echo.
    pause
    exit /b 1
)

echo.
echo Output mode:
echo   auto    = detect automatically
echo   sub2api = force CPA/JSON to sub2api JSON
echo   cpa     = force sub2api JSON/link to CPA ZIP
echo.
set /p "MODE=Mode [auto]: "
if "%MODE%"=="" set "MODE=auto"

echo.
echo Input: "%INPUT%"
echo Mode: %MODE%
echo.
%PYTHON_CMD% "%SCRIPT%" "%INPUT%" --to "%MODE%"

set "ERR=%ERRORLEVEL%"
echo.
if "%ERR%"=="0" (
    echo DONE.
) else (
    echo FAILED. Error code: %ERR%
)
echo.
pause
exit /b %ERR%
