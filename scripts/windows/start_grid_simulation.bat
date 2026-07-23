@echo off
chcp 65001 >nul
setlocal
title Stone AI 长期指数网格持续模拟
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
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',11111); exit 0}catch{exit 1}finally{$c.Dispose()}" >nul 2>&1
if errorlevel 1 (
  echo [错误] 无法连接 Futu OpenD 127.0.0.1:11111，请先启动并登录 OpenD。
  pause
  popd
  exit /b 4
)

echo SIMULATION_ONLY。自动交易始终关闭。按 Ctrl+C 可优雅停止。
"%PYTHON_EXE%" "scripts\run_grid_strategy.py" --watch --interval 60 --symbols VOO,QQQ
set "EXIT_CODE=%ERRORLEVEL%"
echo 网格持续模拟已结束，退出码 %EXIT_CODE%。
pause
popd
exit /b %EXIT_CODE%
