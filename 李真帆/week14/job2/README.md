# 多模态 RAG 问答系统

上传 PDF 构建 RAG 知识库（MineRU 解析 + Kafka 异步任务 + SSE 状态实时推送），支持图文提问、图文混排回答。

详细设计见 [docs/详细设计文档.md](docs/详细设计文档.md)。

## 项目结构

```
job2/
├─ docs/详细设计文档.md   # 设计文档
├─ backend/              # FastAPI + Kafka + Redis + SQLite + mineru-kit
├─ frontend/             # Vue3 + Vite + TS + Ant Design Vue
└─ docker-compose.yml    # Kafka + Redis（可选，环境自备可不用）
```

## 快速开始

### 0. 基础设施（Kafka + Redis）

环境自备，或使用附带的 compose：

```bash
docker compose up -d
```

embedding 使用 vLLM 自部署（默认按 bge-m3 设计）：

```bash
vllm serve BAAI/bge-m3 --port 8001
# 老版本 vLLM 需加 --task embed
```

### 1. 后端（两个进程）

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
cp .env.example .env    # 填 DEEPSEEK_API_KEY，确认 EMBEDDING_* 配置

# 进程1：API 服务 (:8000)
.venv/Scripts/uvicorn app.main:app --port 8000

# 进程2：解析 Worker（另开终端）
.venv/Scripts/python -m app.worker.consumer
```

> MineRU 命令默认 `mineru-kit`，若不在 PATH，请在 `.env` 的 `MINERU_BIN` 填完整路径。

### 2. 前端

```bash
cd frontend
pnpm install
pnpm dev        # http://localhost:5173
```

## 使用说明

- **知识库页**：拖拽/多选上传 PDF → 待解析 → 解析中 → 解析完成（SSE 实时刷新，失败可点"重新解析"）。
- **对话页**：文本或文本+图片提问。后端先相似度检索 top3 并判断相关性：
  相关 → 结合知识库回答（引用文档图片，图文混排）；不相关 → 自由回答。

## 常见问题

| 问题 | 处理 |
|---|---|
| 检索报"维度不一致" | 切换过 embedding 模型，运行 `python scripts/rebuild_vectors.py` 重建向量 |
| 不想部署 vLLM | `.env` 设 `EMBEDDING_PROVIDER=local`（需 `pip install fastembed`），并重建向量 |
| mineru 解析失败 | 看文档列表"解析失败"悬停的错误信息；确认 `mineru-kit` 可独立运行 |
| SSE 不生效 | 检查 Redis 是否可用；SSE 仅影响实时刷新，解析主流程不受影响 |

## 附：compshare GPU Pod 部署记录（2026-11-12 实测）

服务布局（pod 内）：Redis `:6379`、Kafka `:9092`、embedding vLLM(bge-m3) `:8001`、chat vLLM(Qwen3.5-0.8B) `:8002`、本项目 API+前端 `:8080`（对外 `http://8080-cpod-*.pod.compshare.cn`）。

实测踩坑（pod 环境特性，非项目缺陷）：

1. **Kafka 4.4**：自带 `broker.properties` 是纯 broker 角色（无 controller 监听），单机需自写 combined 配置（已存 pod 上 `config/rag.properties`），且 format 必须加 `--standalone`（动态 quorum）。
2. **vLLM 0.25**：`--task embed` 参数已移除（自动识别）；pod 无 nvcc 时 flashinfer JIT 会因缺 ninja 失败——已装 ninja 并设 `VLLM_USE_FLASHINFER_SAMPLER=0` 走原生采样器。
3. **模型下载**：`huggingface-cli` 已是废弃空壳（新版用 `hf`），且 hf-mirror 经代理下载大文件会断流卡死；bge-m3 改用 **ModelScope**（`modelscope.snapshot_download`）下载，国内直连稳定。
4. **环境复用**：后端依赖直接装进了 mineru 所在的 conda `py312` 环境（`/usr/local/miniconda3/envs/py312`），未单独建 venv。
5. pod 上 `bash /workspace/rag/start-all.sh` 可幂加拉起全部六个服务（pod 重启后执行一次即可）。
6. **公网 WAF 限制**：compshare 网关强制 https 且对 SSE 长连接做分批缓冲——SSE 可连但事件有延迟。前端已做双保险（SSE + 5~15 秒自动轮询兜底），状态最终一致；聊天流式可能表现为非逐字输出，内容完整。本地/内网部署无此现象。
