# 本地运行（简版）

已经装好 Docker、MinerU、模型，`.env` 里也有 `DASHSCOPE_API_KEY`。
现在只需要 **两条命令**：

## 1. 启动

```powershell
cd C:\Users\Cooper\Documents\deepseek-harness\week14\multi-modal-rag
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

脚本会自动完成：

1. 启动 Docker Desktop（如果没开）；
2. 启动 Kafka；
3. 启动 MinerU；
4. 启动 Milvus Lite server；
5. 检查/创建 Milvus 集合；
6. 启动 API 和 Worker；
7. 等待 `/health` 通过。

看到下面这行即成功：

```text
System ready: http://127.0.0.1:8000
```

然后打开 <http://127.0.0.1:8000/>，直接上传 PDF、提问、加图片。

## 2. 停止

```powershell
cd C:\Users\Cooper\Documents\deepseek-harness\week14\multi-modal-rag
powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1
```

默认会停止 API、Worker、Milvus Lite、Kafka、MinerU。

只停应用、保留 Kafka/MinerU：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1 -KeepDeps
```

## 日志

```text
data\.local\logs\api.out / api.err
data\.local\logs\worker.out / worker.err
data\.local\logs\lite.out / lite.err
data\.local\logs\kafka.out / kafka.err
data\.local\logs\mineru.out / mineru.err
data\.local\logs\schema.out / schema.err
```

启动时会打印 `[1/7]` 到 `[7/7]`；如果卡住，看最后停在哪一步，再查对应的 `.err`。

## 常见问题

| 现象 | 处理 |
|---|---|
| 端口 8000 被占用 | 先运行 `stop-local.ps1`，或关闭占用 8000 的程序 |
| 启动超时 | 看 `data\.local\logs\` 下对应的 `.err`；按最后一步 `[x/7]` 定位 |
| `.env` 没配置 Key | 在项目根目录 `.env` 中写 `DASHSCOPE_API_KEY=...` |
| 想手动调试 | 参考 [验收操作单](acceptance-runbook.md)，里面是六终端手动流程 |

## 说明

- 数据在 `data\local\`，不会因停止脚本丢失；
- 第一次启动会初始化 Milvus 集合，之后直接复用；
- 不要用单个 `.db` 文件同时给 API 和 Worker 打开，`start-local.ps1` 已统一用 Milvus Lite server 模式；
- **数据与隐私**：MinerU 默认 `tier=flash` 会调用 mineru.net；问答片段/图片会发给 DashScope。敏感文档请改用本地 MinerU/自建 LLM，或先脱敏。
- 更完整的故障排查、验收步骤见 [验收操作单](acceptance-runbook.md)。
