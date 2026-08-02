<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="card-title">
        <span>模型池管理</span>
        <el-tag v-if="routerHealthy" size="small" type="success" style="margin-left:8px">Router 在线</el-tag>
        <el-tag v-else size="small" type="danger" style="margin-left:8px">Router 离线</el-tag>
        <el-button size="small" @click="refresh" style="margin-left:auto"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>

      <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px">
        单容器一体化架构：llama-server router 自动发现 /models 目录下的 GGUF 模型，点击「加载」将模型载入 GPU 显存
      </el-alert>

      <el-table :data="services" v-loading="loading" stripe class="mobile-table" :row-key="row => row.name" :expand-row-keys="expandedRowKeys">
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span class="status-dot" :class="'status-' + (row.loaded ? 'running' : 'stopped')"></span>
            {{ row.loaded ? '已加载' : '未加载' }}
          </template>
        </el-table-column>
        <el-table-column prop="name" label="模型名" min-width="200">
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push('/services/' + row.id)" v-if="row.id">{{ row.name }}</el-link>
            <span v-else>{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="量化" width="100">
          <template #default="{ row }">
            <el-tag size="small" v-if="row.loaded_info?.quant || row.quantization">{{ row.loaded_info?.quant || row.quantization }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="显存占用" width="120">
          <template #default="{ row }">
            <span v-if="row.loaded_info?.mem_total" class="mono">{{ formatSize(row.loaded_info.mem_total) }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="上下文" width="100">
          <template #default="{ row }">
            <span v-if="row.loaded_info?.ctx_size" class="mono">{{ row.loaded_info.ctx_size }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280">
          <template #default="{ row }">
            <el-button v-if="!row.loaded" type="success" size="small" :loading="loadingModel === row.name" @click="doLoad(row)">
              加载
            </el-button>
            <el-button v-else type="warning" size="small" :loading="loadingModel === row.name" @click="doUnload(row)">
              卸载
            </el-button>
            <el-button size="small" @click="$router.push('/services/' + row.id)" :disabled="!row.id">详情</el-button>
            <el-button v-if="row.status === 'unavailable'" size="small" type="danger" @click="doDelete(row)" :disabled="!row.id">删除</el-button>
          </template>
        </el-table-column>

        <!-- 行展开：加载进度 -->
        <el-table-column type="expand" width="1">
          <template #default="{ row }">
            <div v-if="loadingModel === row.name || unloadingModel === row.name" class="loading-panel">
              <el-progress
                :percentage="loadProgress"
                :indeterminate="loadProgress < 30"
                :status="loadProgress >= 100 ? 'success' : ''"
                :stroke-width="16"
                style="margin-bottom:8px"
              />
              <div class="load-status-text">{{ loadStatusText }}</div>
              <div class="load-log-title">加载日志（最新 50 行）</div>
              <div ref="logContainer" class="load-log-view">
                <div v-for="(line, i) in loadLogs" :key="i" class="log-line">{{ line }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && services.length === 0" description="未发现模型，请将 GGUF 文件放入模型目录" />
    </el-card>

    <!-- Router 详情面板 -->
    <el-card shadow="never" style="margin-top:16px" v-if="routerInfo">
      <div class="card-title"><span>Router 详情</span></div>
      <el-descriptions :column="2" size="small" border>
        <el-descriptions-item label="Router URL">{{ routerInfo.router_url }}</el-descriptions-item>
        <el-descriptions-item label="健康状态">
          <el-tag size="small" :type="routerInfo.healthy ? 'success' : 'danger'">{{ routerInfo.healthy ? '在线' : '离线' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="模型目录">{{ routerInfo.model_dir }}</el-descriptions-item>
        <el-descriptions-item label="最大驻留数">{{ routerInfo.models_max }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="routerInfo.loaded_models?.length" style="margin-top:12px">
        <div style="font-size:13px;color:#606266;margin-bottom:8px">当前驻留模型：</div>
        <el-tag v-for="m in routerInfo.loaded_models" :key="m.model || m.id" size="small" type="success" style="margin:2px">
          {{ m.model || m.id }} ({{ formatSize(m.mem_total) }})
        </el-tag>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  listServices, startService, stopService, deleteService, routerStatus,
  getServiceLogs,
} from '../api'

const services = ref([])
const loading = ref(false)
const loadingModel = ref('')
const unloadingModel = ref('')
const routerHealthy = ref(false)
const routerInfo = ref(null)

// 加载进度相关
const loadProgress = ref(0)
const loadStatusText = ref('')
const loadLogs = ref([])
const logContainer = ref(null)
const expandedRowKeys = ref([])
let pollTimer = null
let logTimer = null

function formatSize(bytes) {
  if (!bytes) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(1) + units[i]
}

async function refresh() {
  loading.value = true
  try {
    const [svcList, status] = await Promise.all([listServices(), routerStatus()])
    services.value = svcList
    routerHealthy.value = status.healthy
    routerInfo.value = status
  } catch (e) {
    ElMessage.error('加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function findServiceRow(modelName) {
  return services.value.find(s => s.name === modelName)
}

async function pollLogs(modelName) {
  try {
    const row = findServiceRow(modelName)
    if (!row?.id) return
    const data = await getServiceLogs(row.id, { tail: 50 })
    const lines = (data.logs || data || '').split('\n').filter(l => l.trim())
    loadLogs.value = lines.slice(-50)
    await nextTick()
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  } catch (e) { /* ignore log errors */ }
}

async function pollServiceStatus(modelName, isLoad) {
  try {
    await refresh()
    const row = findServiceRow(modelName)
    if (!row) return false

    if (isLoad) {
      const status = (row.status || '').toLowerCase()
      if (status === 'loaded' || row.loaded) {
        loadProgress.value = 100
        loadStatusText.value = '加载完成'
        ElMessage.success(`${modelName} 加载完成`)
        return true
      }
      if (status === 'unavailable' || status === 'error') {
        loadStatusText.value = '加载失败'
        ElMessage.error(`${modelName} 加载失败`)
        return true
      }
      // 递进进度
      if (loadProgress.value < 30) loadProgress.value = 30
      else if (loadProgress.value < 60) loadProgress.value = 60
      else if (loadProgress.value < 80) loadProgress.value = 80
      loadStatusText.value = '模型加载中，冷启动约需 1-2 分钟…'
    } else {
      const status = (row.status || '').toLowerCase()
      if (!row.loaded || status === 'unloaded') {
        ElMessage.success(`${modelName} 已卸载`)
        return true
      }
      if (status === 'unavailable' || status === 'error') {
        ElMessage.error(`${modelName} 卸载失败`)
        return true
      }
    }
    return false
  } catch (e) {
    return false
  }
}

function clearTimers() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (logTimer) { clearInterval(logTimer); logTimer = null }
}

async function doLoad(row) {
  if (loadingModel.value) {
    ElMessage.warning('已有模型正在加载，请等待完成')
    return
  }
  loadingModel.value = row.name
  loadProgress.value = 0
  loadStatusText.value = '正在启动加载…'
  loadLogs.value = []

  // 自动展开当前行显示进度
  expandedRowKeys.value = [row.name]
  services.value = services.value.map(s => ({ ...s }))

  try {
    await startService(row.id)
    loadProgress.value = 30
    loadStatusText.value = '模型加载中，冷启动约需 1-2 分钟…'

    // 立即拉一次日志
    await pollLogs(row.name)

    // 轮询状态 + 日志
    pollTimer = setInterval(async () => {
      const done = await pollServiceStatus(row.name, true)
      if (done) {
        clearTimers()
        setTimeout(() => {
          loadingModel.value = ''
          loadProgress.value = 0
          loadStatusText.value = ''
          loadLogs.value = []
          expandedRowKeys.value = []
          services.value = services.value.map(s => ({ ...s }))
        }, 1500)
      }
    }, 2000)

    logTimer = setInterval(() => pollLogs(row.name), 2000)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载失败')
    loadingModel.value = ''
    loadProgress.value = 0
    loadStatusText.value = ''
    loadLogs.value = []
    expandedRowKeys.value = []
  }
}

async function doUnload(row) {
  if (loadingModel.value || unloadingModel.value) {
    ElMessage.warning('已有操作正在进行，请等待完成')
    return
  }
  unloadingModel.value = row.name
  loadStatusText.value = '正在卸载…'
  loadProgress.value = 50
  loadLogs.value = []

  // 自动展开当前行显示进度
  expandedRowKeys.value = [row.name]
  services.value = services.value.map(s => ({ ...s }))

  try {
    await stopService(row.id)
    loadStatusText.value = '卸载中…'

    pollTimer = setInterval(async () => {
      const done = await pollServiceStatus(row.name, false)
      if (done) {
        clearTimers()
        setTimeout(() => {
          unloadingModel.value = ''
          loadProgress.value = 0
          loadStatusText.value = ''
          loadLogs.value = []
          expandedRowKeys.value = []
          services.value = services.value.map(s => ({ ...s }))
        }, 1500)
      }
    }, 2000)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '卸载失败')
    unloadingModel.value = ''
    loadProgress.value = 0
    loadStatusText.value = ''
    loadLogs.value = []
    expandedRowKeys.value = []
  }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`确认删除模型注册「${row.name}」？（不会删除模型文件）`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await deleteService(row.id)
    ElMessage.success('已删除')
    refresh()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(refresh)
onUnmounted(clearTimers)
</script>

<style scoped>
.form-tip { font-size: 12px; color: #909399; margin-top: 4px; }

.loading-panel {
  padding: 16px 20px;
  background: #fafafa;
}
.load-status-text {
  font-size: 13px; color: #606266; margin-bottom: 12px;
}
.load-log-title {
  font-size: 12px; color: #909399; margin-bottom: 6px;
}
.load-log-view {
  background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 6px;
  font-size: 12px; font-family: 'JetBrains Mono', Consolas, monospace;
  max-height: 300px; overflow-y: auto; line-height: 1.6;
}
.log-line { white-space: pre-wrap; word-break: break-all; }

/* 移动端 */
@media (max-width: 767px) {
  .mobile-table :deep(.el-table__cell) { padding: 4px 0; }
  .mobile-table :deep(.cell) { padding: 0 4px; }
  :deep(.el-button + .el-button) { margin-left: 4px; }
  .loading-panel { padding: 12px 8px; }
}
</style>
