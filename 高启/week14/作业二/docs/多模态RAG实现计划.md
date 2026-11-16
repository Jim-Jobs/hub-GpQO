# 多模态 RAG 实现计划

> 实现方式：由当前会话逐阶段实现并验证；每阶段先写失败用例，再完成最小实现。用户确认计划后开始编码。

**目标：** 在本地用真实 Kafka/Redis 验证前后端和多消费者流程，再将工程复制到优云，接入真实 MinerU、vLLM 和 Milvus Lite。

**架构：** 前端静态应用与后端代码分别维护。FastAPI 以单个 Uvicorn 进程提供 HTTP API、持有一个 Milvus Lite 客户端，并启动两个独立 Kafka consumer 线程；Kafka 主题有三个分区。consumer 并行解析不同 PDF，但所有 Milvus 写入通过共享存储模块串行提交。

**技术栈：** Python 3.12、FastAPI、Kafka、Redis、MinerU API Server、vLLM OpenAI-compatible APIs、OpenAI Python SDK、BAAI/bge-m3、Qwen/Qwen3-VL-4B-Instruct、Milvus Lite、原生 HTML/CSS/JavaScript。

**设计依据：** [多模态RAG设计说明](多模态RAG设计说明.md)

## 全局约束

- 本地使用 Conda py312；云端 Python 3.12 为部署基准。
- 本地 Docker Compose 只用于 Kafka/Redis；云端不使用 Docker。
- 一份 PDF 产生一条 Kafka JSON 任务消息；消息不包含 PDF 二进制。
- Kafka topic 初始配置三个分区，consumer group 初始配置两个消费者实例，key 使用 document_id。
- FastAPI 运行一个操作系统进程，避免多个进程同时打开同一个 Milvus Lite 数据目录。
- 同一 Milvus Lite collection 的写操作必须串行化；通过稳定 chunk_id 幂等 upsert。
- 同一时刻最多执行一个问答任务；其余任务保留 `accepted` 状态等待执行，默认上限为 1，作为配置项。
- 问答模型和 embedding 模型从各自本机 vLLM Base URL 调用，不配置外部大模型 API。
- 用户图文问题先进入范围识别；无关问题不查 Milvus；相关问题默认检索所有已完成文档。
- 用户上传、解析产物、图片、模型和数据库都使用配置的数据根目录；云端根目录为 /workspace/project03。

## 重点审查与验证

1. **一个损坏文件不能阻断同批次其他 PDF**：任务 2、3 验证逐文件接收、消费和状态隔离。
2. **Kafka 重投不能产生重复向量**：任务 3、5 验证在 Milvus upsert 成功、offset 提交前模拟重启后，稳定 chunk_id 仍只保留一条结果。
3. **范围外问题不能误查资料**：任务 6 验证 route=out_of_scope 时 embedding 与 Milvus search 调用次数均为 0。
4. **相关问题无证据时不能编造文档结论**：任务 6、7 验证状态进入 `needs_confirmation` 且不包含无依据事实。
5. **图片资源不能越权读取**：任务 8 验证未知 asset_id、其他 document_id 的 asset_id 和路径穿越均被拒绝。

## 模块结构

第一层按工程边界分为前端、后端、数据库定义、运行基础设施、文档、测试、脚本和运行数据。后端再按文档处理、问答、适配器等职责拆包；HTTP 路由只做协议转换和调用，不承担完整业务流程。

```text
project--03/
├─ frontend/                         # 网页展示和用户交互
│  ├─ index.html                     # 页面结构
│  ├─ styles/                        # 页面样式
│  └─ src/
│     ├─ api/                        # 上传、状态、问答 HTTP 请求
│     ├─ documents/                  # 多 PDF 选择、上传列表、解析状态
│     └─ chat/                       # 图文输入、回答/引用/图片混排
├─ backend/
│  ├─ app/
│  │  ├─ main.py                     # 创建 FastAPI、装配依赖和生命周期
│  │  ├─ core/                       # 配置、日志、异常和通用类型
│  │  ├─ api/                        # 薄 HTTP 路由，每个文件只负责一类资源
│  │  │  ├─ document_upload.py       # 接收上传请求并调用上传用例
│  │  │  ├─ document_status.py       # 查询单个/批量文档状态
│  │  │  ├─ chat.py                  # 提交图文问答任务并返回 chat_id
│  │  │  ├─ chat_status.py           # 查询问答任务阶段、结果或失败信息
│  │  │  ├─ chat_confirmation.py     # 接收通用回答确认并继续/取消原任务
│  │  │  ├─ assets.py                # 校验资源归属后读取解析图片
│  │  │  └─ schemas/                 # HTTP 输入/输出 DTO（Pydantic）
│  │  │     ├─ documents.py
│  │  │     └─ chat.py
│  │  ├─ documents/                  # 文档域：接收、解析、切块和索引
│  │  │  ├─ application/             # 上传、查询状态、处理单 PDF 等用例
│  │  │  ├─ domain/                  # 文档、任务、解析结果和 chunk 数据模型
│  │  │  ├─ parsing/                 # MinerU 调用、结果读取、文本/图片整理
│  │  │  └─ indexing/                # 切块、embedding 请求和向量写入协调
│  │  ├─ chat/                       # 问答任务生命周期和状态查询
│  │  │  ├─ application/             # 提交、执行、查询、确认/取消问答任务用例
│  │  │  └─ domain/                  # ChatStatusRecord、ChatStatus、ChatStage、ChatExecutionEvent
│  │  ├─ agents/                     # Agent 工作流编排，不放提示词正文
│  │  │  └─ chat/
│  │  │     ├─ agent.py              # ChatAgent 入口和节点顺序/分支调度
│  │  │     ├─ state.py              # ChatState：工作流输入、中间状态和输出
│  │  │     ├─ models.py             # 路由、证据判断、回答片段等工作流类型
│  │  │     └─ nodes/                # 范围判断、检索词整理、检索、回答节点
│  │  ├─ prompts/                    # 与 Agent 代码分离的中文提示词模板
│  │  │  └─ chat/                    # 范围判断、图文改写、回答模板
│  │  ├─ integrations/               # Kafka、Redis、MinerU、vLLM、Milvus 客户端适配
│  │  │  ├─ redis_document_store.py  # 文档状态与资料库摘要读写
│  │  │  └─ redis_chat_status_store.py # 问答任务状态与结果读写
│  │  └─ workers/                    # Kafka consumer 生命周期、消费和重试
│  └─ tests/
│     ├─ unit/                       # 不依赖外部服务的快速测试
│     ├─ integration/                # Kafka/Redis/适配器集成测试
│     ├─ evaluation/                 # 黄金集运行器、指标和评分 Agent
│     │  ├─ agents/scoring_agent.py  # 用 Qwen3-VL 输出结构化评分
│     │  ├─ prompts/scoring.md       # 独立评分标准提示词
│     │  ├─ metrics.py               # Hit@5、引用和状态等确定性指标
│     │  ├─ runner.py                # 执行用例、收集 trace、汇总结果
│     │  └─ schemas.py               # 评测用例、单条结果和运行报告类型
│     └─ fixtures/evaluation/        # JSONL 黄金集和图像输入样例
├─ database/
│  ├─ milvus/                        # collection 字段、索引参数和初始化定义
│  └─ redis/                         # 文档状态、资料库摘要的 key/字段约定
├─ infrastructure/                  # 本地 Kafka/Redis Compose 与服务配置
├─ docs/                             # README 链接的设计说明和实施计划
├─ scripts/                          # 优云 Linux 初始化、启动、停止和状态脚本
├─ data/                             # 运行数据根目录，不纳入源码版本管理
│  ├─ uploads/                       # 原始 PDF
│  ├─ parsed/                        # MinerU Markdown/JSON/图片
│  └─ milvus/                        # Milvus Lite 文件
├─ logs/                             # 结构化应用运行日志
└─ models/                           # 本地模型缓存目录
```

#### 后端职责边界

| 子模块 | 负责内容 | 不负责内容 |
|---|---|---|
| `api/` | HTTP 参数解析、文件/图片接收、响应编码、调用一个用例或 Agent | MinerU 调用、Kafka 消费、检索策略和提示词拼装 |
| `documents/application/` | 上传单据、写文件、初始化状态、发布 Kafka 事件；查询状态；编排单 PDF 处理用例 | 直接持有 FastAPI Request/Response、管理 consumer 线程 |
| `documents/parsing/` | 调用 MinerU、读取解析目录、把输出规整为统一解析结果 | Kafka 消费循环、向量数据库连接管理 |
| `documents/indexing/` | 文本切块、保留页码和图片 ID、请求 embedding、提交向量写入 | HTTP 接口和回答生成 |
| `workers/` | 建立两个同组 consumer、每线程独占 KafkaConsumer、调用文档处理用例、控制 offset/重试 | 具体 PDF 解析和问答业务规则 |
| `chat/application/` | 创建问答 ID、启动 Agent 任务、查询状态、处理通用回答确认/取消；在各阶段更新状态仓库 | Prompt、模型协议和底层 Redis 操作 |
| `chat/domain/` | 定义问答状态/阶段枚举及 `ChatStatusRecord` | HTTP 响应格式和存储连接 |
| `agents/chat/` | 显式 Python 节点工作流：合并的范围判断/检索词整理 → 分支 → 检索/证据检查 → 生成回答 | HTTP 传输、底层 Redis/Milvus 客户端细节、提示词硬编码 |
| `prompts/chat/` | 独立保存可调整的提示词模板及其变量约定 | 工作流控制和外部服务调用 |
| `integrations/` | 为 Kafka、Redis、MinerU、vLLM、Milvus 封装窄接口，统一超时和错误转换 | 多步骤业务编排 |
| `database/` | 记录 Redis 数据结构和 Milvus collection 定义/初始化 | 保存 PDF 文件或业务流程 |
| `logs/` | 保存 JSON Lines 结构化日志，供问题排查 | 持久化用户完整问题、图片、提示词或检索正文 |
| `backend/tests/evaluation/` | 运行黄金集、计算确定性指标、调用评分 Agent、生成评测报告 | 在线用户请求的生产问答逻辑 |

#### 文档 API 拆分

`documents.py` 不再承载上传、状态、图片资源等多种职责。路由按资源拆成 `document_upload.py`、`document_status.py` 和 `assets.py`；上传用例负责“校验并保存文件 → 建立 Redis 待解析状态 → 发布一条 Kafka 任务”，状态接口只读状态仓库，图片接口只按 `document_id + asset_id` 校验并返回图片。Kafka worker 调用 `documents/application/process_document.py` 处理单 PDF，不从 HTTP 路由触发解析。

#### 问答 Agent 节点约定

第一版使用显式 Python 节点工作流，不引入 LangGraph。`ChatAgent` 通过一个明确的状态对象传递用户问题、可选原图、资料库摘要、范围判断、合并检索词、检索片段、证据判断和最终响应；各节点只读写约定字段，入口负责按判断结果调度下一节点。提示词由 `prompts/chat/` 加载，模型调用由 `integrations/` 的 vLLM client 完成。无关问题直接回答并引导；相关问题检索所有完成文档；证据不足时返回“未找到依据”分支；证据充分时生成含引用和图片资源的回答。

## 数据关系

```text
HTTP 上传
  ├─ 原 PDF ───────────────────────────────> data/uploads/{document_id}.pdf
  ├─ document_id/file metadata/state ──────> Redis
  └─ IngestEvent(document_id, relative_path) -> Kafka topic (3 partitions)
                                                   ├─ consumer thread 1 ─┐
                                                   └─ consumer thread 2 ─┴─> MinerU API
                                                                              ├─ Markdown/JSON/images -> data/parsed/{document_id}/
                                                                              ├─ title/summary/profile -> Redis
                                                                              └─ text chunks -> BGE-M3 -> Milvus Lite

Question(text, optional image)
  -> create chat_id -> Redis ChatStatusRecord(accepted/running + current stage)
  -> ChatAgent ChatState -> Qwen3-VL scope route + merged textual search query
  ├─ out of scope -> direct answer + guide; no Milvus query
  └─ in scope -> BGE-M3 query embedding -> Milvus Lite top-k chunks
       ├─ insufficient evidence -> say no document evidence; ask whether general answer is wanted
       └─ evidence found -> Qwen3-VL(text + user image + retrieved text/images)
             -> answer segments + source IDs -> Redis completed result -> frontend polls and renders
  Agent node transitions -> ChatExecutionEvent -> logs/*.jsonl
```

### Stable data contracts

- IngestEvent: event_id, document_id, file_name, relative_path, created_at, attempt。
- DocumentRecord in Redis: document_id, file_name, status, progress, error, title, summary, parsed_at。
- ChunkRecord in Milvus Lite: chunk_id primary key, document_id, page_number, text, embedding, asset_ids。
- ChatRequest: question_text, optional image。
- ChatConfirmationRequest: accept_general_answer, original question_text and optional image when accepted。
- ChatAcceptedResponse: chat_id, status。
- ChatStatusRecord: chat_id, status, stage, created_at, updated_at, optional response/error。
- ChatStatusResponse: chat_id, status, stage, optional progress/response/error。
- ChatState: question_text, optional user_image, corpus_profile, route, search_query, retrieved_chunks, evidence, response。
- ChatExecutionEvent: chat_id, timestamp, stage, event_type, duration_ms, model_name, retrieved_count, optional error fields。
- ChatResponse: route, answer_segments, citations, image_assets。
- ChatStatus values: accepted, running, completed, needs_confirmation, cancelled, failed。
- ChatStage values: scope_and_rewrite, retrieval, evidence_check, answer_generation, awaiting_confirmation。
- Chunk ID is deterministic from document_id, page_number, chunk_index and normalized text so Kafka redelivery can safely upsert.

## 实施步骤

按依赖从底层到用户流程实现。每阶段交付可独立检查的模块；本地验证使用真实 Kafka/Redis 和模型替身，真实 MinerU 与 Qwen3-VL/BGE-M3 全链路评测放在云端。

阶段依赖关系：

```text
任务 1 项目基础
  → 任务 2 上传与文档状态
  → 任务 3 Kafka 消费者
  → 任务 4 MinerU 解析
  → 任务 5 切块、向量索引与资料库摘要
  → 任务 6 ChatAgent 问答节点
  → 任务 7 异步问答状态、确认和日志
  → 任务 8 前端联调
  → 任务 9 黄金集评测
  → 任务 10 云端部署与真实模型验收
```

### 任务 1：项目骨架、配置和公共数据类型

**目标：** 固定源码边界、数据目录、配置来源和跨模块数据合同。

**文件：** 创建 frontend/、backend/app/{core,api,documents,chat,agents,prompts,integrations,workers}/、backend/tests/{unit,integration,evaluation,fixtures}/、database/、infrastructure/、scripts/、data/、logs/、models/；创建 .env.example。

**主要类型/接口：** Settings；DocumentStatus、DocumentRecord、IngestEvent；ChatStatus、ChatStage、ChatRequest、ChatResponse、ChatStatusRecord；Milvus Chunk/VectorRecord。

**步骤与检查：**

- [ ] 编写配置和数据合同检查：缺少必要配置应给出明确错误；本地和云端配置互不覆盖；数据目录初始化不得写出 data_root。
- [ ] 实现 Settings 和公共枚举/模型，密钥只从环境变量读取。
- [ ] 配置 Kafka topic/consumer group/partition 数为 3/同组/2 consumers；配置 chat_max_concurrency 默认 1；配置模型 Base URL、模型名、Milvus Lite 文件路径和数据根目录。
- [ ] 运行配置与类型检查，确认 JSON 序列化、Redis 字段和 Kafka 消息字段一致。

**完成标准：** 空仓库能够读取配置、初始化目录、序列化任务和响应模型；未配置的关键服务地址会明确报错。

### 任务 2：多 PDF 上传、落盘和文档状态

**目标：** 前端一次提交多个 PDF，后端逐文件建立文档记录和 Kafka 任务。

**文件：** backend/app/api/document_upload.py、document_status.py、api/schemas/documents.py；backend/app/documents/application/upload_documents.py、get_document_status.py；backend/app/documents/domain/；backend/app/integrations/redis_document_store.py、kafka_producer.py。

**接口：** upload_documents(files) -> list[DocumentRecord]；get_document_status(document_id) -> DocumentStatusResponse。

**步骤与检查：**

- [ ] 覆盖多文件部分失败、扩展名与 PDF 签名校验、文件名路径穿越、重复文件名、状态初始化和每文件一条 Kafka 消息。
- [ ] 实现单文件独立校验和 document_id 生成；文件保存到 data/uploads/，Redis 初始化为 pending，Kafka 消息只携带 ID 和相对路径。
- [ ] 定义上传/发布失败后的状态及文件处理结果，保证失败文件不会被误显示为已解析。
- [ ] 实现单个文档状态查询；不得让 documents.py 同时承担上传、状态、解析和图片读取。
- [ ] 运行 API 与 Redis/Kafka producer 集成检查；确认一份坏 PDF 不阻断同批次其他文件。

**完成标准：** 多文件上传响应为每份文件返回 document_id 和初始状态；Redis 与 Kafka 均可按 document_id 追踪任务。

### 任务 3：Kafka 多消费者与处理生命周期

**目标：** 体现 Kafka 并行消费；每个 PDF 消息只由一个 worker 处理。

**文件：** backend/app/workers/kafka_consumers.py、consumer_manager.py；backend/app/documents/application/process_document.py；对应 worker 测试。

**接口：** start_consumers(process_document)；handle_ingest_event(event) -> ProcessingOutcome。

**步骤与检查：**

- [ ] 使用 fake processor 检查两个独立 consumer 使用同一 group_id、各自只由所属线程操作，并能同时处理不同 document_id。
- [ ] 实现单 Uvicorn 进程内两个 consumer 线程；每个 consumer 使用独立 KafkaConsumer 对象，不能跨线程共享。
- [ ] 手动提交 offset：成功并已持久化最终状态后提交；终止失败状态写入 Redis 后提交；进程中断时保留未提交消息供 Kafka 重投。
- [ ] 对可重试服务错误最多重试 3 次（最多 4 次总尝试），使用可配置的 1/2/4 秒退避；永久输入错误不重试。达到上限后写 failed 状态和错误摘要，再提交 offset。
- [ ] 使用本地 Kafka 三分区验证分区分配；两条不同 PDF 任务应能并行进入处理器，状态不得串文档。

**完成标准：** 两个消费者并行处理不同 PDF；重复投递可被下游幂等处理；异常文件不会阻断同批次其他任务。

### 任务 4：MinerU 解析和解析产物归一化

**目标：** 将 MinerU 输出统一成带页码和图片资源 ID 的后端数据类型。

**文件：** backend/app/documents/parsing/mineru_parser.py、result_reader.py、asset_registry.py；backend/app/integrations/mineru_client.py；backend/app/documents/domain/models.py。

**接口：** parse_document(event) -> ParsedDocument；register_assets(document_id, output_dir) -> list[ImageAsset]。

**步骤与检查：**

- [x] 用 MinerU client fake 覆盖创建任务、轮询、超时、终态失败、结果下载失败和空解析结果；同时覆盖解析目录和 ZIP 路径边界。
- [x] 按 MinerU 4.2.1 Tasks API 调用；创建请求结果不确定时不自动重发，明确拒绝接单的 `server_shutting_down` 可使用外层有限重试。
- [ ] 接入切块和向量索引前，保持文档 parsing 状态；只有索引和摘要持久化成功后才标记 completed（任务 5 一并接线）。
- [x] 将 Markdown/结构化结果归一为 ParsedDocument/ParsedPage；每页保留 page_number、text 和 asset_ids。
- [x] 将导出图片登记为 ImageAsset，检查 asset_id 唯一性和文件路径必须位于 data/parsed/{document_id}/ 内。
- [ ] 检查示例 PDF 的文字页、表格页、知识图谱示意页是否保留黄金问题所需内容；解析缺失须报告为解析覆盖问题，不允许模型猜测后算通过。

**完成标准：** 解析产物和图片全部位于对应 document_id 目录；统一数据模型可供 chunker 与资源接口使用。

### 任务 5：文本切块、Embedding、Milvus Lite 和资料库摘要

**目标：** 写入可追溯、可重试的文本向量，并构建自动资料库范围摘要。

**文件：** backend/app/documents/indexing/chunk_builder.py、embed_chunks.py、write_vectors.py、corpus_profile.py；backend/app/integrations/embedding_client.py、milvus_store.py、redis_document_store.py；database/milvus/、database/redis/。

**接口：** build_chunks(parsed) -> list[ChunkRecord]；embed_texts(texts) -> list[list[float]]；upsert_chunks(chunks) -> None；update_corpus_profile(completed_records) -> CorpusProfile。

**步骤与检查：**

- [x] 覆盖页码/图片关联切块、空文本、超长文本、稳定 chunk_id、embedding 维度错误、重复 upsert 和 Milvus 写入失败。
- [x] 实现确定性 chunk_id；将 document_id、页码、块序号、正文、向量和 asset_ids 存入 Milvus Lite。
- [x] 通过 OpenAI Python SDK 调用本机 vLLM BGE-M3 Embeddings；批量请求并校验返回数量和向量维度。
- [x] 全进程按数据库路径复用一个 Milvus Lite 客户端；同一 collection 写入通过同一把锁串行化。
- [x] 从 MinerU 标题/目录/正文生成 DocumentRecord 摘要，再汇总为 CorpusProfile 写入 Redis；本地使用固定摘要模型替身验证。
- [x] 只在向量和摘要持久化成功后标记文档 completed；Redis 事务同步写状态与概览，Kafka 重投通过稳定 ID 幂等 upsert。

**完成标准：** 文档可按页和图片资源追溯；重复任务不增加重复 chunk；资料库摘要仅由 completed 文档生成。

### 任务 6：ChatAgent 显式 Python 节点工作流

**目标：** 实现范围判断、合并检索、证据不足处理和多模态回答，不把流程逻辑放进 API 路由。

**文件：** backend/app/agents/chat/{agent.py,state.py,models.py}、nodes/{scope_and_rewrite,retrieve,evaluate_evidence,generate_answer}.py；backend/app/prompts/chat/；backend/app/integrations/vllm_chat.py、embedding_client.py、milvus_store.py。

**接口：** ChatAgent.run(state: ChatState) -> ChatResponse；scope_and_rewrite(question, image, corpus_profile) -> RouteAndQuery；search_chunks(query, limit) -> list[RetrievedChunk]。

**步骤与检查：**

- [x] 先用 fake model/vector store 覆盖范围外零检索、范围内合并检索词、全 completed 文档搜索、用户图像传入 Qwen3-VL、无证据不编造、引用 asset_id 校验。
- [x] 实现 scope_and_rewrite 单节点；资料范围外直接通用回答并引导，不能调用 embedding 或 Milvus。
- [x] 范围内使用合并后的文本检索词调用 BGE-M3 与 Milvus；图像不另建向量索引，用户图像仍随最终问题发给 Qwen3-VL。
- [x] 按检索证据判定充分性；不足时返回 needs_confirmation，不生成伪装成资料结论的答案。
- [x] 证据充分时将问题文字、用户图片、文本证据及关联 PDF 图片交给 Qwen3-VL；回答由文本/图片 AnswerSegment 和 citations 构成。
- [x] 将所有提示词从 agent.py 移至 prompts/chat/，模型请求通过 integrations 适配器执行。

**完成标准：** 单元级 fake 用例覆盖范围外、范围内、有证据和证据不足四条路径；API 文件不含问答编排或硬编码提示词。

### 任务 7：异步问答任务、状态、确认和结构化日志

**目标：** 前端可轮询阶段；每个任务可完成、等待确认、取消或失败；保护 4090 不被并发问答压满。

**文件：** backend/app/main.py、api/chat.py、api/schemas/chat.py；chat/application/{ports,task_manager}.py、domain/{enums,models}.py；integrations/redis_chat_status_store.py、chat_execution_logger.py。

**接口：** submit_chat(request) -> ChatAcceptedResponse；get_chat_status(chat_id) -> ChatStatusResponse；confirm_chat(chat_id, request) -> ChatStatusRecord。

**步骤与检查：**

- [x] 覆盖 accepted → running → completed、needs_confirmation → accepted → running → completed、拒绝后 cancelled、模型/Redis 错误后 failed。
- [x] POST /api/chat 立即返回 chat_id；后台执行 Agent；GET /api/chat/{chat_id} 返回 status、stage、结果或错误摘要。
- [x] 实现单个 FIFO 问答任务队列，worker 数由并发配置控制（默认 1），等待容量默认 16 且可配置；等待中的任务保持 accepted，按入队顺序领取。队列满时 HTTP 429 拒绝新任务，不创建悬空状态记录。
- [x] 通用回答确认接口要求前端重传原问题和可选图片并沿用 chat_id；接受后只生成通用回答，不重新检索；拒绝后取消任务。用户正文和图片只在任务处理期间保留在内存，不长期存入 Redis。
- [x] Agent 在 scope_and_rewrite、retrieval、evidence_check、answer_generation、awaiting_confirmation 阶段更新 Redis；进程重启后将遗留 accepted/running 标记为 failed。
- [x] 每个阶段写 ChatExecutionEvent 到 JSON Lines；日志仅含 chat_id、阶段、耗时、模型、检索数量和错误代码，不含完整问题、图片、提示词或检索正文。
- [x] 验证 ChatStatusRecord 可序列化/反序列化；状态 TTL 默认 7 天，日志默认每 10 MiB 轮转并保留 5 份，且均由配置控制；用户图片默认限制 10 MiB。

**完成标准：** 前端能够查询稳定状态和最终结果；并发上限有效；确认流程不要求服务端长期保存对话内容；日志不泄露正文。

### 任务 8：前端上传、阶段轮询和图文混排

**目标：** 页面能完整操作多 PDF 和多模态问答流程。

**文件：** frontend/index.html、src/api/、src/documents/、src/chat/、styles/；backend/app/api/assets.py。

**接口：** POST /api/documents；GET /api/documents/{document_id}；POST /api/chat；GET /api/chat/{chat_id}；POST /api/chat/{chat_id}/confirmation；GET /api/assets/{document_id}/{asset_id}。

**步骤与检查：**

- [x] 用 HTTP mock 覆盖多文件上传、逐文件状态、问答阶段和终态展示。
- [x] 实现多 PDF 上传及 pending/parsing/completed/failed 显示；某个文件失败不隐藏其他文件状态。
- [x] 实现文字+图片提问、chat_id 轮询、accepted 队列状态、等待确认后的原问题/图片重传。
- [x] 按 AnswerSegment 顺序混排回答文本与受控图片；展示文档名、页码和引用。
- [x] 验证未知 asset_id、跨 document_id asset_id 和路径穿越均无法读取图片。

**完成标准：** 浏览器可完成上传、解析进度查询、图文提问、确认和图文回答呈现。

### 任务 9：黄金集评测代码与评分 Agent

**目标：** 自动验证可确定的系统行为，并由独立评分 Agent 评价自然语言答案质量。

**文件：** backend/tests/evaluation/{runner.py,metrics.py,schemas.py}、agents/scoring_agent.py、prompts/scoring.md；backend/tests/fixtures/evaluation/qa_golden.jsonl、images/；backend/tests/reports/（运行时生成，不纳入版本管理）。

**接口：** run_evaluation(cases, trace_collector) -> EvaluationRunReport；score_answer(case, answer, evidence) -> ScoringResult。

**步骤与检查：**

- [x] 校验 33 条 JSONL、类别数量、必填字段和图片 fixture 路径。
- [x] 实现 test-only trace collector，采集 route、Top-5 chunk/page、stage、citations、模型是否收到用户图像；trace 不进入公开 API 响应。
- [x] 代码确定性计算 Hit@5、范围路由、页码引用、状态流转、图像传递和图片 asset 归属。
- [x] ScoringAgent 复用 Qwen3-VL，按验证集文档中的四项 rubric 输出维度分、fatal_flags 和理由；评分提示词单独维护。
- [x] Python 代码计算加权总分和通过状态；评分 JSON 解析失败或确定性断言失败均不能默认为通过。
- [x] 评测请求串行执行、temperature=0；本地使用替身验证 runner。真实 MinerU/vLLM 评测在任务 10 执行。
- [ ] 生成真实模型评测报告并人工复核五类核心问题各一条及全部 fatal case。

**完成标准：** 可以一条命令运行黄金集并得到可复核报告；20 条核心问答达到既定分数目标，5 条范围外和 5 条证据不足按要求通过确定性断言。

### 任务 10：云端脚本和真实链路验收

**目标：** 在优云 Ubuntu 22.04、Python 3.12.11、RTX 4090 上完成非 Docker 部署和端到端验证。

**文件：** scripts/start.sh、stop.sh、status.sh、优云 /start.d/ 入口；README.md；云端 .env。

**步骤与检查：**

- [ ] 检查启动/停止/状态脚本的重复启动、PID、日志和缺配置行为；脚本不得覆盖模型或用户数据目录。
- [ ] 以原生进程启动 MinerU、vLLM embedding、vLLM Qwen、FastAPI；Kafka/Redis 使用服务器本机服务。
- [ ] 将 Kafka、Redis、Milvus Lite、上传/解析数据、模型缓存和日志限制在 /workspace/project03；仅暴露 Web 服务端口。
- [ ] 上传示例 PDF，确认 MinerU 解析、Redis 状态、Milvus 向量、图片资源和 33 条黄金集问答。
- [ ] 用同一 PDF 创建两个不同 document_id 的任务，确认两个 consumer 可并行；检查 Milvus 写入锁、重复消息幂等和失败状态。
- [ ] 记录首次云端评测基线；任何指标未达阈值时先定位解析、检索、提示词或引用环节，不通过修改黄金答案来提高分数。

**完成标准：** 服务可按脚本启停；重启后已完成文档和向量可查询；真实模型通过本地 OpenAI 兼容接口完成图文问答；黄金集报告可复跑。

---

任务 1 至 8 已完成。任务 9 的评测代码与本地替身验证已实现；真实模型评测报告和人工复核安排在任务 10 云端联调阶段完成。
