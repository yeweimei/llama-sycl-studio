<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="card-title">
        <span>模型池管理</span>
        <el-tag v-if="routerHealthy" size="small" type="success" style="margin-left:8px">Router 在线</el-tag>
        <el-tag v-else size="small" type="danger" style="margin-left:8px">Router 离线</el-tag>
        <el-button type="primary" size="small" @click="openCreate" style="margin-left:auto">
          <el-icon><Plus /></el-icon>&nbsp;注册模型
        </el-button>
        <el-button size="small" @click="refresh"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>

      <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px">
        单容器一体化架构：llama-server router 自动发现 /models 目录下的 GGUF 模型，点击「启动」将模型载入 GPU 显存
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
              启动
            </el-button>
            <el-button v-else type="warning" size="small" :loading="loadingModel === row.name" @click="doUnload(row)">
              停止
            </el-button>
            <el-button size="small" @click="openEdit(row)" :disabled="!row.id">编辑</el-button>
            <el-button size="small" @click="$router.push('/services/' + row.id)" :disabled="!row.id">详情</el-button>
            <el-dropdown trigger="click" @command="(cmd) => onMore(cmd, row)">
              <el-button size="small">更多<el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="restart" :disabled="!row.loaded">重启</el-dropdown-item>
                  <el-dropdown-item command="delete" divided style="color:#f56c6c">删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
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

    <!-- 注册模型对话框 -->
    <el-dialog v-model="createVisible" title="注册模型" width="720px" top="8vh">
      <el-form :model="form" label-width="110px">
        <el-form-item label="模型名称" required>
          <el-input v-model="form.name" placeholder="如 qwen3.5-9b" />
        </el-form-item>
        <el-form-item label="模型路径" required>
          <el-select v-if="!useManualPath" v-model="form.model_path" filterable placeholder="选择已下载模型" style="width:100%">
            <el-option v-for="m in modelList" :key="m.path" :label="`${m.name} (${m.size_human}${m.quantization ? ' ' + m.quantization : ''})`" :value="m.path" />
          </el-select>
          <el-input v-else v-model="form.model_path" placeholder="/models/xxx.gguf" />
          <el-button link type="primary" @click="useManualPath = !useManualPath" style="margin-top:4px">
            {{ useManualPath ? '选择已下载模型' : '手动输入路径' }}
          </el-button>
          <div class="form-tip">支持 .gguf 文件或 HF 目录（含 config.json）</div>
        </el-form-item>
        <el-form-item label="显卡">
          <el-select v-model="form.gpu_id" placeholder="选择 GPU" style="width:100%" clearable>
            <el-option v-for="g in gpuList" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-divider content-position="left">推理参数</el-divider>
        <ParamForm v-model="form.preset" />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">注册</el-button>
      </template>
    </el-dialog>

    <!-- 编辑模型对话框 -->
    <el-dialog v-model="editVisible" title="编辑模型" width="720px" top="8vh">
      <el-form :model="editForm" label-width="110px">
        <el-form-item label="模型名称" required>
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="模型路径" required>
          <el-select v-if="!editUseManualPath" v-model="editForm.model_path" filterable placeholder="选择已下载模型" style="width:100%">
            <el-option v-for="m in modelList" :key="m.path" :label="`${m.name} (${m.size_human}${m.quantization ? ' ' + m.quantization : ''})`" :value="m.path" />
          </el-select>
          <el-input v-else v-model="editForm.model_path" placeholder="/models/xxx.gguf" />
          <el-button link type="primary" @click="editUseManualPath = !editUseManualPath" style="margin-top:4px">
            {{ editUseManualPath ? '选择已下载模型' : '手动输入路径' }}
          </el-button>
        </el-form-item>
        <el-form-item label="显卡">
          <el-select v-model="editForm.gpu_id" placeholder="选择 GPU" style="width:100%" clearable>
            <el-option v-for="g in gpuList" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-divider content-position="left">推理参数</el-divider>
        <ParamForm v-model="editForm.preset" />
        <div class="form-tip" style="margin-top:4px">推理参数通过模型预设(config.ini)生效，保存后需重启容器加载。</div>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSaveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, ArrowDown } from '@element-plus/icons-vue'
import ParamForm from '../components/ParamForm.vue'
import {
  listServices, startService, stopService, deleteService, routerStatus,
  getServiceLogs, createService, updateService, restartService,
  listPresets, createPreset, updatePreset,
  listModels, getSelectableGpus,
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

// 新建/编辑对话框
const createVisible = ref(false)
const creating = ref(false)
const DEFAULT_PRESET = {
  ctx_size: 8192, temp: 0.7, threads: 8, batch_size: 2048,
  ubatch_size: 512, parallel: 4, cache_type_k: 'q8_0', cache_type_v: 'q8_0',
  flash_attn: true, jinja: true, n_gpu_layers: 99, mmap: true,
}
const form = ref({ name: '', model_path: '', gpu_id: '', preset: { ...DEFAULT_PRESET } })
const useManualPath = ref(false)
const editUseManualPath = ref(false)
const modelList = ref([])
const gpuList = ref([])
const editVisible = ref(false)
const saving = ref(false)
const editForm = ref({ id: null, name: '', model_path: '', presetId: null, preset: { ...DEFAULT_PRESET } })
const _allPresets = ref([])

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

// ---------- 新建/编辑 ----------

function openCreate() {
  form.value = { name: '', model_path: '', gpu_id: gpuList.value[0]?.id || '', preset: { ...DEFAULT_PRESET } }
  useManualPath.value = false
  createVisible.value = true
}

async function doCreate() {
  if (!form.value.name || !form.value.model_path) {
    ElMessage.warning('请填写模型名称和模型路径')
    return
  }
  creating.value = true
  try {
    await createService({ name: form.value.name, model_path: form.value.model_path, gpu_id: form.value.gpu_id || null })
    // 保存推理参数为预设
    try {
      await createPreset({ model_name: form.value.name, ...form.value.preset })
    } catch (e) {
      // 预设已存在则忽略，用户可在编辑时更新
    }
    ElMessage.success('已注册，点击「启动」加载模型')
    createVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '注册失败')
  } finally {
    creating.value = false
  }
}

async function openEdit(row) {
  // 拉取预设列表，找当前模型的预设
  try {
    _allPresets.value = await listPresets()
  } catch (e) {
    _allPresets.value = []
  }
  const found = _allPresets.value.find(p => p.model_name === row.name)
  editForm.value = {
    id: row.id,
    name: row.name,
    model_path: row.model_path,
    gpu_id: row.gpu_id || '',
    presetId: found?.id || null,
    preset: found ? { ...found } : { ...DEFAULT_PRESET },
  }
  editUseManualPath.value = !modelList.value.some(m => m.path === row.model_path)
  editVisible.value = true
}

async function doSaveEdit() {
  if (!editForm.value.name || !editForm.value.model_path) {
    ElMessage.warning('请填写模型名称和模型路径')
    return
  }
  saving.value = true
  try {
    // 1. 保存基本信息到 services 表
    await updateService(editForm.value.id, {
      name: editForm.value.name,
      model_path: editForm.value.model_path,
      gpu_id: editForm.value.gpu_id || null,
    })
    // 2. 保存推理参数到 model_presets 表（upsert）
    const p = editForm.value.preset
    const payload = {
      ctx_size: p.ctx_size, temp: p.temp, threads: p.threads,
      batch_size: p.batch_size, ubatch_size: p.ubatch_size, parallel: p.parallel,
      cache_type_k: p.cache_type_k, cache_type_v: p.cache_type_v,
      flash_attn: p.flash_attn, jinja: p.jinja, n_gpu_layers: p.n_gpu_layers,
    }
    if (editForm.value.presetId) {
      await updatePreset(editForm.value.presetId, payload)
    } else {
      await createPreset({ model_name: editForm.value.name, ...payload })
    }
    ElMessage.success('已保存（推理参数需重启容器后生效）')
    editVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

// ---------- 更多操作 ----------

async function onMore(cmd, row) {
  if (cmd === 'restart') await doRestart(row)
  else if (cmd === 'delete') await doDelete(row)
}

async function doRestart(row) {
  if (loadingModel.value) {
    ElMessage.warning('已有操作正在进行，请等待完成')
    return
  }
  loadingModel.value = row.name
  loadProgress.value = 10
  loadStatusText.value = '正在重启（卸载中）…'
  loadLogs.value = []

  // 展开行显示进度
  expandedRowKeys.value = [row.name]
  services.value = services.value.map(s => ({ ...s }))

  try {
    await restartService(row.id)
    loadProgress.value = 60
    loadStatusText.value = '模型加载中…'
    await pollLogs(row.name)

    // 轮询直到 loaded
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
    ElMessage.error(e.response?.data?.detail || '重启失败')
    loadingModel.value = ''
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

onMounted(async () => {
  refresh()
  try {
    const [models, gpus] = await Promise.all([listModels(), getSelectableGpus()])
    modelList.value = models
    gpuList.value = gpus
  } catch (e) { /* ignore */ }
})
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
