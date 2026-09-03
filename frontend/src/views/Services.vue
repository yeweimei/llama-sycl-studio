<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="card-title">
        <span>模型池管理</span>
        <el-tag size="small" type="success" style="margin-left:8px">实例模式</el-tag>
        <el-button type="primary" size="small" @click="openCreate" style="margin-left:auto">
          <el-icon><Plus /></el-icon>&nbsp;注册模型
        </el-button>
        <el-button size="small" @click="refresh"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>

      <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px">
        实例模式：每个模型独立 llama-server 进程，上下文各自独立；点击「启动」将模型载入 GPU 显存
      </el-alert>

      <el-table :data="services" v-loading="loading" stripe class="mobile-table" :row-key="row => row.name" :expand-row-keys="expandedRowKeys" @expand-change="onExpandChange">
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span class="status-dot" :class="'status-' + (row.state || (row.loaded ? 'running' : 'stopped'))"></span>
            {{ row.state === 'degraded' ? '降级' : (row.state === 'starting' ? '启动中' : (row.loaded ? '已加载' : '未加载')) }}
            <el-tooltip v-if="row.state === 'degraded'" content="进程存活但健康检查失败（可能卡死），转发会失败" placement="top">
              <span style="cursor:help; color:#e6a23c">⚠️</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="模型名" min-width="200">
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push('/services/' + row.id)" v-if="row.id">{{ row.name }}</el-link>
            <span v-else>{{ row.name }}</span>
            <div v-if="svcTags(row.name).length" style="margin-top:2px">
              <el-tag v-for="t in svcTags(row.name)" :key="t" size="small" effect="plain" style="margin-right:2px">{{ t }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="设备" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.loaded && row.device_label" size="small" :type="row.device_label.includes('核显') ? 'success' : ''">{{ row.device_label }}</el-tag>
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
        <el-table-column label="操作" width="330">
          <template #default="{ row }">
            <el-button v-if="!row.loaded" type="success" size="small" :loading="loadingModel === row.name" @click="doLoad(row)">
              启动
            </el-button>
            <el-button v-else type="warning" size="small" :loading="loadingModel === row.name" @click="doUnload(row)">
              停止
            </el-button>
            <el-button size="small" :loading="restartingModel === row.name" @click="doRestart(row)" :disabled="!row.loaded">重启</el-button>
            <el-button size="small" @click="openEdit(row)" :disabled="!row.id">编辑</el-button>
            <el-button size="small" @click="$router.push('/services/' + row.id)" :disabled="!row.id">详情</el-button>
            <el-dropdown trigger="click" @command="(cmd) => onMore(cmd, row)">
              <el-button size="small">更多<el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="delete" style="color:#f56c6c">删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>

        <!-- 行展开：加载进度 + 进程详情 -->
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
            <div v-else-if="row.loaded" class="proc-panel">
              <el-descriptions :column="4" size="small" border>
                <el-descriptions-item label="端口">{{ row.port || '-' }}</el-descriptions-item>
                <el-descriptions-item label="设备">{{ row.device || '-' }} ({{ row.device_label || '-' }})</el-descriptions-item>
                <el-descriptions-item label="PID">{{ row.pid || '-' }}</el-descriptions-item>
                <el-descriptions-item label="显存">{{ row.loaded_info?.mem_total ? formatSize(row.loaded_info.mem_total) : '-' }}</el-descriptions-item>
              </el-descriptions>
              <!-- 运行日志（加载后仍可查看） -->
              <el-collapse style="margin-top:8px">
                <el-collapse-item :title="`运行日志（最新 ${runLogs[row.name]?.length || 0} 行）`">
                  <div ref="runLogContainer" class="load-log-view" style="height:240px">
                    <div v-if="!(runLogs[row.name] || []).length" style="color:#909399;font-size:13px">暂无日志，点击展开后自动拉取</div>
                    <div v-for="(line, i) in (runLogs[row.name] || [])" :key="i" class="log-line">{{ line }}</div>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>
            <div v-else class="proc-panel">
              <span style="color:#909399;font-size:13px">未加载</span>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && services.length === 0" description="未发现模型，请将 GGUF 文件放入模型目录" />
    </el-card>

    <!-- 注册模型对话框 -->
    <el-dialog v-model="createVisible" title="注册模型" width="720px" top="8vh">
      <el-form :model="form" label-width="110px">
        <el-form-item label="模型名称">
          <el-input v-model="form.name" :placeholder="autoNameHint || '自动推导（无需填写）'" :disabled="!useManualName">
            <template #prepend><el-tag size="small" type="info" style="border:none">自动</el-tag></template>
            <template #append>
              <el-button @click="toggleNameManual">{{ useManualName ? '改自动' : '手动' }}</el-button>
            </template>
          </el-input>
          <div class="form-tip">默认自动按模型文件/目录推导为 router ID，无需手填；也可切换手动自定义</div>
        </el-form-item>
        <el-form-item label="模型路径" required>
          <el-select v-if="!useManualPath" v-model="form.model_path" filterable placeholder="选择已下载模型" style="width:100%" @change="onPathChange">
            <el-option v-for="m in modelList" :key="m.path" :label="`${m.name} (${m.size_human}${m.quantization ? ' ' + m.quantization : ''})`" :value="m.path" />
          </el-select>
          <el-input v-else v-model="form.model_path" placeholder="/models/xxx.gguf" />
          <el-button link type="primary" @click="useManualPath = !useManualPath" style="margin-top:4px">
            {{ useManualPath ? '选择已下载模型' : '手动输入路径' }}
          </el-button>
          <div class="form-tip">支持 .gguf 文件或 HF 目录（含 config.json）</div>
        </el-form-item>
        <el-form-item label="显卡">
          <el-select v-model="form.gpu_id" placeholder="选择设备" style="width:100%">
            <el-option v-for="g in deviceOptions" :key="g.value" :label="g.label" :value="g.value" />
          </el-select>
          <div class="form-tip">自动/独显/核显（后端无关，启动时按当前引擎映射）</div>
        </el-form-item>
        <el-form-item label="空闲卸载">
          <el-select v-model="form.idle_unload_min" style="width:100%">
            <el-option v-for="o in idleUnloadOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <div class="form-tip">模型无调用超过设定时间后自动卸载释放显存</div>
        </el-form-item>
        <el-divider content-position="left">推理参数</el-divider>
        <ParamForm v-model="form.preset" :model-path="form.model_path" :mmproj-path="form.mmproj_path || ''" :gpu-total-gi-b="gpuTotalGiB" :supports-chat="supportsChatFor(form.model_path)" />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">注册</el-button>
      </template>
    </el-dialog>

    <!-- 编辑模型对话框 -->
    <el-dialog v-model="editVisible" title="编辑模型" width="720px" top="8vh">
      <el-form :model="editForm" label-width="110px">
        <el-form-item label="模型名称">
          <el-input v-model="editForm.name" placeholder="自动推导（无需填写）">
            <template #prepend><el-tag size="small" type="info" style="border:none">自动</el-tag></template>
            <template #append>
              <el-button @click="toggleEditNameManual">{{ editUseManualName ? '改自动' : '手动' }}</el-button>
            </template>
          </el-input>
          <div class="form-tip">默认自动按模型文件/目录推导为 router ID；切换手动可自定义</div>
        </el-form-item>
        <el-form-item label="模型路径" required>
          <el-select v-if="!editUseManualPath" v-model="editForm.model_path" filterable placeholder="选择已下载模型" style="width:100%" @change="onEditPathChange">
            <el-option v-for="m in modelList" :key="m.path" :label="`${m.name} (${m.size_human}${m.quantization ? ' ' + m.quantization : ''})`" :value="m.path" />
          </el-select>
          <el-input v-else v-model="editForm.model_path" placeholder="/models/xxx.gguf" />
          <el-button link type="primary" @click="editUseManualPath = !editUseManualPath" style="margin-top:4px">
            {{ editUseManualPath ? '选择已下载模型' : '手动输入路径' }}
          </el-button>
        </el-form-item>
        <el-form-item label="显卡">
          <el-select v-model="editForm.gpu_id" placeholder="选择设备" style="width:100%">
            <el-option v-for="g in deviceOptions" :key="g.value" :label="g.label" :value="g.value" />
          </el-select>
          <div class="form-tip">自动/独显/核显（后端无关，启动时按当前引擎映射）</div>
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="editForm.custom_tags" multiple filterable allow-create default-first-option
            placeholder="选择或输入标签（回车添加）" style="width:100%">
            <el-option v-for="t in allTagOptions" :key="t" :label="t" :value="t" />
          </el-select>
          <div class="form-tip">自定义标签；自动标签（思考/多模态/Embedding 等）按模型名自动生成</div>
        </el-form-item>
        <el-form-item label="空闲卸载">
          <el-select v-model="editForm.idle_unload_min" style="width:100%">
            <el-option v-for="o in idleUnloadOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <div class="form-tip">模型无调用超过设定时间后自动卸载释放显存；选"一直保持"则常驻</div>
        </el-form-item>
        <el-divider content-position="left">推理参数</el-divider>
        <ParamForm v-model="editForm.preset" :model-path="editForm.model_path" :mmproj-path="editForm.mmproj_path || ''" :gpu-total-gi-b="gpuTotalGiB" :supports-chat="supportsChatFor(editForm.model_path)" />
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
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, ArrowDown } from '@element-plus/icons-vue'
import ParamForm from '../components/ParamForm.vue'
import {
  listServices, startService, stopService, deleteService,
  getServiceLogs, createService, updateService, restartService,
  listPresets, createPreset, updatePreset,
  listModels, getSelectableGpus, listModelTags, updateModelTags,
  gpuStatus, getEngineBackends,
} from '../api'

const services = ref([])
const loading = ref(false)
const loadingModel = ref('')
const unloadingModel = ref('')
const restartingModel = ref('')

// 加载进度相关
const loadProgress = ref(0)
const loadStatusText = ref('')
const loadLogs = ref([])
// 运行日志缓存: {modelName: [lines]}（加载完成后展开行查看）
const runLogs = ref({})
const runLogContainer = ref(null)
const logContainer = ref(null)
const expandedRowKeys = ref([])

function onExpandChange(row, expandedRows) {
  // 手动展开/收起时同步受控展开状态（加载/卸载/重启的自动展开仍走 expandedRowKeys 赋值）
  expandedRowKeys.value = expandedRows.map(r => r.name)
  // 展开已加载模型时自动拉取运行日志
  const expanded = expandedRows.find(r => r.name === row.name)
  if (expanded && row.loaded && !runLogs.value[row.name]) {
    fetchRunLogs(row)
  }
}

async function fetchRunLogs(row, tail = 100) {
  try {
    if (!row?.id) return
    const data = await getServiceLogs(row.id, { tail })
    const lines = (data.logs || data || '').split('\n').filter(l => l.trim())
    runLogs.value = { ...runLogs.value, [row.name]: lines.slice(-tail) }
    await nextTick()
    if (runLogContainer.value) {
      runLogContainer.value.scrollTop = runLogContainer.value.scrollHeight
    }
  } catch (e) { /* ignore */ }
}
let pollTimer = null
let logTimer = null

// 新建/编辑对话框
const createVisible = ref(false)
const creating = ref(false)
// 判断模型是否支持对话/思考（embedding/rerank/OCR 等专用模型不显示思考区块）
// 与后端 _supports_chat 对齐，并补充 ocr/paddle 等视觉/专用模型
function supportsChatFor(modelPath) {
  if (!modelPath) return true
  const nl = String(modelPath).toLowerCase()
  const kw = ['embedding', 'embed-', 'rerank', 'bge-', 'bge_', 'paddleocr', 'ocr']
  return !kw.some(k => nl.includes(k))
}

const DEFAULT_PRESET = {
  ctx_size: 8192, temp: 0.7, threads: 8, batch_size: 2048,
  ubatch_size: 512, parallel: 4, cache_type_k: 'q8_0', cache_type_v: 'q8_0',
  flash_attn: true, jinja: true, n_gpu_layers: 99, mmap: true, cpu_moe: false, cpu_moe_layers: 0, mtp: false, mtp_model: '', mtp_n_max: 3,
}
const form = ref({ name: '', model_path: '', gpu_id: '', idle_unload_min: 0, preset: { ...DEFAULT_PRESET } })
const useManualPath = ref(false)
const editUseManualPath = ref(false)
const useManualName = ref(false)
const autoNameHint = ref('')
const modelList = ref([])
const gpuList = ref([])
const currentBackend = ref('sycl-fp16')
// 设备兜底：语义角色（后端无关，启动时按当前引擎解析）
const defaultDevice = () => 'auto'
// 设备选项：语义角色 + 当前后端解析到的具体设备名（从 selectable 推导）
const deviceOptions = computed(() => {
  const byRole = { discrete: [], integrated: [] }
  for (const g of gpuList.value) {
    const role = (g.name || '').includes('核显') ? 'integrated' : 'discrete'
    byRole[role].push(g.id)
  }
  const fmt = (role, label) => {
    const ids = byRole[role] || []
    return { value: role, label: ids.length ? `${label} (${ids.join(' / ')})` : label }
  }
  return [
    { value: 'auto', label: '自动' },
    fmt('discrete', '独显'),
    fmt('integrated', '核显'),
  ]
})
const gpuTotalGiB = ref(0)

// 空闲自动卸载选项（分钟）
const idleUnloadOptions = [
  { value: 0, label: '一直保持（不自动卸载）' },
  { value: 15, label: '15 分钟无调用自动卸载' },
  { value: 30, label: '30 分钟无调用自动卸载' },
  { value: 60, label: '1 小时无调用自动卸载' },
  { value: 120, label: '2 小时无调用自动卸载' },
  { value: 240, label: '4 小时无调用自动卸载' },
]

// 从模型路径自动推导 router ID（与后端 _match_router_id 规则一致）：
// 子目录模型取目录名（llama.cpp router 的 ID），根目录文件取文件名去扩展名
function deriveNameFromPath(path) {
  if (!path) return ''
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 0) return ''
  const file = parts[parts.length - 1]
  const dir = parts.length >= 2 ? parts[parts.length - 2] : ''
  if (file.endsWith('.gguf') || file.endsWith('.safetensors') || file.endsWith('.bin')) {
    // 子目录模型：目录名（router ID 用目录名）；根目录模型：文件名去扩展名
    if (dir && dir !== 'models') return dir
    return file.replace(/\.(gguf|safetensors|bin)$/i, '')
  }
  return file
}

function onPathChange() {
  if (useManualName.value) return
  const derived = deriveNameFromPath(form.value.model_path)
  form.value.name = derived
  autoNameHint.value = derived ? `自动：${derived}` : ''
}

function toggleNameManual() {
  useManualName.value = !useManualName.value
  if (!useManualName.value) onPathChange()
}
const svcTagMap = ref({})

function svcTags(name) {
  const t = svcTagMap.value[name]
  if (!t) return []
  return [...(t.tags || []), ...(t.custom_tags || [])]
}
const editVisible = ref(false)
const saving = ref(false)
const editForm = ref({ id: null, name: '', model_path: '', presetId: null, preset: { ...DEFAULT_PRESET }, custom_tags: [], idle_unload_min: 0 })
const editUseManualName = ref(false)
const _allPresets = ref([])
const _allTags = ref([])

// 所有可选标签（自动标签 + 所有模型的自定义标签去重）
const allTagOptions = computed(() => {
  const s = new Set()
  for (const t of _allTags.value) {
    for (const x of [...(t.tags || []), ...(t.custom_tags || [])]) s.add(x)
  }
  return [...s]
})

function onEditPathChange() {
  if (editUseManualName.value) return
  const derived = deriveNameFromPath(editForm.value.model_path)
  if (derived) editForm.value.name = derived
}

function toggleEditNameManual() {
  editUseManualName.value = !editUseManualName.value
  if (!editUseManualName.value) onEditPathChange()
}

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
    services.value = await listServices()
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
      // 基于日志内容智能推进进度
      const joined = (loadLogs.value || []).join('\n')
      if (/model loaded|listening on/i.test(joined)) {
        loadProgress.value = 95
      } else if (/llama_model_load|load_model|init|allocating|mmap/i.test(joined)) {
        loadProgress.value = Math.max(loadProgress.value, 50)
      } else if (loadLogs.value.length > 0) {
        // 有日志产生说明进程已起来，缓慢推进（避免卡 0）
        loadProgress.value = Math.max(loadProgress.value, 25, Math.min(45, 25 + loadLogs.value.length * 2))
      } else {
        // 无日志：时间兜底推进
        loadProgress.value = Math.max(loadProgress.value, 10)
      }
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
    loadProgress.value = 10
    loadStatusText.value = '已拉起进程，等待模型加载…'

    // 立即拉一次日志（后端异步返回，日志已开始产生）
    await pollLogs(row.name)

    // 轮询状态 + 日志（每 2s）
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
  form.value = { name: '', model_path: '', gpu_id: defaultDevice(), idle_unload_min: 0, preset: { ...DEFAULT_PRESET } }
  useManualPath.value = false
  useManualName.value = false
  autoNameHint.value = ''
  createVisible.value = true
}

async function doCreate() {
  if (!form.value.model_path) {
    ElMessage.warning('请选择模型路径')
    return
  }
  // 未手动指定 name 时自动推导（前端预览 + 后端兜底）
  if (!useManualName.value || !form.value.name) {
    form.value.name = deriveNameFromPath(form.value.model_path) || form.value.name
  }
  creating.value = true
  try {
    await createService({ name: form.value.name || null, model_path: form.value.model_path, gpu_id: form.value.gpu_id || null, idle_unload_min: form.value.idle_unload_min || 0 })
    // 保存推理参数为预设（模板后端无关，单套）
    try {
      await createPreset({ model_name: form.value.name, ...form.value.preset, device: form.value.gpu_id || defaultDevice() })
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
  // 拉取预设列表（模板后端无关，单套），找当前模型的预设
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
    gpu_id: row.gpu_id || found?.device || defaultDevice(),
    presetId: found?.id || null,
    preset: found ? { ...found } : { ...DEFAULT_PRESET },
    custom_tags: [],
    idle_unload_min: row.idle_unload_min || 0,
  }
  editUseManualPath.value = !modelList.value.some(m => m.path === row.model_path)
  editUseManualName.value = false
  editVisible.value = true
  // 加载标签（自动标签展示用，自定义标签可编辑）
  try {
    _allTags.value = await listModelTags()
    const t = _allTags.value.find(x => x.model_name === row.name)
    editForm.value.custom_tags = t?.custom_tags ? [...t.custom_tags] : []
  } catch (e) {
    _allTags.value = []
  }
}

async function doSaveEdit() {
  if (!editForm.value.model_path) {
    ElMessage.warning('请选择模型路径')
    return
  }
  // 未手动指定 name 时自动推导
  if (!editUseManualName.value || !editForm.value.name) {
    editForm.value.name = deriveNameFromPath(editForm.value.model_path) || editForm.value.name
  }
  saving.value = true
  try {
    // 1. 保存基本信息到 services 表
    await updateService(editForm.value.id, {
      name: editForm.value.name || null,
      model_path: editForm.value.model_path,
      gpu_id: editForm.value.gpu_id || null,
      idle_unload_min: editForm.value.idle_unload_min || 0,
    })
    // 2. 保存推理参数到 model_presets 表（upsert，按当前后端）
    const p = editForm.value.preset
    const payload = {
      ctx_size: p.ctx_size, temp: p.temp, threads: p.threads,
      batch_size: p.batch_size, ubatch_size: p.ubatch_size, parallel: p.parallel,
      cache_type_k: p.cache_type_k, cache_type_v: p.cache_type_v,
      flash_attn: p.flash_attn, jinja: p.jinja, n_gpu_layers: p.n_gpu_layers,
      mmap: p.mmap, device: editForm.value.gpu_id || defaultDevice(),
      cpu_moe: p.cpu_moe, cpu_moe_layers: (p.cpu_moe_layers === '' || p.cpu_moe_layers === null || p.cpu_moe_layers === undefined) ? 0 : Number(p.cpu_moe_layers), mtp: p.mtp, mtp_model: p.mtp_model, mtp_n_max: p.mtp_n_max,
      spec_draft_type_k: p.spec_draft_type_k || '', spec_draft_type_v: p.spec_draft_type_v || '',
      rope_scaling: p.rope_scaling || '', rope_scale: (p.rope_scale === '' || p.rope_scale === null || p.rope_scale === undefined) ? null : Number(p.rope_scale), yarn_orig_ctx: (p.yarn_orig_ctx === '' || p.yarn_orig_ctx === null || p.yarn_orig_ctx === undefined) ? null : Number(p.yarn_orig_ctx),
      reasoning: p.reasoning || '', reasoning_budget: (p.reasoning_budget === '' || p.reasoning_budget === null || p.reasoning_budget === undefined) ? null : Number(p.reasoning_budget),
      reasoning_effort: p.reasoning_effort || '',
      extra_args: p.extra_args || {},
    }
    if (editForm.value.presetId) {
      await updatePreset(editForm.value.presetId, payload)
    } else {
      await createPreset({ model_name: editForm.value.name, ...payload })
    }
    // 3. 保存自定义标签（updateModelTags 同时更新自动标签）
    try {
      await updateModelTags(editForm.value.name, {
        tags: [],
        custom_tags: editForm.value.custom_tags || [],
      })
    } catch (e) { /* 标签保存失败不阻断主流程 */ }
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
  // 重启不依赖 loading/unloading 锁：即使刚停止（锁未释放）也能重启，
  // 后端 stop_instance 对未运行实例安全返回，start 会重新拉起
  loadingModel.value = row.name
  restartingModel.value = row.name
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
          restartingModel.value = ''
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
    restartingModel.value = ''
    loadProgress.value = 0
    loadStatusText.value = ''
    loadLogs.value = []
    expandedRowKeys.value = []
  }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`确认删除模型「${row.name}」？将移除注册、预设与聊天记录（模型文件保留）`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await deleteService(row.id)
    ElMessage.success('已删除（模型文件保留）')
    refresh()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(async () => {
  refresh()
  try {
    const [models, gpus, tags] = await Promise.all([listModels(), getSelectableGpus(), listModelTags()])
    modelList.value = models
    gpuList.value = gpus
    // 当前引擎后端（推理参数按后端存两套模板）
    try {
      const eb = await getEngineBackends()
      currentBackend.value = eb.current || 'sycl-fp16'
    } catch (e) { /* ignore */ }
    const tm = {}
    for (const t of tags) tm[t.model_name] = t
    svcTagMap.value = tm
    // 目标设备显存（估算对比用）：取非集显设备的显存总量
    try {
      const g = await gpuStatus()
      const dgpu = (g.devices || []).find(d => !d.is_integrated && d.memory_total_mib)
      gpuTotalGiB.value = dgpu ? Math.round(dgpu.memory_total_mib / 1024 * 10) / 10 : 0
    } catch (e) { /* ignore */ }
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
.proc-panel {
  padding: 12px 20px;
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
