<template>
  <el-container class="layout">
    <el-header class="header" height="56px">
      <div class="title">
        <el-icon :size="20"><Coin /></el-icon>
        <span>SQL Agent 智能问答</span>
      </div>
      <span class="subtitle">自然语言 → Table 选择 → SQL 生成/校验/改写 → 执行 → 汇总</span>
    </el-header>

    <el-container class="body">
      <!-- 左栏:提问 + 示例 + 历史 -->
      <el-aside width="340px" class="aside">
        <el-card shadow="never" class="ask-card">
          <template #header><span>提问</span></template>
          <el-input
            v-model="question"
            type="textarea"
            :rows="3"
            placeholder="用自然语言提问,例如:哪个国家的客户消费总额最高?"
            :disabled="running"
            @keydown.enter.prevent="onEnter"
          />
          <el-button
            type="primary"
            class="ask-btn"
            :loading="running"
            :disabled="!question.trim()"
            @click="ask"
          >
            {{ running ? '执行中…' : '提问' }}
          </el-button>

          <el-divider style="margin: 12px 0" />
          <div class="samples-label">示例问题</div>
          <el-tag
            v-for="s in SAMPLE_QUESTIONS"
            :key="s"
            class="sample-tag"
            :disable-transitions="true"
            @click="useSample(s)"
          >
            {{ s }}
          </el-tag>
        </el-card>

        <el-card shadow="never" class="history-card">
          <template #header>
            <div class="history-header">
              <span>历史提问</span>
              <el-button link size="small" @click="loadHistory">刷新</el-button>
            </div>
          </template>
          <div v-if="!history.length" class="history-empty">暂无记录</div>
          <div
            v-for="item in history"
            :key="item.id"
            class="history-item"
            @click="replay(item)"
          >
            <div class="history-question">{{ item.question }}</div>
            <div class="history-meta">
              <el-tag size="small" :type="item.status === 'success' ? 'success' : 'danger'">
                {{ item.status === 'success' ? '成功' : '失败' }}
              </el-tag>
              <span v-if="item.rewrite_rounds > 0" class="meta-text">改写 {{ item.rewrite_rounds }} 次</span>
              <span class="meta-text">{{ (item.duration_ms / 1000).toFixed(1) }}s</span>
              <span class="meta-text">{{ formatTime(item.created_at) }}</span>
            </div>
          </div>
        </el-card>
      </el-aside>

      <!-- 右栏:workflow 步骤面板 -->
      <el-main class="main">
        <WorkflowPanel :steps="steps" :final="final" />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Coin } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import WorkflowPanel from './components/WorkflowPanel.vue'
import { askStream, fetchHistory } from './api'
import type { AgentEvent, FinalEvent, HistoryRecord } from './types'

const SAMPLE_QUESTIONS = [
  '哪个国家的客户消费总额最高?请给出前3名',
  '收入最高的前5位艺术家是谁?(按专辑曲目售价合计)',
  '每种音乐流派各有多少首曲目?',
]

const question = ref('')
const running = ref(false)
const history = ref<HistoryRecord[]>([])

// workflow 状态:step 序号 → 最新 data;final 为终结事件
const steps = reactive<Record<number, Record<string, unknown>>>({})
const final = ref<FinalEvent | null>(null)

function resetPanel() {
  Object.keys(steps).forEach((k) => delete steps[Number(k)])
  final.value = null
}

function applyEvent(ev: AgentEvent) {
  if (ev.event === 'step') {
    if (ev.status === 'done') steps[ev.step] = { ...ev.data }
    else steps[ev.step] = { ...(steps[ev.step] ?? {}), running: true }
  } else {
    final.value = ev
  }
}

async function ask() {
  if (!question.value.trim() || running.value) return
  running.value = true
  resetPanel()
  try {
    await askStream(question.value, applyEvent)
    if (!final.value) ElMessage.warning('连接中断,未收到最终结果')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  } finally {
    running.value = false
    loadHistory()
  }
}

function onEnter(e: KeyboardEvent) {
  if (e.shiftKey) return // Shift+Enter 换行
  ask()
}

function useSample(s: string) {
  if (running.value) return
  question.value = s
}

/** 点历史条目:用落库的步骤摘要+final 字段直接回放(不重新调 LLM)。 */
function replay(item: HistoryRecord) {
  if (running.value) return
  resetPanel()
  for (const ev of item.steps) {
    if (ev.status === 'done') steps[ev.step] = { ...ev.data }
  }
  final.value = {
    event: 'final',
    status: item.status === 'success' ? 'success' : 'failed',
    question: item.question,
    tables: item.tables,
    sql: item.sql,
    columns: item.columns,
    rows: item.rows,
    row_count: item.row_count,
    truncated: item.truncated,
    rewrite_rounds: item.rewrite_rounds,
    summary: item.summary,
    error: item.status === 'success' ? undefined : item.error || '(历史记录未保存失败原因)',
    duration_ms: item.duration_ms,
  }
  question.value = item.question
}

async function loadHistory() {
  try {
    history.value = await fetchHistory()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '历史加载失败')
  }
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => {
  loadHistory()
  // 演示模式:?demo=1 打开即自动发起第一个示例问题(用于截图/演示)
  if (new URLSearchParams(location.search).get('demo') === '1') {
    question.value = SAMPLE_QUESTIONS[0]
    ask()
  }
})
</script>

<style scoped>
.layout {
  height: 100%;
}

.header {
  display: flex;
  align-items: center;
  gap: 16px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}

.title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 17px;
  font-weight: 600;
}

.subtitle {
  color: #909399;
  font-size: 13px;
}

.body {
  height: calc(100% - 56px);
}

.aside {
  padding: var(--panel-gap);
  display: flex;
  flex-direction: column;
  gap: var(--panel-gap);
  overflow-y: auto;
  border-right: 1px solid #e4e7ed;
  background: #fafbfc;
}

.ask-btn {
  width: 100%;
  margin-top: 8px;
}

.samples-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 8px;
}

.sample-tag {
  display: block;
  width: fit-content;
  margin-bottom: 6px;
  cursor: pointer;
  white-space: normal;
  height: auto;
  padding: 4px 8px;
  line-height: 1.4;
}

.sample-tag:hover {
  opacity: 0.8;
}

.history-card {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.history-empty {
  color: #c0c4cc;
  font-size: 13px;
  text-align: center;
  padding: 16px 0;
}

.history-item {
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
}

.history-item:hover {
  background: #f0f6ff;
}

.history-question {
  font-size: 13px;
  line-height: 1.5;
  margin-bottom: 4px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.history-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.meta-text {
  font-size: 12px;
  color: #909399;
}

.main {
  padding: var(--panel-gap);
  overflow-y: auto;
}
</style>
