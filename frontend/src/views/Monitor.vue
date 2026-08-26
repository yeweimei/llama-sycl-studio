<template>
  <div class="page-container">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="card-title"><span>内存</span></div>
          <div class="big-num">{{ sys.memory_avail_gb }} <small>GB 可用</small></div>
          <el-progress :percentage="memPercent" :stroke-width="14" :color="'#409eff'" />
          <div class="sub-info">总 {{ sys.memory_total_gb }} GB</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="card-title"><span>磁盘</span></div>
          <div class="big-num">{{ sys.disk_free_gb }} <small>GB 可用</small></div>
          <el-progress :percentage="diskPercent" :stroke-width="14" :color="'#67c23a'" />
          <div class="sub-info">总 {{ sys.disk_total_gb }} GB</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never">
          <div class="card-title"><span>模型目录</span></div>
          <div class="big-num">{{ sys.model_dir_size_gb }} <small>GB</small></div>
          <div class="sub-info mono" style="word-break:break-all">{{ sys.model_dir }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- GPU 状态卡片 -->
    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title">
        <span>GPU 监控</span>
        <el-button size="small" style="margin-left:auto" @click="loadGpu">刷新</el-button>
      </div>

      <!-- 不可用提示 -->
      <el-alert v-if="gpu.source === 'unavailable'" type="error" :closable="false" show-icon
        title="GPU 监控不可用" :description="gpu.error || 'xpu-smi 未安装或无法获取数据'" style="margin-bottom:12px">
        <template #default>
          <el-button size="small" type="primary" @click="loadGpu" style="margin-top:8px">重试</el-button>
        </template>
      </el-alert>

      <!-- 设备信息 -->
      <div v-if="gpu.devices?.length">
        <div v-for="dev in gpu.devices" :key="dev.id" class="gpu-device-block">
          <el-row :gutter="16" align="middle">
            <el-col :xs="24" :sm="12">
              <div class="gpu-dev-name">{{ dev.name }}</div>
              <div class="gpu-dev-bdf">{{ dev.pci_bdf || '-' }}</div>
            </el-col>
            <el-col :xs="24" :sm="12">
              <div class="gpu-stats-row">
                <span class="gpu-stat-label">功耗</span>
                <span class="gpu-stat-val">{{ dev.power_draw_w ?? '-' }}W / {{ dev.power_limit_w ?? '-' }}W</span>
              </div>
              <div class="gpu-stats-row">
                <span class="gpu-stat-label">频率</span>
                <span class="gpu-stat-val">{{ dev.frequency_mhz != null ? dev.frequency_mhz + ' MHz' : '-' }}</span>
              </div>
              <div class="gpu-stats-row">
                <span class="gpu-stat-label">温度</span>
                <span class="gpu-stat-val">{{ dev.temperature_c != null ? dev.temperature_c + '°C' : '-' }}</span>
              </div>
              <div class="gpu-stats-row">
                <span class="gpu-stat-label">能耗</span>
                <span class="gpu-stat-val">{{ dev.energy_consumed_j != null ? dev.energy_consumed_j + ' J' : '-' }}</span>
              </div>
            </el-col>
          </el-row>

          <!-- 内存进度条：核显用真实模型占用（RSS），独显用显存 -->
          <div style="margin-top:12px">
            <div class="gpu-mem-header">
              <span class="gpu-stat-label">{{ dev.is_integrated ? '模型内存(共享)' : '显存' }}</span>
              <template v-if="dev.is_integrated">
                <span class="gpu-mem-text">{{ formatMiB(dev.model_memory_mib || 0) }} 真实占用</span>
                <el-tag size="small" type="warning" style="margin-left:6px">集显共享内存</el-tag>
              </template>
              <span v-else class="gpu-mem-text">{{ formatMiB(dev.memory_used_mib) }} / {{ formatMiB(dev.memory_total_mib) }}</span>
            </div>
            <el-progress
              v-if="!dev.is_integrated"
              :percentage="dev.memory_total_mib ? Math.round(dev.memory_used_mib / dev.memory_total_mib * 100) : 0"
              :stroke-width="18"
              :color="memColor(dev.memory_total_mib ? dev.memory_used_mib / dev.memory_total_mib : 0)"
            />
            <el-progress
              v-else
              :percentage="modelMemPct(dev.model_memory_mib)"
              :stroke-width="18"
              :color="'#e6a23c'"
            />
          </div>
        </div>
      </div>

      <!-- 推理指标 -->
      <div v-if="gpu.inference && gpu.source !== 'unavailable'" style="margin-top:16px">
        <div class="card-title" style="margin-bottom:8px"><span>推理指标</span></div>
        <el-row :gutter="16">
          <el-col :xs="8" :sm="8">
            <div class="inference-block">
              <div class="inference-num">{{ gpu.inference.requests_processing ?? 0 }}</div>
              <div class="inference-label">请求处理中</div>
            </div>
          </el-col>
          <el-col :xs="8" :sm="8">
            <div class="inference-block">
              <div class="inference-num">{{ gpu.inference.prompt_tps?.toFixed(1) ?? '0.0' }}</div>
              <div class="inference-label">Prompt tok/s</div>
            </div>
          </el-col>
          <el-col :xs="8" :sm="8">
            <div class="inference-block">
              <div class="inference-num">{{ gpu.inference.predicted_tps?.toFixed(1) ?? '0.0' }}</div>
              <div class="inference-label">Predicted tok/s</div>
            </div>
          </el-col>
        </el-row>
      </div>

      <!-- 模型实时性能 -->
      <div v-if="perf.instances?.length" style="margin-top:16px">
        <div class="card-title" style="margin-bottom:8px"><span>模型实时性能</span>
          <el-tag size="small" type="info" effect="plain" style="margin-left:8px">每 5s 刷新</el-tag>
        </div>
        <el-table :data="perf.instances" size="small" stripe>
          <el-table-column prop="model" label="模型" min-width="170" show-overflow-tooltip />
          <el-table-column label="状态" width="70">
            <template #default="{ row }">
              <el-tag size="small" :type="row.online ? 'success' : 'info'" effect="plain">{{ row.online ? '在线' : '离线' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Decode" width="100" align="right">
            <template #default="{ row }"><b style="color:#2563eb">{{ row.decode_tps != null ? row.decode_tps + ' t/s' : '-' }}</b></template>
          </el-table-column>
          <el-table-column label="Prefill" width="100" align="right">
            <template #default="{ row }">{{ row.prefill_tps != null ? row.prefill_tps + ' t/s' : '-' }}</template>
          </el-table-column>
          <el-table-column label="MTP 接受率" width="110" align="right">
            <template #default="{ row }">
              <span v-if="row.mtp_accept != null" :style="{ color: mtpColor(row.mtp_accept) }">{{ Math.round(row.mtp_accept * 100) }}%</span>
              <span v-else class="sub-info">-</span>
            </template>
          </el-table-column>
          <el-table-column label="处理中/排队" width="100" align="right">
            <template #default="{ row }">{{ row.requests_processing }} / {{ row.requests_deferred }}</template>
          </el-table-column>
          <el-table-column label="累计 Tokens (入/出)" width="150" align="right">
            <template #default="{ row }">{{ fmtNum(row.prompt_tokens_total) }} / {{ fmtNum(row.predicted_tokens_total) }}</template>
          </el-table-column>
          <el-table-column label="端口" width="70" align="right">
            <template #default="{ row }">{{ row.port }}</template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 进程列表 -->
      <div v-if="gpu.processes?.length" style="margin-top:16px">
        <div class="card-title" style="margin-bottom:8px"><span>GPU 进程</span></div>
        <el-table :data="gpu.processes" size="small" stripe>
          <el-table-column prop="pid" label="PID" width="100" />
          <el-table-column prop="name" label="进程名" min-width="160" />
          <el-table-column label="内存(RSS)" width="120">
            <template #default="{ row }">
              {{ row.memory_mib ? row.memory_mib + ' MiB' : '-' }}
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="gpu.generated_at" class="sub-info" style="margin-top:8px;text-align:right">
        更新于 {{ gpu.generated_at }}
      </div>
    </el-card>

    <!-- 实时趋势 -->
    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title">
        <span>实时趋势（最近 5 分钟）</span>
        <el-radio-group v-model="trendMode" size="small" style="margin-left:auto">
          <el-radio-button value="tps">吞吐</el-radio-button>
          <el-radio-button value="vram">显存</el-radio-button>
          <el-radio-button value="power">功耗</el-radio-button>
        </el-radio-group>
      </div>
      <div ref="trendRef" style="height:220px" v-loading="loadingTrend"></div>
      <el-empty v-if="!loadingTrend && !trendHistory.length" description="等待采样数据…" :image-size="50" style="margin-top:-190px" />
    </el-card>

    <!-- 实例心跳（M6） -->
    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title">
        <span>模型实例心跳</span>
        <el-tag v-if="gw.total" size="small" :type="gw.degraded ? 'warning' : 'success'" style="margin-left:8px">
          {{ gw.running }} 运行 / {{ gw.degraded }} 降级 / {{ gw.starting }} 启动中 / {{ gw.total - gw.running - gw.degraded - gw.starting }} 停止
        </el-tag>
      </div>
      <el-table :data="gw.instances" stripe size="small" class="mobile-table">
        <el-table-column prop="name" label="模型" min-width="180" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span class="status-dot" :class="'status-' + (row.state || 'stopped')"></span>
            {{ row.state === 'running' ? '运行' : (row.state === 'degraded' ? '降级' : (row.state === 'starting' ? '启动中' : '停止')) }}
          </template>
        </el-table-column>
        <el-table-column label="PID" width="80" align="right">
          <template #default="{ row }">{{ row.pid || '-' }}</template>
        </el-table-column>
        <el-table-column label="端口" width="80" align="right">
          <template #default="{ row }">{{ row.port || '-' }}</template>
        </el-table-column>
        <el-table-column label="显存" width="90" align="right">
          <template #default="{ row }">{{ row.mem_mib ? formatMiB(row.mem_mib) : '-' }}</template>
        </el-table-column>
        <el-table-column label="健康延迟" width="100" align="right">
          <template #default="{ row }">{{ row.health_latency_ms != null ? row.health_latency_ms + 'ms' : '-' }}</template>
        </el-table-column>
        <el-table-column label="最后心跳" width="140">
          <template #default="{ row }">{{ row.last_health_at ? fmtTime(row.last_health_at) : '-' }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!gw.total" description="暂无模型实例" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { systemStatus, gpuStatus, gatewayHealth, perfInstances } from '../api'
import * as echarts from 'echarts'

const sys = ref({})
const gpu = ref({})
const gw = ref({ total: 0, running: 0, degraded: 0, starting: 0, instances: [] })
const perf = ref({ instances: [] })
const trendHistory = ref([])
const trendMode = ref('tps')
const loadingTrend = ref(true)
const trendRef = ref(null)
let trendChart = null
let gpuTimer = null
let gwTimer = null
let firstPerf = true

const memPercent = computed(() => {
  if (!sys.value.memory_total_gb) return 0
  return Math.round((1 - sys.value.memory_avail_gb / sys.value.memory_total_gb) * 100)
})
const diskPercent = computed(() => {
  if (!sys.value.disk_total_gb) return 0
  return Math.round((1 - sys.value.disk_free_gb / sys.value.disk_total_gb) * 100)
})

function formatMiB(mib) {
  if (mib == null) return '-'
  const gb = mib / 1024
  if (gb >= 1) return gb.toFixed(1) + ' GB'
  return mib + ' MiB'
}

function memColor(ratio) {
  if (ratio > 0.9) return '#f56c6c'
  if (ratio > 0.7) return '#e6a23c'
  return '#409eff'
}

// 核显模型内存：按系统总内存占比展示（共享内存无独立显存上限）
function modelMemPct(mib) {
  const total = sys.value?.memory_total_gb ? sys.value.memory_total_gb * 1024 : 0
  if (!total) return 0
  return Math.min(100, Math.round((mib || 0) / total * 100))
}

async function loadGpu() {
  try {
    gpu.value = await gpuStatus()
  } catch (e) {
    gpu.value = { source: 'unavailable', devices: [], processes: [], inference: {}, error: e.message }
  }
}
async function loadSys() {
  try {
    sys.value = await systemStatus()
  } catch (e) { /* ignore */ }
}
async function loadGateway() {
  try {
    const d = await gatewayHealth()
    gw.value = { total: d.total || 0, running: d.running || 0, degraded: d.degraded || 0, starting: d.starting || 0, instances: d.instances || [] }
  } catch (e) { /* ignore */ }
}
function fmtTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}
function fmtNum(n) {
  if (n == null) return '-'
  return Number(n).toLocaleString()
}
function mtpColor(rate) {
  if (rate >= 0.5) return '#10b981'
  if (rate >= 0.3) return '#f59e0b'
  return '#ef4444'
}

// ---------- 模型实时性能 ----------
async function loadPerf() {
  try {
    const d = await perfInstances()
    perf.value = d
    // 累积趋势采样点（保留最近 60 个 ≈ 5 分钟）
    const totalDecode = (d.instances || []).reduce((s, i) => s + (i.decode_tps || 0), 0)
    const totalPrefill = (d.instances || []).reduce((s, i) => s + (i.prefill_tps || 0), 0)
    const dgpu = (gpu.value.devices || []).find(x => !x.is_integrated)
    const igpu = (gpu.value.devices || []).find(x => x.is_integrated)
    trendHistory.value.push({
      t: Date.now(),
      decode: Math.round(totalDecode * 10) / 10,
      prefill: Math.round(totalPrefill * 10) / 10,
      vram: dgpu ? dgpu.memory_used_mib : null,
      vramTotal: dgpu ? dgpu.memory_total_mib : null,
      power: dgpu ? dgpu.power_draw_w : null,
      igpuMem: igpu ? igpu.model_memory_mib : null,
    })
    if (trendHistory.value.length > 60) trendHistory.value.shift()
    renderTrend()
  } catch (e) { /* ignore */ }
}

function initTrend() {
  if (!trendChart && trendRef.value) {
    trendChart = echarts.init(trendRef.value)
  }
}

function renderTrend() {
  if (!trendChart) return
  const h = trendHistory.value
  const times = h.map(p => {
    const d = new Date(p.t)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
  })
  let series = []
  let yAxis = []
  if (trendMode.value === 'tps') {
    series = [
      { name: 'Decode t/s', type: 'line', data: h.map(p => p.decode), smooth: true, symbol: 'none', lineStyle: { color: '#2563eb', width: 2.5 }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(37,99,235,.25)' }, { offset: 1, color: 'rgba(37,99,235,0)' }]) } },
      { name: 'Prefill t/s', type: 'line', data: h.map(p => p.prefill), smooth: true, symbol: 'none', lineStyle: { color: '#8b5cf6', width: 2 } },
    ]
    yAxis = [{ type: 'value', name: 't/s', splitLine: { lineStyle: { color: '#f1f5f9' } } }]
  } else if (trendMode.value === 'vram') {
    series = [
      { name: '独显占用', type: 'line', data: h.map(p => p.vram), smooth: true, symbol: 'none', lineStyle: { color: '#f59e0b', width: 2.5 }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(245,158,11,.25)' }, { offset: 1, color: 'rgba(245,158,11,0)' }]) } },
      { name: '核显模型内存', type: 'line', data: h.map(p => p.igpuMem), smooth: true, symbol: 'none', lineStyle: { color: '#06b6d4', width: 2 } },
    ]
    yAxis = [{ type: 'value', name: 'MiB', splitLine: { lineStyle: { color: '#f1f5f9' } } }]
  } else {
    series = [
      { name: '功耗 W', type: 'line', data: h.map(p => p.power), smooth: true, symbol: 'none', lineStyle: { color: '#ef4444', width: 2.5 }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(239,68,68,.25)' }, { offset: 1, color: 'rgba(239,68,68,0)' }]) } },
    ]
    yAxis = [{ type: 'value', name: 'W', splitLine: { lineStyle: { color: '#f1f5f9' } } }]
  }
  trendChart.setOption({
    tooltip: { trigger: 'axis', backgroundColor: '#0f172a', textStyle: { color: '#e2e8f0', fontSize: 12 }, borderWidth: 0 },
    legend: { top: 0, textStyle: { fontSize: 12 } },
    grid: { left: 56, right: 40, top: 34, bottom: 24 },
    xAxis: { type: 'category', data: times, axisLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { fontSize: 10 } },
    yAxis,
    series,
  }, true)
}

watch(trendMode, () => renderTrend())

function resizeCharts() {
  trendChart?.resize()
}

onMounted(() => {
  loadSys()
  loadGpu()
  loadGateway()
  loadPerf()
  gpuTimer = setInterval(() => {
    loadGpu()
    loadPerf()
  }, 5000)
  gwTimer = setInterval(loadGateway, 5000)
  nextTick(() => {
    initTrend()
    loadingTrend.value = false
    renderTrend()
  })
  window.addEventListener('resize', resizeCharts)
})
onUnmounted(() => {
  if (gpuTimer) clearInterval(gpuTimer)
  if (gwTimer) clearInterval(gwTimer)
  window.removeEventListener('resize', resizeCharts)
  trendChart?.dispose()
})
</script>

<style scoped>
.big-num { font-size: 32px; font-weight: 700; color: #303133; margin-bottom: 12px; }
.big-num small { font-size: 14px; font-weight: 400; color: #909399; }
.sub-info { color: #909399; font-size: 12px; margin-top: 8px; }

.gpu-device-block {
  padding: 12px 0;
  border-bottom: 1px solid #ebeef5;
}
.gpu-device-block:last-child { border-bottom: none; }
.gpu-dev-name { font-size: 16px; font-weight: 600; color: #303133; }
.gpu-dev-bdf { font-size: 12px; color: #909399; font-family: 'JetBrains Mono', Consolas, monospace; margin-top: 2px; }
.gpu-stats-row { display: flex; justify-content: space-between; padding: 3px 0; }
.gpu-stat-label { color: #909399; font-size: 13px; }
.gpu-stat-val { color: #303133; font-size: 13px; font-weight: 500; }
.gpu-mem-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.gpu-mem-text { font-size: 13px; color: #303133; font-weight: 500; }

.inference-block { text-align: center; padding: 8px 0; }
.inference-num { font-size: 28px; font-weight: 700; color: #409eff; }
.inference-label { font-size: 12px; color: #909399; margin-top: 4px; }

/* 移动端 */
@media (max-width: 767px) {
  .big-num { font-size: 26px; }
  .el-col + .el-col { margin-top: 12px; }
  .gpu-stats-row { font-size: 12px; }
}
</style>
