<template>
  <div class="workflow-panel">
    <!-- 空态 -->
    <el-empty v-if="empty" description="在左侧输入自然语言问题,Workflow 将逐步点亮" />

    <template v-else>
      <!-- 7 步进度条 -->
      <el-steps :active="activeStep" finish-status="success" align-center>
        <el-step
          v-for="s in STEP_ORDER"
          :key="s"
          :title="`${s}. ${STEP_TITLES[s]}`"
          :status="stepStatus(s)"
        />
      </el-steps>

      <!-- 各步详情 -->
      <div class="step-details">
        <!-- ① 数据库解析 -->
        <el-card v-if="steps[1]" shadow="never" class="detail-card">
          <template #header>
            <span>① 数据库解析</span>
            <el-tag size="small" type="info" style="margin-left: 8px">
              {{ steps[1].tables }} 张业务表 / {{ steps[1].relations }} 个外键
            </el-tag>
          </template>
          <el-tag
            v-for="t in (steps[1].table_names as string[])"
            :key="t"
            :type="t in selectedSet ? 'primary' : 'info'"
            :effect="t in selectedSet ? 'dark' : 'plain'"
            size="small"
            class="table-tag"
          >
            {{ t }}
          </el-tag>
        </el-card>

        <!-- ② Table 选择 -->
        <el-card v-if="steps[2]" shadow="never" class="detail-card">
          <template #header>
            <span>② Table 选择</span>
            <el-tag v-if="steps[2].fallback" size="small" type="warning" style="margin-left: 8px">
              选表结果为空,已兜底全部业务表
            </el-tag>
          </template>
          <el-tag v-for="t in (steps[2].tables as string[])" :key="t" class="table-tag">
            {{ t }}
          </el-tag>
        </el-card>

        <!-- ③ SQL 生成 -->
        <el-card v-if="steps[3]" shadow="never" class="detail-card">
          <template #header><span>③ SQL 生成</span></template>
          <pre class="sql-block">{{ steps[3].sql }}</pre>
        </el-card>

        <!-- ④ SQL 校验 -->
        <el-card v-if="steps[4]" shadow="never" class="detail-card">
          <template #header>
            <span>④ SQL 校验</span>
            <el-tag size="small" :type="validateOk ? 'success' : 'danger'" style="margin-left: 8px">
              {{ validateOk ? '通过' : '未通过' }}
            </el-tag>
          </template>
          <div v-if="!validateOk">
            <div v-for="(e, i) in validateErrors" :key="i" class="err-line">⚠ {{ e }}</div>
          </div>
        </el-card>

        <!-- ⑤ SQL 改写 -->
        <el-card v-if="steps[5]" shadow="never" class="detail-card">
          <template #header>
            <span>⑤ SQL 改写</span>
            <el-tag
              size="small"
              :type="rewriteRounds > 0 ? 'warning' : 'success'"
              style="margin-left: 8px"
            >
              {{ rewriteRounds > 0 ? `改写 ${rewriteRounds} 次` : '未触发' }}
            </el-tag>
          </template>
          <template v-if="rewriteRounds > 0">
            <div v-for="(e, i) in rewriteErrors" :key="i" class="err-line">⚠ {{ e }}</div>
            <pre v-if="rewriteSql" class="sql-block">{{ rewriteSql }}</pre>
          </template>
        </el-card>

        <!-- ⑥ SQL 执行 + 结果表格 -->
        <el-card v-if="execColumns.length" shadow="never" class="detail-card">
          <template #header>
            <span>⑥ SQL 执行</span>
            <el-tag size="small" type="info" style="margin-left: 8px">
              {{ final?.row_count ?? 0 }} 行{{ final?.truncated ? '(已截断)' : '' }}
            </el-tag>
          </template>
          <ResultTable :columns="execColumns" :rows="execRows" />
        </el-card>

        <!-- ⑦ 结果汇总 -->
        <el-card v-if="summaryHtml" shadow="never" class="detail-card">
          <template #header>
            <span>⑦ 结果汇总</span>
            <el-tag v-if="final" size="small" type="info" style="margin-left: 8px">
              耗时 {{ (final.duration_ms / 1000).toFixed(1) }}s
            </el-tag>
          </template>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="summary-md" v-html="summaryHtml" />
        </el-card>

        <!-- 失败提示 -->
        <el-alert
          v-if="final?.status === 'failed'"
          type="error"
          :closable="false"
          show-icon
          :title="`问答失败(改写 ${final.rewrite_rounds} 次后仍未通过)`"
          :description="final.error"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import ResultTable from './ResultTable.vue'
import type { FinalEvent } from '@/types'

const props = defineProps<{
  steps: Record<number, Record<string, unknown>> // step 序号 → 最新 data
  final: FinalEvent | null
}>()

const STEP_ORDER = [1, 2, 3, 4, 5, 6, 7] as const
const STEP_TITLES: Record<number, string> = {
  1: '数据库解析',
  2: 'Table 选择',
  3: 'SQL 生成',
  4: 'SQL 校验',
  5: 'SQL 改写',
  6: 'SQL 执行',
  7: '结果汇总',
}

const md = new MarkdownIt({ breaks: true, linkify: false })

const empty = computed(() => Object.keys(props.steps).length === 0 && !props.final)

/** 当前进行到的步骤(el-steps active,从 0 计)。 */
const activeStep = computed(() => {
  const doneMax = STEP_ORDER.filter((s) => props.steps[s] !== undefined).pop() ?? 0
  return props.final ? 7 : Math.max(0, doneMax - 1)
})

/** el-step 状态:done → success;最后可见且未完结 → process。 */
const stepStatus = (s: number): 'success' | 'process' | 'wait' => {
  if (props.steps[s] === undefined) return 'wait'
  if (props.final) return 'success'
  const maxVisible = Math.max(...Object.keys(props.steps).map(Number))
  return s === maxVisible ? 'process' : 'success'
}

/** 选中的表集合(解析卡片高亮)。 */
const selectedSet = computed<Record<string, boolean>>(() => {
  const set: Record<string, boolean> = {}
  const sel = (props.steps[2]?.tables as string[] | undefined) ?? props.final?.tables ?? []
  sel.forEach((t) => (set[t] = true))
  return set
})

const execColumns = computed<string[]>(
  () => (props.final?.columns as string[] | undefined) ?? [],
)
const execRows = computed<unknown[][]>(() => (props.final?.rows as unknown[][]) ?? [])

// 类型收窄:校验/改写卡片的数据从 unknown 里安全取出
const validateOk = computed(() => Boolean(props.steps[4]?.ok))
const validateErrors = computed(
  () => (props.steps[4]?.errors as string[] | undefined) ?? [],
)
const rewriteRounds = computed(() => Number(props.steps[5]?.rounds ?? 0))
const rewriteErrors = computed(
  () => (props.steps[5]?.errors as string[] | undefined) ?? [],
)
const rewriteSql = computed(() => (props.steps[5]?.sql as string | undefined) ?? '')

const summaryHtml = computed(() => {
  const text = props.final?.summary
  return text ? md.render(text) : ''
})
</script>

<style scoped>
.workflow-panel {
  display: flex;
  flex-direction: column;
  gap: var(--panel-gap);
}

.step-details {
  display: flex;
  flex-direction: column;
  gap: var(--panel-gap);
  margin-top: 8px;
}

.detail-card :deep(.el-card__header) {
  padding: 10px 14px;
  font-weight: 600;
}

.detail-card :deep(.el-card__body) {
  padding: 12px 14px;
}

.table-tag {
  margin: 0 6px 6px 0;
}

.err-line {
  color: #f56c6c;
  font-size: 13px;
  line-height: 1.7;
}
</style>
