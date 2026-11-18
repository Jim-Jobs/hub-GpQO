# 情感识别与回应：双框架演示项目

> 本文说明项目目标、运行方法和核心流程。AgentLoop 数据采集边界仍待单独确认，本项目暂不接入远端内容采集。

## 目标与已确认范围

- 分别使用 LangGraph 和 OpenAI Agents 实现同一题目，作为两个独立项目。
- 每个项目提供 Node.js 聊天网页；用户每次输入按单轮请求独立处理，不使用多轮对话记忆。输入框支持 Enter 发送、Shift+Enter 换行。
- API 后端接受前端输入后返回 `request_id`；前端通过 SSE 显示排队、情感识别、生成回复、完成或失败状态。后端通过 Redis Streams 与两个独立工作进程通信。
- 情感类别为正向、负向、中性；页面展示识别类别和对应回复。
- 使用 Dockerfile 和 Docker Compose 启动。
- 两个项目均通过 `.env` 配置 DeepSeek 密钥/地址/模型、Redis 地址和主机端口、API 主机/端口及前端端口。LangGraph 项目使用 LangChain 接口连接 DeepSeek；OpenAI Agents 项目使用 OpenAI SDK 的兼容接口连接 DeepSeek。
- 不接入工具；第一版工具选择评测记为“不适用”。
- 部署和可观测性平台选择阿里云。这里的 AgentLoop 指阿里云 Agent 观测与优化平台，不是代码模块；目前是否以及如何接入仍属于可观测性阶段的设计内容。

## 开发流程与评测

1. **需求意图澄清**：明确目标用户、使用场景、输入输出、范围和非目标。
2. **设计测试用例**：覆盖典型情绪、混合或模糊情绪、异常输入、格式错误、模型失败和超时。
3. **Agent 与 Workflow 拆分**：为每个步骤明确职责、输入、输出、提示词和进程间通信。
4. **定义评测指标**：分别评估情感识别、回复质量、提示词格式约束和端到端流程；工具选择因第一版无工具而不适用。
5. **部署**：确定阿里云上的具体运行方式、配置和发布步骤。
6. **可观测性**：确定 AgentLoop 接入方式、采集字段、脱敏、访问范围及数据保留策略。

- 准备 **30 条人工标注的中文样例**，用于情感识别评测。
- 回复质量采用人工打分；具体评分维度、量表和通过阈值尚待定义。
- 情感分类的统计指标、输出格式有效率和端到端性能指标尚待定义，不在本记录中预设通过阈值。
- 单独建立 `project--04/test/` 文件夹存放测试用例和评测记录，两套实现共用同一组样例，结果分别记录在 `test/results/langgraph/` 和 `test/results/openai-agents/`；字段说明见[test 目录说明](test/README.md)。

## 待进一步澄清：可观测数据边界

AgentLoop 的 Trace 可能包含用户输入、模型调用输入输出、执行步骤、Token 和耗时等数据；部分采集配置还支持常见敏感信息脱敏。此前提出的以下建议尚未得到确认：

- 课程演示只使用合成文本，不输入真实用户个人经历或联系方式。
- 记录阶段状态、耗时、模型名、Token、输出格式校验结果和最终分类/回复；不采集内部推理文本。
- 开启采集端可用的敏感信息脱敏，并避免将真实凭据写入输入或日志。
- 将 Trace 保留 7 天，课程结束后清理评测数据和 Trace。
- AgentLoop 和评测数据的可见范围尚未确定。

以上为尚待确认的建议；本项目当前不向 AgentLoop 发送用户文本或模型输入输出。

## 运行方法

需预先安装 Docker Desktop（含 Docker Compose）。两个实现使用不同端口，但各自有独立的 Redis 容器和数据卷。首次启动会构建 Python 后端和 Node.js 前端镜像并下载基础镜像及依赖。

先进入要运行的子项目，复制环境变量模板并填写 DeepSeek 配置：

```powershell
cd .\langgraph
Copy-Item .env.example .env
# 编辑 .env，填写 DEEPSEEK_API_KEY、DEEPSEEK_BASE_URL、DEEPSEEK_MODEL
docker compose up --build
```

LangGraph 页面：<http://localhost:8080>；API 文档：<http://localhost:8000/docs>。停止：`docker compose down`。

另一个终端进入 `project--04/openai-agents`，执行相同的复制、配置和启动命令。OpenAI Agents 页面：<http://localhost:8081>；API 文档：<http://localhost:8001/docs>。两个项目不要在同一目录运行 Compose 命令。

`.env` 是本地密钥文件，不要提交到版本控制。

若想在 Windows 或 WSL2 本机运行 API 和 worker，只用 Docker 启动 Redis，可按任一子项目 README 中的“本机运行应用，只用 Docker 启动 Redis”步骤操作。此模式只启动 `redis` 服务，不构建项目 API/前端镜像；前端由本机 Node.js 运行。

## 测试与评测

在 `project--04` 根目录运行共享数据和评测器测试：

```powershell
python -m unittest discover -s test -p "test_*.py" -v
```

分别在 `langgraph` 或 `openai-agents` 子项目目录运行实现测试：

```powershell
python -m unittest discover -s tests -v
```

启动某一实现的 Redis、API 和两个 worker 后，在 `project--04` 根目录批量运行 30 条样例。例如 OpenAI Agents：

```powershell
python -m test.run_cases --api-url http://localhost:8001 --framework openai-agents --results .\test\results\openai-agents\run.jsonl
```

LangGraph 使用 `--api-url http://localhost:8000 --framework langgraph --results .\test\results\langgraph\run.jsonl`。批量脚本会调用完整流程，每条样例触发情感识别和回复两次模型请求，会消耗 DeepSeek API 额度。脚本结束时会打印生成的 JSONL 路径。评测记录汇总命令示例：

```powershell
python -m test.evaluate --results .\test\results\langgraph\run.jsonl
```

OpenAI Agents 的汇总命令为 `python -m test.evaluate --results .\test\results\openai-agents\run.jsonl`。每条结果字段见 [评测数据模型](test/schemas.py)；指标包含分类 Accuracy、Macro-F1、格式有效率、人工评分均值、延迟和失败数。API 当前不返回 Token 用量，所以 Token 指标暂不可用。

## 项目目录

- [LangGraph](langgraph/README.md)：LangGraph 编排流程，LangChain `ChatOpenAI` 接入 DeepSeek。
- [OpenAI Agents](openai-agents/README.md)：OpenAI Agents SDK，使用 OpenAI 兼容 Chat Completions 接入 DeepSeek。

实现步骤和检查点见[实施计划](docs/superpowers/plans/2026-11-17-emotion-agent-implementation.md)。

## 运行流程概览

```text
第 1 步：聊天网页提交文本，API 后端生成请求编号并写入 Redis 请求流。
第 2 步：情感识别进程取出请求，识别为正向、负向或中性并写入结果流。
第 3 步：情绪回应进程取出识别结果，按情感类别生成回复并写入请求事件流。
第 4 步：API 后端将对应事件转为 SSE，聊天网页展示阶段状态和最终结果。
```

识别结果为正向、负向或中性。正向输入得到鼓励，负向输入得到共情和建议，中性输入得到自然回应。两个子项目 README 记录实现结构和各自运行细节。
