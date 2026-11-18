# 变更日志

## 2026-11-16

- 修复本地启动卡在 `Waiting for application startup`：`.env.example` 的 `DATABASE_URL` 密码改为与 `DB_PASSWORD` 一致并改用 `127.0.0.1`（避免 `localhost` 的 IPv6 回落等待）；README 与 AGENTS 的本地命令改为 `python -m app.main`，规避 Windows ProactorEventLoop 与 `--reload` 子进程回退问题
- 可维护性：`pyproject.toml` 运行时与测试直接依赖改为精确版本，Docker 基础镜像固定到 `python:3.12-slim` digest，避免构建漂移
- 可维护性：迁移从 API 入口拆为 Compose 一次性 `migrate` 服务，`api` 通过 `service_completed_successfully` 等待迁移成功，避免多实例并发执行；`entrypoint.sh` 只保留 `exec`
- 文档同步：README、规格与 AGENTS 更新迁移流程与依赖锁定说明
- 性能优化：`ConversationLocks` 改用 `WeakValueDictionary`，会话请求结束后锁可被回收；新增 3 项锁单元测试，测试总数 71
- 性能优化：Docker 容器内 Uvicorn 改用 uvloop（镜像内 0.23.0），本地 Windows 仍保留 `--loop none` 的 SelectorEventLoop 兼容策略；README 同步说明
- 安全加固：Docker Compose 将 API 与 PostgreSQL 端口改为仅绑定 `127.0.0.1`；新增可选 `API_TOKEN`（至少 16 字符）与 `X-API-Token` 校验，覆盖 `/chat` 与历史接口，`/health` 保持公开；新增 4 项双引擎参数化集成测试与 2 项 Settings 单元测试，测试总数 68
- 文档同步：README、规格与 AGENTS 更新访问控制边界；`.env.example` 增加 `API_TOKEN` 占位符
- README 补充 Docker Compose 停止与清理命令，明确 `down` 保留数据卷、`down -v` 会删除会话历史
- 完成 Docker 交付：`docker/Dockerfile` 使用非 root 用户并内置 `/health` 健康检查；`entrypoint.sh` 在启动 API 前执行 `alembic upgrade head`；`docker-compose.yml` 编排 api + postgres，并校验必填的 `DASHSCOPE_API_KEY` / `DB_PASSWORD`
- 新增 `scripts/seed.py`：Pydantic 种子模型、两段固定会话、`ON CONFLICT DO NOTHING` 幂等写入；缺表时提示先迁移，不自动建表；容器内连续执行两次仍为 2 会话 / 8 消息，新增集成测试后总数 58
- 文档同步：README 验收新增「种子数据幂等」，Docker 命令统一使用 `--env-file .env`，补充 `docker/entrypoint.sh` 与 `.dockerignore`；AGENTS 与规格完成定义更新为 1–15
- 实现静态前端 `frontend/`：本地会话 ID、消息气泡与情感徽章、刷新历史恢复、新对话、快捷输入与错误提示；FastAPI 在 `/` 同源挂载，新增静态资源集成测试，测试总数 57
- 文档同步：README 验收清单新增第 14 项「前端可用」，状态更新为前后端已实现；AGENTS 与规格的完成定义同步为 1–14
- 修复 Windows 下 `python -m app.main` 启动失败：uvicorn 0.36+ 在非 reload 模式显式选择 ProactorEventLoop，改为 `loop="none"` 沿用 Selector 策略；README 补充直接启动 uvicorn 的 `--loop none` 说明
- 实现后端 `app/`：配置与领域层、业务表 + Alembic 迁移、两套编排引擎、聊天 / 历史服务、FastAPI 装配与统一错误体；53 项单元 + 集成测试通过
- 集成测试适配 `openai 3.x` 的 `httpx2` mock；Windows 下切换 SelectorEventLoop 以支持 psycopg 异步模式
- 文档同步：业务表由 Alembic 创建，LangGraph / Agents 状态表由 SDK 幂等创建；补充 `INVALID_CURSOR` 与 `VALIDATION_ERROR`
- 新增 `tests/`：单元测试（Settings、枚举、parser、prompts、schemas）与集成测试（HTTP 契约、双引擎参数化、游标分页、错误映射）；历史消息补充 `id` 字段以支撑游标
- AGENTS.md 新增编码规则：Pydantic 数据结构、文件末尾 `main` 演示、代码完成后自动提交
- 新增 `.env.example`：补全大模型加载配置（端点、模型名、超时、输入上限）
- 新增 `AGENTS.md`：编码代理协作约定（命令、架构约束、接口契约、测试与文档维护）
- README 瘦身：合并重复的并发 / 日志 / 密钥说明，压缩课程示例索引，582 → 463 行（保留逐文件目录结构）
- 新增规格说明书 `docs/specs/2026-11-16-sentiment-chat.md`
- 修订文档：新增「交付清单与验收标准」；`input` 字段统一为 trim 后文本；`before` 游标改为消息 ID；更新记录迁至本文件
- `docs/` 下 6 个示例脚本移除硬编码 Key，改为从 `DASHSCOPE_API_KEY` 环境变量读取
- 重写文档：修复 Markdown 渲染（未闭合代码块、缺失标题、伪表格）；澄清「日志不记原文 / 业务库存储原文」；补充切换引擎导致上下文丢失的说明；明确并发锁的实例边界；统一环境变量必填与默认值语义；补 API 字段约束、错误体与 404 语义；目录结构与接口对齐；新增前置条件、启动验证、排障表、部署运维与 `docs/` 示例索引

## 2026-11-15

- 初版设计规格
