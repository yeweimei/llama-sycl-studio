<template>
  <div class="page-container stats-page">
    <!-- 头部 -->
    <div class="stats-hero">
      <div>
        <div class="hero-title">📊 API 调用统计</div>
        <div class="hero-sub">全量端点监控 · 成功率 · 延迟 · Token 消耗</div>
      </div>
      <div class="hero-actions">
        <el-radio-group v-model="range" size="small" @change="load">
          <el-radio-button :value="1">近 1 小时</el-radio-button>
          <el-radio-button :value="6">近 6 小时</el-radio-button>
          <el-radio-button :value="24">近 24 小时</el-radio-button>
          <el-radio-button :value="168">近 7 天</el-radio-button>
        </el-radio-group>
        <el-button size="small" @click="load"><el-icon style="margin-right:4px"><Refresh /></el-icon>刷新</el-button>
      </div>
    </div>

    <!-- KPI 卡片 -->
    <el-row :gutter="14" class="kpi-row">
      <el-col :xs="12" :sm="8" :md="4" v-for="k in kpis" :key="k.label">
        <div class="kpi-card">
          <div class="kpi-icon" :style="{ background: k.iconBg }">{{ k.icon }}</div>
          <div class="kpi-info">
            <div class="kpi-value" :style="{ color: k.color }">{{ k.value }}</div>
            <div class="kpi-label">{{ k.label }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 趋势 + 状态码分布 -->
    <el-row :gutter="14">
      <el-col :xs="24" :md="16">
        <el-card shadow="never" class="panel-card">
          <div class="card-title">
            <span>📈 请求趋势</span>
            <el-radio-group v-model="chartMode" size="small">
              <el-radio-button value="req">请求量</el-radio-button>
              <el-radio-button value="lat">延迟</el-radio-button>
              <el-radio-button value="tok">Tokens</el-radio-button>
            </el-radio-group>
          </div>
          <div ref="trendRef" class="chart" v-loading="loading"></div>
          <el-empty v-if="!loading && !trends.length" description="暂无趋势数据" :image-size="60" style="margin-top:-60px" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never" class="panel-card">
          <div class="card-title"><span>🎯 状态码分布</span></div>
          <div ref="codeRef" class="chart"></div>
          <el-empty v-if="!loading && !codeData.length" description="暂无数据" :image-size="60" style="margin-top:-60px" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 端点统计 -->
    <el-card shadow="never" class="panel-card">
      <div class="card-title"><span>🔀 端点调用统计</span>
        <el-tag size="small" type="info" effect="plain">总计 {{ endpointTotal }} 次调用</el-tag>
        <span class="card-hint">按 /v1/* 代理全量记录</span>
      </div>
      <el-table :data="endpoints" v-loading="loading" stripe size="small">
        <el-table-column label="端点" min-width="230">
          <template #default="{ row }">
            <span class="ep-method" :class="'m-' + (row.method || 'POST').toLowerCase()">{{ row.method || 'POST' }}</span>
            <code class="ep-path">{{ row.endpoint }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="requests" label="调用次数" width="90" align="right" sortable />
        <el-table-column label="成功率" width="170">
          <template #default="{ row }">
            <div class="rate-wrap">
              <el-progress :percentage="Number(row.success_rate || 0)" :stroke-width="8"
                :color="rateColor(row.success_rate)" :format="() => row.success_rate + '%'" />
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态码" min-width="160">
          <template #default="{ row }">
            <el-tag v-for="sc in row.status_code_list" :key="sc.code" size="small"
              :type="codeType(sc.code)" effect="plain" class="code-tag">
              {{ sc.code }}×{{ sc.count }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="平均延迟" width="100" align="right">
          <template #default="{ row }">{{ row.avg_total_ms }} ms</template>
        </el-table-column>
        <el-table-column label="Tokens (入/出)" width="130" align="right">
          <template #default="{ row }">{{ row.prompt_tokens }} / {{ row.completion_tokens }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !endpoints.length" description="暂无端点调用记录" :image-size="60" />
    </el-card>

    <!-- 模型统计 -->
    <el-card shadow="never" class="panel-card">
      <div class="card-title"><span>🤖 模型调用统计</span></div>
      <el-table :data="stats" v-loading="loading" stripe size="small">
        <el-table-column prop="model_name" label="模型" min-width="180" />
        <el-table-column prop="request_count" label="调用次数" width="90" align="right" sortable />
        <el-table-column label="成功率" width="150">
          <template #default="{ row }">
            <el-progress :percentage="modelRate(row)" :stroke-width="8" :color="rateColor(modelRate(row))" />
          </template>
        </el-table-column>
        <el-table-column label="Prompt Tokens" width="120" align="right">
          <template #default="{ row }">{{ (row.prompt_tokens || 0).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="Completion Tokens" width="140" align="right">
          <template #default="{ row }">{{ (row.completion_tokens || 0).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="Prefill 均耗时" width="110" align="right">
          <template #default="{ row }">{{ row.avg_prefill_ms || 0 }} ms</template>
        </el-table-column>
        <el-table-column label="Decode 均耗时" width="110" align="right">
          <template #default="{ row }">{{ row.avg_decode_ms || 0 }} ms</template>
        </el-table-column>
        <el-table-column label="Prefill 吞吐" width="105" align="right">
          <template #default="{ row }">
            <span v-if="row.total_prefill_ms > 0" style="color:#8b5cf6;font-weight:600">{{ fmtTps(row.prompt_tokens / (row.total_prefill_ms / 1000)) }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="Decode 吞吐" width="105" align="right">
          <template #default="{ row }">
            <span v-if="row.total_decode_ms > 0" style="color:#2563eb;font-weight:600">{{ fmtTps(row.completion_tokens / (row.total_decode_ms / 1000)) }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !stats.length" description="暂无模型调用记录" :image-size="60" />
    </el-card>

    <!-- 最近请求 -->
    <el-card shadow="never" class="panel-card">
      <div class="card-title"><span>🕘 最近请求（{{ requests.length }} 条）</span></div>
      <el-table :data="requests" stripe size="small" max-height="420">
        <el-table-column label="时间" width="140">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="端点" min-width="180">
          <template #default="{ row }">
            <code class="ep-path small">{{ row.endpoint || '-' }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型" min-width="150" />
        <el-table-column label="类型" width="76">
          <template #default="{ row }">
            <el-tag size="small" :type="row.stream ? 'info' : 'success'" effect="plain">{{ row.stream ? '流式' : '一次性' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="86">
          <template #default="{ row }">
            <el-tag size="small" :type="codeType(row.status_code)" effect="plain">{{ row.status_code }}</el-tag>
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
        <el-table-column label="耗时" width="100" align="right">
          <template #default="{ row }">{{ row.total_ms }} ms</template>
        </el-table-column>
        <el-table-column label="错误" min-width="110">
          <template #default="{ row }">
            <span v-if="!row.ok && row.error" class="err-text" :title="row.error">{{ row.error }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { getStats, getStatsTrends, getEndpointStats, getRecentRequests } from '../api'

const stats = ref([])
const endpoints = ref([])
const trends = ref([])
const requests = ref([])
const loading = ref(false)
const range = ref(24)
const chartMode = ref('req')

const trendRef = ref(null)
const codeRef = ref(null)
let trendChart = null
let codeChart = null

// ---------- KPI ----------
const kpis = computed(() => {
  const totalReq = stats.value.reduce((s, r) => s + (r.request_count || 0), 0)
  const totalPrompt = stats.value.reduce((s, r) => s + (r.prompt_tokens || 0), 0)
  const totalCompletion = stats.value.reduce((s, r) => s + (r.completion_tokens || 0), 0)
  const okCount = stats.value.reduce((s, r) => s + (r.ok_count || 0), 0)
  const rate = totalReq ? Math.round(okCount / totalReq * 100) : 100
  const totalMs = stats.value.reduce((s, r) => s + ((r.avg_prefill_ms || 0) + (r.avg_decode_ms || 0)) * (r.request_count || 0), 0)
  const avgMs = totalReq ? Math.round(totalMs / totalReq) : 0
  const cur = trends.value.length ? trends.value[trends.value.length - 1] : null
  const qps = cur && range.value <= 24 ? cur.qps : (totalReq && range.value >= 168 ? (totalReq / 604800).toFixed(3) : '-')
  return [
    { label: '总调用次数', value: totalReq.toLocaleString(), icon: '📨', color: '#2563eb', iconBg: 'linear-gradient(135deg,#dbeafe,#eff6ff)' },
    { label: '成功率', value: rate + '%', icon: '✅', color: '#059669', iconBg: 'linear-gradient(135deg,#d1fae5,#ecfdf5)' },
    { label: '平均延迟', value: avgMs + ' ms', icon: '⚡', color: '#d97706', iconBg: 'linear-gradient(135deg,#fef3c7,#fffbeb)' },
    { label: 'QPS', value: String(qps), icon: '🚀', color: '#7c3aed', iconBg: 'linear-gradient(135deg,#ede9fe,#f5f3ff)' },
    { label: 'Prompt Tokens', value: totalPrompt.toLocaleString(), icon: '📥', color: '#0891b2', iconBg: 'linear-gradient(135deg,#cffafe,#ecfeff)' },
    { label: 'Completion Tokens', value: totalCompletion.toLocaleString(), icon: '📤', color: '#db2777', iconBg: 'linear-gradient(135deg,#fce7f3,#fdf2f8)' },
  ]
})

const endpointTotal = computed(() => endpoints.value.reduce((s, e) => s + (e.requests || 0), 0))

const codeData = computed(() => {
  const map = {}
  requests.value.forEach(r => {
    const k = r.status_code
    map[k] = (map[k] || 0) + 1
  })
  return Object.entries(map).map(([code, count]) => ({ code: Number(code), count })).sort((a, b) => b.count - a.count)
})

// ---------- 图表 ----------
function initCharts() {
  if (!trendChart && trendRef.value) trendChart = echarts.init(trendRef.value)
  if (!codeChart && codeRef.value) codeChart = echarts.init(codeRef.value)
}

function renderTrend() {
  if (!trendChart) return
  const times = trends.value.map(b => {
    const d = new Date(b.ts * 1000)
    return range.value >= 168 ? `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}时` : `${String(d.getHours()).padStart(2, '0')}:00`
  })
  const mode = chartMode.value
  let series = []
  let yAxis = []
  if (mode === 'req') {
    series = [
      { name: '请求量', type: 'bar', data: trends.value.map(b => b.requests), itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: '#3b82f6' }, { offset: 1, color: '#93c5fd' }]), borderRadius: [4, 4, 0, 0] } },
      { name: '失败', type: 'bar', data: trends.value.map(b => b.fail), itemStyle: { color: '#f87171', borderRadius: [4, 4, 0, 0] }, barGap: '-100%', barWidth: 8 },
      { name: '延迟', type: 'line', yAxisIndex: 1, data: trends.value.map(b => b.avg_total_ms), smooth: true, symbol: 'none', lineStyle: { color: '#f59e0b', width: 2 } },
    ]
    yAxis = [
      { type: 'value', name: '请求数', splitLine: { lineStyle: { color: '#f1f5f9' } } },
      { type: 'value', name: 'ms', splitLine: { show: false }, axisLabel: { color: '#d97706' } },
    ]
  } else if (mode === 'lat') {
    series = [
      { name: '平均总延迟', type: 'line', data: trends.value.map(b => b.avg_total_ms), smooth: true, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(245,158,11,.3)' }, { offset: 1, color: 'rgba(245,158,11,0)' }]) }, lineStyle: { color: '#f59e0b', width: 2.5 }, symbol: 'circle', symbolSize: 5 },
      { name: 'Prefill', type: 'line', data: trends.value.map(b => b.avg_prefill_ms), smooth: true, symbol: 'none', lineStyle: { color: '#8b5cf6', width: 2 } },
      { name: 'Decode', type: 'line', data: trends.value.map(b => b.avg_decode_ms), smooth: true, symbol: 'none', lineStyle: { color: '#06b6d4', width: 2 } },
    ]
    yAxis = [{ type: 'value', name: 'ms', splitLine: { lineStyle: { color: '#f1f5f9' } } }]
  } else {
    series = [
      { name: 'Prompt', type: 'bar', stack: 'tok', data: trends.value.map(b => b.prompt_tokens), itemStyle: { color: '#3b82f6' } },
      { name: 'Completion', type: 'bar', stack: 'tok', data: trends.value.map(b => b.completion_tokens), itemStyle: { color: '#ec4899' } },
    ]
    yAxis = [{ type: 'value', name: 'Tokens', splitLine: { lineStyle: { color: '#f1f5f9' } } }]
  }
  trendChart.setOption({
    tooltip: { trigger: 'axis', backgroundColor: '#0f172a', textStyle: { color: '#e2e8f0', fontSize: 12 }, borderWidth: 0 },
    legend: { top: 0, textStyle: { fontSize: 12 } },
    grid: { left: 50, right: 50, top: 34, bottom: 24 },
    xAxis: { type: 'category', data: times, axisLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { fontSize: 11 } },
    yAxis,
    series,
  }, true)
}

function renderCodes() {
  if (!codeChart) return
  const data = codeData.value
  const colors = { 200: '#10b981', 400: '#f59e0b', 401: '#f59e0b', 404: '#94a3b8', 500: '#ef4444', 502: '#ef4444', 503: '#ef4444', 504: '#ef4444' }
  codeChart.setOption({
    tooltip: { trigger: 'item', backgroundColor: '#0f172a', textStyle: { color: '#e2e8f0' }, borderWidth: 0 },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['45%', '70%'],
      center: ['50%', '44%'],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: true, formatter: '{b}\n{c}', fontSize: 11 },
      data: data.map(d => ({ name: d.code, value: d.count, itemStyle: { color: colors[d.code] || '#64748b' } })),
    }],
  }, true)
}

function resizeCharts() {
  trendChart?.resize()
  codeChart?.resize()
}

// ---------- 辅助 ----------
function rateColor(r) {
  r = Number(r || 0)
  if (r >= 95) return '#10b981'
  if (r >= 80) return '#f59e0b'
  return '#ef4444'
}
function modelRate(row) {
  const total = row.request_count || 0
  const ok = row.ok_count || 0
  return total ? Math.round(ok / total * 100) : 100
}
function codeType(code) {
  if (code >= 500) return 'danger'
  if (code >= 400) return 'warning'
  return 'success'
}
function fmtTps(v) {
  if (v == null || !isFinite(v) || v <= 0) return '-'
  return v.toFixed(1) + ' t/s'
}
function fmtTime(ts) {
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

async function load() {
  loading.value = true
  try {
    const bucket = range.value >= 168 ? 1440 : (range.value >= 24 ? 60 : 5)
    const [s, t, e, r] = await Promise.all([
      getStats(),
      getStatsTrends(range.value, bucket),
      getEndpointStats(range.value),
      getRecentRequests(80),
    ])
    stats.value = s
    trends.value = t.buckets || []
    endpoints.value = e.endpoints || []
    requests.value = r
    await nextTick()
    initCharts()
    renderTrend()
    renderCodes()
  } catch (err) {
    ElMessage.error('加载失败: ' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

watch(chartMode, () => renderTrend())
watch(codeData, () => renderCodes(), { deep: true })

onMounted(() => {
  load()
  window.addEventListener('resize', resizeCharts)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  trendChart?.dispose()
  codeChart?.dispose()
})
</script>

<style scoped>
.stats-page { padding: 20px; }
.stats-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%);
  border-radius: 12px;
  padding: 18px 22px;
  margin-bottom: 16px;
  color: #fff;
}
.hero-title { font-size: 20px; font-weight: 700; }
.hero-sub { font-size: 13px; opacity: 0.85; margin-top: 4px; }
.hero-actions { display: flex; align-items: center; gap: 10px; }
.kpi-row { margin-bottom: 14px; }
.kpi-card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #fff;
  border-radius: 12px;
  padding: 14px 16px;
  border: 1px solid #e2e8f0;
  transition: transform .2s, box-shadow .2s;
  margin-bottom: 14px;
}
.kpi-card:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(15, 23, 42, .08); }
.kpi-icon {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.kpi-value { font-size: 20px; font-weight: 800; line-height: 1.2; font-variant-numeric: tabular-nums; }
.kpi-label { font-size: 12px; color: #909399; margin-top: 2px; }
.panel-card { border-radius: 10px; margin-bottom: 14px; }
.card-hint { margin-left: auto; font-size: 12px; color: #909399; }
.chart { height: 260px; }
.rate-wrap { padding-right: 12px; }
.ep-method {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  margin-right: 8px;
  color: #fff;
  vertical-align: 1px;
}
.m-get { background: #10b981; }
.m-post { background: #3b82f6; }
.m-put { background: #8b5cf6; }
.m-delete { background: #ef4444; }
.m-patch { background: #f59e0b; }
.ep-path {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  background: #f1f5f9;
  color: #334155;
  padding: 2px 6px;
  border-radius: 4px;
}
.ep-path.small { font-size: 11px; background: #f8fafc; }
.code-tag { margin-right: 4px; margin-bottom: 2px; }
.err-text {
  color: #f56c6c;
  font-size: 12px;
  display: inline-block;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
@media (max-width: 767px) {
  .stats-page { padding: 12px; }
  .chart { height: 200px; }
  .hero-actions { width: 100%; justify-content: space-between; }
}
</style>
