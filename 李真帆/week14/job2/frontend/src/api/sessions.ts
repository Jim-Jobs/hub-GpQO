import type { ChatMessage, SessionItem } from '@/types'
import { request } from './request'

export function listSessions(): Promise<SessionItem[]> {
  return request('/api/sessions')
}

export function createSession(): Promise<SessionItem> {
  return request('/api/sessions', { method: 'POST' })
}

export function deleteSession(id: string): Promise<{ ok: boolean }> {
  return request(`/api/sessions/${id}`, { method: 'DELETE' })
}

export function listMessages(sessionId: string): Promise<ChatMessage[]> {
  return request(`/api/sessions/${sessionId}/messages`)
}
