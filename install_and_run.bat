@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Stone AI Investment Manager Pro V10
echo.
echo 正在安装依赖...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo 依赖安装失败。请确认 Python 和网络环境可用。
    pause
    exit /b 1
)
echo.
echo 正在运行系统自检和日报生成...
python run.py
echo.
pause
