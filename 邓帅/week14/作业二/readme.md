# week14 作业二 · 基于 Kafka 的多模态 RAG 问答

> 作业要求（原需求）：
> 1. 前端网页上传多个 PDF，通过 Kafka 到后端，后端用 MinerU 解析并存储
> 2. 前端展示解析状态：待解析 / 解析中 / 解析完成（本实现额外增加「解析失败」态）
> 3. 多模态问答：回答图文混排；提问支持「图＋问」一起送 LLM
>
> 参考实现：`/Users/drdidi/Desktop/badou2026/project/05-multimodal-rag-chatbot`（在其骨架上工程化复刻并修复缺陷）

## 架构

```mermaid
flowchart LR
    subgraph 前端 Vue3 :5173
        UI1[📄 文档管理 Tab<br/>上传/状态轮询/删除/重试]
        UI2[💬 多模态对话 Tab<br/>markdown 图文渲染/SSE 流式]
    end
    subgraph 后端 FastAPI :8001
        API[上传→落盘→发 Kafka<br/>状态/删除/重试 API<br/>检索+VL 组装+SSE]
        STATIC[/files 静态图片服务/]
        DB[(SQLite 元数据<br/>files 表·四态状态机)]
    end
    subgraph Kafka brew :9092
        TOPIC[topic rag-data ×3 分区]
    end
    subgraph Workers ×3 进程
        W1[worker 消费循环<br/>手动 commit]
        MU[MinerU 4.x 本地引擎<br/>managed·standard 档]
        EMB[bge-small-zh 512d<br/>+ ChineseCLIP 512d 三向量]
    end
    subgraph 云端
        Z[(Zilliz Cloud Serverless<br/>week14_rag_chunks)]
        VL[qwen3-vl-plus<br/>阿里云百炼]
    end

    UI1 -->|multipart| API -->|引用消息| TOPIC --> W1 --> MU --> EMB --> Z
    UI1 -->|轮询 2.5s| API --> DB
    UI2 -->|POST /api/chat SSE| API
    API -->|检索| Z
    API -->|image_url 多模态消息| VL
    W1 -->|状态回写| DB
    UI2 -.->|/files/... 图片| STATIC
```

- **三进程分离**：`uvicorn` API ＋ `python -m mrag.worker`×3（同消费者组分摊 3 分区）＋ Vue3 dev server；Kafka 由 Homebrew 安装的 KRaft 单节点承担
- **消息只传引用**：`{file_id, file_name, file_path, out_dir}`，PDF 本体不进 Kafka（规避 1MB 消息限制）
- **状态机**：`pending → parsing → done | failed(带错误信息)`；offset 处理完手动 commit——崩溃不丢消息、失败不阻塞分区；重试=重发消息，入库前先按 `db_id` 清旧向量（幂等）

## 多模态问答链路（作业核心）

1. **检索双路**：问题文本 → `text_vector`(bge) top-5；用户附图 → base64 → ChineseCLIP 图像塔 → `clip_image_vector` top-3，去重合并
2. **真·看图**：OpenAI 兼容多模态消息 `[{type:text,...},{type:image_url,image_url:{url:data:image/jpeg;base64,...}}]`——用户图＋命中资料图（≤2 张）真实送入 `qwen3-vl-plus`，与参考项目「图片只是 markdown 链接给纯文本 LLM」有本质区别
3. **图文混排**：入库时图片引用已重写为 `/files/processed/...`，prompt 要求模型原样保留图片链接于合适位置，前端 marked 渲染 + vite proxy 转发静态服务显示

## 与参考实现的差异（修复/增强）

| # | 参考项目 | 本实现 |
|---|---|---|
| 1 | Streamlit 前后端一体，LLM 为纯文本（图片仅 md 链接） | FastAPI+Vue3 分离；图片真实进 VL 模型消息 |
| 2 | CLIP 图像向量因 `dirname+filename` 拼接缺分隔符**全为 0 向量**，图像检索失效 | `os.path.join` + 文件存在校验，真实图像向量；测试把关非零 |
| 3 | Zilliz collection 无建表代码（需手工建） | 启动时幂等自建 schema（三向量字段+COSINE 索引） |
| 4 | 无多轮对话（session_state 不回写）、无失败态 | 多轮历史前端持有随请求携带；四态状态机+失败重试 |
| 5 | MinerU 2.x CLI（`-p -o -b vlm-http-client`，需 GPU vLLM 服务） | MinerU 4.2 本地 managed 引擎（standard 档 1.2B GGUF，Apple Silicon llama.cpp/Metal），定位符图片经 `mineru read -f image` 导出 |
| 6 | embedding=jina-clip-v2(1024d) | ChineseCLIP(512d)——jina 的 remote code 依赖 transformers 4.x 的 `clip_loss`，与 mineru 4.2 拉入的 transformers 5.x 不兼容（详见踩坑 5） |

## 环境与启动

前置：conda py312、Homebrew、Node v20+（`~/.nvs/default/bin`）、`邓帅/.env`（key 清单见 `.env.example`）。

```bash
# 0) python 依赖（mineru/mlx-lm 大件单独装）
pip install -r backend/requirements.txt && pip install mineru mlx-lm

# 1) Kafka（KRaft 单节点 + 幂等建 topic rag-data×3 分区）
brew install kafka
./scripts/start_kafka.sh

# 2) MinerU 本地引擎（一次性）
mineru config set parse_server.local.mode managed
mineru-kit models download --tier standard   # ~1GB GGUF 权重
/opt/miniconda3/envs/py312/bin/mineru server start

# 3) 后端（API + 3 worker）
./scripts/start_backend.sh          # 日志在 logs/

# 4) 前端
cd frontend && pnpm install && pnpm dev   # http://localhost:5173
```

> `docker-compose.yml` 为设计的原始 Kafka 部署方案，因本机 Docker Hub 拉取受限（无代理、国内加速源限流）暂以 brew Kafka 等价替代——集群行为完全一致，恢复代理后可直接 `docker compose -p week14-hw2 up -d` 切回。

## 运行效果（screenshots/）

| 截图 | 场景 |
|---|---|
| `01-documents.png` | 文档管理：解析完成（绿）/解析失败（红，带错误信息与重试按钮）状态展示 |
| `02-chat-multimodal.png` | 多模态对话：SSE 流式回答 + `/files/` 图片真实渲染（图文混排） |
| `03-parallel-parsing.png` | Kafka 并行解析实证：两个文档同时「解析中」、一个「待解析」 |
| `04-visual-question.png` | 图问：用户附图（缩略图）→ VL 看图回答并关联命中文档 |

## 测试

```bash
cd backend
/opt/miniconda3/envs/py312/bin/python -m pytest tests/ -q        # 31 用例（纯函数+状态机 mock）
/opt/miniconda3/envs/py312/bin/python -m pytest tests/ -m slow -q # embedding 维度/归一化（需模型缓存）
/opt/miniconda3/envs/py312/bin/ruff check src tests
```

覆盖：切块累积边界与引用行过滤、图片路径解析（参考 bug 回归测试）、`/files` URL 重写、VL 消息组装（图片上限/无图/空库/历史截断）、worker 状态机流转（done/failed/skipped/parsing 顺序）、config 缺失字段报错。

## 踩坑记录

1. **Zilliz Cloud 两套凭据**：`ZILLIZ_TOKEN` 是控制面 REST API（`api.cloud.zilliz.com.cn`，仅 v2 路径可用，v1 一律 503）的 key；pymilvus 数据面连接用 `ZILLIZ_USER_NAME + ZILLIZ_PASSWORD`。拿 token 只认 `Bearer`，等 free 集群从 `CREATING` 到 `RUNNING` 才有 `connectAddress`。
2. **`.env` 手写引号笔误**：`ZILLIZ_TOKEN="xxx`（缺闭引号）导致 python-dotenv 静默解析失败，token 读出 None——报错却发生在千里之外的 gRPC 认证。dotenv 解析失败只打一行 warning，极易漏看。
3. **torch/torchvision 错位**：装 mineru 把 torch 升到 2.15.0，torchvision 还停在 0.20.1（配 2.5.1），import 报 `operator torchvision::nms does not exist`——`pip install -U torchvision torchaudio` 对齐即可。
4. **MinerU 4.x 是重写版**：CLI 从 `mineru -p -o -b` 变成 client-server 文档中心（`parse/read/server/config` 子命令）。`-b` 后端没了，本地引擎需 `config set parse_server.local.mode managed` + `mineru-kit models download --tier standard`（注意：模型缺失时 set 会报错中断，**先下模型再 set**）。图片不再落盘而是 `doc:<sha>/tier:standard/page:N/block:M` 定位符，需 `mineru read <定位符> -f image -o x.png` 逐张导出。
5. **jina-clip-v2 × transformers 5.x 不兼容**：其 trust_remote_code 代码 `from transformers...clip import clip_loss` 在 5.x 已删除，装完 einops/timm 后仍 ImportError。改用 transformers 原生 `ChineseCLIPModel`（无 remote code，中文图文对齐更好，512 维）。教训：remote_code 模型绑定 HF 生态版本，重依赖环境里优先选原生支持模型。
6. **subprocess 的 PATH 陷阱**：worker 里 `subprocess.run(["mineru", ...])` 找不到命令——miniconda 的 bin 不在服务进程 PATH。解法：`Path(sys.executable).parent / "mineru"` 解析同环境绝对路径。
7. **Docker Hub 拉取链路**：本机 Docker Desktop 配的代理（http.docker.internal:3128）无进程监听（死配置）；国内加速源（daocloud/1ms/1panel/xuanyuan/aityp）接连限流或要凭据。最终 brew 直装 Kafka 等价替代。
8. **apple silicon 上 mineru 4.2 的引擎预热**：server 起来后 Parse Server 要「启动中」几十秒加载 GGUF，期间 parse 报 `quality_tier_unavailable`——不是配置错，等状态变「健康」再测。

## 目录

```
作业二/
├── backend/                    # ── 后端（Python / FastAPI + Kafka worker）──
│   ├── src/mrag/
│   │   ├── config.py           # pydantic-settings（向上找 邓帅/.env）
│   │   ├── models.py           # SQLite files 表 + 四态常量
│   │   ├── chunker.py          # 256 字符切块（移植参考逻辑）
│   │   ├── paths.py            # 图片路径/URL 重写（修参考 bug）
│   │   ├── embeddings.py       # bge + ChineseCLIP 单例
│   │   ├── zilliz_store.py     # 幂等建表/检索/按 db_id 删
│   │   ├── api/                # FastAPI：documents 路由 / retrieval / vl 组装 / main(SSE)
│   │   └── worker/             # mineru_parser(4.x 适配) / ingest / main(消费循环)
│   ├── tests/                  # 38 用例（31 快 + 7 slow）
│   ├── pyproject.toml          # pytest / ruff 配置
│   └── requirements.txt
├── frontend/                   # ── 前端（Vue3 + Vite + Element Plus + marked）──
│   └── src/App.vue             # 双 Tab：文档管理 / 多模态对话
├── scripts/                    # 启停：start_kafka / start_backend / stop_all
├── docker-compose.yml          # Kafka 容器方案（备用，见启动说明）
├── screenshots/                # 运行效果截图
└── data/                       # 运行时数据 uploads/ processed/ meta.sqlite（gitignore）
```
