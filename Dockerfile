FROM python:3.11-slim

WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

# 安装基础系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制全量业务代码与静态资源
COPY fin_verifier ./fin_verifier
COPY README.md .

# 创建运行时任务持久化目录并赋予读写权限
RUN mkdir -p fin_verifier/data/tasks && chmod -R 777 fin_verifier/data/tasks

# 暴露端口（兼容 Hugging Face 7860 与常规 8000/PORT）
EXPOSE 7860

# 启动命令：自动读取宿主机注入的 PORT（HuggingFace 为 7860，Render/Railway 为注入端口，本地默认为 7860）
CMD ["sh", "-c", "uvicorn fin_verifier.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
