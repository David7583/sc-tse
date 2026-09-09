@echo off
REM ============================================================
REM 文件名: launch_sc_tse_integration_v0001.cmd
REM 中文名: SC-TSE Integration MVP 启动器
REM 版本号: v0001
REM
REM 主层级: action
REM 层级: development / sc_tse / frontend / launcher
REM 脚本定位: 从自身位置找到虚拟环境并打开本地工作台
REM
REM 职责说明:
REM - 启动唯一 Integration MVP HTTP 入口
REM
REM 本脚本做什么:
REM - 查找项目根、打开浏览器并保留错误诊断
REM
REM 本脚本不做什么:
REM - 不安装依赖、不执行计算或登记数据库
REM
REM 制度边界声明:
REM - 不依赖已激活的虚拟环境，失败保留窗口
REM - 运行时不修改项目输入
REM
REM 可更新: True
REM ============================================================

REM ============================================================
REM ALIAS_META
REM ============================================================
REM alias: launch_sc_tse_integration_v0001
REM family: launch_sc_tse_integration
REM role: integration_launcher
REM version: v0001
REM status: active
REM entry_point: scripts/action/development/scripts/sc_tse/frontend/launch_sc_tse_integration_v0001.cmd
REM input:
REM   - optional CLI arguments
REM output:
REM   - local browser and server status
REM depends_on:
REM   - run_sc_tse_integration_v0001
REM used_by: []
REM ============================================================

setlocal
set "DEFAULT_ENCODING=utf-8"
set "SCRIPT_FAMILY=launch_sc_tse_integration"
set "SCRIPT_NAME=launch_sc_tse_integration_v0001"
set "SCRIPT_VERSION=v0001"
set "integration_root=%~dp0"

REM ============================================================
REM 工具函数区 / CLI 入口
REM ============================================================
:find_root
if exist "%integration_root%PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md" goto run
for %%D in ("%integration_root%..") do set "integration_parent=%%~fD\"
if "%integration_parent%"=="%integration_root%" goto missing
set "integration_root=%integration_parent%"
goto find_root
:run
if not exist "%integration_root%.venv\Scripts\python.exe" goto missing
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
"%integration_root%.venv\Scripts\python.exe" -B "%~dp0run_sc_tse_integration_v0001.py" --open %*
set "integration_exit=%errorlevel%"
if not "%integration_exit%"=="0" pause
exit /b %integration_exit%
:missing
echo {"status":"ERROR","error_type":"MissingRuntime","detail":"Project root or .venv Python unavailable"}
pause
exit /b 2
