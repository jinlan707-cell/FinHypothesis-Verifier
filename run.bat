@echo off
chcp 65001 >nul
echo ==============================================================================
echo 启动 FinHypothesis-Verifier 投资命题与多证据验证系统...
echo ==============================================================================

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未检测到 .venv 虚拟环境，请先执行: python -m venv .venv
    pause
    exit /b 1
)

echo [1/2] 检查依赖...
.\.venv\Scripts\python.exe -c "import fastapi, uvicorn, pydantic, openai; print('核心依赖环境就绪')"

echo [2/2] 正在启动 FastAPI 服务 (默认端口: 8000)...
echo 本地访问地址: http://127.0.0.1:8000
echo API 文档地址: http://127.0.0.1:8000/docs
echo 按 Ctrl+C 可停止服务
echo.

.\.venv\Scripts\uvicorn.exe fin_verifier.main:app --host 0.0.0.0 --port 8000 --reload
pause
