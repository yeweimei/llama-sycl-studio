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

    <!-- 请求趋势（24h 按小时） -->
    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title"><span>请求趋势（近 24h / 小时桶）</span></div>
      <div v-if="trends.length" class="trend-wrap">
        <div v-for="b in trends" :key="b.ts" class="trend-bar-col" :title="trendTip(b)">
          <div class="trend-bar" :style="{ height: barHeight(b) + 'px' }">
            <span v-if="b.fail > 0" class="trend-fail">{{ b.fail }}</span>
          </div>
          <div class="trend-label">{{ fmtHour(b.ts) }}</div>
        </div>
      </div>
      <el-empty v-else description="暂无趋势数据" :image-size="60" />
      <div class="trend-legend">
        <span>请求数（失败红色角标）</span>
        <span style="margin-left:16px">QPS: {{ lastQps }}</span>
        <span style="margin-left:16px">平均总延迟: {{ lastAvgMs }} ms</span>
      </div>
    </el-card>

    <!-- 最近请求明细 -->
    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title"><span>最近请求（{{ requests.length }} 条）</span></div>
      <el-table :data="requests" stripe size="small" class="mobile-table" max-height="420">
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型" min-width="160" />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.stream ? 'info' : 'success'" effect="plain">{{ row.stream ? '流式' : '一次性' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="70">
          <template #default="{ row }">
            <el-tag size="small" :type="row.ok ? 'success' : 'danger'" effect="plain">{{ row.ok ? '成功' : '失败' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Tokens (入/出)" width="120" align="right">
          <template #default="{ row }">{{ row.prompt_tokens }} / {{ row.completion_tokens }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="130" align="right">
          <template #default="{ row }">
            {{ row.total_ms }} ms
            <template v-if="row.prefill_ms">（prefill {{ row.prefill_ms }}）</template>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="120">
          <template #default="{ row }">
            <span v-if="!row.ok && row.error" class="err-text">{{ row.error }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { getStats, getStatsTrends, getRecentRequests } from '../api'

const stats = ref([])
const loading = ref(false)
const trends = ref([])
const requests = ref([])

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

const maxReqs = computed(() => Math.max(1, ...trends.value.map(b => b.requests)))
const lastQps = computed(() => trends.value.length ? trends.value[trends.value.length - 1].qps : 0)
const lastAvgMs = computed(() => trends.value.length ? trends.value[trends.value.length - 1].avg_total_ms : 0)

function barHeight(b) {
  return Math.max(3, Math.round(b.requests / maxReqs.value * 90))
}

function trendTip(b) {
  return `${fmtHour(b.ts)}: ${b.requests} 次请求（成功 ${b.ok} / 失败 ${b.fail}），QPS ${b.qps}，平均 ${b.avg_total_ms}ms`
}

function fmtHour(ts) {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:00`
}

function fmtTime(ts) {
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

async function load() {
  loading.value = true
  try {
    const [s, t, r] = await Promise.all([getStats(), getStatsTrends(24, 60), getRecentRequests(50)])
    stats.value = s
    trends.value = t.buckets || []
    requests.value = r
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
.trend-wrap {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 120px;
  overflow-x: auto;
  padding: 8px 0;
}
.trend-bar-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
}
.trend-bar {
  width: 22px;
  background: linear-gradient(180deg, #409eff, #79bbff);
  border-radius: 3px 3px 0 0;
  position: relative;
  min-height: 3px;
}
.trend-fail {
  position: absolute;
  top: -14px;
  left: 50%;
  transform: translateX(-50%);
  color: #f56c6c;
  font-size: 11px;
  font-weight: 700;
}
.trend-label {
  font-size: 10px;
  color: #909399;
  margin-top: 4px;
}
.trend-legend {
  margin-top: 8px;
  font-size: 12px;
  color: #606266;
}
.err-text {
  color: #f56c6c;
  font-size: 12px;
  word-break: break-all;
}
</style>
