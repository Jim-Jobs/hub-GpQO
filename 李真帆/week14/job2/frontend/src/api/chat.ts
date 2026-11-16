import type { MessageMeta } from '@/types'

export interface ChatStreamHandlers {
  onToken: (text: string) => void
  onDone: (data: { message_id: string; meta: MessageMeta }) => void
  onError: (message: string) => void
}

export async function streamChat(form: FormData, handlers: ChatStreamHandlers, signal?: AbortSignal): Promise<void> {
  const resp = await fetch('/api/chat', { method: 'POST', body: form, signal })
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      if (j?.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const ln of frame.split('\n')) {
        if (ln.startsWith('event: ')) event = ln.slice(7).trim()
        else if (ln.startsWith('data: ')) data += ln.slice(6)
      }
      if (!data) continue
      let payload: any
      try {
        payload = JSON.parse(data)
      } catch {
        continue
      }
      if (event === 'token') handlers.onToken(payload.text ?? '')
      else if (event === 'done') handlers.onDone(payload)
      else if (event === 'error') handlers.onError(payload.message ?? '生成失败')
    }
  }
}
