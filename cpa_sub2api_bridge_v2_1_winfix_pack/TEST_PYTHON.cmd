@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo Python command test
echo ============================================
echo.

echo Test: py -3
py -3 -c "import sys; print(sys.executable); print(sys.version)"
echo ErrorLevel=%ERRORLEVEL%
echo.

echo Test: python
python -c "import sys; print(sys.executable); print(sys.version)"
echo ErrorLevel=%ERRORLEVEL%
echo.

echo Test: python3
python3 -c "import sys; print(sys.executable); print(sys.version)"
echo ErrorLevel=%ERRORLEVEL%
echo.

pause
