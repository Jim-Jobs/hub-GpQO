import { streamChat } from '@/api/chat'
import * as api from '@/api/sessions'
import type { ChatMessage, SessionItem } from '@/types'
import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface PendingImage {
  file: File
  url: string
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<SessionItem[]>([])
  const currentId = ref('')
  const messages = ref<ChatMessage[]>([])
  const streaming = ref(false)
  const errorText = ref('')
  let controller: AbortController | null = null

  async function init() {
    await loadSessions()
    if (sessions.value.length === 0) {
      await newSession()
    } else {
      await select(sessions.value[0].id)
    }
  }

  async function loadSessions() {
    sessions.value = await api.listSessions()
  }

  async function newSession() {
    const s = await api.createSession()
    sessions.value.unshift(s)
    await select(s.id)
  }

  async function select(id: string) {
    currentId.value = id
    messages.value = await api.listMessages(id)
  }

  async function removeSession(id: string) {
    await api.deleteSession(id)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    if (currentId.value === id) {
      if (sessions.value.length > 0) await select(sessions.value[0].id)
      else await newSession()
    }
  }

  function stop() {
    controller?.abort()
  }

  async function send(content: string, image: PendingImage | null) {
    if (!currentId.value || streaming.value) return
    streaming.value = true
    errorText.value = ''
    const now = new Date().toISOString()
    messages.value.push({
      id: `local-user-${Date.now()}`,
      session_id: currentId.value,
      role: 'user',
      content,
      image_path: image?.url ?? '',
      meta: null,
      created_at: now,
    })
    const assistant: ChatMessage = {
      id: `local-assistant-${Date.now()}`,
      session_id: currentId.value,
      role: 'assistant',
      content: '',
      image_path: '',
      meta: null,
      created_at: now,
    }
    messages.value.push(assistant)

    const form = new FormData()
    form.append('session_id', currentId.value)
    form.append('content', content)
    if (image) form.append('image', image.file)

    controller = new AbortController()
    try {
      await streamChat(
        form,
        {
          onToken: (t) => {
            assistant.content += t
          },
          onDone: (d) => {
            assistant.id = d.message_id
            assistant.meta = d.meta
          },
          onError: (m) => {
            errorText.value = m
          },
        },
        controller.signal,
      )
      await loadSessions()
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        errorText.value = '已中断生成'
      } else {
        errorText.value = e instanceof Error ? e.message : String(e)
      }
    } finally {
      streaming.value = false
      controller = null
    }
  }

  return {
    sessions, currentId, messages, streaming, errorText,
    init, loadSessions, newSession, select, removeSession, send, stop,
  }
})
