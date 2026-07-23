@echo off
chcp 65001 >nul
setlocal
title Stone AI 盘中监控（邮件 Dry-Run）
set "PROJECT_ROOT=%~dp0..\.."
pushd "%PROJECT_ROOT%"
set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
set "SYMBOLS=VOO,NVDA,GOOG,TLT,IBKR,XLF,BABA,03033"
set "PYTHONUTF8=1"

if not exist "%PYTHON_EXE%" (
  echo [错误] 未找到项目 Python 3.11 虚拟环境：%PYTHON_EXE%
  pause
  popd
  exit /b 2
)
"%PYTHON_EXE%" -c "import futu, yaml" >nul 2>&1
if errorlevel 1 (
  echo [错误] 虚拟环境缺少 futu-api 或 PyYAML。请按 requirements.txt 安装依赖。
  pause
  popd
  exit /b 3
)
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',11111); exit 0}catch{exit 1}finally{$c.Dispose()}" >nul 2>&1
if errorlevel 1 (
  echo [错误] 无法连接 Futu OpenD 127.0.0.1:11111，请先启动并登录 OpenD。
  pause
  popd
  exit /b 4
)

echo Dry-run 模式：符合条件的邮件只会显示在窗口和日志中，不连接 SMTP。
"%PYTHON_EXE%" "scripts\run_intraday_monitor.py" --watch --interval 60 --symbols "%SYMBOLS%" --dry-run
set "EXIT_CODE=%ERRORLEVEL%"
echo Stone AI 盘中监控已结束，退出码 %EXIT_CODE%。
pause
popd
exit /b %EXIT_CODE%
