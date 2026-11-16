# Kafka 多模态 RAG 问答

## 项目目标

开发一个前后端分离的 PDF 多模态 RAG 问答系统。网页支持一次上传多个 PDF、查看逐文件解析状态，并用文字和图片提问；相关问题基于已解析文档检索回答，回答可混排文字、文档图片和来源。

本地用 Python 3.12 与 Docker Compose 启动 Kafka、Redis 做流程验证；模型服务用替身。验证通过后复制到优云的 /workspace/project03，使用 Ubuntu 22.04、RTX 4090 和已安装的原生服务做完整联调。云端运行不使用 Docker。云端 MinerU 环境为 Python 3.12.11，已解析安装 `mineru[torch]>=4.0,<5` 得到 MinerU 4.2.1；后端按其 Tasks API 调用。

## 技术实现

### PDF 写入与解析

1. 前端通过 HTTP 一次上传多个 PDF；FastAPI 为每个文件生成 document_id，将原始文件保存到项目数据目录。
2. FastAPI 在 Redis 创建“待解析”记录，并由 Kafka producer 为每个 PDF 发送一条任务消息。Kafka 消息只放任务 ID、文档 ID、文件路径和重试信息，不传输 PDF 二进制。
3. 任务主题配置三个分区；两个 Kafka consumer 使用相同 group_id，由 Kafka 将分区分配给它们。消费者实例以线程方式运行在唯一的后端进程内，所以可以并行领取并解析不同 PDF，同时只有该进程打开 Milvus Lite 数据目录。
4. 消费者更新 Redis 状态，调用本机 MinerU API Server 解析文件，将原始 PDF、Markdown、结构化结果和导出图片保存到文档目录。
5. 后端把解析文本切成带文档 ID、页码和图片路径的文本块，调用本机 vLLM BGE-M3 生成向量，再通过共享 Milvus Lite 客户端写入 collection。写操作通过同一把锁串行化；稳定 chunk_id 保证重试时可幂等 upsert。
6. 索引和摘要成功后，在 Redis 同一事务中更新“解析完成”状态与资料库概览，再提交 Kafka offset。失败时记录错误并按有限次数重试，网页显示失败状态。

文档切块按页进行，目标 800 字符、重叠 120 字符；BGE-M3 产生 1024 维向量，按稳定 chunk ID 幂等写入 Milvus Lite。Qwen3-VL 为每份文档生成标题和摘要；Redis 的资料库概览只汇总已完成摘要。字段细节见 [Milvus collection](database/milvus/collection.md) 和 [Redis keys](database/redis/keys.md)。

### 用户提问与图文回答

1. 前端提交图文问题后，后端创建 `chat_id` 并立即返回；问答任务在后端执行，状态写入 Redis，前端按 `chat_id` 查询当前阶段和结果。
2. 后端将问题交给 `ChatAgent` 的显式 Python 节点工作流。Agent 从 Redis 读取已解析 PDF 自动汇总的资料库主题摘要；Qwen3-VL 同时分析问题文字和可选图片，判断是否属于当前资料范围，并把图文问题合并成一条文字检索语句。Agent 节点代码与 `prompts/chat/` 下的提示词模板分开维护，不引入 LangGraph。
3. 若问题不相关，直接由 Qwen3-VL 回答，并引导用户询问资料库涉及的主题；此路径不访问 Milvus。
4. 若问题相关，后端使用 BGE-M3 将合并后的检索语句向量化，在 Milvus Lite 中检索全部已完成 PDF 的文本块。检索不够相关时，说明资料库没有找到依据，并询问用户是否需要通用回答。
5. 检索有依据时，后端把用户文字、用户图片、相关文本块和这些文本块关联的 PDF 图片一起传给本机 vLLM Qwen3-VL。Python 后端通过 OpenAI SDK 调用本机 OpenAI 兼容接口。
6. 后端返回回答、文档/页码引用和受控图片资源；前端以文本、图片和来源混排呈现。

范围内问题默认检索 Top-5 文本块；单独由 Qwen3-VL 判断检索证据能否回答问题，证据判断只读取问题文本和 PDF 证据，不把用户图当作文档依据。最终回答最多接收 3 张去重的 PDF 图片，另可附 1 张用户图；vLLM 服务需允许每次请求至少输入 4 张图片。

问答状态分为已接收、处理中、已完成、等待用户确认、已取消和失败；处理中还会标明范围判断/检索词整理、检索、证据判断、回答生成等阶段。问答任务通过单个 FIFO 队列调度，并发默认 1、最多 16 个等待任务；用户图单张默认限制 10 MiB。等待确认后，前端可重传原问题和图片并沿用原 `chat_id` 继续通用回答。Redis 问答状态默认保留 7 天。结构化运行日志记录阶段、耗时和错误，不保存完整问题、图片或提示词；日志文件按 10 MiB 轮转并保留 5 份；第一版不持久化聊天历史。

### 数据分别存在哪里

| 数据 | 存储位置 | 用途 |
|---|---|---|
| 原始 PDF、MinerU 解析文件、图片 | 项目数据目录 | 文件持久化和网页图片读取 |
| 任务状态、文档标题/摘要、失败信息 | Redis | 上传列表状态与资料库范围摘要 |
| PDF 解析任务 | Kafka | 异步排队、消费者组分配和失败重试 |
| 文本块、向量、页码、文档 ID、图片路径 | Milvus Lite | 文本语义检索和来源关联 |
| 问答状态和最终响应 | Redis | 前端轮询问答阶段、读取结果或失败信息 |
| 问答节点执行事件 | `logs/` 下的结构化日志 | 排查节点耗时、模型调用和失败原因，不保存对话正文 |
| 模型权重 | 项目模型目录 | MinerU、BGE-M3 和 Qwen3-VL 运行 |

Milvus Lite 同一数据目录由一个操作系统进程持有；本系统将 FastAPI、Kafka consumer threads 和共享 Milvus 客户端放在一个后端进程中。消费者可并行处理不同文件，但 Milvus 同一 collection 的写入串行化。Kafka 的消费者并行度受主题分区数限制，实际解析并发还受 4090 显存预算限制。

## 目录结构

```text
project--03/
├─ frontend/             # 页面、HTTP 调用、文档上传状态和图文问答交互
├─ backend/              # 薄 API、文档处理域、问答任务、ChatAgent、适配器和 Kafka workers
├─ backend/tests/        # 后端单元、集成与黄金问答评测
├─ database/             # Milvus collection 与 Redis 数据结构定义
├─ infrastructure/       # 本地 Kafka/Redis Compose 配置
├─ docs/                 # 设计说明与详细实现计划
├─ scripts/              # 优云 Linux 服务初始化、启动、停止和状态脚本
├─ data/                 # 原始 PDF、MinerU 产物、图片与 Milvus Lite 数据
├─ models/               # 本地模型缓存
├─ logs/                 # 运行日志
├─ .env.example          # 本地/云端配置模板
└─ README.md             # 目标、流程、运行方法和预期结果
```

后端按职责分层：API 路由不直接解析 PDF 或执行检索；文档上传、状态查询、MinerU 解析和向量索引分别由文档域用例处理；Kafka consumer 负责并行调度单 PDF 任务；`ChatAgent` 负责问答节点流转，提示词单独存放。模块职责和文件级拆分见 [多模态RAG实现计划](docs/多模态RAG实现计划.md)。

## 实现阶段

1. 建立项目骨架、配置、公共数据类型和数据目录。
2. 实现多 PDF 上传、Redis 文档状态及 Kafka 任务发布。
3. 实现两个 Kafka consumer 并行消费和 MinerU 解析。
4. 实现文本切块、BGE-M3 向量化、Milvus Lite 幂等写入及资料库摘要。
5. 实现 ChatAgent 范围判断、合并检索、证据判断和图文回答。
6. 实现异步问答状态、单任务并发限制、确认流程和结构化日志。
7. 完成前端上传/状态/图文问答，再在测试模块中加入黄金集评测和评分 Agent。
8. 部署到云端，运行真实 MinerU、vLLM 和 33 条黄金验证集。

## 目标验证

首轮黄金验证集共 33 条，覆盖 NLP 五类 QA、范围外、证据不足和图文输入/输出。测试模块中的评分 Agent 使用 Qwen3-VL 按事实正确性、证据忠实度、完整性和行为符合度评分；代码自动检查检索、路由、引用、状态和图片传递。目标包括核心问答评分通过率不低于 90%、检索 Top-5 命中率不低于 90%、引用页码正确率不低于 95%。详细评分标准、问题和验收口径见 [多模态RAG验证集设计](docs/多模态RAG验证集设计.md)。

详细模块接口和验收步骤见 [多模态RAG实现计划](docs/多模态RAG实现计划.md)，设计取舍见 [多模态RAG设计说明](docs/多模态RAG设计说明.md)。

## 开发环境与当前进度

使用 Python 3.12 开发。首次设置时复制 `.env.example` 为 `.env`，按本机或云端环境填写服务地址；进程环境变量会覆盖 `.env` 中的同名值。`RAG_ENV=local` 默认将运行数据写入仓库的 `data/`，`RAG_ENV=cloud` 默认使用 `/workspace/project03`，也可用 `RAG_DATA_ROOT` 明确指定根目录。单个 PDF 默认限制为 50 MiB，可通过 `RAG_MAX_PDF_BYTES` 调整。

```powershell
conda activate py312
python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
python -m pytest backend/tests/unit -q
python -m backend.app.main --host 127.0.0.1 --port 8000
```

启动后浏览器访问 `http://127.0.0.1:8000/`。前端接口 mock 使用 Node.js 内置测试运行器（无需 npm 依赖）：

```powershell
node --test frontend/tests/api.test.mjs
```

只查看页面布局时，不需要填写 `.env` 或启动外部服务，可运行静态预览模式：

```powershell
python -m backend.app.main --frontend-preview --host 127.0.0.1 --port 8000
```

然后访问 `http://127.0.0.1:8000/`。此模式只提供页面和静态文件，上传与问答 API 不可用。

开发按[实现计划](docs/多模态RAG实现计划.md)逐阶段推进。配置、多 PDF 上传、两个 Kafka consumer 线程、MinerU 4.2.1 解析、按页切块、BGE-M3 向量化、Milvus Lite 幂等 upsert、Qwen3-VL 文档摘要和 Redis 资料库概览已完成单元级实现。可重试错误默认最多尝试 4 次，退避 1/2/4 秒；MinerU 创建请求结果不确定时不自动重发。真实云端模型与 Milvus Lite 联调仍待部署阶段验证。运行数据、日志和模型目录不会提交到 Git。

问答阶段已实现显式 `ChatAgent` 流程：范围判断、completed 文档 Top-5 检索、Qwen3-VL 文本证据充分性判断和带引用的图文回答。范围外问题不访问向量库；证据不足时返回 `needs_confirmation`。最终回答可接收 1 张用户图和最多 3 张去重 PDF 图片；证据判断器只接收问题和 PDF 文本证据。真实 vLLM、Redis 与 Milvus 服务联调仍待部署阶段验证。

前端已接入多 PDF 上传和逐文件解析状态、图文提问和异步阶段轮询、证据不足确认流程，以及按回答顺序呈现文本、文档图片和来源页码。浏览器入口由 FastAPI 提供；文档图片只允许读取已完成文档中登记的资源 ID。真实模型与服务的浏览器端到端联调仍需在服务启动后验证。

黄金集评测代码已支持校验 33 条固定用例、串行运行 ChatAgent、由 Qwen3-VL 独立评分并生成可复核 JSON 报告。云端完成示例 PDF 解析后，使用 Redis 中的 `document_id` 运行：

```powershell
python -m backend.tests.evaluation.runner --document-id DOCUMENT_ID
```

评测需要 `.env` 中 Redis、BGE-M3 Embedding、Qwen3-VL 地址可用，以及该文档的 Milvus Lite 数据；不需要重新启动 Kafka 或 MinerU。报告写入 `backend/tests/reports/`，不会提交到 Git。模型评分和人工复核需在真实服务联调时完成。

MinerU 使用独立服务。云端已知解析版本为 4.2.1；后端向其 V1 Tasks API 提交任务、上传 PDF、轮询状态并下载 default ZIP。单任务默认最多等待 30 分钟、每 2 秒轮询，可通过 `.env` 配置；任务创建结果不确定时不自动重发，以免产生重复解析任务。4.2.1 的 PyPI 发布状态带有 Linux 任务卡住的撤回说明，云端真实解析需重点验收。
