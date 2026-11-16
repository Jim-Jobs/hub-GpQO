<template>
  <div class="kb-page">
    <a-alert
      v-if="!sse.connected"
      class="mb16"
      type="warning"
      show-icon
      message="实时推送可能被网关缓冲，列表每 5~15 秒自动刷新"
    />
    <UploadCard class="mb16" />
    <div class="toolbar mb16">
      <a-radio-group :value="store.filter" @change="onFilter">
        <a-radio-button value="">全部</a-radio-button>
        <a-radio-button value="pending">待解析</a-radio-button>
        <a-radio-button value="parsing">解析中</a-radio-button>
        <a-radio-button value="completed">解析完成</a-radio-button>
        <a-radio-button value="failed">解析失败</a-radio-button>
      </a-radio-group>
      <a-button @click="store.fetch()">刷新</a-button>
    </div>
    <DocTable />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import DocTable from '@/components/DocTable.vue'
import UploadCard from '@/components/UploadCard.vue'
import { useDocumentsStore, type StatusFilter } from '@/stores/documents'
import { useSseStore } from '@/stores/sse'

const sse = useSseStore()
const store = useDocumentsStore()

let off: (() => void) | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// SSE 优先；网关可能缓冲 SSE 数据（连接成功但事件延迟），故保留慢轮询兜底
function togglePolling() {
  stopPolling()
  const interval = sse.connected ? 15000 : 5000
  pollTimer = setInterval(() => store.fetch(), interval)
}

onMounted(() => {
  sse.connect()
  off = sse.onEvent((m) => {
    if (m.type === 'doc_status') store.applyStatus(m.payload)
    else if (m.type === 'doc_deleted') store.applyDeleted(m.payload?.id)
    else if (m.type === '_open') store.fetch()
  })
  watch(() => sse.connected, togglePolling, { immediate: true })
  store.fetch()
})

onUnmounted(() => {
  off?.()
  stopPolling()
})

function onFilter(e: any) {
  store.changeFilter(e.target.value as StatusFilter)
}
</script>

<style scoped>
.kb-page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
}
.mb16 {
  margin-bottom: 16px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
