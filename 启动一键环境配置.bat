@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动「一键环境配置」...
where python >nul 2>nul
if %errorlevel%==0 (
  python main.py
) else (
  py main.py
)
pause
