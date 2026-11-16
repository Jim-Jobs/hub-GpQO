# 05 · MinerU 接入

> 本文档基于 **2026-11-11 对官方文档页的实际抓取**整理（`mineru.net/apiManage/docs`、`/limit`、`/apiManage`、`/token`）。
> 官方文档：https://mineru.net/apiManage/docs

---

## 1. 官方 API 全貌

MinerU 官方提供**两套**解析 API，**必须区分清楚**：

| | **精准解析 API（v4）** | **Agent 轻量解析 API** |
|---|---|---|
| 鉴权 | 需要 Token（`Authorization: Bearer <token>`） | **免 Token**，按 IP 限频，超限 HTTP 429 |
| 端点 | `POST /api/v4/extract/task` 等 5 个 | `POST /api/v1/agent/parse/url`、`/file` 等 3 个 |
| 产出 | **zip**：`full.md` + `*_content_list.json` + `images/` | **仅 Markdown**（`markdown_url`） |
| content_list | ✅ 有 | ❌ 无 |
| 图片 | ✅ 有（`images/` 目录） | ❌ 无 |
| 限制 | ≤200MB、≤200 页 | ≤10MB、≤20 页 |
| **本项目适用性** | ✅ **唯一选择** | ❌ **不可用** |

> ### ⚠️ 关于 `/api/v1/agent/parse/url`
> 你在需求里提到的这个路径**确实是官方接口**（属于 Agent 轻量 API，官方定位是"专为 AI Agent 工作流设计"）。但它：
> - **没有 zip、没有 content_list、没有图片** → 拿不到版面块，无法切块、无法做多模态召回；
> - 单文件限 10MB / 20 页；
> - 免 Token 意味着**没有配额隔离**，并发一高就 429。
>
> **结论：功能 1（PDF 解析入库）与功能 3（多模态问答）必须用 v4 精准解析 API。**
> Agent API 可以作为"只想要纯文本快速摘要"的旁路，但本项目用不上。

### v4 的全部端点

```
POST  /api/v4/extract/task                     单文件建任务（URL 方式）
GET   /api/v4/extract/task/{task_id}           查询单任务结果
POST  /api/v4/file-urls/batch                  申请批量文件上传链接（本地文件）
POST  /api/v4/extract/task/batch               批量 URL 建任务
GET   /api/v4/extract-results/batch/{batch_id} 批量查结果
```

---

## 2. 鉴权与配额

### 2.1 Token

- 申请：https://mineru.net/apiManage/token
- 请求头：`Authorization: Bearer <token>` + `Content-Type: application/json`
- 错误码：`A0202` Token 错误（检查 Bearer 前缀与空格）｜`A0211` Token 过期

### 2.2 配额与限流（官方限流页原文口径）

| 项 | 限制 |
|---|---|
| 每日高优先级解析页数 | **1000 页/账号/天**（超出部分**降优先级**排队，不是硬停） |
| 每日文件数 | 5000 个（其中 html ≤ 100 个） |
| **提交任务频控** | **50 文件/分钟**（创建任务 / 批量建任务 / 批量上传 **共用同一个池子**） |
| 查询结果频控 | 1000 次/分钟（3 秒轮询完全安全） |

> ### ⚠️ 配额对本项目的影响（重要）
> 50 份 PDF × 约 40 页 ≈ **2000 页** → **超过每日 1000 页高优先级额度**，超出的部分会排队等待。
>
> **应对**：
> 1. 用 `page_ranges` **按需截取**（例如只要前 30 页）——既省配额又快；
> 2. 批量导入时**分多天**，或接受排队；
> 3. 前端在上传时给出提示："当前免费额度约 1000 页/天，剩余额度可在 API 管理页查看"。
>
> 官方"暂无商业化收费计划"，但高优先级额度是有限资源。

---

## 3. 传文件：两种模式

### 3.1 模式 A —— URL 方式（v4 主推）

```
POST https://mineru.net/api/v4/extract/task
Authorization: Bearer <token>
Content-Type: application/json

{
  "url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
  "model_version": "vlm",
  "is_ocr": false,
  "enable_table": true,
  "enable_formula": true,
  "language": "ch",
  "no_cache": true
}
→ { "code": 0, "msg": "ok", "trace_id": "...",
    "data": { "task_id": "..." } }
```

**限制与坑（官方明写）**
- 文件 ≤ **200 MB**，页数 ≤ **200 页**
- **必须公网可达**；官方明写"因网络限制，github、aws 等国外 URL 会请求超时"，错误码 `-60008 文件读取超时`
- **有缓存**：`no_cache` 默认 `false`，`cache_tolerance` 默认 **900 秒**。同一 URL 短期内会命中缓存 → **必须设 `no_cache: true`**，否则改了文件重新解析拿到的是旧结果
- 格式：`.pdf .doc .docx .ppt .pptx .xls .xlsx` + 图片 + `.html`（html 需显式 `model_version="MinerU-HTML"`）

### 3.2 模式 B —— 文件上传（两段式签名 PUT）✅ **推荐**

官方 v4 **不支持 multipart、也不支持 base64**。本地文件必须走：

```
① POST https://mineru.net/api/v4/file-urls/batch
   Authorization: Bearer <token>
   {
     "files": [{"name": "demo.pdf", "data_id": "doc-001"}],
     "model_version": "vlm", "enable_table": true, ...
   }
   → { "code": 0,
       "data": { "batch_id": "...",
                 "file_urls": ["https://mineru.oss-cn-shanghai.aliyuncs.com/api-upload/..."] } }

② PUT <file_url>          ← 原始字节，官方要求「不要设置 Content-Type 请求头」
   → 200

③ GET https://mineru.net/api/v4/extract-results/batch/{batch_id}
   → data.extract_result[] 里每项含 state / full_zip_url / err_msg
```

**要点**
- 单次申请链接 **≤ 50 个**，链接有效期 **24 小时**
- 上传完成后**系统自动扫描并提交解析任务**，无需再调创建任务接口
- 批量上传链路**必须轮询**（因为上传完才建任务）

> ### ✅ 这解决了"服务器没有公网 IP"的顾虑
> 模式 B 直接把文件 PUT 到官方 OSS，**服务器完全不需要公网 IP**。
> 因此本项目**默认采用模式 B**；模式 A 仅在"文件本来就在公网 URL 上"时使用。

### 3.3 模式 C —— Agent 轻量 API 的文件上传

`POST /api/v1/agent/parse/file`（签名上传，免 Token）—— 只出 Markdown，本项目不用。

### 3.4 三种模式对照

| | URL 方式 | 文件上传（两段式） | Agent 轻量 |
|---|---|---|---|
| 入口 | `/api/v4/extract/task` | `/api/v4/file-urls/batch` + PUT | `/api/v1/agent/parse/*` |
| 需要公网 IP | ✅ | ❌ | ❌ |
| 大小 / 页数 | 200MB / 200 页 | 200MB / 200 页 | 10MB / 20 页 |
| Token | 必需 | 必需 | 不需要 |
| 产出 zip + content_list + images | ✅ | ✅ | ❌ 仅 Markdown |
| 额外坑 | 15 分钟缓存；海外 URL 超时 | PUT 不能带 Content-Type；链接 24h 过期 | IP 限频 |
| **本项目** | 备选 | ✅ **默认** | ❌ |

---

## 4. 查询结果与产出

### 4.1 轮询

```
GET https://mineru.net/api/v4/extract/task/{task_id}
→ { "code": 0,
    "data": { "task_id": "...", "state": "done",
              "full_zip_url": "https://cdn-mineru.openxlab.org.cn/pdf/018e....zip",
              "err_msg": "",
              "extract_progress": { "extracted_pages": 1, "total_pages": 2 } } }
```

**`state` 取值**

| 值 | 含义 | 处理 |
|---|---|---|
| `pending` | 排队中 | 继续等 |
| `running` | 解析中 | 继续等（`extract_progress` 此时有效，可映射到进度条） |
| `converting` | 格式转换中 | 继续等 |
| `done` | 完成 | 取 `full_zip_url` |
| `failed` | 失败 | 抛错，带上 `err_msg` |
| `waiting-file` | 等待上传（批量场景） | 继续等 |

官方示例代码用 **interval=3s, timeout=300s**（单任务）；批量 `timeout=1800s`。
> 官方错误码 `-60008` 是"文件读取超时"（URL 不可达），**不是**任务超时，两者要区分。

### 4.2 zip 结构

```
full.md                       Markdown 主结果
layout.json                   中间处理结果（等价 middle.json）
<name>_model.json             模型推理结果（等价 model.json）
<name>_content_list.json      内容列表  ← 切块用这个
images/                       图片资源目录，content_list 的 img_path 指向这里
```

`extra_formats` 可额外导出 `docx` / `html` / `latex`；**markdown 与 json 是默认免设项**。RAG 场景建议 `extra_formats: []`，省下载带宽。

---

## 5. ⚠️ content_list 字段：必须实测确认

**官方 API 文档页没有给出 `content_list.json` 的字段级定义**（只链接到 output_files 参考页，而该页现已改为 MinerU 4.1 的 `docvortex` 新协议：`schema: "docvortex.middle"`、`pages[].blocks[]`，并明确说经典的 `_backend`/`pdf_info` 字段不属于该契约）。

因此下面是**社区/旧版口径的经典字段（未在官方页面查证到）**：

```json
{"type": "text",     "text": "…", "text_level": 1, "page_idx": 0, "bbox": [x0,y0,x1,y1]}
{"type": "image",    "img_path": "images/xxx.jpg", "image_caption": "…",
 "image_footnote": "…", "page_idx": 2, "bbox": [...]}
{"type": "table",    "table_body": "<table>…</table>", "table_caption": "…", "page_idx": 3}
{"type": "equation", "text": "E=mc^2", "text_format": "latex", "page_idx": 4}
```

**接入第一步必须做的事**（写进实施清单）：

```python
import json, zipfile, io, collections
content_list = json.loads(z.read(cl_name))
print("字段集合:", set(k for e in content_list for k in e))
print("type 分布:", collections.Counter(e.get("type") for e in content_list))
```

拿到**你这套 API 实际返回**的 type 集合与字段名后，再定稿切块代码。
代码要写成**字段容错**的（`e.get("text") or e.get("content")`、`e.get("img_path") or e.get("image_path")`），这样字段名有出入也不会炸。

### 5.1 ✅ 实测结论（2026-11-12，`model_version=vlm`，两个真实 PDF）

用 `backend/scripts/probe_mineru.py` 跑了两份 PDF（9 页 + 4 页），结果与上面的"经典口径"**有多处不符**，实现以实测为准：

| 项 | 文档假设 | **实测** |
|---|---|---|
| `type` 取值 | text / image / table / equation | **`text` / `table` / `footer` / `page_number`**（另有 v2 格式的 `title` / `paragraph`） |
| 独立图片块 | `{"type": "image", "img_path": ...}` | **没有**。图片以 **`table.img_path`** 出现（12 个表格中 9 个带渲染图） |
| 公式块 | `{"type": "equation", ...}` | 两个样本中**均未出现**（保留容错分支） |
| `text_format` | `"markdown"` / `"latex"` | **字段不存在**。标题靠 `text_level` 识别（非空即标题） |
| `table_caption` / `table_footnote` | string | **`list`**（可能为空 list），必须 join 后使用 |
| `table_body` | 可能是 md | 确认是 **HTML** `<table>...</table>` |
| `img_path` 命名 | 未提及 | 内容哈希：`images/cac13bd4...a7c.jpg`（不是 `page_0_image_0`） |
| 产物文件 | — | 同时有 `*_content_list.json`（v1，扁平）与 `*_content_list_v2.json`（分页分组） |

**v2 格式**（`content_list_v2.json`）是「每页一个数组」，块结构为
`{"type": "title|paragraph|table|page_footer|page_number", "content": {...}}`，
其中 `table.content` 含 `html` / `image_source.path` / `table_caption` / `table_type`。
本项目切块用 **v1**（扁平、字段更简单），定位时必须排除 `_content_list_v2.json`。

对应实现：`backend/app/pipeline/chunking.py`（归一化 + 跳噪声块）、
`backend/app/pipeline/table_md.py`（HTML → Markdown 表格）。

**已确认的 MinerU 4.1 相关信息**：`RenderFormat.CONTENT_LIST` → Content List V1（`list[dict]`）；`CONTENT_LIST_V2` → 按页分组（`list[list[dict]]`）；图片命名 `images/page_{page_idx}_{parent_type}_{parent_index}.{ext}`，**page_idx 从 0 开始**。

---

## 6. 解析参数（官方参数表）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `url` | string | — | **（URL 模式必填）** 文件 URL |
| `is_ocr` | bool | `false` | 仅 pipeline/vlm 有效。**扫描件/图片型 PDF 必须 true**，否则识别为空 |
| `enable_formula` | bool | `true` | 仅 pipeline/vlm 有效；**vlm 下只影响行内公式** |
| `enable_table` | bool | `true` | 仅 pipeline/vlm 有效 |
| `language` | string | `ch` | `ch`(中英) / `ch_server`(繁+手写) / `en` / `japan` / `korean` / `latin` / `arabic` / … |
| `model_version` | string | `pipeline` | `pipeline` / `vlm` / `MinerU-HTML` |
| `data_id` | string | — | ≤128 字符，`[A-Za-z0-9_.-]`，业务侧唯一标识 → **本项目传 `doc_id`** |
| `page_ranges` | string | — | 逗号分隔，如 `"2,4-6"` → [2,4,5,6]；`"2--2"` → 到倒数第二页 |
| `no_cache` | bool | `false` | **本项目恒为 true**（否则 15 分钟内拿旧结果） |
| `cache_tolerance` | int | 900 | 缓存容忍秒数 |
| `extra_formats` | [string] | — | 仅 `docx`/`html`/`latex`；**本项目留空** |
| `callback` / `seed` | string | — | 主动回调；用 callback 时 seed 必填，`checksum = SHA256(uid+seed+content)`，失败最多重推 5 次。**本项目用轮询，不用 callback** |

> **注意**：`data_vlm` **不是云 API 参数**（官方 API 文档全文 grep 命中数为 0），它是开源版本地部署的概念。

### 多模态 RAG 推荐配置

```json
{
  "model_version": "vlm",     // 官方对比表把 vlm 标为"推荐"；pipeline 更快但精度低
  "is_ocr": false,            // 扫描件必须改 true
  "enable_table": true,       // 表格是 RAG 最易丢信息的两类块之一
  "enable_formula": true,     // 同上
  "language": "ch",
  "no_cache": true,           // 必须
  "extra_formats": [],
  "page_ranges": "1-50",      // 按需截取，省配额又快
  "data_id": "<doc_id>"       // 幂等关联
}
```

### 错误码速查

| 码 | 含义 | 处理 |
|---|---|---|
| `A0202` / `A0211` | Token 错误 / 过期 | 检查配置，提示用户换 token |
| `-500` | 传参错误 | 检查参数表 |
| `-60005` | 文件超 200MB | 拒绝并提示 |
| `-60006` | 页数超 200 页 | 用 `page_ranges` 截取或拆分 |
| `-60008` | **文件读取超时** | URL 不可达（换国内 CDN / 改用上传模式） |
| `-60009` | 提交队列已满 | 退避重试 |
| `-60018` | 每日任务数达上限 | 提示用户次日再试 |
| `-60019` | html 额度不足 | 与本项目无关 |
| HTTP 429 | 触发频控 | 退避重试（50 文件/分钟池子） |

---

## 7. Python SDK

包名 **`mineru-open-sdk`**（PyPI，v0.2.5）：

```bash
pip install mineru-open-sdk
```

```python
from mineru import MinerU

client = MinerU("your-api-token")
result = client.extract("https://cdn-mineru.openxlab.org.cn/demo/example.pdf")
print(result.markdown)
print(result.images)

with MinerU("your-api-token") as c:
    r = c.extract("./paper.pdf", model="vlm", ocr=True, formula=True,
                  table=True, language="ch", pages="1-20",
                  extra_formats=["docx"], timeout=600)
    r.save_all("./output/")
```

| 项 | 说明 |
|---|---|
| 构造 | `MinerU(token=None, base_url="https://mineru.net/api/v4", flash_base_url=None)`，支持 `with` |
| 阻塞式 | `extract` / `extract_batch` / `crawl` / `flash_extract` |
| 异步原语 | `submit` / `submit_batch` / `get_batch` / `get_task` —— ⚠️ **`submit()` 返回 `batch_id` 不是 `task_id`** |
| Result 字段 | `state` / `progress` / `markdown` / `images` / `content_list` / `docx` / `html` / `latex` / `task_id` |
| 保存 | `save_markdown(path, with_images=True)` / `save_docx` / `save_all(dir)` |

> ⚠️ **SDK README 与 API 文档有两处矛盾**
> 1. 页数上限：SDK 说 600，API 文档说 **200** → **以 API 文档的 200 为准**（SDK 滞后）
> 2. `model_version` 默认：SDK 是 vlm，API 是 **pipeline** → 本项目**显式指定**，不依赖默认值
>
> ⚠️ `mineru` 这个 pip 包是**开源本地解析版**，**不是**云客户端，别混用。`mineru-api-client` 这个包名不存在。

---

## 8. 本项目的适配器设计

### 8.1 配置项

```env
# MinerU
MINERU_BASE_URL=https://mineru.net/api
MINERU_API_KEY=<你的 token>
MINERU_SUBMIT_MODE=upload          # upload | url
MINERU_TASK_PATH=/v4/extract/task
MINERU_RESULT_PATH=/v4/extract/task/{task_id}/result
MINERU_FILE_URLS_PATH=/v4/file-urls/batch
MINERU_BATCH_RESULT_PATH=/v4/extract-results/batch/{batch_id}
MINERU_MODEL_VERSION=vlm
MINERU_IS_OCR=false
MINERU_ENABLE_TABLE=true
MINERU_ENABLE_FORMULA=true
MINERU_LANGUAGE=ch
MINERU_NO_CACHE=true
MINERU_PAGE_RANGES=                 # 留空=全量；如 "1-50"
MINERU_POLL_INTERVAL=3
MINERU_POLL_TIMEOUT=1800
```

### 8.2 适配器接口

```python
class MineruClient:
    def __init__(self, settings: Settings): ...

    async def parse_pdf(self, pdf_path: Path) -> MineruResult: ...
    async def health(self) -> bool: ...


@dataclass
class MineruResult:
    task_id: str
    markdown: str
    content_list: list[dict]
    image_files: list[Path]     # 已解压到 work_dir
    work_dir: Path
```

### 8.3 两种提交模式的实现要点

**`url` 模式**
1. 需要一个公网可达的 PDF URL（若是内部文件，需先传到对象存储）。
2. `POST {base}{task_path}`，body 含 `url` + 解析参数 + `data_id=doc_id`。
3. 轮询 `GET {base}{result_path}`。

**`upload` 模式（默认）**
1. `POST {base}{file_urls_path}`，body `{"files":[{"name":..., "data_id": doc_id}], ...解析参数}` → 拿 `file_urls[0]` 与 `batch_id`。
2. `PUT file_urls[0]`，body = 原始字节，**不设 Content-Type**。
3. 轮询 `GET {base}{batch_result_path.format(batch_id)}` → `data.extract_result[0]`。

### 8.4 统一抽象：轮询统一到"任务状态"

两种模式的轮询响应结构不同，适配器内部统一成：

```python
@dataclass
class TaskState:
    state: str        # pending|running|converting|done|failed
    progress: float   # 0~1（extract_progress 映射而来）
    zip_url: str | None
    error: str | None
```

上层（Worker）只认 `TaskState`，不关心是哪种模式。

### 8.5 解压与安全

- 下载 zip → 解压到 `work_dir/{task_id}`。
- **必须防 Zip Slip**：逐个成员校验 `Path(member).resolve()` 仍在目标目录内，否则跳过。
- 从 zip 中找 `*_content_list.json`（`rglob`）与 `full.md`；图片在 `images/` 目录。
- 解析成功后删除 `work_dir`（图片已转存到 `images/`）。

---

## 9. 接入检查清单（实施时逐条打勾）

- [ ] 在 https://mineru.net/apiManage/token 申请 Token，填入 `.env`
- [ ] 用官方示例 PDF 跑通一次 v4 任务（URL 模式），确认返回 zip
- [ ] **打印 `content_list` 的字段集合与 type 分布**，据此定稿切块代码
- [ ] 验证 `images/` 目录名与 `img_path` 的对应关系，确认图片能对上
- [ ] 实测 3 页 PDF 的解析耗时，校准 `MINERU_POLL_TIMEOUT`
- [ ] 确认单份 PDF 页数 < 200；超出的用 `page_ranges` 截断
- [ ] 确认"上传模式"的 PUT 不带 Content-Type 能成功
- [ ] 统计常用 PDF 的平均页数，估算每日配额消耗（1000 页高优先级/天）
- [ ] 检查 `table_body` 是 HTML 还是 Markdown，决定转换逻辑
