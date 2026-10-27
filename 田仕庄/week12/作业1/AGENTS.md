# AGENTS.md

供 AI 编码代理使用的项目指南：环境、命令、约束与不可破坏的约定。

## 项目概述

楼道消防风险识别系统：FastAPI 后端 + 单页前端，调用 **deepseek-flash** 多模态模型对楼道照片做五级风险分类（高风险 / 中风险 / 低风险 / 无风险 / 非楼道）。

## 环境

- Python 3.12（本机环境：`C:\Users\Cooper\miniconda3\envs\py312\python.exe`，或工作区 `.venv`）
- 依赖：`backend\requirements.txt`；测试另需 `pytest httpx pillow`
- 配置：根目录 `.env`（`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `MODEL_NAME`）

## 常用命令

```bash
# 启动服务（前端托管在 /，API 在 /api/v1/）
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

# 单元 + 接口测试（27 用例，mock 模型层，不消耗 API 额度）
python -m pytest backend\tests\test_classifier.py backend\tests\test_api.py -v

# 准确率验收（真实调用模型，需有效 Key；--mode api 走 HTTP 链路）
python backend\tests\accuracy_test.py
```

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
- 当前唯一已知误判倾向：被遮盖车辆（低风险）可能被保守判为高风险——业务可接受，勿为此放宽高风险判定。
