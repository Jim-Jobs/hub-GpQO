# week15 作业二 · Dockerfile 打包（基于作业一 openai-agents 版）

> 作业原文：使用 dockerfile 打包镜像，基于作业 1 的 openai-agents 版本的代码，
> 编写 dockerfile，通过 docker 或阿里云镜像服务镜像打包。
>
> 实现方式：**本地 docker 构建 + compose 编排**（作业允许二选一）。
> 作业一代码**零改动、零复制**——所有镜像的 build context 直指
> [`../作业一/openai-agents/`](../作业一/openai-agents/readme.md)，
> 本目录只放"打包层"（Dockerfile×3 / compose / nginx.conf / requirements）。

## 架构（6 容器，compose 一键编排）

```
浏览器 ──:8080──▶ frontend(nginx) ──/api 反代──▶ gateway:8001(FastAPI+Temporal client+SSE)
                                                    │ start_workflow + 订阅 Workflow Streams
                                                    ▼
                    temporal(dev server, 内嵌 SQLite, Web UI :8233→宿主 :18233)
                                                    │ task queue "emotion-oa"
                                                    ▼
                    worker(无HTTP) ──httpx──▶ sentiment:8002(进程1) / respond:8003(进程2)
                                                    │                        │
                                                    └── {label,reason} ──────┤ token 经 Workflow Streams
                                                                             ▼ 回网关 → SSE → 前端打字机
```

| 镜像 | Dockerfile | 大小 | 说明 |
|---|---|---|---|
| `emotion-oa-sentiment:0.1.0` | `Dockerfile` target=sentiment | 402MB | 进程1 情感识别（py312-slim+依赖底座） |
| `emotion-oa-respond:0.1.0` | `Dockerfile` target=respond | 402MB | 进程2 情感回应（SSE 流） |
| `emotion-oa-worker:0.1.0` | `Dockerfile` target=worker | 402MB | Temporal worker（workflow+2 activity） |
| `emotion-oa-gateway:0.1.0` | `Dockerfile` target=gateway | 402MB | 网关（4 个 Python 服务共享同一 deps 底座层） |
| `emotion-oa-frontend:0.1.0` | `Dockerfile.frontend` | 95MB | node20+pnpm build → nginx:alpine 多阶段 |
| `emotion-oa-temporal:0.1.0` | `Dockerfile.temporal` | 165MB | 官方 CLI binary + alpine（自建，见踩坑4） |

## Dockerfile 设计要点

- **单文件 5 target 共享底座**：`deps` 层装一次依赖（requirements.txt 与
  py312 conda 环境锁定版本一致），sentiment/respond/worker/gateway 四个
  target 都 `FROM deps`——依赖层在本地只存一份，四个服务镜像增量只有业务代码。
- **镜像内布局 = 仓库布局**：`/app` 即 openai-agents 套根（`contracts.py`、
  `streams.py`、`worker/`、`gateway/`…），gateway/worker 源码里的
  `parents[1]` sys.path 注入逻辑原样成立，**一行代码不用改**。
  gateway target 连整个 `worker/` 一起带上——`workflows.py` 会
  pass-through import `activities.py`。
- **前端多阶段 + 同源反代**：前端 `fetch("/api/chat")` 是相对路径，nginx
  把 `/api` 反代到 `gateway:8001`，天然同源免 CORS——gateway 的 CORS
  白名单（5173）原样保留不影响。`proxy_buffering off` 保证 SSE token
  即时下发（否则打字机退化为整段刷出）。
- **凭据不进镜像**：`API_KEY/BASE_URL/BASE_MODEL` 经 compose `env_file`
  指向项目根 `.env`（相对路径 `../../../../.env`）注入容器；容器内
  `load_dotenv()` 找不到 .env 文件属预期，`os.getenv` 直接读进程环境。
- **数据卷**：`gateway-data`（SQLiteSession 会话历史）、`temporal-data`
  （dev server 内嵌 SQLite）——`docker compose down` 不丢，`-v` 才清。

## 构建与启动

```bash
cd 作业二
docker compose build     # 构建 6 个镜像（见踩坑1-3：需先拉基础镜像/备 vendor）
docker compose up -d     # 按依赖顺序拉起：temporal → 进程1/2 → worker → 网关 → 前端
docker compose ps        # 全部 healthy 即就绪

# 入口：前端 http://localhost:8080 · 网关 :18001 · Temporal UI http://localhost:18233
docker compose logs -f worker    # 看 activity 重试等执行细节
docker compose down              # 停止（加 -v 连数据卷一起清）
```

宿主端口刻意避开作业一常驻进程（8001/8002/8003/8233）→ **容器版与作业一
本地版可同时运行对照**；容器网内仍是标准 80/8001/8002/8003/7233/8233。

手工构建单个镜像（compose 即等价封装）：

```bash
docker build -f 作业二/Dockerfile --target sentiment -t emotion-oa-sentiment ..   # context=week15/
docker build -f 作业二/Dockerfile.frontend -t emotion-oa-frontend ..
docker build -f 作业二/Dockerfile.temporal -t emotion-oa-temporal ..
```

## 验证记录

- `docker compose ps`：6 容器全绿（worker 无 HTTP 探针按设计）：

  ```
  NAME           SERVICE     STATUS
  oa-frontend    frontend    Up (healthy)
  oa-gateway     gateway     Up (healthy)
  oa-respond     respond     Up (healthy)
  oa-sentiment   sentiment   Up (healthy)
  oa-temporal    temporal    Up (healthy)
  oa-worker      worker      Up
  ```

- curl 经 nginx 反代 `POST /api/chat`（正/负向 + thread_id 复用多轮）：
  SSE 事件序列完整 `meta → sentiment → delta×18 → done`，负向识别
  `negative` + 共情排解文案，多轮上下文生效（第一轮"代码跑通"被第二轮回应引用）。

- 截图索引（screenshots/，headless Chrome CDP 截取，见踩坑8）：

  | 编号 | 内容 |
  |---|---|
  | 01-前端对话-正向鼓励.png | 正向输入 → 开心徽标 + 鼓励式回应 |
  | 02-前端对话-负向排解.png | 负向输入 → 低落徽标 + 共情排解（侧栏双会话） |
  | 03-TemporalUI工作流.png | Web UI :18233 中 5 条 workflow 全 Completed（emotion-{thread}-t{轮次} 可追溯） |
  | 04-容器故障降级.png | `docker compose stop respond` → error 红条"进程2 调用失败…connection refused"，不出 5xx；重启后自动恢复 healthy |
  | 05-容器界面全绿.png | Docker Desktop Containers 视图：compose 组 emotion-oa-docker 六容器全部 Running（5 个 healthy） |
  | 06-容器详情-网关日志.png | oa-gateway 详情页 Logs 标签：`Temporal client 已连接 temporal:7233`、workflow 启动/完成日志（容器化编排可观测） |

## 踩坑记录

1. **Docker Hub 直连不通（无代理时）**：本机出口直连 registry-1.docker.io
   超时；公共加速器（daocloud/1ms/1panel）时好时坏。最终解法：启用本机
   v2ray（http 入站 `127.0.0.1:1080`），写入 Docker Desktop 代理
   （settings-store.json：`ProxyHttpMode=manual` +
   `OverrideProxyHttp/Https=http://127.0.0.1:1080`，重启 Docker Desktop
   生效；daemon 会把它映射为 `http.docker.internal:3128` 转发进 VM），
   `docker pull` 全部恢复。
2. **BuildKit 不走 daemon 代理**：`docker pull` 通了，但 `docker compose
   build` 解析基础镜像（auth.docker.io）仍直连超时——BuildKit 的 resolver
   不读 daemon 代理配置。对策：**基础镜像先 `docker pull` 预拉**（python:
   3.12-slim / node:20-alpine / nginx:alpine / alpine:3.21），build 默认
   `--pull=false` 直接用本地。同时删掉 Dockerfile 首行
   `# syntax=docker/dockerfile:1`——该指令会额外拉 docker/dockerfile:1
   frontend 镜像（同样不走代理），而所用指令全是基础语法，无需 frontend。
3. **容器内够不到宿主回环代理**：v2ray 只听 127.0.0.1，容器/构建容器内
   无法经它出网。对策三件套：temporal binary 由**宿主机**经代理下载进
   `vendor/`（build 时 COPY，离线可复现；已 .gitignore，重新获取：

   ```bash
   cd 作业二/vendor
   for a in amd64 arm64; do curl -L -x http://127.0.0.1:1080 -O \
     "https://github.com/temporalio/cli/releases/download/v1.10.1/temporal_cli_1.10.1_linux_${a}.tar.gz"; done
   ```

   ）；pip 走清华源；corepack/pnpm 走 npmmirror（`COREPACK_NPM_REGISTRY` +
   `pnpm config set registry`）。
4. **`temporalio/temporal-cli` 镜像不存在**：官方未发布 CLI dev server
   容器镜像（pull 报 repository does not exist）。社区标准做法是拿官方
   release binary 自建：多阶段 `alpine` 解压 `temporal_cli_1.10.1_linux_
   {amd64,arm64}.tar.gz`（`TARGETARCH` 自动选择），最终镜像只含单个静态
   binary。与作业一 brew temporal 1.10.1 dev server 完全等价。
5. **temporal CLI 默认只听 127.0.0.1**：容器间互访必须
   `--ip 0.0.0.0`（写进 Dockerfile CMD），否则 worker/网关连接被拒。
6. **nginx 反代 SSE 必须关缓冲**：`proxy_buffering off; proxy_cache off;`
   否则 chunked 响应被攒到缓冲区，打字机效果退化成"憋几秒整段刷出"。
7. **截图自动化**：Chrome `--headless=new --screenshot` 对含挂起网络
   （SSE 长连接）的页面会在渲染完成前就出图（`--virtual-time-budget`/
   `--timeout` 都拦不住，连拍两张空白页）；桌面 AppleScript 键盘方案又
   依赖坐标点击且打扰在用的桌面。最终方案：headless Chrome 常驻 +
   `--remote-debugging-port`，CDP WebSocket 控制——`Page.navigate` 后
   轮询 `Runtime.evaluate`（user 气泡出现且 assistant 文本长度连续两次
   采样不变 = 流式结束）再 `Page.captureScreenshot`（@2x 高清），
   全程无人值守。前端自带的 `#q=<输入>` URL hash 自动发送（作业一演示
   辅助）在 headless 下工作正常，中文 hash 需 percent-encode。
