<template>
  <div class="chat-input">
    <a-alert
      v-if="chat.errorText"
      type="error"
      :message="chat.errorText"
      closable
      class="err"
      @close="chat.errorText = ''"
    />
    <div v-if="image" class="img-preview">
      <img :src="image.url" alt="待发送图片" />
      <close-circle-filled class="rm" @click="clearImage" />
    </div>
    <div class="row">
      <a-textarea
        v-model:value="text"
        :auto-size="{ minRows: 1, maxRows: 6 }"
        :disabled="chat.streaming"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        @keydown="onKeydown"
      />
      <a-upload
        accept=".jpg,.jpeg,.png,.gif,.webp"
        :show-upload-list="false"
        :before-upload="pickImage"
        :disabled="chat.streaming"
      >
        <a-button :disabled="chat.streaming" title="附加图片">
          <paper-clip-outlined />
        </a-button>
      </a-upload>
      <a-button
        v-if="chat.streaming"
        danger
        @click="chat.stop()"
      >停止</a-button>
      <a-button
        v-else
        type="primary"
        :disabled="!canSend"
        @click="send"
      >发送</a-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { CloseCircleFilled, PaperClipOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { computed, ref } from 'vue'
import { useChatStore, type PendingImage } from '@/stores/chat'

const MAX_IMAGE_SIZE = 10 * 1024 * 1024

const chat = useChatStore()
const text = ref('')
const image = ref<PendingImage | null>(null)

const canSend = computed(() => text.value.trim().length > 0 && !!chat.currentId)

function pickImage(file: File) {
  if (file.size > MAX_IMAGE_SIZE) {
    message.error('图片不能超过 10MB')
    return false
  }
  if (image.value) URL.revokeObjectURL(image.value.url)
  image.value = { file, url: URL.createObjectURL(file) }
  return false
}

function clearImage() {
  if (image.value) URL.revokeObjectURL(image.value.url)
  image.value = null
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function send() {
  if (!canSend.value || chat.streaming) return
  chat.send(text.value.trim(), image.value)
  text.value = ''
  image.value = null
}
</script>

<style scoped>
.chat-input {
  border-top: 1px solid #f0f0f0;
  padding: 12px 24px 16px;
  background: #fff;
}
.err {
  margin-bottom: 8px;
}
.img-preview {
  position: relative;
  display: inline-block;
  margin-bottom: 8px;
}
.img-preview img {
  height: 64px;
  border-radius: 6px;
  border: 1px solid #eee;
}
.img-preview .rm {
  position: absolute;
  top: -6px;
  right: -6px;
  color: #999;
  cursor: pointer;
  background: #fff;
  border-radius: 50%;
}
.img-preview .rm:hover {
  color: #ff4d4f;
}
.row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}
</style>
