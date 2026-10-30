@echo off
chcp 65001 >nul
echo STTP 科学之间 - 探索版和工程版
echo 打开 http://127.0.0.1:8773/demo/
echo 工程版 http://127.0.0.1:8765/
echo 请保持此窗口打开；结束预览可按 Ctrl+C。
echo.
py -3.11 "%~dp0launcher.py"
if errorlevel 1 echo 启动失败：请安装所需 Python 依赖或检查端口 8765、8773 是否被占用。
pause
