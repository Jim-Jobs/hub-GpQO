# week15 作业二 · openai-agents 套服务镜像（单文件 5 target，共享依赖底座）
#
# build context = week15/（compose 已封装）。源码零改动、零复制进本目录——
# 每个 target 只 COPY 运行所需文件，镜像内布局与仓库一致（/app = 套根），
# 代码里 gateway/worker 的 parents[1] sys.path 注入逻辑原样成立。
#
# 手工构建示例（compose build 即等价封装）：
#   docker build -f 作业二/Dockerfile --target sentiment -t emotion-oa-sentiment ..

FROM python:3.12-slim AS deps
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY 作业二/requirements.txt /tmp/requirements.txt
# 容器网络不出代理：pip 走清华源（构建环境直连 pypi 慢且不稳）
RUN pip install --no-cache-dir \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      -r /tmp/requirements.txt

# ---------- 进程1 情感识别 :8002 ----------
FROM deps AS sentiment
COPY 作业一/openai-agents/contracts.py ./
COPY 作业一/openai-agents/sentiment-service/ ./sentiment-service/
WORKDIR /app/sentiment-service
EXPOSE 8002
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]

# ---------- 进程2 情感回应 :8003 ----------
FROM deps AS respond
COPY 作业一/openai-agents/contracts.py ./
COPY 作业一/openai-agents/respond-service/ ./respond-service/
WORKDIR /app/respond-service
EXPOSE 8003
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]

# ---------- Temporal worker（无 HTTP 端口）----------
FROM deps AS worker
COPY 作业一/openai-agents/contracts.py 作业一/openai-agents/streams.py ./
COPY 作业一/openai-agents/worker/ ./worker/
WORKDIR /app/worker
CMD ["python", "main.py"]

# ---------- 网关 :8001 ----------
# workflows.py 会 pass-through import activities，故连整个 worker/ 一起带上
FROM deps AS gateway
COPY 作业一/openai-agents/contracts.py 作业一/openai-agents/streams.py ./
COPY 作业一/openai-agents/worker/ ./worker/
COPY 作业一/openai-agents/gateway/ ./gateway/
WORKDIR /app/gateway
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
