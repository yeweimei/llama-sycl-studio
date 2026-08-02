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
                  <el-tag size="small" :type="service?.loaded ? 'success' : 'info'">
                    {{ service?.loaded ? '已加载' : (service?.status === 'unavailable' ? '不可用' : '未加载') }}
                  </el-tag>
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

      <!-- ================= 聊天测试台 ================= -->
      <el-tab-pane label="💬 聊天测试台" name="chat">
        <div class="chat-panel">
          <div class="chat-messages" ref="chatView">
            <div v-if="!messages.length" class="chat-empty">输入消息开始测试（需模型已加载）</div>
            <div v-for="(m, i) in messages" :key="i" class="chat-msg" :class="m.role">
              <div class="chat-bubble">
                <div v-if="m.role === 'user'" class="chat-label">你</div>
                <div v-else class="chat-label" style="color:#409eff">助手</div>
                <div class="chat-content" style="white-space:pre-wrap">{{ m.content }}</div>
                <div v-if="m.thinking" class="chat-thinking">
                  <div class="thinking-header" @click="toggleThinking(i)">
                    <span>🤔 思考过程</span>
                    <el-icon class="thinking-arrow" :class="{ collapsed: !thinkingExpanded[i] }"><ArrowDown /></el-icon>
                  </div>
                  <div v-show="thinkingExpanded[i]" class="thinking-body">{{ m.thinking }}</div>
                </div>
              </div>
            </div>
            <div v-if="chatLoading" class="chat-msg assistant">
              <div class="chat-bubble"><div class="chat-label" style="color:#409eff">助手</div><div class="chat-streaming">▋</div></div>
            </div>
          </div>
          <div class="chat-controls">
            <el-checkbox v-model="chatThinking">思考模式</el-checkbox>
            <span style="margin-left:12px;font-size:13px;color:#909399">max_tokens</span>
            <el-input-number v-model="chatMaxTokens" :min="32" :max="8192" :step="64" size="small" style="width:130px" />
            <el-button size="small" type="primary" style="margin-left:auto" :disabled="!canChat || chatLoading" @click="sendChat">发送</el-button>
            <el-button size="small" @click="clearChat">清空</el-button>
          </div>
          <el-input
            v-model="chatInput"
            type="textarea"
            :rows="2"
            placeholder="输入消息，Enter 发送 / Shift+Enter 换行"
            @keydown.enter.exact.prevent="sendChat"
          />
        </div>
      </el-tab-pane>

      <!-- ================= 接入配置 ================= -->
      <el-tab-pane label="🔌 接入配置" name="config">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px">
          通过统一网关访问（WebUI 端口 /v1/*），OpenAI 兼容端点
        </el-alert>
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
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import {
  getService, startService, stopService, getServiceLogs,
  chatProxy, clientConfig,
} from '../api'

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

// ---------- 聊天 ----------
const messages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
const chatThinking = ref(false)
const chatMaxTokens = ref(512)
const chatView = ref(null)

const canChat = computed(() => service.value?.loaded)

function stripThink(text) {
  if (!text) return text
  return text.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '').trim()
}

// thinking 折叠状态
const thinkingExpanded = ref({})

function toggleThinking(index) {
  thinkingExpanded.value[index] = !thinkingExpanded.value[index]
}

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text || chatLoading.value) return
  messages.value.push({ role: 'user', content: text })
  chatInput.value = ''
  chatLoading.value = true
  messages.value.push({ role: 'assistant', content: '', thinking: '' })
  const aiMsg = messages.value[messages.value.length - 1]
  scrollChat()
  try {
    const payload = {
      messages: messages.value
        .filter(m => m.role !== 'thinking')
        .map(m => ({
          role: m.role,
          content: m.role === 'assistant' ? stripThink(m.content) : m.content,
        })),
      max_tokens: chatMaxTokens.value,
      temperature: 0.7,
      stream: true,
    }
    if (chatThinking.value) payload.chat_template_kwargs = { enable_thinking: true }
    else payload.chat_template_kwargs = { enable_thinking: false }

    const resp = await fetch(`/api/services/${sid}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('auth_token')}`,
      },
      body: JSON.stringify(payload),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }))
      throw new Error(err.detail || `HTTP ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop()
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed.startsWith('data:')) continue
        const data = trimmed.slice(5).trim()
        if (data === '[DONE]') continue
        try {
          const chunk = JSON.parse(data)
          const delta = chunk.choices?.[0]?.delta || {}
          if (delta.reasoning_content) {
            aiMsg.thinking += delta.reasoning_content
            // 流式过程中自动展开
            const idx = messages.value.indexOf(aiMsg)
            if (idx >= 0) thinkingExpanded.value[idx] = true
          }
          if (delta.content) {
            aiMsg.content += delta.content
            // 如果 content 里含 <think> 标签（某些模型兼容），解析出来
            if (aiMsg.content.includes('<think')) {
              const m = aiMsg.content.match(/<think\b[^>]*>([\s\S]*?)(?:<\/think>|$)/i)
              if (m) {
                if (!aiMsg.thinking) aiMsg.thinking = m[1] || ''
                aiMsg.content = aiMsg.content.replace(/<think\b[^>]*>[\s\S]*?(?:<\/think>|$)/i, '').trim()
              }
            }
          }
          scrollChat()
        } catch (e) { /* 忽略解析错误 */ }
      }
    }
    if (!aiMsg.content && !aiMsg.thinking) {
      aiMsg.content = '（无输出，可能思考中或已截断）'
    } else if (!aiMsg.content && aiMsg.thinking) {
      aiMsg.content = '（模型仅返回了思考内容，未生成正式回答）'
    } else {
      // 流式结束后默认折叠思考内容
      const idx = messages.value.indexOf(aiMsg)
      if (idx >= 0 && aiMsg.thinking) thinkingExpanded.value[idx] = false
    }
  } catch (e) {
    aiMsg.content = `❌ 调用失败: ${e.message || e}`
  } finally {
    chatLoading.value = false
    scrollChat()
  }
}

function clearChat() {
  messages.value = []
  thinkingExpanded.value = {}
}

function scrollChat() {
  nextTick(() => {
    const el = chatView.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// ---------- 接入配置 ----------
const configTab = ref('curl')
const clientCfg = ref({ curl: '', openclaw: '', python: '' })

async function loadClientConfig() {
  try {
    clientCfg.value = await clientConfig(sid)
  } catch (e) {
    clientCfg.value = { curl: '', openclaw: '', python: '' }
  }
}

function copyText(t) {
  navigator.clipboard.writeText(t)
  ElMessage.success('已复制')
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
  window.addEventListener('keydown', onGlobalKey)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKey)
  stopLogPolling()
})

function onGlobalKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && activeTab.value === 'chat' && document.activeElement?.tagName === 'TEXTAREA') {
    e.preventDefault()
    sendChat()
  }
}
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
.form-tip { font-size: 12px; color: #909399; margin-top: 6px; }

.chat-panel { display: flex; flex-direction: column; gap: 10px; }
.chat-messages {
  height: 420px; overflow-y: auto; background: #fafafa; border-radius: 8px;
  padding: 16px; border: 1px solid #ebeef5;
}
.chat-empty { color: #c0c4cc; text-align: center; margin-top: 180px; font-size: 14px; }
.chat-msg { margin-bottom: 14px; display: flex; }
.chat-msg.user { justify-content: flex-end; }
.chat-msg.assistant { justify-content: flex-start; }
.chat-bubble {
  max-width: 80%; padding: 10px 14px; border-radius: 10px;
  background: #fff; border: 1px solid #e4e7ed; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.chat-msg.user .chat-bubble { background: #ecf5ff; border-color: #d9ecff; }
.chat-label { font-size: 12px; color: #909399; margin-bottom: 4px; }
.chat-content { font-size: 14px; line-height: 1.6; }
.chat-thinking {
  margin-top: 6px; font-size: 12px; color: #b88230;
  background: #fdf6ec; border-radius: 6px;
  border: 1px dashed #e6a23c; overflow: hidden;
}
.thinking-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 4px 8px; cursor: pointer; user-select: none;
  font-weight: 600; font-size: 12px;
}
.thinking-header:hover { background: #faecd8; }
.thinking-arrow { transition: transform 0.2s; font-size: 12px; }
.thinking-arrow.collapsed { transform: rotate(-90deg); }
.thinking-body {
  padding: 6px 8px; max-height: 200px; overflow-y: auto;
  white-space: pre-wrap; border-top: 1px dashed #e6a23c;
}
.chat-streaming {
  color: #409eff; font-size: 18px; animation: blink 1s infinite;
}
@keyframes blink { 50% { opacity: 0.2; } }
.chat-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

/* 移动端适配 */
@media (max-width: 767px) {
  .log-toolbar > * { margin-bottom: 4px; }
  .log-toolbar :deep(.el-date-editor) { width: 100% !important; }
  .chat-messages { height: 320px; padding: 10px; }
  .chat-bubble { max-width: 90%; }
  .el-col + .el-col { margin-top: 12px; }
}
</style>
