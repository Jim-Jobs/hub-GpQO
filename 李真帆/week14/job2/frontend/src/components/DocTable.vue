<template>
  <a-table
    :data-source="store.items"
    :columns="columns"
    :loading="store.loading"
    row-key="id"
    :pagination="{
      current: store.page,
      pageSize: store.size,
      total: store.total,
      showSizeChanger: false,
      showTotal: (t: number) => `共 ${t} 条`,
      onChange: (p: number) => store.changePage(p),
    }"
  >
    <template #bodyCell="{ column, record }">
      <template v-if="column.key === 'file_name'">
        <a-tooltip :title="record.file_name">
          <span>{{ record.file_name }}</span>
        </a-tooltip>
      </template>
      <template v-else-if="column.key === 'description'">
        <a-tooltip :title="record.description">
          <span>{{ record.description || '-' }}</span>
        </a-tooltip>
      </template>
      <template v-else-if="column.key === 'file_size'">
        {{ formatSize(record.file_size) }}
      </template>
      <template v-else-if="column.key === 'status'">
        <a-tag v-if="record.status === 'pending'">待解析</a-tag>
        <a-tag v-else-if="record.status === 'parsing'" color="processing">
          <loading-outlined />&nbsp;解析中
        </a-tag>
        <a-tag v-else-if="record.status === 'completed'" color="success">解析完成</a-tag>
        <a-tooltip v-else :title="record.error_msg">
          <a-tag color="error">解析失败</a-tag>
        </a-tooltip>
      </template>
      <template v-else-if="column.key === 'created_at'">
        {{ formatTime(record.created_at) }}
      </template>
      <template v-else-if="column.key === 'actions'">
        <a-space>
          <a
            v-if="record.status === 'failed' || record.status === 'completed'"
            @click="onReparse(record)"
          >重新解析</a>
          <a-popconfirm title="删除后知识库将不再包含该文档，确认删除？" @confirm="onDelete(record)">
            <a class="danger">删除</a>
          </a-popconfirm>
        </a-space>
      </template>
    </template>
  </a-table>
</template>

<script setup lang="ts">
import { LoadingOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { useDocumentsStore } from '@/stores/documents'
import type { DocItem } from '@/types'
import { formatSize, formatTime } from '@/utils/format'

const store = useDocumentsStore()

const columns = [
  { title: '文件名', dataIndex: 'file_name', key: 'file_name', width: 220, ellipsis: true },
  { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
  { title: '大小', dataIndex: 'file_size', key: 'file_size', width: 90 },
  { title: '状态', key: 'status', width: 110 },
  { title: '切片数', dataIndex: 'chunk_count', key: 'chunk_count', width: 80 },
  { title: '上传时间', key: 'created_at', width: 170 },
  { title: '操作', key: 'actions', width: 150 },
]

async function onReparse(record: DocItem) {
  try {
    await store.reparse(record.id)
    message.success('已重新加入解析队列')
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : '操作失败')
  }
}

async function onDelete(record: DocItem) {
  try {
    await store.remove(record.id)
    message.success('已删除')
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : '删除失败')
  }
}
</script>

<style scoped>
.danger {
  color: #ff4d4f;
}
</style>
