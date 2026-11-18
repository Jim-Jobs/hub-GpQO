# ============================================================
# 作业1 · OpenAI Agents 版情感识别服务
# 构建上下文：sentiment-agent-demo/ 根目录
# ============================================================

FROM python:3.12-slim

LABEL maintainer="sentiment-agent-demo"
LABEL app="openai-agents-sentiment"
LABEL description="Sentiment recognition + response (OpenAI Agents)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # 容器内默认端口（可用 -e AGENTS_PORT 覆盖）
    AGENTS_PORT=8002 \
    # 运行时通过 -e 注入，勿写入镜像
    OPENAI_API_KEY= \
    OPENAI_BASE_URL=https://api.deepseek.com \
    OPENAI_MODEL=deepseek-chat

WORKDIR /app

# 使用阿里云 PyPI 镜像加速（国内构建更稳）
COPY requirements-agents.txt .
RUN pip install -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r requirements-agents.txt

# 仅拷贝 Agents 方案所需代码与前端
COPY shared/ ./shared/
COPY openai_agents_impl/ ./openai_agents_impl/
COPY frontend/ ./frontend/

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health')" || exit 1

# 监听 0.0.0.0，便于容器外访问
CMD ["sh", "-c", "uvicorn openai_agents_impl.server:app --host 0.0.0.0 --port ${AGENTS_PORT}"]
