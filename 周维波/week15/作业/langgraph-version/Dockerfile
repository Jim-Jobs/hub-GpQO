# 情感支持流水线 · LangGraph 版镜像
# 构建：docker build -t sentiment-langgraph:1.0.0 .
# 基础镜像可用阿里云 ACR 的镜像加速地址覆盖：
#   --build-arg BASE_IMAGE=registry.cn-hangzhou.aliyuncs.com/library/python:3.12-slim

ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG APP_UID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    TZ=Asia/Shanghai \
    OPENAI_MODEL=gpt-4o-mini \
    LLM_TIMEOUT=60 \
    LLM_TEMPERATURE=0.4 \
    PORT=8000

WORKDIR /app

# 非 root 运行；useradd 默认创建同名用户组
RUN useradd --create-home --uid ${APP_UID} app

# 依赖层单独缓存：只改代码时不会重装依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN chown -R app:app /app

USER app
EXPOSE 8000

# 容器内自检：不依赖 curl，减少基础镜像体积
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --timeout-keep-alive 60"]

