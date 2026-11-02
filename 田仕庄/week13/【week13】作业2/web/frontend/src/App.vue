<script setup>
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import MessageItem from './components/MessageItem.vue'
import {
  backendOrigin,
  checkHealth,
  isFileProtocol,
  newSession,
  streamChat,
} from './lib/api.js'

/** 示例问题（空状态展示，点击直接提问） */
const EXAMPLES = [
  '销量最高的前 5 位艺术家是谁？',
  '每种音乐流派各有多少首歌曲？',
  '客户数量最多的前 5 个国家是哪些？',
  '专辑数量最多的 3 位艺术家及其专辑数',
]

const messages = ref([]) // 消息列表
const question = ref('') // 输入框内容
const loading = ref(false) // 是否有请求进行中
const threadId = ref('') // 会话 ID（多轮记忆）
const chatBody = ref(null) // 聊天滚动容器
const inputEl = ref(null)
// 极速模式（默认）：跳过评审回路，延迟更低；严谨模式：SQL 经评审把关后再执行
const fastMode = ref(localStorage.getItem('sqlagent.fast') !== '0')
const waitElapsed = ref(0) // 当前请求的实时等待秒数
// 后端连通性：null=检测中，true=正常，false=连不上（顶部显示排查指引）
const backendOk = ref(null)
const fileMode = isFileProtocol()

let tokenBuf = '' // 累积的流式 token（按帧上屏，避免每个字触发一次重排）
let rafId = null
let timerId = null

onMounted(async () => {
  await Promise.all([startNewSession(false), probeBackend()])
})

/** 探测后端连通性：失败时顶部横幅直接给出排查步骤，避免只有一句 Failed to fetch */
async function probeBackend() {
  backendOk.value = (await checkHealth()) !== null
}

/** 重新检测（用户在终端启动后端后点此按钮即可，无需刷新页面） */
async function retryProbe() {
  backendOk.value = null
  await Promise.all([startNewSession(false), probeBackend()])
}

onUnmounted(() => {
  if (rafId) cancelAnimationFrame(rafId)
  if (timerId) clearInterval(timerId)
})

/** 开启新会话（清空消息与记忆） */
async function startNewSession(clear = true) {
  try {
    threadId.value = await newSession()
  } catch {
    threadId.value = crypto.randomUUID().replaceAll('-', '')
  }
  if (clear) messages.value = []
}

function toggleFastMode() {
  fastMode.value = !fastMode.value
  localStorage.setItem('sqlagent.fast', fastMode.value ? '1' : '0')
}

/** 按帧批量上屏：把高频 token 合并为每帧一次 DOM 更新 */
function flushTokens() {
  rafId = null
  if (!tokenBuf) return
  const msg = messages.value[messages.value.length - 1]
  if (msg && msg.role === 'assistant') msg.text += tokenBuf
  tokenBuf = ''
  autoScroll()
}

/** 发送问题：走 SSE 流式接口，逐事件更新助手消息 */
async function send(text) {
  const q = (text ?? question.value).trim()
  if (!q || loading.value) return

  messages.value.push({ role: 'user', text: q })
  const msg = {
    role: 'assistant',
    text: '',
    sql: '',
    result: '',
    error: '',
    nodes: [],
    streaming: true,
    elapsed: null,
    cached: false,
    originElapsed: null,
  }
  messages.value.push(msg)
  question.value = ''
  loading.value = true
  waitElapsed.value = 0
  // 实时等待计时（100ms 刷新）：让用户明确感知"在进行中"
  const t0 = performance.now()
  timerId = setInterval(() => {
    waitElapsed.value = (performance.now() - t0) / 1000
  }, 200)
  scrollToBottom()

  try {
    await streamChat({
      question: q,
      threadId: threadId.value,
      fast: fastMode.value,
      onEvent: (event, data) => {
        switch (event) {
          case 'node':
            msg.nodes.push({ node: data.node, label: data.label, elapsed: data.elapsed })
            autoScroll()
            break
          case 'sql':
            msg.sql = data.sql
            autoScroll()
            break
          case 'result':
            msg.result = data.query_result || ''
            if (data.error) msg.error = data.error
            autoScroll()
            break
          case 'token':
            // 只累积 + 申请一帧，真正的 DOM 更新在 flushTokens 里做
            tokenBuf += data.text
            if (rafId === null) rafId = requestAnimationFrame(flushTokens)
            break
          case 'done':
            // 先把缓冲 token 上屏，再做兜底赋值：否则 rAF 未触发时
            // 完整 answer + 缓冲 token 会被叠加，答案出现两遍。
            flushTokens()
            if (!msg.text && data.answer) msg.text = data.answer
            msg.elapsed = data.elapsed
            msg.cached = !!data.cached
            msg.originElapsed = data.origin_elapsed ?? null
            if (data.error) msg.error = data.error
            break
          case 'error':
            msg.error = data.message || '处理出错'
            break
        }
      },
    })
  } catch (e) {
    msg.error = e.message || String(e)
  } finally {
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
    flushTokens() // 收尾：把剩余 token 一次性上屏
    clearInterval(timerId)
    timerId = null
    msg.streaming = false
    loading.value = false
    waitElapsed.value = 0
    scrollToBottom()
    inputEl.value?.focus()
  }
}

/** 立即滚到底部（发送/结束时用，忽略用户是否已上滑） */
function scrollToBottom() {
  nextTick(() => {
    const el = chatBody.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

/** 流式过程中的自动滚动：用户上滑查看历史时不再打断 */
function autoScroll() {
  nextTick(() => {
    const el = chatBody.value
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160
    if (nearBottom) el.scrollTop = el.scrollHeight
  })
}

/** 输入框自适应高度 + Ctrl/Cmd+Enter 发送 */
function onInput(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="shell">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="brand">
        <div class="logo">Q</div>
        <div class="titles">
          <h1>SQL Agent · 数据问答</h1>
          <p>自然语言 → SQL → 安全执行 → 智能回答（Chinook 示例库 · 只读）</p>
        </div>
      </div>
      <div class="actions">
        <button
          class="btn mode-btn"
          :class="{ on: fastMode }"
          :disabled="loading"
          :title="fastMode
            ? '极速模式：跳过评审回路，延迟更低（适合简单查询）'
            : '严谨模式：SQL 经评审把关后再执行，更稳（复杂查询推荐）'"
          @click="toggleFastMode()"
        >
          {{ fastMode ? '⚡ 极速模式' : '🛡 严谨模式' }}
        </button>
        <button class="btn" @click="startNewSession()" :disabled="loading">
          ✨ 新会话
        </button>
      </div>
    </header>

    <!-- 连通性告警：后端没起 / 用 file:// 打开页面时直接给出排查步骤 -->
    <div v-if="fileMode || backendOk === false" class="alert">
      <div class="alert-body">
        <strong>
          {{ fileMode ? '页面打开方式不正确' : '无法连接后端服务' }}
        </strong>
        <template v-if="fileMode">
          <p>
            当前是 <code>file://</code> 直接打开的静态文件，浏览器不允许请求后端接口。
            请先运行 <code>python server.py</code>，再访问
            <code>http://127.0.0.1:8000</code>。
          </p>
        </template>
        <template v-else>
          <p>
            页面已加载，但请求 <code>{{ backendOrigin() }}/api</code> 失败。请依次确认：
          </p>
          <ol>
            <li>终端已执行 <code>python server.py</code>，且没有报错退出（看到“启动 Web 服务”提示）</li>
            <li>浏览器地址是 <code>http://127.0.0.1:8000</code>（改过端口则与 .env 的 <code>WEB_PORT</code> 一致）</li>
            <li>后端终端未被关闭；若刚重启后端，点右侧按钮重新检测</li>
          </ol>
        </template>
      </div>
      <button class="btn retry-btn" :disabled="backendOk === null" @click="retryProbe()">
        {{ backendOk === null ? '检测中…' : '重新检测' }}
      </button>
    </div>

    <!-- 消息区 -->
    <main class="chat" ref="chatBody">
      <!-- 空状态欢迎卡片 -->
      <div v-if="!messages.length" class="welcome">
        <div class="welcome-icon">🗄️</div>
        <h2>你好，我是数据库查询助手</h2>
        <p>
          直接用自然语言提问，我会自动生成 SQL、安全执行并给出结论。
          <br />支持多轮对话——先问"销量最高的前 5 位艺术家"，再问"那前 10 名呢？"。
        </p>
        <div class="examples">
          <button
            v-for="ex in EXAMPLES"
            :key="ex"
            class="example-chip"
            @click="send(ex)"
          >
            {{ ex }}
          </button>
        </div>
      </div>

      <MessageItem
        v-for="(m, i) in messages"
        :key="i"
        :message="m"
        :wait-elapsed="i === messages.length - 1 ? waitElapsed : 0"
      />
    </main>

    <!-- 输入区 -->
    <footer class="composer">
      <div class="composer-inner">
        <textarea
          ref="inputEl"
          v-model="question"
          rows="1"
          placeholder="用自然语言描述你想查询的数据…（Enter 发送，Shift+Enter 换行）"
          :disabled="loading"
          @input="onInput"
          @keydown="onKeydown"
        ></textarea>
        <button
          class="btn btn-primary send-btn"
          :disabled="loading || !question.trim()"
          @click="send()"
        >
          {{ loading ? '回答中…' : '发送 ➤' }}
        </button>
      </div>
      <p class="hint">
        只读查询助手：仅支持 SELECT 查询，无法修改、删除或写入数据。
      </p>
    </footer>
  </div>
</template>

<style scoped>
.shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  max-width: 920px;
  margin: 0 auto;
  padding: 0 16px;
}

/* 顶栏 */
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 4px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  position: sticky;
  top: 0;
  z-index: 5;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.logo {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--accent) 0%, #6a5cff 100%);
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  font-family: monospace;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(79, 107, 247, 0.3);
}
.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mode-btn.on {
  border-color: #f59e0b;
  color: #b45309;
  background: #fff8ec;
}
.mode-btn.on:hover {
  border-color: #f59e0b;
  color: #b45309;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.18);
}
.titles h1 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
}
.titles p {
  margin: 1px 0 0;
  font-size: 12px;
  color: var(--text-3);
}

/* 连通性告警 */
.alert {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin: 10px 4px 0;
  padding: 12px 14px;
  border: 1px solid #fecaca;
  background: #fff5f5;
  border-radius: 12px;
  color: #7f1d1d;
  font-size: 13px;
  line-height: 1.6;
}
.alert-body {
  flex: 1;
}
.alert strong {
  font-size: 13.5px;
}
.alert p {
  margin: 4px 0 0;
}
.alert ol {
  margin: 6px 0 0;
  padding-left: 20px;
}
.alert code {
  background: #fee2e2;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12.5px;
}
.retry-btn {
  flex-shrink: 0;
  border-color: #fca5a5;
  color: #991b1b;
  background: #fff;
  padding: 6px 12px;
  font-size: 12.5px;
}

/* 消息区 */
.chat {
  flex: 1;
  overflow-y: auto;
  padding: 8px 4px 16px;
  scroll-behavior: smooth;
}

/* 欢迎卡片 */
.welcome {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow);
  padding: 40px 32px;
  text-align: center;
  margin: 40px auto 20px;
  max-width: 560px;
}
.welcome-icon {
  font-size: 40px;
}
.welcome h2 {
  margin: 12px 0 6px;
  font-size: 19px;
}
.welcome p {
  color: var(--text-2);
  font-size: 14px;
  margin: 0 0 22px;
}
.examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
}
.example-chip {
  border: 1px dashed #c8d2f5;
  background: var(--accent-soft);
  color: var(--accent-deep);
  border-radius: 999px;
  padding: 7px 16px;
  font-size: 13.5px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.example-chip:hover {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

/* 输入区 */
.composer {
  padding: 10px 4px 14px;
  background: var(--bg);
  position: sticky;
  bottom: 0;
}
.composer-inner {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow);
  padding: 10px 10px 10px 16px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.composer-inner:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(79, 107, 247, 0.12);
}
textarea {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  font: inherit;
  font-size: 15px;
  line-height: 1.55;
  max-height: 160px;
  background: transparent;
  color: var(--text);
}
textarea::placeholder {
  color: var(--text-3);
}
.send-btn {
  border-radius: 12px;
  padding: 9px 20px;
  font-size: 14px;
  flex-shrink: 0;
}
.hint {
  text-align: center;
  font-size: 11.5px;
  color: var(--text-3);
  margin: 8px 0 0;
}

/* 窄屏适配 */
@media (max-width: 640px) {
  .titles p {
    display: none;
  }
  .bubble-user {
    max-width: 88%;
  }
}
</style>
