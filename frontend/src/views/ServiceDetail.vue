<template>
  <div class="page-container" v-loading="loading">
    <el-page-header @back="$router.back()" :content="service?.name || '模型详情'" style="margin-bottom:16px">
      <template #extra>
        <el-button v-if="!service?.loaded" type="success" size="small" :loading="actionLoading" @click="doLoad">加载模型</el-button>
        <el-button v-else type="warning" size="small" :loading="actionLoading" @click="doUnload">卸载模型</el-button>
      </template>
    </el-page-header>

    <el-tabs v-model="activeTab" type="border-card">
      <!-- ================= 模型信息 ================= -->
      <el-tab-pane label="📊 模型信息" name="info">
        <el-row :gutter="16">
          <el-col :xs="24" :sm="12">
            <el-card shadow="never" style="border:none">
              <div class="card-title"><span>基本信息</span></div>
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="模型名">{{ service?.name }}</el-descriptions-item>
                <el-descriptions-item label="模型路径">{{ service?.model_path }}</el-descriptions-item>
                <el-descriptions-item label="状态">
                  <el-tag size="small" :type="service?.state === 'degraded' ? 'warning' : (service?.loaded ? 'success' : 'info')">
                    {{ service?.state === 'degraded' ? '降级（健康检查失败）' : (service?.loaded ? '已加载' : (service?.status === 'unavailable' ? '不可用' : '未加载')) }}
                  </el-tag>
                  <el-tag v-if="service?.supports_chat === false" size="small" type="warning" style="margin-left:6px">Embedding 模型（不支持对话）</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="API 端点"><code class="mono">{{ apiEndpoint }}</code></el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-card shadow="never" style="border:none">
              <div class="card-title"><span>Router 驻留信息</span></div>
              <div v-if="service?.loaded_info && Object.keys(service.loaded_info).length">
                <el-descriptions :column="1" size="small" border>
                  <el-descriptions-item v-for="(v, k) in service.loaded_info" :key="k" :label="k">
                    {{ typeof v === 'number' ? formatSize(v) : v }}
                  </el-descriptions-item>
                </el-descriptions>
              </div>
              <el-empty v-else description="模型未加载" :image-size="60" />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ================= 运行日志 ================= -->
      <el-tab-pane label="📋 运行日志" name="logs">
        <div class="log-toolbar">
          <el-button size="small" @click="refreshLogs" :loading="logsLoading">刷新</el-button>
          <el-select v-model="logTail" size="small" style="width:130px; margin-left:8px" @change="refreshLogs">
            <el-option :value="50" label="最近 50 条" />
            <el-option :value="100" label="最近 100 条" />
            <el-option :value="200" label="最近 200 条" />
            <el-option :value="500" label="最近 500 条" />
          </el-select>
          <span style="margin-left:12px;font-size:13px;color:#909399">自动刷新</span>
          <el-switch v-model="logAutoRefresh" size="small" style="margin-left:4px" />
          <el-divider direction="vertical" />
          <span style="font-size:13px;color:#909399">导出</span>
          <el-date-picker
            v-model="logExportRange"
            type="datetimerange"
            size="small"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            format="YYYY-MM-DD HH:mm"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width:360px; margin-left:4px"
          />
          <el-button size="small" type="primary" @click="exportLogs" :disabled="!logExportRange">导出日志</el-button>
        </div>
        <pre class="log-view" v-loading="logsLoading">{{ logs || '（无日志，加载模型后显示）' }}</pre>
      </el-tab-pane>

      <!-- ================= 对话日志（虚拟滚动）================= -->
      <el-tab-pane label="💬 对话日志" name="chatlogs">
        <div class="chatlog-toolbar">
          <span class="chatlog-count">共 {{ chatLogs.length }} 条</span>
          <el-switch v-model="chatLogAutoRefresh" size="small" active-text="自动刷新" style="margin-left:8px" />
          <el-button size="small" @click="refreshChatLogs" :loading="chatLogsLoading" style="margin-left:8px">刷新</el-button>
          <el-button size="small" type="danger" plain @click="doClearChatLogs" style="margin-left:8px">清空</el-button>
        </div>
        <!-- 虚拟滚动容器 -->
        <div class="chatlog-vlist" ref="chatLogViewport" @scroll.passive="onChatLogScroll">
          <div :style="{ height: chatLogTotalHeight + 'px', position: 'relative' }">
            <div
              v-for="log in visibleChatLogs"
              :key="log.id"
              class="chatlog-item"
              :class="{ 'is-running': log.status === 'running', 'is-error': log.status === 'error' }"
              :style="{ transform: `translateY(${log._offset}px)` }"
              @click="openChatLogDetail(log)"
              :title="'点击查看详情'"
            >
              <div class="chatlog-head">
                <el-tag size="small" :type="chatLogStatusType(log.status)" effect="dark" class="chatlog-status">{{ chatLogStatusLabel(log.status) }}</el-tag>
                <span class="chatlog-model">{{ log.model_name }}</span>
                <span class="chatlog-time">{{ fmtChatLogTime(log.created_at) }}</span>
                <span v-if="log.completion_tokens" class="chatlog-tok">{{ log.completion_tokens }} tok</span>
                <span v-if="log.status === 'running'" class="chatlog-running">生成中...</span>
                <span class="chatlog-preview">{{ chatLogPreview(log) }}</span>
              </div>
            </div>
          </div>
          <div v-if="!chatLogsLoading && chatLogs.length === 0" class="chatlog-empty">暂无对话日志</div>
        </div>

        <!-- 对话日志详情弹窗 -->
        <el-dialog v-model="chatLogDetailVisible" :title="chatLogDetailTitle" width="640px" top="6vh" class="chatlog-dialog">
          <div v-if="chatLogDetail" class="chatlog-detail">
            <div v-if="chatLogDetail.error" class="chatlog-error">{{ chatLogDetail.error }}</div>
            <div class="chatlog-block">
              <div class="chatlog-label">用户输入</div>
              <pre class="chatlog-pre user">{{ chatLogDetail.user_message || '(空)' }}</pre>
            </div>
            <div v-if="chatLogDetail.thinking" class="chatlog-block">
              <div class="chatlog-label thinking" @click="chatLogThinkingOpen = !chatLogThinkingOpen">
                <el-icon><CaretRight v-if="!chatLogThinkingOpen" /><CaretBottom v-else /></el-icon>
                思考过程 ({{ chatLogDetail.thinking.length }} 字符)
              </div>
              <pre v-if="chatLogThinkingOpen" class="chatlog-pre thinking">{{ chatLogDetail.thinking }}</pre>
            </div>
            <div class="chatlog-block">
              <div class="chatlog-label">模型输出</div>
              <pre class="chatlog-pre response">{{ chatLogDetail.response || (chatLogDetail.status === 'running' ? '(等待输出...)' : '(空)') }}</pre>
            </div>
          </div>
          <template #footer>
            <span class="chatlog-dialog-meta">
              <span v-if="chatLogDetail?.total_ms">耗时 {{ (chatLogDetail.total_ms / 1000).toFixed(1) }}s</span>
              <span v-if="chatLogDetail?.prompt_tokens">输入 {{ chatLogDetail.prompt_tokens }} tok</span>
              <span v-if="chatLogDetail?.completion_tokens">输出 {{ chatLogDetail.completion_tokens }} tok</span>
              <span v-if="chatLogDetail?.created_at">时间 {{ fmtChatLogFull(chatLogDetail.created_at) }}</span>
            </span>
            <el-button @click="chatLogDetailVisible = false">关闭</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- ================= 聊天测试台（共享 ChatPanel，状态在 pinia chatStore） ================= -->
      <el-tab-pane v-if="service?.supports_chat !== false" label="💬 聊天测试台" name="chat">
        <ChatPanel
          :service-id="sid"
          :model-loaded="!!service?.loaded"
          :is-vision="!!service?.has_mmproj"
          :max-tokens-limit="maxTokensLimit"
          :service-loaded-at="service?.loaded_at || 0"
          empty-text="输入消息开始测试（需模型已加载）"
          height="540px"
        />
      </el-tab-pane>

      <!-- ================= 接入配置 ================= -->
      <el-tab-pane label="🔌 接入配置" name="config">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px">
          通过统一网关访问（WebUI 端口 /v1/*），OpenAI 兼容端点
        </el-alert>
        <div v-if="clientCfg.keys?.length" style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
          <span style="font-size:13px;color:#606266">API Token</span>
          <el-select v-model="activeKeyId" style="width:280px" size="small" @change="onKeyChange">
            <el-option v-for="k in clientCfg.keys" :key="k.id" :value="k.id" :label="`${k.name || ('Key#' + k.id)}（${k.key.substring(0,8)}…）`" />
          </el-select>
          <el-tag v-if="clientCfg.keys.length > 1" size="small" type="info">共 {{ clientCfg.keys.length }} 个可用 Token</el-tag>
        </div>
        <el-tabs v-model="configTab">
          <el-tab-pane label="curl" name="curl">
            <el-input v-model="clientCfg.curl" type="textarea" :rows="8" class="mono-area" readonly />
            <el-button size="small" style="margin-top:8px" @click="copyText(clientCfg.curl)">复制</el-button>
          </el-tab-pane>
          <el-tab-pane label="openclaw.json" name="openclaw">
            <el-input v-model="clientCfg.openclaw" type="textarea" :rows="10" class="mono-area" readonly />
            <el-button size="small" style="margin-top:8px" @click="copyText(clientCfg.openclaw)">复制</el-button>
          </el-tab-pane>
          <el-tab-pane label="Python" name="python">
            <el-input v-model="clientCfg.python" type="textarea" :rows="10" class="mono-area" readonly />
            <el-button size="small" style="margin-top:8px" @click="copyText(clientCfg.python)">复制</el-button>
          </el-tab-pane>
        </el-tabs>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CaretRight, CaretBottom } from '@element-plus/icons-vue'
import ChatPanel from '../components/ChatPanel.vue'
import {
  getService, startService, stopService, getServiceLogs,
  clientConfig, listPresets,
  getChatLogs, clearChatLogs,
} from '../api'

// 通用复制：优先 Clipboard API，非 HTTPS 环境降级 textarea+execCommand（接入配置 tab 用）
async function copyText(text) {
  if (!text) return false
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (e) { /* 继续走降级 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch (e) {
    return false
  }
}

const route = useRoute()
const sid = route.params.id
const service = ref(null)
const loading = ref(true)
const actionLoading = ref(false)
const logs = ref('')
const activeTab = ref('info')

function formatSize(bytes) {
  if (!bytes) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(1) + units[i]
}

// ---------- 日志 ----------
const logView = ref(null)
const logsLoading = ref(false)
const logTail = ref(200)
const logAutoRefresh = ref(true)
const logExportRange = ref(null)
let logPollTimer = null

async function refreshLogs() {
  logsLoading.value = true
  try {
    const d = await getServiceLogs(sid, logTail.value)
    logs.value = d.logs
  } catch (e) {
    logs.value = '获取日志失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    logsLoading.value = false
  }
}

function startLogPolling() {
  stopLogPolling()
  if (logAutoRefresh.value) {
    logPollTimer = setInterval(refreshLogs, 5000)
  }
}

function stopLogPolling() {
  if (logPollTimer) { clearInterval(logPollTimer); logPollTimer = null }
}

watch(logAutoRefresh, (v) => {
  if (v && activeTab.value === 'logs') startLogPolling()
  else stopLogPolling()
})

watch(activeTab, (v) => {
  if (v === 'logs') { refreshLogs(); startLogPolling() }
  else stopLogPolling()
})

async function exportLogs() {
  if (!logExportRange.value || logExportRange.value.length !== 2) return
  const [since, until] = logExportRange.value
  try {
    const d = await getServiceLogs(sid, 0, since, until)
    const blob = new Blob([d.logs], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const modelName = service.value?.name || 'model'
    a.href = url
    a.download = `${modelName}-${since.replace(/[:]/g, '')}-${until.replace(/[:]/g, '')}.log`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${d.total} 条日志`)
  } catch (e) {
    ElMessage.error('导出失败: ' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 对话日志（虚拟滚动 + 弹窗详情）----------
const CHATLOG_ROW_H = 52           // 每行固定高度
const CHATLOG_OVERSCAN = 6         // 可视区外预渲染行数
const chatLogs = ref([])
const chatLogsLoading = ref(false)
const chatLogAutoRefresh = ref(true)
const chatLogViewport = ref(null)
const chatLogScrollTop = ref(0)
const chatLogViewportH = ref(480)
// 详情弹窗
const chatLogDetailVisible = ref(false)
const chatLogDetail = ref(null)
const chatLogThinkingOpen = ref(false)
let chatLogTimer = null

const chatLogDetailTitle = computed(() => {
  const d = chatLogDetail.value
  return d ? `对话日志 #${d.id} · ${d.model_name || ''} · ${chatLogStatusLabel(d.status)}` : '对话日志'
})

const chatLogTotalHeight = computed(() => chatLogs.value.length * CHATLOG_ROW_H)

const visibleChatLogs = computed(() => {
  const start = Math.max(0, Math.floor(chatLogScrollTop.value / CHATLOG_ROW_H) - CHATLOG_OVERSCAN)
  const count = Math.ceil(chatLogViewportH.value / CHATLOG_ROW_H) + CHATLOG_OVERSCAN * 2
  return chatLogs.value.slice(start, start + count).map((log, i) => ({
    ...log,
    _offset: (start + i) * CHATLOG_ROW_H,
  }))
})

function onChatLogScroll() {
  if (chatLogViewport.value) chatLogScrollTop.value = chatLogViewport.value.scrollTop
}

function chatLogStatusType(s) {
  if (s === 'running') return 'primary'
  if (s === 'error') return 'danger'
  return 'success'
}
function chatLogStatusLabel(s) {
  if (s === 'running') return '生成中'
  if (s === 'error') return '失败'
  return '完成'
}
function fmtChatLogTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = n => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function fmtChatLogFull(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function chatLogPreview(log) {
  const r = (log.response || '').replace(/\s+/g, ' ').trim()
  if (r) return r.slice(0, 60) + (r.length > 60 ? '...' : '')
  if (log.status === 'running') return '生成中...'
  if (log.error) return log.error.slice(0, 60)
  return ''
}
function openChatLogDetail(log) {
  chatLogDetail.value = log
  chatLogThinkingOpen.value = false
  chatLogDetailVisible.value = true
}

async function refreshChatLogs() {
  chatLogsLoading.value = true
  try {
    const modelName = service.value?.name || ''
    const d = await getChatLogs(modelName, 300)
    const items = (d && (d.items || d.data?.items)) || []
    chatLogs.value = items
  } catch (e) {
    // 静默
  } finally {
    chatLogsLoading.value = false
  }
}

async function doClearChatLogs() {
  try {
    const modelName = service.value?.name || ''
    await ElMessageBox.confirm('确定清空该模型的全部对话日志？', '清空确认', { type: 'warning' })
    await clearChatLogs(modelName)
    ElMessage.success('已清空')
    refreshChatLogs()
  } catch (e) {
    if (e !== 'cancel' && e?.message) ElMessage.error('清空失败')
  }
}

function startChatLogPolling() {
  stopChatLogPolling()
  if (chatLogAutoRefresh.value) chatLogTimer = setInterval(refreshChatLogs, 2000)
}
function stopChatLogPolling() {
  if (chatLogTimer) { clearInterval(chatLogTimer); chatLogTimer = null }
}

watch(chatLogAutoRefresh, v => { if (v) startChatLogPolling(); else stopChatLogPolling() })
watch(activeTab, v => {
  if (v === 'chatlogs') { refreshChatLogs(); startChatLogPolling() }
  else stopChatLogPolling()
})

// 当前模型可用上下文（决定 max_tokens 上限）
// llama.cpp 机制：总 ctx(--ctx-size) 按 parallel(slot) 均分，meta.n_ctx 即每 slot 上下文；
// max_tokens 上限 = 每 slot 上下文 × 0.75（预留 25% 给对话历史 prompt）
const presets = ref([])
const maxTokensLimit = computed(() => {
  const svc = service.value
  if (!svc) return 8192
  const preset = presets.value.find(p => p.model_name === svc.name)
  // 每 slot 上下文：加载后 meta.n_ctx 最准（已均分），否则 ctx/parallel 推算
  let perSlot = svc.loaded_info?.meta?.n_ctx
  if (!perSlot) {
    const ctx = preset?.ctx_size || svc.loaded_info?.ctx_size || 8192
    // parallel：预设优先，loaded_info args 里 --parallel 兜底
    let parallel = preset?.parallel
    if (!parallel && svc.loaded_info?.args) {
      const args = svc.loaded_info.args
      const pi = Array.isArray(args) ? args.indexOf('--parallel') : -1
      if (pi >= 0 && pi + 1 < args.length) parallel = parseInt(args[pi + 1])
    }
    perSlot = Math.floor(ctx / Math.max(1, parallel || 1))
  }
  return Math.max(512, Math.floor(perSlot * 0.75))
})

// ---------- 接入配置 ----------
const configTab = ref('curl')
const clientCfg = ref({ curl: '', openclaw: '', python: '', keys: [], active_key: '' })
const activeKeyId = ref(null)

function onKeyChange() {
  const k = clientCfg.value.keys?.find(x => x.id === activeKeyId.value)
  if (!k) return
  // 本地替换配置片段中的 key
  const old = clientCfg.value.active_key || ''
  const replace = (s) => s ? s.split(old).join(k.key) : s
  clientCfg.value.curl = replace(clientCfg.value.curl)
  clientCfg.value.openclaw = replace(clientCfg.value.openclaw)
  clientCfg.value.python = replace(clientCfg.value.python)
  clientCfg.value.active_key = k.key
}

async function loadClientConfig() {
  try {
    clientCfg.value = await clientConfig(sid)
    if (clientCfg.value.keys?.length) {
      activeKeyId.value = clientCfg.value.keys[0].id
    }
  } catch (e) {
    clientCfg.value = { curl: '', openclaw: '', python: '', keys: [] }
  }
}

// ---------- 模型操作 ----------
async function doLoad() {
  actionLoading.value = true
  try {
    await startService(sid)
    ElMessage.success('加载中（约需 30-60 秒）')
    setTimeout(load, 3000)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载失败')
  } finally {
    actionLoading.value = false
  }
}

async function doUnload() {
  actionLoading.value = true
  try {
    await stopService(sid)
    ElMessage.success('已卸载')
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '卸载失败')
  } finally {
    actionLoading.value = false
  }
}

const apiEndpoint = computed(() => service.value ? `http://${location.hostname}:${location.port}/v1` : '')

async function loadPresets() {
  try { presets.value = await listPresets() } catch (e) { presets.value = [] }
}

async function load() {
  loading.value = true
  try {
    service.value = await getService(sid)
    await loadClientConfig()
    // 日志由 tab watcher 自动加载
    if (activeTab.value === 'logs') {
      await refreshLogs()
      startLogPolling()
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  loadPresets()
  // 聊天测试台由 <ChatPanel> 自管理（pinia chatStore，离开页面不中断流式）
})
onUnmounted(() => {
  stopLogPolling()
  stopChatLogPolling()
})
</script>

<style scoped>
.mono-area :deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
.log-toolbar { margin-bottom: 10px; display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.log-view {
  background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 6px;
  font-size: 12px; font-family: 'JetBrains Mono', Consolas, monospace;
  max-height: 480px; overflow: auto; white-space: pre-wrap; word-break: break-all;
}
/* 对话日志（虚拟滚动） */
.chatlog-toolbar { display: flex; align-items: center; margin-bottom: 8px; }
.chatlog-count { font-size: 13px; color: #909399; }
.chatlog-vlist {
  background: #1e1e1e; border-radius: 6px;
  height: 480px; overflow-y: auto; position: relative;
}
.chatlog-item {
  position: absolute; left: 0; right: 0; top: 0;
  height: 52px; padding: 0 12px; box-sizing: border-box;
  cursor: pointer; border-bottom: 1px solid #2a2a2a;
  background: transparent; transition: background .15s;
}
.chatlog-item:hover { background: #2a2a2a; }
.chatlog-item.is-running { background: rgba(64,158,255,.12); }
.chatlog-item.is-error { background: rgba(245,108,108,.12); }
.chatlog-head { display: flex; align-items: center; gap: 8px; height: 100%; overflow: hidden; }
.chatlog-status { flex-shrink: 0; }
.chatlog-model { color: #e8e8e8; font-weight: 600; font-size: 12px; flex-shrink: 0; }
.chatlog-time { color: #888; font-size: 11px; flex-shrink: 0; }
.chatlog-tok { color: #888; font-size: 11px; flex-shrink: 0; }
.chatlog-running { color: #409eff; font-size: 11px; animation: pulse 1.2s infinite; flex-shrink: 0; }
.chatlog-preview {
  color: #aaa; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  flex: 1; min-width: 0; margin-left: 4px; font-family: 'JetBrains Mono', Consolas, monospace;
}
/* 详情弹窗 */
.chatlog-detail { max-height: 60vh; overflow-y: auto; }
.chatlog-dialog-meta { color: #909399; font-size: 12px; margin-right: 12px; display: inline-flex; gap: 14px; }
.chatlog-block { margin-bottom: 10px; }
.chatlog-block:last-child { margin-bottom: 0; }
.chatlog-label { font-size: 12px; color: #909399; margin-bottom: 4px; font-weight: 500; }
.chatlog-label.thinking { color: #b37feb; cursor: pointer; display: flex; align-items: center; gap: 4px; }
.chatlog-pre {
  margin: 0; padding: 10px 12px; border-radius: 6px; font-size: 12px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word; max-height: 240px; overflow-y: auto;
  font-family: 'JetBrains Mono', Consolas, monospace; color: #d4d4d4; background: #232323;
}
.chatlog-pre.response { background: #1d2b1d; color: #a6e3a1; }
.chatlog-pre.thinking { background: #231d2b; color: #c9a6e3; }
.chatlog-error { color: #f56c6c; font-size: 13px; padding: 6px 10px; background: #2b1d1d; border-radius: 4px; margin-bottom: 8px; }
.chatlog-empty { color: #666; text-align: center; padding: 40px 0; font-size: 13px; }
@keyframes pulse { 0%,100% {opacity:1} 50% {opacity:.4} }
.form-tip { font-size: 12px; color: #909399; margin-top: 6px; }

/* 聊天测试台样式已抽到 ChatPanel.vue（scoped） */

/* 移动端适配 */
@media (max-width: 767px) {
  .log-toolbar > * { margin-bottom: 4px; }
  .log-toolbar :deep(.el-date-editor) { width: 100% !important; }
  .el-col + .el-col { margin-top: 12px; }
}
</style>
