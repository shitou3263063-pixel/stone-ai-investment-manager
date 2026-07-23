@echo off
chcp 65001 >nul
setlocal
title Stone AI 长期指数网格模拟摘要
set "PROJECT_ROOT=%~dp0..\.."
pushd "%PROJECT_ROOT%"
set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
set "PYTHONUTF8=1"

if not exist "%PYTHON_EXE%" (
  echo [错误] 未找到项目 Python 3.11 虚拟环境：%PYTHON_EXE%
  pause
  popd
  exit /b 2
)
"%PYTHON_EXE%" "scripts\run_grid_strategy.py" --summary
set "EXIT_CODE=%ERRORLEVEL%"
echo 摘要读取完成，退出码 %EXIT_CODE%。
pause
popd
exit /b %EXIT_CODE%
