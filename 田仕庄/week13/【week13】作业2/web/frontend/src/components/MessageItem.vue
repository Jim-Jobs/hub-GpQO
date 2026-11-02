<script setup>
import { computed } from 'vue'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: false })

const props = defineProps({
  message: { type: Object, required: true },
  /** 实时等待秒数（仅对正在流式输出的消息传值） */
  waitElapsed: { type: Number, default: 0 },
})

/** 查询结果（Markdown 表格）渲染为 HTML */
const resultHtml = computed(() => {
  if (!props.message.result) return ''
  return marked.parse(props.message.result)
})

/** 节点中文名（执行过程时间线用） */
const NODE_ICONS = {
  get_schema: '🔍',
  write_query: '✍️',
  optimize_query: '✨',
  review_query: '🧐',
  execute_query: '⚡',
  rewrite_query: '🔧',
  generate_answer: '💬',
}
</script>

<template>
  <div class="msg" :class="message.role">
    <!-- 用户消息：右侧气泡 -->
    <div v-if="message.role === 'user'" class="bubble-user">{{ message.text }}</div>

    <!-- 助手消息：过程卡片 + 回答 -->
    <div v-else class="bubble-assistant">
      <!-- 执行过程时间线 -->
      <div v-if="message.nodes.length" class="steps">
        <div class="steps-title">执行过程</div>
        <div class="steps-list">
          <span
            v-for="(n, i) in message.nodes"
            :key="i"
            class="step-chip"
            :title="`${n.label} · ${n.elapsed}s`"
          >
            <i>{{ NODE_ICONS[n.node] || '⚙️' }}</i>
            {{ n.label }}
            <em v-if="n.elapsed != null">{{ n.elapsed }}s</em>
          </span>
          <span v-if="message.streaming" class="step-chip active">
            <i class="dot"></i>进行中…
          </span>
        </div>
      </div>

      <!-- 生成的 SQL -->
      <div v-if="message.sql" class="sql-block">
        <div class="sql-head">
          <span>SQL</span>
          <button
            v-if="message.sql"
            class="copy-btn"
            title="复制 SQL"
            @click="navigator.clipboard && navigator.clipboard.writeText(message.sql)"
          >
            复制
          </button>
        </div>
        <pre><code>{{ message.sql }}</code></pre>
      </div>

      <!-- 执行错误 -->
      <div v-if="message.error" class="error-banner">⚠️ {{ message.error }}</div>

      <!-- 查询结果表格 -->
      <div v-if="resultHtml" class="table-wrap">
        <div class="table-head">查询结果</div>
        <!-- 结果由服务端 db_parser 从可信数据库生成，仅含表格/文本 -->
        <div class="markdown-body" v-html="resultHtml"></div>
      </div>

      <!-- 自然语言回答（流式） -->
      <div v-if="message.text" class="answer">
        {{ message.text }}<span v-if="message.streaming" class="cursor"></span>
      </div>
      <div v-else-if="message.streaming" class="thinking">
        <span class="dot"></span><span class="dot"></span><span class="dot"></span>
        正在查询…<em v-if="waitElapsed">{{ waitElapsed.toFixed(1) }}s</em>
      </div>

      <!-- 底部元信息 -->
      <div v-if="!message.streaming && message.elapsed != null" class="meta">
        <span v-if="message.cached" class="cache-tag">
          ⚡ 缓存命中 · 原耗时 {{ message.originElapsed }}s ·
        </span>
        ⏱ 共耗时 {{ message.elapsed }}s
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  margin: 18px 0;
}
.msg.user {
  justify-content: flex-end;
}
.msg.assistant {
  justify-content: flex-start;
}

/* 用户气泡 */
.bubble-user {
  max-width: 72%;
  background: linear-gradient(135deg, var(--accent) 0%, #6a5cff 100%);
  color: #fff;
  padding: 10px 16px;
  border-radius: 14px 14px 4px 14px;
  box-shadow: 0 4px 12px rgba(79, 107, 247, 0.25);
  white-space: pre-wrap;
  word-break: break-word;
}

/* 助手卡片 */
.bubble-assistant {
  width: 100%;
  max-width: 92%;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 4px 14px 14px 14px;
  box-shadow: var(--shadow);
  padding: 14px 18px;
}

/* 执行过程 */
.steps {
  margin-bottom: 12px;
}
.steps-title {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 6px;
  letter-spacing: 0.05em;
}
.steps-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.step-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-2);
  background: #f6f8fc;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 10px;
}
.step-chip i {
  font-style: normal;
}
.step-chip em {
  font-style: normal;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
.step-chip.active {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
}

/* SQL 代码块 */
.sql-block {
  border-radius: 10px;
  overflow: hidden;
  margin: 10px 0;
  border: 1px solid #2c3a52;
}
.sql-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #172033;
  color: #9fb3d1;
  font-size: 12px;
  padding: 5px 12px;
  letter-spacing: 0.1em;
}
.copy-btn {
  background: transparent;
  border: 1px solid #3b4a66;
  color: #9fb3d1;
  border-radius: 6px;
  font-size: 11px;
  padding: 1px 8px;
  cursor: pointer;
}
.copy-btn:hover {
  color: #fff;
  border-color: #6b7fa5;
}
.sql-block pre {
  margin: 0;
  background: var(--code-bg);
  color: #dbe7ff;
  padding: 12px 14px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
  font-family: 'JetBrains Mono', Consolas, 'Courier New', monospace;
}

/* 错误横幅 */
.error-banner {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: var(--err);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  margin: 10px 0;
  word-break: break-all;
}

/* 结果表格 */
.table-wrap {
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  margin: 10px 0;
}
.table-head {
  font-size: 12px;
  color: var(--text-3);
  background: #f8fafd;
  padding: 5px 12px;
  border-bottom: 1px solid var(--border);
  letter-spacing: 0.05em;
}
.markdown-body {
  max-height: 340px;
  overflow: auto;
}
.markdown-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
}
.markdown-body :deep(th) {
  position: sticky;
  top: 0;
  background: #f1f4fa;
  color: var(--text-2);
  font-weight: 600;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.markdown-body :deep(td) {
  padding: 5px 12px;
  border-bottom: 1px solid #f0f2f7;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.markdown-body :deep(tr:last-child td) {
  border-bottom: none;
}
.markdown-body :deep(tr:hover td) {
  background: #f8faff;
}

/* 回答文本 */
.answer {
  font-size: 15px;
  white-space: pre-wrap;
  word-break: break-word;
  padding: 2px 0;
}
.cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--accent);
  margin-left: 2px;
  vertical-align: -0.15em;
  animation: blink 0.9s steps(1) infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}

/* 思考动画 */
.thinking {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-3);
  font-size: 13px;
  padding: 4px 0;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  display: inline-block;
}
.thinking em {
  font-style: normal;
  color: var(--accent);
  margin-left: 4px;
  font-variant-numeric: tabular-nums;
}
.thinking .dot {
  animation: bounce 1.2s ease-in-out infinite;
}
.thinking .dot:nth-child(2) {
  animation-delay: 0.15s;
}
.thinking .dot:nth-child(3) {
  animation-delay: 0.3s;
}
.step-chip .dot {
  width: 5px;
  height: 5px;
}
@keyframes bounce {
  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  40% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

/* 元信息 */
.meta {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-3);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.cache-tag {
  color: var(--accent);
}
</style>
