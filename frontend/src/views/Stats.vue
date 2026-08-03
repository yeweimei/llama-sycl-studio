<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="card-title">
        <span>API 调用统计</span>
        <el-button size="small" @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>

      <!-- 汇总卡片 -->
      <el-row :gutter="16" style="margin-bottom:16px">
        <el-col :span="6" v-for="card in summary" :key="card.label">
          <el-card shadow="never" class="summary-card">
            <div class="summary-value">{{ card.value }}</div>
            <div class="summary-label">{{ card.label }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-table :data="stats" v-loading="loading" stripe size="small" class="mobile-table">
        <el-table-column prop="model_name" label="模型" min-width="180" />
        <el-table-column prop="request_count" label="调用次数" width="100" align="right" />
        <el-table-column label="Prompt Tokens" width="140" align="right">
          <template #default="{ row }">{{ row.prompt_tokens || 0 }}</template>
        </el-table-column>
        <el-table-column label="Completion Tokens" width="150" align="right">
          <template #default="{ row }">{{ row.completion_tokens || 0 }}</template>
        </el-table-column>
        <el-table-column label="Prefill 均耗时" width="130" align="right">
          <template #default="{ row }">{{ row.avg_prefill_ms || 0 }} ms</template>
        </el-table-column>
        <el-table-column label="Decode 均耗时" width="130" align="right">
          <template #default="{ row }">{{ row.avg_decode_ms || 0 }} ms</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && stats.length === 0" description="暂无调用记录" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { getStats } from '../api'

const stats = ref([])
const loading = ref(false)

const summary = computed(() => {
  const totalReq = stats.value.reduce((s, r) => s + (r.request_count || 0), 0)
  const totalPrompt = stats.value.reduce((s, r) => s + (r.prompt_tokens || 0), 0)
  const totalCompletion = stats.value.reduce((s, r) => s + (r.completion_tokens || 0), 0)
  const models = stats.value.length
  return [
    { label: '总调用次数', value: totalReq },
    { label: 'Prompt Tokens', value: totalPrompt.toLocaleString() },
    { label: 'Completion Tokens', value: totalCompletion.toLocaleString() },
    { label: '活跃模型数', value: models },
  ]
})

async function load() {
  loading.value = true
  try {
    stats.value = await getStats()
  } catch (e) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.summary-card {
  text-align: center;
  border: 1px solid #ebeef5;
}
.summary-value {
  font-size: 28px;
  font-weight: 700;
  color: #409eff;
}
.summary-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
