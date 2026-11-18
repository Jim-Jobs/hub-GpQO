# AGENTS.md

面向在本仓库工作的编码代理。动手前先读 [README.md](README.md) 与 [规格说明书](docs/specs/2026-11-16-sentiment-chat.md)；术语以 [CONTEXT.md](CONTEXT.md) 为准。

## 项目概览

- FastAPI + Qwen 情感对话服务：输入文本 → 二分类情感 → 温和中文回复，支持多轮对话与历史恢复。
- 两套可切换引擎：LangGraph 与 OpenAI Agents SDK，由 `PIPELINE_ENGINE` 选择。
- 课程作业交付物；后端、前端、测试、Docker 与种子脚本均已实现。

## 常用命令

```bash
conda create -n sentiment-chat python=3.12 -y && conda activate sentiment-chat
pip install -e .
cp .env.example .env                # 填入 DASHSCOPE_API_KEY 与 DATABASE_URL
docker compose --env-file .env -f docker/docker-compose.yml up -d db
alembic upgrade head                # 创建业务表；引擎状态表由 SDK 幂等创建
python scripts/seed.py              # 可选：写入两段演示会话，可重复执行
python -m app.main            # Windows 必须用此方式：固定 SelectorEventLoop；uvicorn 直接启动或 --reload 会退回 Proactor
pytest                              # 全部 mock LLM，不消耗真实 API
```

> Compose 文件位于 `docker/`，项目目录因此是 `docker/`；从仓库根执行时必须带 `--env-file .env`，否则根目录 `.env` 不会被读取。

## 必须遵守的架构约束

- 依赖方向 `api → services → domain ← engines`；`domain` 不 import FastAPI / LangGraph / Agents；`services` 只依赖 `domain.ports.PipelineAdapter`。
- `engines/factory.py` 是唯一读取 `PIPELINE_ENGINE` 的地方；非法取值启动即失败，不做静默回退。
- 业务表 `conversations` / `messages` 是前端历史的唯一真相来源；两套引擎状态表同库不同表、互不读取。不要在 `ChatService` 中从业务历史重建引擎上下文（见 [ADR-0001](docs/adr/0001-引擎切换接受上下文重置.md)）。
- Alembic 负责业务表 `conversations` / `messages` 的建表与变更；两套引擎状态表由各自 SDK 幂等创建（`AsyncPostgresSaver.setup()` / `SQLAlchemySession(create_tables=True)`）；`scripts/seed.py` 只写数据，不建表。Docker 下迁移由 compose 的一次性 `migrate` 服务执行，`api` 等待其成功；`docker/entrypoint.sh` 只负责 `exec`，不要在容器入口重复跑迁移。
- 前端为无框架静态页面，由 FastAPI 挂载；不要引入前端构建链。

## 接口与数据契约

- `input`：校验、存储、回显统一使用 trim 后文本，不保留未 trim 原文。
- 历史分页 `before`：取值为消息 ID（对前端不透明），后端按 `(created_at, id)` 定位。
- `sentiment` 仅 `positive` / `negative`；解析失败按 `negative` 兜底，并记录 `parse_failed`。
- 错误统一为 `{"error": {"code": ..., "message": ...}}`，不泄露堆栈与内部路径。
- 引擎切换后业务历史保留、模型上下文重置，这是既定行为，不要"顺手修复"。

## 编码约定

- Python 3.12，全链路 `async`；禁止在请求路径中调用阻塞 IO。
- **所有数据结构用 Pydantic 定义**：请求 / 响应 schema、领域对象、配置（`pydantic-settings`）均用 Pydantic 模型；函数之间不传递裸 `dict` 表达结构化数据。
- 同一 `conversation_id` 用进程内 `asyncio.Lock` 串行化；该保证仅单实例有效，多实例需外部锁（不在本期范围）。
- 日志为 JSON 行，字段含 `conversation_id`、`engine`、`sentiment`、`latency_ms`、`status`；**不记录用户原文**，仅记 `input_length` 与 `input_sha256` 前 8 位。
- 术语用「引擎」，不要用「管线」指代；`PipelineAdapter` 专指接口。
- 不写无必要的注释；注释只解释不明显的约束与原因。
- **每个 Python 文件末尾都要有 `main` 演示**：同步代码用 `if __name__ == "__main__":`，异步代码用 `asyncio.run(main())`，演示该文件的典型用法。

## 提交规则

- **写完代码后自动提交变更**，无需等待确认：`git add` 明确文件后 `git commit`。
- 提交前确保代码可解析、相关测试通过；测试失败时不提交。
- 禁止提交 `.env` 与任何真实 Key；`.env.example` 只放占位符。
- 只提交，不自动 `push`；提交信息用简洁中文，说明改动目的。

## 测试约定

- 主接缝是 HTTP API：集成测试用真实 PostgreSQL（优先 `TEST_DATABASE_URL`，否则 testcontainers）与假 LLM，**不要 mock `PipelineAdapter`**，两套引擎的真实编排路径都要执行。
- LLM 在 `engines/shared/llm.py` 的对外调用边界注入假实现；真实模型永不在测试中调用，`DASHSCOPE_API_KEY` 可为空。
- 单元测试覆盖 parser 规整、prompts 拼装、Settings 校验（非法引擎启动失败）、trim 与长度校验，不依赖数据库。

## 安全

- 禁止硬编码任何 Key；`docs/` 脚本已改为从 `DASHSCOPE_API_KEY` 读取，不要改回明文。
- `.env` 已被 `.gitignore` 忽略；`.env.example` 只放占位符。
- `conversation_id` 不构成鉴权；不要把它当作身份凭证使用。
- Docker 默认只绑定 `127.0.0.1`；公网部署必须启用至少 16 字符的 `API_TOKEN` 或反向代理认证，静态前端不得内嵌令牌。

## 文档维护

- README 保持单文件，是交付入口；改动功能或契约时同步更新「交付清单与验收标准」。
- 文档与设计变更记入 [CHANGELOG.md](CHANGELOG.md)；难以逆转的决策写 ADR（`docs/adr/`）；规格变化更新 `docs/specs/`。
- "完成"的定义：README 验收清单 1–15 全过、`pytest` 全绿且零真实调用、两套引擎各演示一次、`grep -rn "sk-" docs/` 无命中。
