import type { DocItem, DocList } from '@/types'
import { request } from './request'

export async function uploadDocuments(files: File[]): Promise<DocItem[]> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  return request<DocItem[]>('/api/documents', { method: 'POST', body: form })
}

export async function listDocuments(params: { page: number; size: number; status?: string }): Promise<DocList> {
  const q = new URLSearchParams({ page: String(params.page), size: String(params.size) })
  if (params.status) q.set('status', params.status)
  return request<DocList>(`/api/documents?${q.toString()}`)
}

export function deleteDocument(id: string): Promise<{ ok: boolean }> {
  return request(`/api/documents/${id}`, { method: 'DELETE' })
}

export function reparseDocument(id: string): Promise<DocItem> {
  return request(`/api/documents/${id}/reparse`, { method: 'POST' })
}
