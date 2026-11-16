# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

week14 作业二：基于 Kafka 的多模态 RAG 问答（FastAPI + Vue3）。`readme.md` 是权威文档——含完整架构图、与参考实现的差异表、8 条踩坑记录；本文件只缓存高频命令与跨文件要点。

## 常用命令

Python 一律用 `/opt/miniconda3/envs/py312/bin/python`（下称 `python`）。

### 测试与 lint（必须在 `backend/` 目录下跑，`pyproject.toml` 的 `pythonpath=["src"]` 才生效）

```bash
cd backend
python -m pytest -q                       # 31 个快用例（addopts 默认排除 slow 标记）
python -m pytest tests/test_worker.py -q  # 单文件；-k "done" 按名筛选用例
python -m pytest -m slow -q               # 7 个 embedding 用例，需模型缓存，首跑数百秒
python -m ruff check src tests            # line-length 110
```

### 启动全链路（顺序执行；日志在 `logs/*.log`）

```bash
./scripts/start_kafka.sh    # brew KRaft :9092 + 幂等建 topic rag-data × 3 分区
./scripts/start_backend.sh  # API :8001 + 3 个 worker；mineru server 未运行时会自动拉起
cd frontend && pnpm dev     # :5173；需 node v20（默认 v16 跑不动，前置 ~/.nvs/default/bin）
./scripts/stop_all.sh       # 停 API/worker，保留 Kafka 与 mineru server
```

## 架构

三进程 + Kafka 解耦，消息只传引用：

- **API**（`mrag/api/main.py`，uvicorn :8001）：上传落盘 `data/uploads/` + 写 SQLite `data/meta.sqlite`（files 表）→ 发 Kafka 消息（仅 `{file_id, file_name, file_path, out_dir}`，PDF 本体不进消息，规避 1MB 限制）；另有状态轮询/删除/重试、SSE 问答、`/files` 静态图片服务。
- **Worker**（`mrag/worker/main.py`，×3 进程同消费者组分摊 3 分区）：消费 → MinerU 4.x 解析（client-server，本地 managed 引擎）→ 图片导出与 `/files/` URL 重写（`paths.py`）→ 256 字符切块（`chunker.py`）→ 向量入库（`embeddings.py`：bge-small-zh 文本 512d + ChineseCLIP 图文 512d；`zilliz_store.py`：幂等建表/检索/按 db_id 删）。offset 手动 commit：处理完（done/failed）才提交，崩溃不丢消息。
- **前端**（`frontend/src/App.vue`，Vue3 :5173）：双 Tab 文档管理/多模态对话；vite proxy 把 `/api`、`/files` 转发到 8001。

状态机：`pending → parsing → done | failed`；重试＝重发消息，入库前先按 db_id 清旧向量（幂等）。

多模态问答（`api/retrieval.py` + `api/vl.py`）：双路检索（问题文本 top-5 / 用户附图 ChineseCLIP 图像塔 top-3）→ 组装 OpenAI 兼容多模态消息（用户图＋命中资料图 ≤2 张真实进 `VL_MODEL`）→ SSE 流式。多轮历史由前端持有随请求携带，后端无会话状态。

## 关键坑（readme「踩坑记录」共 8 条，以下是最高频的）

- **`.env` 在 `邓帅/.env`**（父目录，不在本仓库内）；`config.py` 用 find_dotenv 向上查找。缺变量时 `get_settings()` 报可读错误并列出缺失项；key 清单见 `.env.example`。
- **Zilliz Cloud 两套凭据**：控制面 REST 用 `ZILLIZ_TOKEN`，数据面 pymilvus 用 `ZILLIZ_USER_NAME + ZILLIZ_PASSWORD`。
- **subprocess 调 mineru 必须用绝对路径**：`Path(sys.executable).parent / "mineru"`——服务进程 PATH 里没有 conda bin。
- **transformers 5.x 环境选原生 `ChineseCLIPModel`**，勿换 jina-clip-v2（其 trust_remote_code 依赖 5.x 已删的 `clip_loss`）。
- **MinerU 4.x 是重写版**：CLI 是 client-server 文档中心（`parse/read/server/config` 子命令），没有 `-p -o -b`；解析产物图片以 `doc:<sha>/...` 定位符表示，需 `mineru read -f image` 逐张导出；server 启动后引擎预热几十秒，期间 parse 报 `quality_tier_unavailable` 属正常。
- 后端固定 8001（8000 常被占用），前端钉死 5173。
