import * as api from '@/api/documents'
import type { DocItem, DocStatus } from '@/types'
import { defineStore } from 'pinia'
import { ref } from 'vue'

export type StatusFilter = '' | DocStatus

export const useDocumentsStore = defineStore('documents', () => {
  const items = ref<DocItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const size = ref(10)
  const filter = ref<StatusFilter>('')
  const loading = ref(false)

  async function fetch() {
    loading.value = true
    try {
      const res = await api.listDocuments({
        page: page.value,
        size: size.value,
        status: filter.value || undefined,
      })
      items.value = res.items
      total.value = res.total
    } finally {
      loading.value = false
    }
  }

  let refetchTimer: ReturnType<typeof setTimeout> | null = null
  function refetchSoon() {
    if (refetchTimer) return
    refetchTimer = setTimeout(() => {
      refetchTimer = null
      fetch()
    }, 400)
  }

  /** SSE doc_status 事件：权威状态在服务端，本地按 updated_at 丢弃乱序旧消息 */
  function applyStatus(p: Partial<DocItem> & { id: string; updated_at: string; status: DocStatus }) {
    const idx = items.value.findIndex((d) => d.id === p.id)
    if (idx >= 0) {
      const cur = items.value[idx]
      if (p.updated_at > cur.updated_at) {
        items.value[idx] = { ...cur, ...p }
        if (filter.value && p.status !== filter.value) refetchSoon()
      }
      return
    }
    const matchesFilter = !filter.value || p.status === filter.value
    if (page.value === 1 && matchesFilter) refetchSoon()
  }

  function applyDeleted(id: string) {
    const idx = items.value.findIndex((d) => d.id === id)
    if (idx >= 0) {
      items.value.splice(idx, 1)
      total.value = Math.max(0, total.value - 1)
    } else {
      refetchSoon()
    }
  }

  async function upload(files: File[]) {
    const saved = await api.uploadDocuments(files)
    if (page.value === 1 && !filter.value) await fetch()
    return saved
  }

  async function remove(id: string) {
    await api.deleteDocument(id)
    applyDeleted(id)
  }

  async function reparse(id: string) {
    await api.reparseDocument(id)
    await fetch()
  }

  function changePage(p: number) {
    page.value = p
    fetch()
  }

  function changeFilter(f: StatusFilter) {
    filter.value = f
    page.value = 1
    fetch()
  }

  return {
    items, total, page, size, filter, loading,
    fetch, applyStatus, applyDeleted, upload, remove, reparse, changePage, changeFilter,
  }
})
