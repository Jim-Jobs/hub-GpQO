# 作业 2：基于作业 1 的 openai-agents 版本打包
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY common/ ./common/
COPY openai_agents_version/ ./openai_agents_version/

EXPOSE 8002

# 运行时用 -e 注入密钥；未注入时自动进入 mock 模式
ENV OPENAI_API_KEY="" \
    OPENAI_BASE_URL="" \
    OPENAI_MODEL="gpt-4o-mini" \
    USE_MOCK_LLM="0"

CMD ["python", "-m", "uvicorn", "openai_agents_version.backend:app", "--host", "0.0.0.0", "--port", "8002"]
