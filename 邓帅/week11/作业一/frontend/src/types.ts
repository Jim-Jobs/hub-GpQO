// 后端 SSE 事件与 history/schema 记录的类型定义(与 runner.py 事件协议对齐)

export type StepStatus = 'start' | 'done'

export interface StepEvent {
  event: 'step'
  step: number // 1..7
  name: string // 步骤中文名
  status: StepStatus
  data: Record<string, unknown>
}

export interface FinalEvent {
  event: 'final'
  status: 'success' | 'failed'
  question: string
  tables?: string[]
  sql?: string
  columns?: string[]
  rows?: unknown[][]
  row_count?: number
  truncated?: boolean
  rewrite_rounds: number
  summary?: string
  error?: string
  duration_ms: number
}

export type AgentEvent = StepEvent | FinalEvent

export interface HistoryRecord {
  id: number
  question: string
  status: 'success' | 'failed' | string
  tables: string[]
  sql: string
  summary: string
  error: string
  columns: string[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  rewrite_rounds: number
  duration_ms: number
  steps: StepEvent[]
  created_at: number
}

export interface SchemaInfo {
  tables: { name: string; fields: string[] }[]
  relations: number
}
