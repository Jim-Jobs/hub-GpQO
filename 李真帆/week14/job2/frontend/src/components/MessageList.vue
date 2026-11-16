<template>
  <div ref="listRef" class="message-list">
    <a-empty v-if="chat.messages.length === 0" description="向知识库提问吧，支持文字 + 图片" class="empty" />
    <div v-for="m in chat.messages" :key="m.id" class="msg" :class="m.role">
      <div class="bubble">
        <a-image
          v-if="m.role === 'user' && m.image_path"
          :src="staticUrl(m.image_path)"
          :width="180"
          class="user-img"
        />
        <div v-if="m.role === 'user'" class="text">{{ m.content }}</div>
        <div v-else class="md-content" v-html="renderMarkdown(m.content)"></div>
        <div v-if="m.role === 'assistant' && !m.content && chat.streaming" class="typing">思考中…</div>
        <div v-if="m.role === 'assistant' && m.meta" class="refs">
          <a-tooltip :title="m.meta.reason">
            <a-tag :color="m.meta.related ? 'green' : 'orange'">
              {{ m.meta.related ? '结合知识库回答' : '与知识库无关，自由回答' }}
            </a-tag>
          </a-tooltip>
          <a-tooltip
            v-for="(r, i) in m.meta.refs"
            :key="i"
            :title="`${r.heading || '（无标题）'} 相似度 ${r.score}`"
          >
            <a-tag class="ref-tag">{{ r.doc_name }}</a-tag>
          </a-tooltip>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import { staticUrl } from '@/utils/format'
import { renderMarkdown } from '@/utils/markdown'

const chat = useChatStore()
const listRef = ref<HTMLElement>()

watch(
  () => chat.messages.map((m) => m.content).join(''),
  async () => {
    await nextTick()
    if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
  },
)
</script>

<style scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}
.empty {
  margin-top: 120px;
}
.msg {
  display: flex;
  margin-bottom: 16px;
}
.msg.user {
  justify-content: flex-end;
}
.msg.assistant {
  justify-content: flex-start;
}
.bubble {
  max-width: 76%;
  padding: 10px 14px;
  border-radius: 10px;
  background: #f5f5f5;
  word-break: break-word;
}
.msg.user .bubble {
  background: #e6f4ff;
}
.user-img {
  margin-bottom: 8px;
  border-radius: 6px;
}
.text {
  white-space: pre-wrap;
}
.typing {
  color: #999;
}
.refs {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.ref-tag {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}
:deep(.md-content img) {
  max-width: 100%;
  border-radius: 6px;
}
:deep(.md-content table) {
  border-collapse: collapse;
  margin: 8px 0;
}
:deep(.md-content th),
:deep(.md-content td) {
  border: 1px solid #e8e8e8;
  padding: 4px 10px;
}
:deep(.md-content p) {
  margin: 6px 0;
}
</style>
