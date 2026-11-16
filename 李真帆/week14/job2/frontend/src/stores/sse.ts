import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface SseEvent {
  type: string
  payload: any
}

export const useSseStore = defineStore('sse', () => {
  const connected = ref(false)
  const listeners = new Set<(evt: SseEvent) => void>()
  let started = false

  function onEvent(fn: (evt: SseEvent) => void): () => void {
    listeners.add(fn)
    return () => listeners.delete(fn)
  }

  function emit(evt: SseEvent) {
    listeners.forEach((l) => l(evt))
  }

  function connect() {
    if (started || typeof EventSource === 'undefined') return
    started = true
    const es = new EventSource('/api/events')
    // 每次(重)连成功都广播 _open，订阅方据此全量刷新，兜底断线期间的漏推
    es.onopen = () => {
      connected.value = true
      emit({ type: '_open', payload: null })
    }
    es.onmessage = (ev: MessageEvent) => {
      try {
        emit(JSON.parse(ev.data))
      } catch {
        /* 忽略非法帧 */
      }
    }
    es.onerror = () => {
      connected.value = false
      // EventSource 自动重连
    }
  }

  return { connected, onEvent, connect }
})
