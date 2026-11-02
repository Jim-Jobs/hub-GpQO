# 楼道消防风险识别系统

带前后端的多模态 Web 应用：通过网页**拍照或上传**楼道照片，调用 **deepseek-flash** 多模态大模型，实时识别消防安全隐患并给出风险等级、判断依据与证据标签。

## 风险分级规则

| 等级 | 判定规则 |
|---|---|
| 高风险 | 楼道中出现电动车、电瓶、飞线充电等可能起火的元素 |
| 中风险 | 大量堆积物严重影响通行，或堆放大量纸箱、木质家具等可造成火势蔓延的堵塞物 |
| 低风险 | 存在楼道堆物现象但不严重 |
| 无风险 | 楼道干净，无堆放物品 |
| 非楼道 | 与楼道无关的图片 |

## 项目结构

```
.
├── .env                        # API Key 与模型配置
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 入口：API 路由 + 前端托管
│   │   ├── config.py           # 配置（读取 .env）
│   │   ├── classifier.py       # 模型输出解析、别名归一化、关键词兜底
│   │   ├── prompt.py           # 消防专家 prompt（严格 JSON 输出）
│   │   ├── logger.py           # loguru 日志（按天轮转）
│   │   └── services/
│   │       └── vision.py       # deepseek-flash 调用（含 1 次重试）
│   └── tests/
│       ├── test_classifier.py  # 解析/分类单元测试
│       ├── test_api.py         # API 契约测试（mock 模型层）
│       └── accuracy_test.py    # 准确率验收脚本（真实调用模型）
├── frontend/
│   └── index.html              # 拍照页：摄像头/上传/结果展示/识别记录
├── test_images/                # 标注样本（目录名即标注）
│   ├── 高风险/  中风险/  低风险/  无风险/  非楼道/
├── hallway-risk-acceptance-report/  # 验收报告（自包含 HTML，离线可开）
├── logs/                       # app.log / recognize.log / accuracy.log
├── AGENTS.md                   # AI 代理指南：命令、约束、硬性约定
└── README.md
```

## 快速开始

> **环境说明**：本项目在 Windows + conda 环境 `py312`（Python 3.12.13）下开发与验证。
> 下列命令以 **cmd / PowerShell** 为准；Git Bash 或 macOS/Linux 需把 `\` 改为 `/`，并使用对应平台的解释器路径。

### 1. 准备环境（conda py312）

依赖已装在 conda 环境 `py312` 中。激活后即可使用：

```bat
conda activate py312
python -m pip install -r backend\requirements.txt
```

若终端提示 `conda: command not found`（如 Git Bash 未做 `conda init`），可跳过激活，直接用解释器绝对路径：

```bat
C:\Users\Cooper\miniconda3\envs\py312\python.exe -m pip install -r backend\requirements.txt
```

> 不想用 conda 时，也可自建虚拟环境：`python -m venv .venv` 后改用 `.venv\Scripts\python`。
> `requirements.txt` 已包含运行与测试所需全部依赖（fastapi / uvicorn / openai / pydantic / pydantic-settings / loguru / httpx / pytest / Pillow），**无需额外安装**。

### 2. 配置

首次运行需从模板复制配置文件：

```bat
copy .env.example .env
```

然后编辑 `.env`，填入自己的 Key：

```ini
DEEPSEEK_API_KEY=sk-你的Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-flash
```

### 3. 启动

```bat
conda activate py312
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

未激活环境时用绝对路径：

```bat
C:\Users\Cooper\miniconda3\envs\py312\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

打开 http://127.0.0.1:8000 即可拍照识别。

> 注意：浏览器摄像头要求 `localhost` 或 HTTPS 环境；局域网手机访问请使用 HTTPS，或改用"相册上传"。

## API

### POST /api/v1/recognize

请求体为 `application/json`（**不是** multipart 表单）：

```json
{
  "image_base64": "iVBORw0KGgoAAAANSUhEUg...",
  "mime_type": "image/png"
}
```

| 字段 | 说明 |
|---|---|
| `image_base64` | 图片 base64；带或不带 `data:image/png;base64,` 前缀均可（自动兼容） |
| `mime_type` | `image/jpeg` / `image/png` / `image/webp` / `image/gif`，缺省 `image/jpeg` |

成功返回：

```json
{
  "success": true,
  "request_id": "145c1130",
  "category": "高风险",
  "level": 4,
  "reason": "楼道内停放了一辆电动滑板车，电池存在自燃起火风险……",
  "evidence": ["楼道内停放的一辆电动滑板车", "狭窄的公共走廊空间"],
  "latency_ms": 4848
}
```

`level` 取值：`非楼道=0`、`无风险=1`、`低风险=2`、`中风险=3`、`高风险=4`。

出错时返回 `{"success": false, "request_id": "...", "error": "..."}`：

| 状态码 | 场景 |
|---|---|
| 422 | 请求体校验失败（base64 非法 / MIME 不支持 / 缺字段） |
| 413 | 图片超过 `MAX_IMAGE_MB` |
| 502 | 模型调用失败，或模型输出无法解析 |
| 503 | 服务未就绪（API Key 未配置） |

### GET /api/v1/health

```json
{"status": "ok", "model": "deepseek-flash", "vision_ready": true}
```

`vision_ready=false` 表示 Key 未配置，识别接口会返回 503。

## 测试

> 以下命令假设已 `conda activate py312`（工作目录为项目根目录）。
> 未激活环境时，把 `python` 替换为 `C:\Users\Cooper\miniconda3\envs\py312\python.exe`。

### 单元 / 接口测试（27 个用例，不消耗 API 额度）

```bat
python -m pytest backend\tests\test_classifier.py backend\tests\test_api.py -v
```

### 准确率验收（真实调用模型）

将标注照片放入 `test_images/` 对应类别目录（支持 jpg/jpeg/png/webp/gif），然后：

```bat
:: 直连模式（默认，进程内直接调 API，快）
python backend\tests\accuracy_test.py

:: HTTP 模式（需先启动服务，验证 uvicorn → FastAPI → VisionService 完整链路）
python backend\tests\accuracy_test.py --url http://127.0.0.1:8000
```

输出每张样本的对错、混淆矩阵与总准确率，写入 `logs/accuracy.log`；准确率 ≥ 80% 时退出码为 0。
另有 `--dir`（测试集目录）与 `--threshold`（阈值）可调。

## 关键实现说明

- **思考型模型**：deepseek-flash 会消耗 reasoning tokens，`max_tokens` 必须给足（当前 4096），否则 `content` 为空。
- **稳定解析**：模型被要求输出严格 JSON；解析层支持别名归一化与关键词兜底，保证类别始终落在五个合法值内。
- **日志审计**：每次识别的类别、依据、耗时均写入 `logs/recognize.log` 可追溯。

## 文档

- **验收报告**：`hallway-risk-acceptance-report/hallway-risk-acceptance-report.html`（混淆矩阵、逐样本明细、问题修复记录）
- **AGENTS.md**：供 AI 编码代理使用——环境、命令、六条硬性约定（五类标签、max_tokens ≥ 4096、API 契约等）

## 验收结论

2026-10-28 复验：39 张标注样本（实拍 15 + 生成场景 24）经完整 HTTP 链路由真实模型识别，**37 正确，准确率 94.9% ≥ 80%，验收通过**。

- **高风险 12/12 零漏判**、**非楼道 9/9 零误入**、无风险 6/6 全对
- 中风险 8/9、低风险 2/3
- 2 处误判均为相邻一级"判轻"（中风险→低风险、低风险→无风险），无跨级跳变
- 单元/接口测试 **27/27** 通过；39 次真实调用 0 异常，平均耗时 3.1s

已知边界与逐样本明细见 `hallway-risk-acceptance-report/hallway-risk-acceptance-report.html`。需注意 `低风险` 类样本仅 3 张，统计效力不足，补样后应复验。
