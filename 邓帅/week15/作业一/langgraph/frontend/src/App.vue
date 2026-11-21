<script setup>
import { nextTick, onMounted, reactive, ref } from "vue";
import { marked } from "marked";
import { chatStream } from "./api.js";

/**
 * 情感陪伴 Pipeline（openai-agents + Temporal 套，5173）。
 *
 * 数据模型：
 * - threads：会话列表 + 各自消息（localStorage 持久化，刷新恢复会话）；
 * - messages：当前会话消息流。assistant 消息 = sentiment 徽标（label+reason）
 *   + text（delta 真流式逐批追加 = 打字机效果）+ error 红条。
 */

const STORAGE_KEY = "emotion-lg-threads";

const LABEL_META = {
  positive: { text: "开心", type: "success", emoji: "😄", desc: "正向 · 鼓励回应" },
  negative: { text: "低落", type: "danger", emoji: "🫂", desc: "负向 · 排解回应" },
  neutral: { text: "平静", type: "info", emoji: "🙂", desc: "中性 · 平和回应" },
};

const threads = ref([]);
const activeThreadId = ref(null);
const messages = reactive([]);
const question = ref("");
const sending = ref(false);
const listRef = ref(null);

function loadThreads() {
  try {
    threads.value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    threads.value = [];
  }
}

function saveThreads() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(threads.value.slice(0, 50)));
}

function persistMessages() {
  const t = threads.value.find((x) => x.id === activeThreadId.value);
  if (t) {
    t.messages = messages.map((m) => ({
      role: m.role,
      text: m.text,
      sentiment: m.sentiment,
      error: m.error,
    }));
    t.updatedAt = Date.now();
    saveThreads();
  }
}

function newThread() {
  activeThreadId.value = null;
  messages.length = 0;
}

function switchThread(id) {
  activeThreadId.value = id;
  messages.length = 0;
  const t = threads.value.find((x) => x.id === id);
  for (const m of t?.messages || []) {
    messages.push(reactive({ ...m }));
  }
  if (!messages.length) {
    messages.push({
      role: "system",
      text: `已切换会话（thread: ${id.slice(0, 13)}…），网关 SQLiteSession 仍持有该会话历史，直接输入即可延续上下文。`,
    });
  }
  scrollBottom();
}

function rememberThread(threadId, title) {
  const existing = threads.value.find((t) => t.id === threadId);
  if (existing) {
    existing.updatedAt = Date.now();
    activeThreadId.value = threadId;
  } else {
    threads.value.unshift({ id: threadId, title, updatedAt: Date.now(), messages: [] });
    activeThreadId.value = threadId;
  }
  saveThreads();
}

function scrollBottom() {
  nextTick(() => {
    const el = listRef.value?.wrapRef;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

async function send() {
  const text = question.value.trim();
  if (!text || sending.value) return;

  messages.push({ role: "user", text });
  question.value = "";
  sending.value = true;
  scrollBottom();

  const msg = reactive({
    role: "assistant",
    text: "",
    sentiment: null,
    error: null,
  });
  messages.push(msg);

  try {
    await chatStream(text, activeThreadId.value, ({ event, data }) => {
      if (event === "meta") {
        rememberThread(data.thread_id, text.slice(0, 18));
      } else if (event === "sentiment") {
        msg.sentiment = data;
        scrollBottom();
      } else if (event === "delta") {
        // delta 逐批真流式到达，直接追加即为打字机效果
        msg.text += data.text;
        scrollBottom();
      } else if (event === "error") {
        msg.error = data.message;
      }
    });
  } catch (err) {
    msg.error = String(err);
  } finally {
    sending.value = false;
    persistMessages();
    scrollBottom();
  }
}

function onKeydown(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

function renderMarkdown(text) {
  return marked.parse(text || "", { breaks: true });
}

onMounted(() => {
  loadThreads();
  // 演示辅助：#q=<输入> 自动发送；#t=<thread_id> 恢复会话（截图/冒烟用）
  const threadMatch = location.hash.match(/[#&]t=([^&]+)/);
  const match = location.hash.match(/[#&]q=([^&]+)/);
  if (threadMatch) {
    switchThread(decodeURIComponent(threadMatch[1]));
  } else if (match) {
    question.value = decodeURIComponent(match[1]);
    send();
  }
});
</script>

<template>
  <el-container class="shell">
    <el-aside width="240px" class="aside">
      <div class="brand">
        <div class="brand-title">情感陪伴 Pipeline</div>
        <div class="brand-sub">langgraph · StateGraph 直连编排</div>
      </div>
      <el-button type="primary" plain class="new-btn" @click="newThread">
        ＋ 新会话
      </el-button>
      <el-scrollbar class="thread-list">
        <div
          v-for="t in threads"
          :key="t.id"
          class="thread-item"
          :class="{ active: t.id === activeThreadId }"
          :title="t.title"
          @click="switchThread(t.id)"
        >
          <span class="thread-title">{{ t.title }}</span>
        </div>
      </el-scrollbar>
      <div class="aside-foot">识别→回应 · 多进程 · 图编排</div>
    </el-aside>

    <el-container class="main">
      <el-header class="header" height="52px">
        <span>说说你的近况，我会先识别情绪再回应</span>
        <span class="header-hint">Enter 发送 · Shift+Enter 换行</span>
      </el-header>

      <el-main class="chat">
        <el-scrollbar ref="listRef" class="scroller">
          <div v-if="messages.length === 0" class="empty">
            <el-empty description="随便聊聊，例如：今天面试通过了，特别开心！" />
          </div>

          <div v-for="(m, i) in messages" :key="i" class="row" :class="m.role">
            <!-- 用户气泡 -->
            <div v-if="m.role === 'user'" class="bubble user-bubble">{{ m.text }}</div>

            <!-- 系统提示 -->
            <div v-else-if="m.role === 'system'" class="system-tip">{{ m.text }}</div>

            <!-- 助手消息 -->
            <div v-else class="bubble assistant-bubble">
              <!-- 情感徽标（回应开始前展示，三样式 + reason） -->
              <div v-if="m.sentiment" class="sentiment-badge">
                <el-tag :type="LABEL_META[m.sentiment.label]?.type || 'info'" effect="dark" size="small" round>
                  {{ LABEL_META[m.sentiment.label]?.emoji }} 识别到{{ LABEL_META[m.sentiment.label]?.text }}
                </el-tag>
                <span class="sentiment-reason">{{ m.sentiment.reason }}</span>
              </div>

              <!-- 回应文本（delta 真流式追加） -->
              <div
                v-if="m.text"
                class="answer"
                v-html="renderMarkdown(m.text)"
              ></div>
              <div v-else-if="sending && i === messages.length - 1 && !m.error" class="typing">
                <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
              </div>

              <!-- 错误红条 -->
              <el-alert
                v-if="m.error"
                type="error"
                :closable="false"
                show-icon
                title="本轮回应失败"
                :description="m.error"
              />
            </div>
          </div>
        </el-scrollbar>
      </el-main>

      <el-footer class="input-bar" height="auto">
        <div class="input-row">
          <el-input
            v-model="question"
            type="textarea"
            :rows="2"
            resize="none"
            placeholder="说说最近的开心或烦心事……"
            :disabled="sending"
            @keydown="onKeydown"
          />
          <el-button type="primary" :loading="sending" @click="send">
            发送
          </el-button>
        </div>
      </el-footer>
    </el-container>
  </el-container>
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
}
#app {
  font-family: "PingFang SC", "Helvetica Neue", Arial, sans-serif;
}
</style>

<style scoped>
.shell {
  height: 100%;
}
.aside {
  border-right: 1px solid var(--el-border-color-light);
  display: flex;
  flex-direction: column;
  padding: 14px 12px;
  background: #fafbfc;
}
.brand {
  padding: 4px 6px 12px;
}
.brand-title {
  font-weight: 600;
  font-size: 15px;
}
.brand-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
.new-btn {
  width: 100%;
  margin-bottom: 12px;
}
.thread-list {
  flex: 1;
}
.thread-item {
  padding: 9px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--el-text-color-regular);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.thread-item:hover {
  background: var(--el-fill-color-light);
}
.thread-item.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.aside-foot {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  padding-top: 10px;
}
.main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--el-border-color-light);
  font-size: 14px;
  font-weight: 500;
}
.header-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-placeholder);
}
.chat {
  flex: 1;
  min-height: 0;
  padding: 8px 0;
}
.scroller {
  height: 100%;
}
.empty {
  padding-top: 12vh;
}
.row {
  display: flex;
  margin: 10px 18px;
}
.row.user {
  justify-content: flex-end;
}
.row.system {
  justify-content: center;
}
.system-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 4px 12px;
  border-radius: 10px;
  white-space: pre-line;
}
.bubble {
  max-width: 76%;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.65;
}
.user-bubble {
  background: var(--el-color-primary);
  color: #fff;
  white-space: pre-wrap;
}
.assistant-bubble {
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  min-width: 220px;
}
.sentiment-badge {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}
.sentiment-reason {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.typing {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}
.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-text-color-placeholder);
  animation: blink 1.2s infinite ease-in-out;
}
.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}
.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes blink {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}
.input-bar {
  border-top: 1px solid var(--el-border-color-light);
  padding: 12px 18px 14px;
  background: #fff;
}
.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.input-row .el-button {
  height: 54px;
}
</style>
