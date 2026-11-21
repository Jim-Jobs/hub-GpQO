# 作业 2：把作业 1 的 OpenAI Agents SDK 版本打包成镜像。
#
#   构建：docker build -t sentiment-companion:agents .
#   运行：docker run -p 8000:8000 sentiment-companion:agents
#
# 国内拉不到 Docker Hub 时，可以换个基础镜像源：
#   docker build --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim -t sentiment-companion:agents .

ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# pip 走阿里云镜像，构建机上不用额外配代理；海外构建可覆盖为官方源。
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

WORKDIR /app

# 先装依赖再拷代码，改业务代码时不会重复下载依赖。
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

# 镜像默认跑 Agents SDK 版本，进程 1 / 进程 2 的串联由 app/engines/agents_engine.py 完成。
ENV ENGINE=agents \
    DEFAULT_ENGINE=agents \
    APP_MODE=auto \
    LLM_MODEL=gpt-4o-mini \
    PORT=8000

# 非 root 运行。
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
