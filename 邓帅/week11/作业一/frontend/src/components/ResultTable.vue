<template>
  <el-table :data="rowsData" size="small" border stripe max-height="420">
    <el-table-column
      v-for="(col, i) in columns"
      :key="col + '-' + i"
      :prop="String(i)"
      :label="col"
      :min-width="Math.max(90, col.length * 12 + 30)"
      show-overflow-tooltip
    >
      <template #default="{ row }">
        {{ row[String(i)] === null || row[String(i)] === undefined ? 'NULL' : row[String(i)] }}
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { computed } from 'vue'

// 查询结果表格:列名来自 SELECT 输出,行数据为二维数组
const props = defineProps<{
  columns: string[]
  rows: unknown[][]
}>()

// el-table 需要对象数组,把每行数组转成 { '0': v0, '1': v1, ... }
const rowsData = computed(() =>
  props.rows.map((row) => {
    const obj: Record<string, unknown> = {}
    row.forEach((v, i) => (obj[String(i)] = v))
    return obj
  }),
)
</script>
