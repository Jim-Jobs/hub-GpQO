# 冗余文件与清理审计

| 项目 | 内容 |
|---|---|
| 审计日期 | 2026-11-10 |
| 审计范围 | 源码、脚本、文档、部署文件、`data/` 运行产物 |
| 结论 | **源码/文档几乎没有冗余；可安全清理约 21MB 过期测试产物，模型与验收证据必须保留** |

## 1. 总体结论

- `app/`、`docs/`、`scripts/`、`deploy/` 中没有发现可删的重复源文件；
- `requirements.txt` 与 `requirements-lite.txt`、`compose.yaml` 与 `compose.local.yaml` 是不同用途，不冗余；
- `reference/` 是只读参考，不能删；
- 真正的冗余集中在：
  1. `__pycache__` / `tests/.runtime` 等生成缓存；
  2. 已被后续验收替代的 mock/调试运行目录；
  3. `data/` 根目录的临时日志、空数据库、失败尝试产物。

> 不要为了“清理”删除 `data/models`、`data/uploads`、`data/local`、S4/LLM 验收证据和 `.env`。

## 2. 建议删除：生成缓存

| 路径 | 说明 |
|---|---|
| `app/**/__pycache__` | Python 字节码缓存，可自动重建 |
| `scripts/__pycache__`、`tests/**/__pycache__` | 同上 |
| `tests/.runtime` | 测试临时 DB/日志；测试会重建 |

这些文件已在 `.gitignore` 中，删除不影响代码或验收。

## 3. 建议删除：过期运行产物

以下目录都已被后续更完整的验收替代，保留价值低：

| 路径 | 约大小 | 原因 |
|---|---:|---|
| `data/smoke-20261110-140758` | 1.10MB | 第一次 mock 冒烟失败现场 |
| `data/smoke-20261110-141054` | 1.11MB | 已被后续 S4/LLM 验收替代 |
| `data/flow-happy-20261110-141514` | 4.00MB | 早期双 PDF mock 成功路径 |
| `data/llm-e2e-20261110-153557` | 3.13MB | Milvus Lite 锁冲突失败现场 |
| `data/llm-server-e2e-20261110-153802` | 3.11MB | 模型名 `qwen-plus` 404 失败现场 |
| `data/mineru-2file-20261110-144234` | 3.11MB | 2 页样本，已被 3 份完整 PDF 替代 |
| `data/mineru-e2e-20261110-144117` | 0.59MB | 单文件 MinerU 探针 |
| `data/real-e2e-20261110-150313` | 2.98MB | 早期真实链路，已被 `s4-accept`/`llm-final` 替代 |
| `data/trace-check-141822` | 0.99MB | Worker trace 单点验证 |
| `data/milvus-lite-server-20261110-153802` | 0.14MB | 一次失败启动的 Lite 数据 |
| `data/milvus-lite-real.db` | 0.28MB | 早期检索探针 DB |
| `data/hf-cache` | 0.01MB | 空缓存目录，模型已落在 `data/models` |
| `data/milvus-lite.db` | 0 | 空/废弃 Lite 文件 |
| `data/milvus-lite-llm-final` | 0 | 空目录 |

根目录临时日志（均可删）：

```text
data/ollama-text.out
data/ollama-text.err
data/ollama-text2.out
data/ollama-text2.err
data/local-api.out
data/local-api.err
data/local-lite.out
data/local-lite.err
data/lite-final.out
data/lite-final.err
data/lite-query.out
data/lite-query.err
data/probe.png
```

## 4. 必须保留

| 路径 | 约大小 | 原因 |
|---|---:|---|
| `data/models/` | 1.62GB | BGE + Chinese-CLIP 模型，运行必需 |
| `data/uploads/` | 3.79MB | 用户上传的原始 PDF |
| `data/local/` | 55.57MB | 当前本地系统数据库与日志 |
| `data/s4-accept-20261110-154913/` | 20.42MB | S4 三份完整 PDF 验收证据 |
| `data/llm-final-20261110-154118/` | 2.94MB | 真实文本/图文 LLM 14/14 证据 |
| `data/flow-failure-20261110-141638/` | 0.24MB | FAILED → 重解析证据 |
| `data/milvus-lite-server/` | 2.65MB | 当前 Lite server 数据目录 |
| `data/milvus-lite-s4-.../`、`milvus-lite-final-.../` | 2.75MB | 验收向量库快照 |
| `data/s4-upload-result.json` | 小 | 3 PDF 四态结果 |
| `data/s4-replay-result.json` | 小 | 重复消息回放结果 |
| `data/final-answer.json` | 小 | 真实 LLM 回答证据 |
| `data/acceptance/full3.pdf` | 3.78MB | S4 的第三份完整 PDF，复现用 |
| `data/mineru-probe/quick.pdf`、`quick2.pdf` | 少量 | LLM/解析小样本，复现用 |
| `.env` | 小 | 本地密钥，绝不入库 |
| `reference/` | — | 只读参考实现 |

`data/db/`（旧根目录 DB）与 `data/processed/`、`data/questions/`、`data/logs/` 当前基本为空/非当前运行数据，可保留；`data/db` 是否删除取决于你是否还需要早期手工运行数据。

## 5. 源码/文档是否存在冗余

结论：**没有发现应删除的源文件或文档**。

| 文件 | 判断 |
|---|---|
| `requirements.txt` / `requirements-lite.txt` | 核心依赖 vs Lite 可选，保留 |
| `deploy/compose.yaml` / `deploy/compose.local.yaml` | Docker Milvus 目标栈 vs 本地 Kafka，保留 |
| `docs/test-report.md` / `acceptance-record.md` / `acceptance-runbook.md` | 测试报告 / 验收结论 / 操作步骤，职责不同 |
| `docs/project-review-2026-11-10.md` | 全局审计记录，P0 处理状态可追溯，保留 |
| `scripts/start-local.ps1` / `stop-local.ps1` / `local-env.ps1` | 启动、停止、环境变量，保留 |
| `scripts/*.py` | 均为评测/契约/Key/DLQ/冒烟工具，保留 |
| `reference/` | 只读参考，不删 |

如果只想交付“最小可运行代码”，可以把 `reference/`、`docs/design/`、`docs/project-review` 移出交付包，但不建议删除。

## 6. 清理命令（建议先停系统）

```powershell
$ROOT = "C:\Users\Cooper\Documents\deepseek-harness\week14\multi-modal-rag"
Set-Location $ROOT

# 1) 停止当前系统
powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1

# 2) 清 Python 缓存
Get-ChildItem app,scripts,tests -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force
Remove-Item tests\.runtime -Recurse -Force -ErrorAction SilentlyContinue

# 3) 清过期运行目录
$old = @(
  "data\smoke-20261110-140758",
  "data\smoke-20261110-141054",
  "data\flow-happy-20261110-141514",
  "data\llm-e2e-20261110-153557",
  "data\llm-server-e2e-20261110-153802",
  "data\mineru-2file-20261110-144234",
  "data\mineru-e2e-20261110-144117",
  "data\real-e2e-20261110-150313",
  "data\trace-check-141822",
  "data\milvus-lite-server-20261110-153802",
  "data\milvus-lite-llm-final",
  "data\hf-cache"
)
foreach ($p in $old) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue }
Remove-Item data\milvus-lite-real.db, data\milvus-lite.db -Force -ErrorAction SilentlyContinue

# 4) 根目录临时日志
Remove-Item data\ollama-text*.out, data\ollama-text*.err,
  data\local-api.*, data\local-lite.*, data\lite-final.*,
  data\lite-query.*, data\probe.png -Force -ErrorAction SilentlyContinue
```

预计释放：**约 21～25MB**（模型 1.62GB 与用户数据不动）。

## 7. 建议

- 删除前先确认不需要复现早期失败现场；S4/LLM 证据目录不要动。
- 若准备长期维护，建议保留：
  - `data/s4-accept-20261110-154913/`
  - `data/llm-final-20261110-154118/`
  - `data/flow-failure-20261110-141638/`
  - `data/final-answer.json`、`data/s4-*.json`
- 后续可把“清理脚本”固化成 `scripts/cleanup.py`，但先人工确认一次再删更安全。

## 8. 执行记录（2026-11-10）

已按本报告第 3 节删除 **11 个过期运行目录**，共释放 **20.27MB**：

```text
smoke-20261110-140758
smoke-20261110-141054
flow-happy-20261110-141514
llm-e2e-20261110-153557
llm-server-e2e-20261110-153802
mineru-2file-20261110-144234
mineru-e2e-20261110-144117
real-e2e-20261110-150313
trace-check-141822
milvus-lite-server-20261110-153802
milvus-lite-llm-final
```

未删除：`data/models`、`data/uploads`、`data/local`、S4/LLM/FAILED 证据、`data/milvus-lite-server`、`data/acceptance`、`data/mineru-probe`、`.env` 和 `reference/`。执行清理后重新运行 `start-local.ps1`，7 步启动通过，`/health=ok`。
