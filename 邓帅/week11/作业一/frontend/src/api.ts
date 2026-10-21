// API 封装:POST /api/ask 的 SSE 流式消费 + history/schema 普通请求
import type { AgentEvent, HistoryRecord, SchemaInfo } from './types'

/** 发起问答,逐事件回调(POST 流,EventSource 不支持 POST,手写解析)。 */
export async function askStream(
  question: string,
  onEvent: (ev: AgentEvent) => void,
): Promise<void> {
  const resp = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`请求失败: HTTP ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  const handleFrame = (frame: string) => {
    for (const line of frame.split('\n')) {
      if (!line.startsWith('data:')) continue
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as AgentEvent)
      } catch {
        // 非 JSON 行忽略(如注释心跳)
      }
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    // SSE 以空行分帧
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      handleFrame(buf.slice(0, idx))
      buf = buf.slice(idx + 2)
    }
  }
  if (buf.trim()) handleFrame(buf)
}

/** 历史问答列表(倒序)。 */
export async function fetchHistory(limit = 50): Promise<HistoryRecord[]> {
  const resp = await fetch(`/api/history?limit=${limit}`)
  if (!resp.ok) throw new Error(`获取历史失败: HTTP ${resp.status}`)
  const data = (await resp.json()) as { items: HistoryRecord[] }
  return data.items
}

/** 数据库 schema 概览。 */
export async function fetchSchema(): Promise<SchemaInfo> {
  const resp = await fetch('/api/schema')
  if (!resp.ok) throw new Error(`获取 schema 失败: HTTP ${resp.status}`)
  return (await resp.json()) as SchemaInfo
}
