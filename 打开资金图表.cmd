@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "TUSHARE_TOKEN="
if exist ".runtime\python.exe" goto portable
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
    if not errorlevel 1 goto venv
)
for %%V in (3.13 3.12 3.11 3) do (
    py -%%V -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
    if not errorlevel 1 (
        py -%%V -B -X utf8 "程序\启动助手.py"
        goto result
    )
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
    python -B -X utf8 "程序\启动助手.py"
    goto result
)
echo 正在为当前文件夹准备便携 Python 环境，首次使用需要联网。
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "程序\准备便携环境.ps1"
if errorlevel 1 goto failed
:portable
".runtime\python.exe" -B -X utf8 "程序\启动助手.py"
goto result
:venv
".venv\Scripts\python.exe" -B -X utf8 "程序\启动助手.py"
:result
if not errorlevel 1 exit /b 0
:failed
echo 未能启动。如需手动安装，请安装 Python 3.13 后重新双击本文件。
echo https://www.python.org/downloads/windows/
pause
exit /b 1
