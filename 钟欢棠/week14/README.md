# Week14 作业：Redis / Kafka 实践 + 基于 Kafka 的多模态 RAG 问答

项目根目录：`homework/`。所有代码均可直接在课程镜像（Linux + Python 3.10+）中运行。

---

## 作业 1

### 1.1 注册 compshare 并发送 Company ID

Company ID: 66167624

### 1.2 在镜像中写两个程序实践 Redis 和 Kafka

| 文件 | 内容 | 运行 |
| --- | --- | --- |
| `homework1/demo_redis_chat.py` | Redis List 存多轮对话、Hash 存元信息、TTL 过期、`LTRIM` 限制长度、转成 LLM messages | `python homework1/demo_redis_chat.py --demo` |
| `homework1/demo_kafka_chat.py` | Kafka 生产者 / 消费者、消息 key 分区、投递回调、消费组与手动提交 offset | `python homework1/demo_kafka_chat.py --role demo` |

```bash
cd homework
docker compose up -d                       # 启动 Redis + Kafka
python homework1/demo_redis_chat.py --demo
python homework1/demo_kafka_chat.py --role demo

# 两个终端体验「通过 Kafka 传递消息」：
python homework1/demo_kafka_chat.py --role consumer --group chat-a
python homework1/demo_kafka_chat.py --role producer --user 张三
```

作业 2 的系统复用了作业 1 的能力：`app/store.py` 用 Redis 保存会话历史，`app/messaging.py` 用 Kafka 传递全部任务与回答。

---

## 作业 2：基于 Kafka 的多模态 RAG 问答

### 快速开始

```bash
cd homework
docker compose up -d                       # Redis:6379  Kafka:9092（单节点 KRaft）
pip install -r requirements.txt
copy .env.example .env                     # Linux/macOS: cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（OpenAI 兼容接口，课程示例为阿里云百炼）
python run.py                              # 同时启动 API + 两个 Kafka Worker
# 浏览器打开 http://localhost:8000
```

> 课程镜像中如已按 `02_MinerU.md` 安装 MinerU（`mineru` 命令可用），会被自动优先使用；
> 未安装时自动降级为 PyMuPDF 逐页抽取文本 + 渲染页面图片，功能同样可跑通。
> 若镜像里已有 Redis/Kafka，可以跳过 `docker compose`，直接改 `.env` 指向现有地址。

### Windows 本机没有 Docker 怎么办

本机 Windows 上出现 `无法将"docker"项识别为 cmdlet...` 是正常的：Docker Desktop 没装。
另外 PowerShell 提示 `无法加载 profile.ps1，因为在此系统上禁止运行脚本` 只是登录脚本被
`ExecutionPolicy` 拦截的警告，**不影响任何命令**，需要消除的话（用户级、无需管理员）：

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
```

有两条可行的路：

#### A. 交作业：在课程镜像里跑真实 Redis + Kafka（推荐）

把整个 `homework/` 目录上传到 compshare 镜像（Jupyter 上传或打包 zip），然后：

```bash
cd homework
bash scripts/start_infra.sh          # 有 Docker 就用 docker compose；没有则原生安装 Redis + Kafka(KRaft)
pip install -r requirements.txt
cp .env.example .env                 # 保持 APP_MODE=kafka
python run.py
```

`scripts/start_infra.sh` 会在镜像里安装/启动 `redis-server`、Java 与 Kafka 3.7.0 单节点，
**用的仍是真实 Kafka**（Topic、消费组、offset 都是真的），不依赖 Docker Desktop。

#### B. 本机预览：APP_MODE=local，零外部服务

```powershell
cd homework
pip install -r requirements.txt
$env:APP_MODE = "local"
python run.py
# 浏览器打开 http://localhost:8000
```

`local` 模式把 broker 换成进程内消息总线（`_LocalBus`：Topic + 每消费组独立 offset）、
把 Redis 换成文件状态（`data/local_state.json`），Worker 内嵌在网页进程里。
Topic 名、消息格式、消费组语义、前端与全部业务代码都和 Kafka 版一致，
因此可以先在本机把上传/解析/问答流程跑通；**提交时请务必用 `APP_MODE=kafka`**。

> 本地模式仍需 `PyMuPDF`（已在 requirements 中）来解析 PDF；未装 MinerU 时自动用它。
> 作业 1 的两个程序必须使用真实 Redis/Kafka，请在镜像或装好 Docker 的环境里运行。

### 功能对照

| 作业要求 | 实现位置 |
| --- | --- |
| ① 前端上传多个 PDF，经 Kafka 到后端，MinerU 解析并存储 | `web/index.html` / `web/app.js` 上传区 → `POST /api/upload` → `mmrag.pdf.tasks` → `app/worker.py:handle_parse` → `app/parser.py` → `data/docs/<id>/content.md` + `assets/` + `data/index/chunks.json` |
| ② 前端展示「待解析 / 解析中 / 解析完成」（另有「解析失败」） | `app/store.py` 文档状态 Hash + `GET /api/documents` 每 1.5s 轮询；状态变更同时发到 `mmrag.pdf.status`，左侧有状态徽章、进度条与实时 Kafka 事件流 |
| ③ 多模态问答：输出图文混排 | 检索片段中的图片随上下文一起送视觉大模型（base64），提示词要求用 `![说明](/media/...)` 引用；`web/app.js` 的 Markdown 渲染器把图片直接内嵌在回答里，并附来源与图片画廊 |
| ③ 多模态问答：图 + 问一起送 LLM | 前端「＋ 图片提问」→ `POST /api/qa` multipart → `mmrag.qa.requests` → `app/llm.py` 把「用户图片 + 问题文字 + 检索片段 + 检索图片」组装成一条 multimodal user message 调用 `qwen-vl-max-latest` |

### 数据流

```
浏览器 ──上传 PDF──> FastAPI ──[mmrag.pdf.tasks]──> 解析 Worker(MinerU)
                                                      │ 更新 Redis 状态
                                                      ├─[mmrag.pdf.status]──> 状态面板
                                                      └─ Markdown/图片/向量索引落盘

浏览器 ──问题(+图片)──> FastAPI ──[mmrag.qa.requests]──> 问答 Worker
                                                      │ Redis 历史 + 向量/词法检索
                                                      │ 图片 base64 + 文本 -> 视觉 LLM
FastAPI <──[mmrag.qa.responses]────────────────────────┘
   │ 写入 Redis 问答状态与会话历史
浏览器 <── 轮询 GET /api/qa/{id} ── 图文混排回答
```

| Topic | 生产者 → 消费者 | 作用 |
| --- | --- | --- |
| `mmrag.pdf.tasks` | 网页 → `mmrag-pdf-parser` | 每个 PDF 一条解析任务 |
| `mmrag.pdf.status` | 解析 Worker → 网页 | 解析状态事件 |
| `mmrag.qa.requests` | 网页 → `mmrag-qa-worker` | 文字 / 图文提问 |
| `mmrag.qa.responses` | 问答 Worker → 网页 | 最终回答、来源、图片 |
| `mmrag.events` | 全链路 → 网页 | 前端「Kafka 事件流」面板 |

### 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/upload` | multipart 多文件 `files[]`，返回每个文档的初始状态 |
| GET | `/api/documents` | 文档列表 + 状态 + 片段数/图片数 |
| GET | `/api/documents/{id}/content` | 解析后的 Markdown 与图片（前端「预览内容」） |
| POST | `/api/qa` | `question` / `session_id` / `doc_ids` / `use_vision` / `image` |
| GET | `/api/qa/{request_id}` | 轮询问答结果（`排队中 → 生成中 → 完成`） |
| GET/DELETE | `/api/history` | Redis 会话历史 |
| GET | `/api/events` | Kafka 事件流 |
| GET | `/api/health` | Redis / Kafka / MinerU / LLM / 索引状态 |

### 配置与降级

- `.env` 中 `PARSER_BACKEND=auto|mineru|mineru-api|pymupdf`；`MINERU_API_URL` 可指向课程文档里的 `mineru-api` 服务（支持返回 Markdown / JSON / zip）。
- `LLM_BASE_URL` / `LLM_MODEL` 使用 OpenAI 兼容协议，默认百炼 `qwen-vl-max-latest`；`LLM_TEXT_MODEL` 用于视觉调用失败时降级。
- `EMBED_ENABLED=true` 时用 `text-embedding-v3` 做向量检索，并与中文 bigram 词法分数混合排序；无 Key 或接口失败时自动使用纯词法检索。
- **未配置 `LLM_API_KEY` 时系统会明确标注「演示模式」**，只展示检索片段摘录与图片，不会伪称是模型回答。配置 Key 后即得到真正的图文混排回答。
- 图片被压缩到 1024px / JPEG 后再以 base64 送入模型，避免请求体过大。

### 目录

```
homework/
├── homework1/                   # 作业 1：Redis / Kafka 两个独立程序
├── app/
│   ├── config.py                # 环境变量、Topic、状态常量
│   ├── messaging.py             # Kafka 生产者/消费者 + 本地模式消息总线
│   ├── store.py                 # Redis / 文件版：文档状态、问答结果、历史、事件
│   ├── parser.py                # MinerU CLI / API / PyMuPDF 解析与图片改写
│   ├── rag.py                   # 分块、向量+词法检索、JSON 索引
│   ├── llm.py                   # embeddings + 多模态 chat completions
│   ├── worker.py                # 解析 Worker、问答 Worker（两个消费组）
│   └── main.py                  # FastAPI 接口 + 回答/事件消费线程
├── web/                         # 前端页面（原生 HTML/CSS/JS，无 CDN 依赖）
├── scripts/start_infra.sh       # 镜像内原生启动真实 Redis + Kafka（无 Docker 时）
├── tests/                       # 离线单测：核心逻辑 / API / Worker / 本地模式
├── docker-compose.yml           # Redis + Kafka(KRaft)
├── requirements.txt
├── .env.example
└── run.py                       # 一键启动 API + Worker
```

### 自检与常见问题

```bash
python -m compileall app homework1 run.py tests
python -m unittest discover -s tests -v
```

- **`docker: 无法将"docker"项识别为 cmdlet...`**：本机没装 Docker Desktop。用 `APP_MODE=local` 本机预览，或把代码上传到镜像执行 `bash scripts/start_infra.sh`。
- **`无法加载 profile.ps1，禁止运行脚本`**：PowerShell 执行策略拦截了个人登录脚本，仅警告、不阻塞命令；`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force` 可消除。
- **网页能开但上传后一直「待解析」**：Worker 没启动或 Kafka 未就绪，看 `python run.py` 日志；Kafka 模式用 `docker compose ps` 或 `bash scripts/start_infra.sh` 检查。
- **一直「解析失败」**：查看文档卡片上的错误信息；MinerU 未安装会自动降级 PyMuPDF，如两者都缺，执行 `pip install PyMuPDF`。
- **回答一直是演示模式**：`.env` 里填 `LLM_API_KEY` 并重启 `python run.py`。
- **端口被占用**：`python run.py --port 8080`，浏览器访问对应端口。
- 课程环境若通过端口转发访问，请把 `localhost:8000` 换成镜像对外映射的地址。
