export type DocStatus = 'pending' | 'parsing' | 'completed' | 'failed'

export interface DocItem {
  id: string
  file_name: string
  file_size: number
  file_path: string
  description: string
  status: DocStatus
  error_msg: string
  page_count: number
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface DocList {
  total: number
  items: DocItem[]
}

export interface SessionItem {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ChatRef {
  doc_name: string
  heading: string
  chunk_type: string
  score: number
}

export interface MessageMeta {
  related: boolean
  reason: string
  model: string
  refs: ChatRef[]
}

export interface ChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  content: string
  image_path: string
  meta: MessageMeta | null
  created_at: string
}
