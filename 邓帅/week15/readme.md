# week15

作业1: 分别使用 langgraph 和 openai-agents实现如下的逻辑：
	进程1，接受请求进行情感识别
	进程2，得到识别结果后，正向则鼓励，负面则排解。
	后端进程：接受前端输入，串联进程1和进程。
	前端进程：用户输入。

作业2: 使用dockerfile打包镜像，基于作业1的openai-agents版本的代码，编写dockerfile，通过docker或 阿里云镜像服务镜像打包。

---

## 作业一实现索引：情感识别→回应 多进程 Pipeline（双框架复刻）

同一套进程间 HTTP 契约、两种框架各建一套完整实现，可同时运行并排对比：

| 实现 | 编排哲学 | 入口 |
|---|---|---|
| **openai-agents 套** | durable orchestration：网关经 Temporal workflow 持久化编排（analyze→respond activity、Workflow Streams 流式、自动重试、Web UI :8233 可视化） | [作业一/openai-agents/readme.md](作业一/openai-agents/readme.md) |
| **langgraph 套** | in-memory graph orchestration：网关本身是 StateGraph 直连（analyze 节点→条件边→respond 节点、`get_stream_writer()` custom stream、AsyncSqliteSaver checkpointer），零 Temporal 依赖 | [作业一/langgraph/readme.md](作业一/langgraph/readme.md) |

共同点：4 进程拓扑（前端/网关/进程1 情感识别/进程2 情感回应）、端到端 token 级
流式级联（进程2 SSE → 网关转发 → 前端打字机）、多轮会话由网关持有（下游无状态）、
下游失败降级 SSE `error` 事件；进程1/2 契约统一可交叉替换（已验证）。

技术设计文档：[作业一/design.md](作业一/design.md)。

## 作业二

见 [作业二/](作业二/readme.md)：**dockerfile 打包 openai-agents 版**。
单文件多 target Dockerfile（4 个 Python 服务共享依赖底座）+ 前端多阶段镜像
（pnpm build → nginx `/api` 反代，SSE 不缓冲）+ 自建 Temporal dev server
镜像（官方 CLI binary + alpine）+ compose 一键编排 6 容器（宿主端口
8080/18001/18233，与作业一本地版并存）；作业一代码零改动零复制，
curl SSE 序列 + 4 张截图（正/负向对话、Temporal UI、容器故障降级）验证通过。
