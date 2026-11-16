<template>
  <a-spin :spinning="uploading">
    <a-upload-dragger
      :file-list="[]"
      :multiple="true"
      accept=".pdf"
      :show-upload-list="false"
      :before-upload="handleBeforeUpload"
    >
      <p class="ant-upload-drag-icon"><inbox-outlined /></p>
      <p class="ant-upload-text">点击或拖拽 PDF 文件到此处上传（可多选）</p>
      <p class="ant-upload-hint">上传后进入待解析队列，解析完成后即可用于问答</p>
    </a-upload-dragger>
  </a-spin>
</template>

<script setup lang="ts">
import { InboxOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { ref } from 'vue'
import { useDocumentsStore } from '@/stores/documents'

const store = useDocumentsStore()
const uploading = ref(false)
const pending: File[] = []
let timer: ReturnType<typeof setTimeout> | null = null

function handleBeforeUpload(file: File) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    message.warning(`已跳过非 PDF 文件: ${file.name}`)
    return false
  }
  pending.push(file)
  if (timer) clearTimeout(timer)
  timer = setTimeout(flush, 200)
  return false
}

async function flush() {
  const files = pending.splice(0)
  if (!files.length) return
  uploading.value = true
  try {
    const saved = await store.upload(files)
    message.success(`已上传 ${saved.length} 个文档，等待解析`)
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : '上传失败')
  } finally {
    uploading.value = false
  }
}
</script>
