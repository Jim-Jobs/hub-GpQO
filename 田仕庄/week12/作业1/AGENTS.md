# AGENTS.md

供 AI 编码代理使用的项目指南：环境、命令、约束与不可破坏的约定。

## 项目概述

楼道消防风险识别系统：FastAPI 后端 + 单页前端，调用 **deepseek-flash** 多模态模型对楼道照片做五级风险分类（高风险 / 中风险 / 低风险 / 无风险 / 非楼道）。

## 环境

- **标准运行环境：conda 环境 `py312`（Python 3.12.13）**，解释器 `C:\Users\Cooper\miniconda3\envs\py312\python.exe`
- `conda` 不在 Git Bash 的 PATH 中：该 shell 下请直接用解释器绝对路径；cmd / PowerShell（Anaconda Prompt）里可 `conda activate py312`
- 工作区 `.venv` 为可选方案，**非默认**；不要假设它存在
- 依赖：`backend\requirements.txt`，已含运行与测试全部依赖（pytest / httpx / Pillow 均在列，测试无需另装）
- 配置：根目录 `.env`（首次从 `.env.example` 复制）：`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `MODEL_NAME`

## 常用命令

> 以下命令工作目录为项目根目录；`python` 指 py312 环境的解释器（未激活时用上述绝对路径替换）。

```bash
# 启动服务（前端托管在 /，API 在 /api/v1/）
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

# 单元 + 接口测试（27 用例，mock 模型层，不消耗 API 额度）
python -m pytest backend\tests\test_classifier.py backend\tests\test_api.py -v

# 准确率验收（真实调用模型，需有效 Key 与账户余额）
python backend\tests\accuracy_test.py

# HTTP 模式：经完整链路验证（需先启动服务）
python backend\tests\accuracy_test.py --url http://127.0.0.1:8000
```

## 当前验收基线（2026-10-28）

- 单元 + 接口：**27/27 通过**
- 准确率（HTTP 模式，39 样本）：**37/39 = 94.9%**，`logs/accuracy.log` 有完整记录
- 分类别：高风险 12/12、中风险 8/9、低风险 2/3、无风险 6/6、非楼道 9/9
- 单张耗时 1.1–7.3s（中位 2.7s，均值 3.1s）
- 改动后若无明显理由，上述数字不应变差；变差需在提交说明中解释

## 代码结构

| 文件 | 职责 |
|---|---|
| `backend/app/main.py` | FastAPI 入口、路由、前端静态托管 |
| `backend/app/services/vision.py` | 模型调用（OpenAI 兼容 SDK，含 1 次重试） |
| `backend/app/classifier.py` | JSON 解析、类别别名归一化、关键词兜底 |
| `backend/app/prompt.py` | 消防专家 prompt（严格 JSON 输出契约） |
| `backend/app/logger.py` | loguru 按天轮转日志 |
| `frontend/index.html` | 拍照页（无框架，原生 JS） |
| `backend/tests/accuracy_test.py` | 逐样本验收 + 混淆矩阵，写 `logs/accuracy.log` |

## 硬性约定（修改时不得破坏）

1. **五类标签固定**：`高风险|中风险|低风险|无风险|非楼道`，解析层输出不得超出此集合。
2. **思考型模型**：deepseek-flash 消耗 reasoning tokens，`max_tokens` 不得小于 4096，否则 `content` 为空。
3. **模型输出契约**：prompt 要求严格 JSON（`category/level/reason/evidence`）；改 prompt 必须同步更新 `test_classifier.py`。
4. **测试先行**：改动 `classifier.py` / `vision.py` 后必须跑通 27 个用例；改动分级规则后必须重跑准确率验收（≥80%）。
5. **日志**：识别审计写 `logs/recognize.log`，不得删除现有日志字段。
6. **API 契约**：`POST /api/v1/recognize` 响应字段 `category/level/reason/evidence/latency_ms` 保持不变，前端依赖之。

## 已知边界

- 浏览器摄像头仅支持 `localhost` 或 HTTPS；局域网访问用 HTTPS 或相册上传。

### 误判倾向（2026-10-28 实测更新）

- **偏差方向不稳定，不可依赖**：历史记录有"遮盖车辆（低风险）被判高风险"的判重倾向，但 2026-10-28 复验时该样本（`低风险/Snipaste_2026-10-26_17-26-10.png`）已判对，当轮 2 处误判反而都是**判轻**。不要按固定偏差方向做兜底设计。
- **判轻风险**：堆积物"体量"容易被低估（中风险堆物被判成低风险，已有实例）；门前鞋/手提袋等小件是否算低风险存在标注口径分歧。
- **输出非确定性**：`temperature=0.0` 下同一张图仍可能在不同轮次给出不同结论（`中风险/Snipaste_2026-10-26_17-25-28.png` 曾在两轮判对、一轮判错）。争议样本应多次采样取多数，勿以单次结果下结论或据此改 prompt。
- **安全指标优先**：高风险漏判是唯一不可接受的错误类型（当前 12/12 零漏判）。为压低其他类别误判而放宽高风险判定属错误方向。

### 运行依赖

- 账户余额耗尽时模型接口返回 `402 insufficient_balance_error`，后端统一映射为 `502` + `success:false`。看到 502 且日志含 402 时先查余额，不要当代码缺陷排查。
- `低风险` 类样本仅 3 张，该类准确率统计意义有限；调整分级规则前先补样至 8–10 张。
