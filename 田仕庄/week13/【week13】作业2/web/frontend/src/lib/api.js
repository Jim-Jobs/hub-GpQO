/**
 * SSE 客户端：POST /api/chat 并解析 text/event-stream。
 * （原生 EventSource 只支持 GET，故用 fetch + ReadableStream 手工解析）
 */

/** 当前页面来源（首页连通性诊断用） */
export function backendOrigin() {
  if (typeof location === 'undefined') return 'http://127.0.0.1:8000'
  return `${location.protocol}//${location.host}`
}

/** 是否以 file:// 打开页面：此时 fetch 相对路径必然失败（浏览器报 Failed to fetch） */
export function isFileProtocol() {
  return typeof location !== 'undefined' && location.protocol === 'file:'
}

/** 把网络层错误（TypeError: Failed to fetch）翻译成可操作提示 */
export function describeNetworkError(err) {
  const raw = err?.message || String(err)
  if (isFileProtocol()) {
    return '页面是通过 file:// 直接打开的，浏览器不允许请求后端接口。'
      + '请先运行 python server.py，再访问 http://127.0.0.1:8000（不要双击 index.html）。'
  }
  return `无法连接后端服务（${backendOrigin()}）。请确认：`
    + '① 终端已运行 python server.py 且未报错退出；'
    + '② 浏览器地址是 http://127.0.0.1:8000（改过端口则与 WEB_PORT 一致）；'
    + '③ 后端终端没有被关闭或重启。'
    + `（原始错误：${raw}）`
}

/** 统一的 fetch 包装：把网络层异常转成中文可操作提示 */
async function request(path, init) {
  let resp
  try {
    resp = await fetch(path, init)
  } catch (err) {
    throw new Error(describeNetworkError(err))
  }
  return resp
}

/**
 * 发起流式对话请求。
 * @param {object} opts
 * @param {string} opts.question  用户问题
 * @param {string} opts.threadId 会话 ID（多轮记忆）
 * @param {(event: string, data: object) => void} opts.onEvent 事件回调
 * @param {AbortSignal} [opts.signal] 取消信号
 * @param {boolean} [opts.fast] 极速模式（跳过评审回路）
 */
export async function streamChat({ question, threadId, onEvent, signal, fast = false }) {
  const resp = await request('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, thread_id: threadId, fast }),
    signal,
  })

  if (resp.status === 409) {
    const body = await resp.json().catch(() => ({ detail: '已有请求正在处理' }))
    throw new Error(body.detail || '已有请求正在处理，请稍候再试')
  }
  if (!resp.ok || !resp.body) {
    throw new Error(`服务异常（HTTP ${resp.status}）`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''

  for (;;) {
    let chunk
    try {
      // SSE 帧以空行分隔；每行形如 "event: xxx" / "data: {...}"
      // 另含 ": ping" 心跳注释帧（无 data，解析时自动忽略）
      chunk = await reader.read()
    } catch (err) {
      throw new Error(
        '与后端的数据连接中断（可能是后端被关闭或重启）。请确认 python server.py 仍在运行后重试。'
        + `（原始错误：${err?.message || err}）`,
      )
    }
    const { done, value } = chunk
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (data) {
        try {
          onEvent(event, JSON.parse(data))
        } catch {
          onEvent(event, { text: data })
        }
      }
    }
  }
}

/** 开启新会话：向服务端申请新 thread_id。 */
export async function newSession() {
  const resp = await request('/api/session/new', { method: 'POST' })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return (await resp.json()).thread_id
}

/** 健康检查（用于首页加载时的连通性提示）。 */
export async function checkHealth() {
  try {
    const resp = await fetch('/api/health')
    return resp.ok ? await resp.json() : null
  } catch {
    return null
  }
}
