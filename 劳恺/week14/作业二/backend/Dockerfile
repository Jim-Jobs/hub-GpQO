FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖再拷代码，改代码时可命中层缓存
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir -e .

RUN mkdir -p /data/uploads /data/images /data/work

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
