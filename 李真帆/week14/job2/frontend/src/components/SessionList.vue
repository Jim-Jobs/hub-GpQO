<template>
  <div class="session-list">
    <div class="new-btn">
      <a-button type="primary" block @click="chat.newSession()">+ 新会话</a-button>
    </div>
    <div class="items">
      <div
        v-for="s in chat.sessions"
        :key="s.id"
        class="item"
        :class="{ active: s.id === chat.currentId }"
        @click="chat.select(s.id)"
      >
        <span class="title" :title="s.title">{{ s.title }}</span>
        <a-popconfirm title="删除该会话及全部消息？" @confirm="chat.removeSession(s.id)">
          <delete-outlined class="del" @click.stop />
        </a-popconfirm>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { DeleteOutlined } from '@ant-design/icons-vue'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()
</script>

<style scoped>
.session-list {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.new-btn {
  padding: 12px;
}
.items {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 8px;
}
.item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
}
.item:hover {
  background: #f0f0f0;
}
.item.active {
  background: #e6f4ff;
}
.title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.del {
  color: #999;
  flex-shrink: 0;
}
.del:hover {
  color: #ff4d4f;
}
</style>
