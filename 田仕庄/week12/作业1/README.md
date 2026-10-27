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

### 1. 安装依赖

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
# 测试另需：.venv\Scripts\python -m pip install pytest httpx pillow
```

### 2. 配置

编辑 `.env`：

```ini
DEEPSEEK_API_KEY=sk-你的Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-flash
```

### 3. 启动

```bash
.venv\Scripts\python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

打开 http://127.0.0.1:8000 即可拍照识别。

> 注意：浏览器摄像头要求 `localhost` 或 HTTPS 环境；局域网手机访问请使用 HTTPS，或改用"相册上传"。

## API

### POST /api/v1/recognize

`multipart/form-data` 上传图片字段 `file`，返回：

```json
{
  "category": "高风险",
  "level": 3,
  "reason": "楼道内停放电动车并连接插线板充电……",
  "evidence": ["电动车", "插线板", "纸箱"],
  "latency_ms": 3654
}
```

### GET /api/v1/health

返回 `{"status": "ok"}`。

## 测试

### 单元 / 接口测试（27 个用例，不消耗 API 额度）

```bash
.venv\Scripts\python -m pytest backend\tests\test_classifier.py backend\tests\test_api.py -v
```

### 准确率验收（真实调用模型）

将标注照片放入 `test_images/` 对应类别目录（支持 jpg/jpeg/png/webp/gif），然后：

```bash
# 直连模式（默认，直接调 API，快）
.venv\Scripts\python backend\tests\accuracy_test.py

# 接口模式（走 HTTP，需先启动服务，验证完整链路）
.venv\Scripts\python backend\tests\accuracy_test.py --mode api
```

输出每张样本的对错、混淆矩阵与总准确率，写入 `logs/accuracy.log`；准确率 ≥ 80% 时退出码为 0。

## 关键实现说明

- **思考型模型**：deepseek-flash 会消耗 reasoning tokens，`max_tokens` 必须给足（当前 4096），否则 `content` 为空。
- **稳定解析**：模型被要求输出严格 JSON；解析层支持别名归一化与关键词兜底，保证类别始终落在五个合法值内。
- **日志审计**：每次识别的类别、依据、耗时均写入 `logs/recognize.log` 可追溯。

## 文档

- **验收报告**：`hallway-risk-acceptance-report/hallway-risk-acceptance-report.html`（混淆矩阵、逐样本明细、问题修复记录）
- **AGENTS.md**：供 AI 编码代理使用——环境、命令、六条硬性约定（五类标签、max_tokens ≥ 4096、API 契约等）

## 验收结论

15 张标注样本（实拍 5 + 生成场景 10）真实识别，**14 正确，准确率 93.3% ≥ 80%，验收通过**。
