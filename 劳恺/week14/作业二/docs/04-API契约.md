# 04 · API 契约

> 本文档是**前后端边界**。前端可据此直接开发，后端实现必须严格符合。
> 基础路径：`/api`（静态图片走 `/static`）。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 编码 | UTF-8 |
| 鉴权 | **无**（单用户演示；预留 `X-Session-Key` 头但当前不校验） |
| 时间 | ISO 8601 带时区，如 `2026-11-11T09:30:00.123456+00:00` |
| 文档 ID | `d_` + 12 位 hex；会话 ID `s_` + 12 位 hex |
| 错误 | HTTP 状态码 + `{"detail": "...", "code": "..."}` |
| 交互式文档 | `GET /docs`（FastAPI 自动生成 OpenAPI） |

**错误码**

| HTTP | code | 场景 |
|---|---|---|
| 400 | `invalid_request` | 参数非法（如空问题、非 PDF） |
| 404 | `not_found` | doc_id / session_id 不存在 |
| 409 | `conflict` | 重复上传同一文件（可选启用） |
| 413 | `payload_too_large` | 文件超过 `max_upload_mb` |
| 503 | `service_unavailable` | Kafka / ES 不可用 |
| 500 | `internal_error` | 未预期异常 |

---

## 2. 文档管理

### 2.1 上传（批量）

```
POST /api/documents
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `files` | file[] | 是 | **一次可传多个 PDF** |

**响应 `202 Accepted`**

```json
{
  "accepted": [
    {"doc_id": "d_ab12cd34ef56", "filename": "2024年度财报.pdf", "status": "pending",
     "message": "已入队，等待解析"},
    {"doc_id": "d_77e88a12bb34", "filename": "产品手册.pdf", "status": "pending",
     "message": "已入队，等待解析"}
  ],
  "rejected": [
    {"filename": "readme.txt", "reason": "仅支持 PDF 文件"}
  ]
}
```

**语义**：
- 接口**立即返回**（不等解析），前端拿到 `doc_id` 后立刻插入列表，显示「待解析」。
- 单个文件失败**不影响**其他文件（部分成功语义）。
- 非 PDF 或超限文件进 `rejected`，不产生 `doc_id`。
- 上传后后端会往 `pdf.upload` 发消息，随后状态经 SSE 推送。

**限制**：`max_upload_mb`（默认 100MB/文件）、`max_files_per_upload`（默认 20）。

---

### 2.2 列表

```
GET /api/documents
```

**响应 `200`**

```json
[
  {
    "doc_id": "d_ab12cd34ef56",
    "filename": "2024年度财报.pdf",
    "size_bytes": 4829133,
    "page_count": 42,
    "status": "done",
    "progress": 1.0,
    "stage": "finished",
    "error": null,
    "chunk_count": 128,
    "image_count": 17,
    "created_at": "2026-11-11T09:30:00+00:00",
    "updated_at": "2026-11-11T09:38:02+00:00",
    "finished_at": "2026-11-11T09:38:02+00:00"
  }
]
```

按 `created_at` 倒序。

---

### 2.3 详情

```
GET /api/documents/{doc_id}
```

响应同列表项（单对象）。`404` 若不存在。

---

### 2.4 删除

```
DELETE /api/documents/{doc_id}
```

**响应 `200`**

```json
{"doc_id": "d_ab12cd34ef56", "deleted": true}
```

**副作用**：删除 ES 中的 document + chunks + images；删除磁盘上的原始 PDF 与图片目录。
> 若该文档正在解析中，删除后 Worker 写回时会因 `doc_id` 不存在而被忽略（写 chunk 前先校验 document 存在）。

---

### 2.5 重试解析

```
POST /api/documents/{doc_id}/retry
```

**响应 `200`**

```json
{"doc_id": "d_ab12cd34ef56", "status": "pending", "message": "已重新入队"}
```

仅对 `status=failed` 的文档有效（其他状态返回 `409 conflict`）。重试会**重置** `progress=0`、`error=null`，并往 `pdf.upload` 发一条新消息。

---

### 2.6 图片静态访问

```
GET /static/images/{doc_id}/{filename}
```

返回图片二进制。**服务端必须做路径穿越校验**（拒绝 `..`、绝对路径、符号链接逃逸）。

响应头：`Content-Type: image/png`、`Cache-Control: public, max-age=86400, immutable`（图片内容不可变，可长期缓存）。

---

## 3. 解析状态流（SSE）

```
GET /api/documents/events
Accept: text/event-stream
```

**响应 `200`，`Content-Type: text/event-stream`**

```
event: status
data: {"event":"status","doc_id":"d_ab12cd34ef56","status":"pending","progress":0.0,"stage":"queued","error":null,"updated_at":"2026-11-11T09:30:00+00:00"}

event: status
data: {"event":"status","doc_id":"d_ab12cd34ef56","status":"processing","progress":0.05,"stage":"mineru_parse","error":null,"updated_at":"2026-11-11T09:30:03+00:00"}

event: status
data: {"event":"status","doc_id":"d_ab12cd34ef56","status":"processing","progress":0.55,"stage":"caption","error":null,"updated_at":"2026-11-11T09:34:20+00:00"}

event: status
data: {"event":"status","doc_id":"d_ab12cd34ef56","status":"done","progress":1.0,"stage":"finished","error":null,"chunk_count":128,"image_count":17,"updated_at":"2026-11-11T09:38:02+00:00"}
```

**事件类型**

| event | 时机 | 说明 |
|---|---|---|
| `status` | 每次状态变化 | 完整状态对象（覆盖式，不是增量） |
| `ping` | 每 25 秒 | 心跳注释行 `: ping`，防止中间代理断连 |
| `error` | 总线异常 | `{"event":"error","code":"...","message":"..."}` |

**前端要点**
1. 状态是**覆盖式**的完整对象 → 前端直接按 `doc_id` 合并进本地列表，无需增量逻辑。
2. **首屏建议先 `GET /api/documents` 拉全量，再开 SSE**；SSE 只负责后续增量。
3. 断线重连：SSE 规范自带 `Last-Event-ID`，但本项目事件不编号，**重连后应重新拉一次全量**。
4. 兜底：若 SSE 不可用，退化为 `GET /api/documents` 轮询（建议 2s 间隔，仅在有 `processing` 文档时轮询）。

---

## 4. 问答（SSE）

```
POST /api/chat
Content-Type: application/json
Accept: text/event-stream
```

**请求体**

```json
{
  "session_id": "s_3f9a2b7c1d40",
  "question": "2024 年营收峰值出现在哪个季度？如图所示",
  "images": [
    {"data": "data:image/png;base64,iVBORw0KGgo...", "filename": "shot.png"}
  ],
  "doc_ids": ["d_ab12cd34ef56", "d_77e88a12bb34"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `question` | string | 是 | 用户问题，**非空** |
| `session_id` | string | 否 | 省略则新建会话；传了则延续历史（最近 3 轮） |
| `images` | object[] | 否 | 用户图片，base64（可带 `data:image/png;base64,` 前缀）。**最多 4 张**，不落盘 |
| `doc_ids` | string[] | 否 | 限定检索范围；省略=全库 |

**响应 `200`，`Content-Type: text/event-stream`**

```
event: delta
data: {"event":"delta","text":"根据《2024年度财报.pdf》第 7 页的柱状图"}

event: delta
data: {"event":"delta","text":"【图1】显示，营收峰值出现在 "}

event: citation
data: {"event":"citation","text":"【图1】","image_id":"d_ab12cd34ef56_p7_i1","doc_id":"d_ab12cd34ef56","doc_filename":"2024年度财报.pdf","page_no":7,"url":"/static/images/d_ab12cd34ef56/d_ab12cd34ef56_p7_i1.png","caption":"柱状图，横轴为季度，纵轴为营收（亿元）"}

event: delta
data: {"event":"delta","text":"2024 年第三季度，营收达到 42.3 亿元。"}

event: done
data: {"event":"done","session_id":"s_3f9a2b7c1d40","answer":"根据《2024年度财报.pdf》第 7 页的柱状图【图1】显示，营收峰值出现在 2024 年第三季度，营收达到 42.3 亿元。","citations":[{"image_id":"d_ab12cd34ef56_p7_i1","doc_id":"d_ab12cd34ef56","doc_filename":"2024年度财报.pdf","page_no":7,"url":"/static/images/d_ab12cd34ef56/d_ab12cd34ef56_p7_i1.png","caption":"柱状图，横轴为季度，纵轴为营收（亿元）","text":"【图1】"}],"retrieved_chunk_ids":["d_ab12cd34ef56_c0007","d_ab12cd34ef56_c0012"]}
```

**事件类型**

| event | 时机 | 载荷 |
|---|---|---|
| `delta` | 流式生成中 | `{"text": "增量文本"}` —— 前端**追加**到消息气泡 |
| `citation` | 检测到 `[[cite:...]]` 时 | 完整引用对象 —— 前端**插入**引用卡片/角标 |
| `done` | 流结束 | 完整答案全文 + citations 数组 + 命中的 chunk_id 列表 |
| `error` | 出错 | `{"code":"llm_unavailable","message":"..."}` |

**图文混排的渲染契约（前端必读）**

1. `delta` 里的文本中，引用标记已被替换为 `【图N】`（N = 该图在本次送模图片列表中的 1 基序号，**用户图片在前，文档图片在后**）。
2. 前端**不需要**自己解析 `【图N】`——它同时会收到 `citation` 事件。但若要做"点击【图1】跳到图片"，可用 `citation.text` 字段里的 `【图N】` 与 `delta` 文本做替换匹配。
3. `done.answer` 是**完整答案的最终形态**（含 `【图N】`），可用于消息落库。
4. `citations` 数组是**本次回答实际引用到的图**（不是"检索到的全部图"）。未引用到的图不出现在这里。
5. 图片的 `url` 是**相对路径**，前端需拼接后端 base URL。

**降级行为**：若后端解析引用标记失败，`delta` 中会保留原始 `[[cite:xxx]]` 文本，答案仍然可读；此时 `done.citations` 会退化为"本次检索到的全部图片"。

---

## 5. 会话

### 5.1 会话列表

```
GET /api/sessions
```

```json
[
  {"session_id": "s_3f9a2b7c1d40", "title": "2024 年营收峰值出现在哪个季度？",
   "message_count": 4, "created_at": "...", "updated_at": "..."}
]
```

### 5.2 会话详情

```
GET /api/sessions/{session_id}
```

```json
{
  "session_id": "s_3f9a2b7c1d40",
  "messages": [
    {"role": "user", "content": "…", "images": [{"filename":"shot.png","sha256":"…"}], "created_at": "…"},
    {"role": "assistant", "content": "…【图1】…",
     "citations": [{"image_id":"d_ab12cd34ef56_p7_i1","url":"/static/images/…","page_no":7,"caption":"…"}],
     "created_at": "…"}
  ]
}
```

---

## 6. 健康检查

```
GET /healthz
```

```json
{
  "status": "ok",
  "kafka": "up",
  "elasticsearch": "up",
  "vllm": "up",
  "embedding": "up",
  "mock_mode": false
}
```

任一依赖 `down` 时整体 `status` 为 `degraded`，但 HTTP 仍返回 200（便于容器编排的存活探针）。

---

## 7. 前端对接速查

### 7.1 上传 + 状态更新（Vue 3 示例）

```js
// 上传
const form = new FormData()
for (const f of files) form.append('files', f)
const res = await fetch('/api/documents', { method: 'POST', body: form })
const { accepted, rejected } = await res.json()
accepted.forEach(d => documents.value.push({ ...d, progress: 0 }))  // 立即出现在列表

// 状态订阅
const es = new EventSource('/api/documents/events')
es.addEventListener('status', (e) => {
  const s = JSON.parse(e.data)
  const idx = documents.value.findIndex(d => d.doc_id === s.doc_id)
  if (idx >= 0) documents.value[idx] = { ...documents.value[idx], ...s }  // 覆盖式合并
})
```

### 7.2 问答流（fetch + SSE 手动解析）

> `EventSource` **只支持 GET**，问答是 POST，所以要用 `fetch` + `ReadableStream` 手动解析 SSE 帧。

```js
const resp = await fetch('/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ question, images: [{ data: base64DataUrl }] })
})
const reader = resp.body.getReader()
const decoder = new TextDecoder()
let buf = ''
const citations = []
while (true) {
  const { done, value } = await reader.read()
  if (done) break
  buf += decoder.decode(value, { stream: true })
  const frames = buf.split('\n\n')
  buf = frames.pop()                       // 保留不完整的最后一帧
  for (const frame of frames) {
    const ev = /^event: (.+)$/m.exec(frame)?.[1]
    const data = /^data: (.+)$/m.exec(frame)?.[1]
    if (!data) continue
    const payload = JSON.parse(data)
    if (ev === 'delta')  answerText.value += payload.text
    if (ev === 'citation') citations.value.push(payload)
    if (ev === 'done')   { answerText.value = payload.answer; citations.value = payload.citations }
    if (ev === 'error')  showError(payload.message)
  }
}
```

### 7.3 图片展示

```vue
<img :src="backendBase + citation.url" :alt="citation.caption" loading="lazy" />
```

---

## 8. 契约变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-11 | 首版，锁定全部接口 |
