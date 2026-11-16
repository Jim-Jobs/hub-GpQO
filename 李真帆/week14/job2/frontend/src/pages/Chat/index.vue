<template>
  <div class="chat-page">
    <aside class="side">
      <SessionList />
    </aside>
    <main class="main">
      <MessageList />
      <ChatInput />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import ChatInput from '@/components/ChatInput.vue'
import MessageList from '@/components/MessageList.vue'
import SessionList from '@/components/SessionList.vue'
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()

onMounted(() => {
  chat.init()
})
</script>

<style scoped>
.chat-page {
  display: flex;
  height: calc(100vh - 64px);
}
.side {
  width: 240px;
  flex-shrink: 0;
  border-right: 1px solid #f0f0f0;
  background: #fafafa;
}
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
</style>
