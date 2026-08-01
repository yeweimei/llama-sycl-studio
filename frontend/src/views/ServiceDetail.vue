<template>
  <div class="page-container" v-loading="loading">
    <el-page-header @back="$router.back()" :content="service?.name || '服务详情'" style="margin-bottom:16px">
      <template #extra>
        <el-button v-if="service?.status !== 'running'" type="success" size="small" @click="doStart">启动</el-button>
        <el-button v-else type="warning" size="small" @click="doStop">停止</el-button>
        <el-button size="small" @click="doRestart">重启</el-button>
      </template>
    </el-page-header>

    <el-tabs v-model="activeTab" type="border-card">
      <!-- ================= 参数配置 ================= -->
      <el-tab-pane label="⚙️ 参数配置" name="params">
        <el-row :gutter="16">
          <el-col :span="14">
            <el-card shadow="never" style="border:none">
              <div class="card-title">
                <span>推理参数</span>
                <el-select v-model="selectedTemplate" size="small" placeholder="套用模板" style="width:160px;margin-left:auto" @change="applyTemplate">
                  <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
                </el-select>
                <el-button size="small" @click="saveAsTemplate">存模板</el-button>
                <el-button size="small" type="primary" @click="save">保存</el-button>
                <el-tooltip content="保存参数并重启服务（运行中才会显示）" placement="top">
                  <el-button v-if="service?.status === 'running'" size="small" type="warning" :loading="savingRestart" @click="saveAndRestart">保存并重启</el-button>
                </el-tooltip>
              </div>

              <el-form :model="args" label-width="160px" size="small">
                <el-row :gutter="12">
                  <el-col :span="12">
                    <el-form-item label="GPU 层数 (-ngl)">
                      <el-input-number v-model="args.n_gpu_layers" :min="0" :max="999" style="width:100%" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="上下文长度 (-c)">
                      <el-input-number v-model="args.ctx_size" :min="512" :max="262144" :step="1024" style="width:100%" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="批大小 (-b)">
                      <el-input-number v-model="args.batch_size" :min="32" :max="8192" style="width:100%" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="微批大小 (--ubatch-size)">
                      <el-input-number v-model="args.ubatch_size" :min="16" :max="4096" style="width:100%" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="并发槽位 (-np)">
                      <el-input-number v-model="args.parallel" :min="1" :max="64" style="width:100%" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="Flash Attention">
                      <el-switch v-model="args.flash_attn" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="KV 缓存类型 K">
                      <el-select v-model="args.cache_type_k" style="width:100%">
                        <el-option v-for="t in kvTypes" :key="t" :label="t" :value="t" />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="KV 缓存类型 V">
                      <el-select v-model="args.cache_type_v" style="width:100%">
                        <el-option v-for="t in kvTypes" :key="t" :label="t" :value="t" />
                      </el-select>
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="Jinja 模板">
                      <el-switch v-model="args.jinja" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="温度 (--temp)">
                      <el-slider v-model="args.temp" :min="0" :max="2" :step="0.1" show-input />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="Top-K">
                      <el-input-number v-model="args.top_k" :min="1" :max="100" style="width:100%" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="Top-P">
                      <el-slider v-model="args.top_p" :min="0.1" :max="1" :step="0.05" show-input />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="重复惩罚">
                      <el-slider v-model="args.repeat_penalty" :min="1" :max="2" :step="0.05" show-input />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item label="线程数 (-t)">
                      <el-input-number v-model="args.threads" :min="1" :max="32" style="width:100%" />
                    </el-form-item>
                  </el-col>
                </el-row>
              </el-form>
            </el-card>
          </el-col>

          <el-col :span="10">
            <el-card shadow="never" style="border:none">
              <div class="card-title"><span>启动命令（双向同步）</span></div>
              <el-input
                v-model="commandText"
                type="textarea"
                :rows="14"
                class="mono-area"
                placeholder="编辑命令行，或由左侧表单自动生成"
              />
              <div class="form-tip">左侧表单改动自动更新命令行；直接编辑命令行后点「应用命令」回写表单</div>
              <el-button size="small" type="primary" style="margin-top:8px" @click="applyCommand">应用命令行 → 表单</el-button>
              <el-button size="small" style="margin-top:8px" @click="copyCommand">复制</el-button>
            </el-card>

            <el-card shadow="never" style="border:none;margin-top:16px">
              <div class="card-title"><span>服务信息</span></div>
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="模型">{{ service?.model_path }}</el-descriptions-item>
                <el-descriptions-item label="端口"><span class="mono">{{ service?.port }}</span></el-descriptions-item>
                <el-descriptions-item label="API 端点"><code class="mono">{{ apiEndpoint }}</code></el-descriptions-item>
                <el-descriptions-item label="API Key">
                  <el-tag v-if="service?.api_key" size="small" type="warning">已设置</el-tag>
                  <el-tag v-else size="small" type="info">未设置（无鉴权）</el-tag>
                </el-descriptions-item>
              </el-descriptions>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ================= 运行日志 ================= -->
      <el-tab-pane label="📋 运行日志" name="logs">
        <div class="log-toolbar">
          <el-switch v-model="logLive" active-text="实时流" inactive-text="手动刷新" @change="toggleLogStream" />
          <el-button size="small" style="margin-left:12px" @click="refreshLogs">刷新</el-button>
          <el-tag v-if="logConnected" size="small" type="success" style="margin-left:12px">已连接</el-tag>
          <el-tag v-else size="small" type="info" style="margin-left:12px">未连接</el-tag>
        </div>
        <pre class="log-view" ref="logView">{{ logs || '（无日志，启动后显示）' }}</pre>
      </el-tab-pane>

      <!-- ================= 聊天测试台 ================= -->
      <el-tab-pane label="💬 聊天测试台" name="chat">
        <div class="chat-panel">
          <div class="chat-messages" ref="chatView">
            <div v-if="!messages.length" class="chat-empty">输入消息开始测试（需服务处于运行状态）</div>
            <div v-for="(m, i) in messages" :key="i" class="chat-msg" :class="m.role">
              <div class="chat-bubble">
                <div v-if="m.role === 'user'" class="chat-label">你</div>
                <div v-else class="chat-label" style="color:#409eff">助手</div>
                <div class="chat-content" style="white-space:pre-wrap">{{ m.content }}</div>
                <div v-if="m.thinking" class="chat-thinking">🤔 {{ m.thinking }}</div>
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
          该服务的 OpenAI 兼容端点接入方式，可用于 openclaw / 其他工具
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
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getService, updateService, startService, stopService, restartService,
  getServiceLogs, getParamSchema, listTemplates, createTemplate,
  chatProxy, clientConfig,
} from '../api'

const route = useRoute()
const sid = route.params.id
const service = ref(null)
const args = ref({})
const loading = ref(true)
const savingRestart = ref(false)
const logs = ref('')
const templates = ref([])
const selectedTemplate = ref(null)
const kvTypes = ['f16', 'bf16', 'q8_0', 'q4_0', 'q4_1', 'iq4_nl', 'f32']
const activeTab = ref('params')

// ---------- 命令行 ----------
const commandText = ref('')

function buildCommand() {
  const a = args.value
  const parts = ['llama-server', '-m', service.value?.model_path || '<model>', '--port', String(service.value?.port || 8081), '--host', '0.0.0.0']
  const map = {
    n_gpu_layers: ['-ngl', 'N'], ctx_size: ['-c', 'N'], batch_size: ['-b', 'N'],
    ubatch_size: ['--ubatch-size', 'N'], parallel: ['-np', 'N'], temp: ['--temp', 'F'],
    top_k: ['--top-k', 'N'], top_p: ['--top-p', 'F'], repeat_penalty: ['--repeat-penalty', 'F'],
    threads: ['-t', 'N'],
  }
  for (const [k, [flag]] of Object.entries(map)) {
    if (a[k] !== undefined && a[k] !== null && a[k] !== '') parts.push(flag, String(a[k]))
  }
  if (a.flash_attn) parts.push('--flash-attn', 'on')
  if (a.jinja) parts.push('--jinja')
  if (a.no_webui) parts.push('--no-webui')
  if (a.cache_type_k) parts.push('--cache-type-k', a.cache_type_k)
  if (a.cache_type_v) parts.push('--cache-type-v', a.cache_type_v)
  return parts.join(' ')
}

watch(args, () => { commandText.value = buildCommand() }, { deep: true })

function applyCommand() {
  const tokens = commandText.value.split(/\s+/).filter(Boolean)
  const a = { ...args.value }
  const flagMap = {
    '-ngl': ['n_gpu_layers', parseInt], '-c': ['ctx_size', parseInt], '-b': ['batch_size', parseInt],
    '--ubatch-size': ['ubatch_size', parseInt], '-np': ['parallel', parseInt],
    '--temp': ['temp', parseFloat], '--top-k': ['top_k', parseInt], '--top-p': ['top_p', parseFloat],
    '--repeat-penalty': ['repeat_penalty', parseFloat], '-t': ['threads', parseInt],
    '--cache-type-k': ['cache_type_k', String], '--cache-type-v': ['cache_type_v', String],
  }
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i]
    if (t === '--flash-attn') a.flash_attn = true
    else if (t === '--jinja') a.jinja = true
    else if (t === '--no-webui') a.no_webui = true
    else if (flagMap[t] && tokens[i + 1]) {
      const [key, cast] = flagMap[t]
      a[key] = cast(tokens[i + 1])
    }
  }
  args.value = a
  ElMessage.success('命令已应用')
}

async function save() {
  await updateService(sid, { args: args.value })
  if (service.value?.status === 'running') {
    // 运行中：询问是否重启生效
    try {
      await ElMessageBox.confirm(
        '参数已保存，但需要重启服务才能生效。是否立即重启？',
        '重启确认',
        { confirmButtonText: '重启', cancelButtonText: '稍后重启', type: 'warning' }
      )
      await doRestart()
    } catch (e) {
      if (e !== 'cancel') return
      ElMessage.success('参数已保存，重启服务后生效')
    }
  } else {
    ElMessage.success('参数已保存')
  }
}

async function saveAndRestart() {
  savingRestart.value = true
  try {
    await updateService(sid, { args: args.value })
    ElMessage.success('参数已保存，正在重启...')
    await restartService(sid)
    ElMessage.success('重启中（模型加载约需 1 分钟）')
    setTimeout(load, 3000)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存/重启失败')
  } finally {
    savingRestart.value = false
  }
}

async function saveAsTemplate() {
  const { value } = await ElMessageBox.prompt('模板名称', '保存为模板', { confirmButtonText: '保存', cancelButtonText: '取消' })
  if (value) {
    await createTemplate({ name: value, args: args.value })
    ElMessage.success('模板已保存')
    loadTemplates()
  }
}

async function applyTemplate(tid) {
  const t = templates.value.find(x => x.id === tid)
  if (t) {
    args.value = { ...t.args }
    await save()
    ElMessage.success('模板已应用')
  }
}

function copyCommand() {
  navigator.clipboard.writeText(commandText.value)
  ElMessage.success('已复制')
}

// ---------- 日志 ----------
const logLive = ref(false)
const logConnected = ref(false)
let ws = null
const logView = ref(null)

async function refreshLogs() {
  const d = await getServiceLogs(sid, 300)
  logs.value = d.logs
  scrollLog()
}

function scrollLog() {
  nextTick(() => {
    const el = logView.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function toggleLogStream(on) {
  if (on) {
    startLogStream()
  } else {
    stopLogStream()
  }
}

function startLogStream() {
  stopLogStream()
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/services/${sid}/logs/ws`)
  ws.onopen = () => { logConnected.value = true }
  ws.onmessage = (e) => {
    const d = JSON.parse(e.data)
    if (d.type === 'log') {
      logs.value += d.line
      if (logs.value.length > 200000) logs.value = logs.value.slice(-150000)
      scrollLog()
    } else if (d.type === 'error') {
      logs.value += `\n[${d.message}]\n`
    }
  }
  ws.onclose = () => { logConnected.value = false }
  ws.onerror = () => { logConnected.value = false }
}

function stopLogStream() {
  if (ws) { ws.close(); ws = null }
  logConnected.value = false
}

// ---------- 聊天 ----------
const messages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
const chatThinking = ref(false)
const chatMaxTokens = ref(512)
const chatView = ref(null)

const canChat = computed(() => service.value?.status === 'running')

// 剥掉 <think>...</think> 标签（含空标签），只保留正文
function stripThink(text) {
  if (!text) return text
  return text.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '').trim()
}

// 把原始输出拆成 thinking + content（兼容流式未闭合的 <think>）
function splitThink(raw) {
  const m = raw.match(/<think\b[^>]*>([\s\S]*?)(?:<\/think>|$)/i)
  if (!m) return { thinking: '', content: raw }
  const after = raw.slice(m.index + m[0].length)
  // 如果 think 标签还没闭合，content 里不应该显示任何东西
  const closed = /<\/think>/i.test(m[0])
  return { thinking: m[1] || '', content: closed ? after.replace(/^\s*/, '') : '' }
}

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text || chatLoading.value) return
  messages.value.push({ role: 'user', content: text })
  chatInput.value = ''
  chatLoading.value = true
  // 先插入一个空的 assistant 消息，流式填充
  messages.value.push({ role: 'assistant', content: '', thinking: '' })
  const aiMsg = messages.value[messages.value.length - 1]
  scrollChat()
  try {
    const payload = {
      messages: messages.value
        .filter(m => m.role !== 'thinking')
        .map(m => ({
          role: m.role,
          // 剥掉历史 assistant 消息里的 <think> 标签，避免诱导模型继续思考
          content: m.role === 'assistant' ? stripThink(m.content) : m.content,
        })),
      max_tokens: chatMaxTokens.value,
      temperature: 0.7,
      stream: true,
    }
    if (chatThinking.value) payload.chat_template_kwargs = { enable_thinking: true }
    else payload.chat_template_kwargs = { enable_thinking: false }

    // 用 fetch 读 SSE 流
    const resp = await fetch(`/api/services/${sid}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
      // 按行解析 SSE
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
          if (delta.reasoning_content) aiMsg.thinking += delta.reasoning_content
          if (delta.content) {
            // 流式分块：实时把 <think> 内容路由到 thinking 字段
            aiMsg.rawContent = (aiMsg.rawContent || '') + delta.content
            const parsed = splitThink(aiMsg.rawContent)
            aiMsg.thinking = parsed.thinking
            aiMsg.content = parsed.content
          }
          scrollChat()
        } catch (e) { /* 忽略解析错误 */ }
      }
    }
    if (!aiMsg.content && !aiMsg.thinking) {
      aiMsg.content = '（无输出，可能思考中或已截断）'
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
  clientCfg.value = await clientConfig(sid)
}

function copyText(t) {
  navigator.clipboard.writeText(t)
  ElMessage.success('已复制')
}

// ---------- 服务操作 ----------
async function doStart() { await startService(sid); ElMessage.success('启动中'); setTimeout(load, 3000) }
async function doStop() { await stopService(sid); ElMessage.success('已停止'); load() }
async function doRestart() { await restartService(sid); ElMessage.success('重启中'); setTimeout(load, 3000) }

const apiEndpoint = computed(() => service.value ? `http://${location.hostname}:${service.value.port}/v1` : '')

async function loadTemplates() {
  templates.value = await listTemplates()
}

async function load() {
  loading.value = true
  try {
    service.value = await getService(sid)
    args.value = { ...service.value.args }
    commandText.value = buildCommand()
    await loadTemplates()
    await refreshLogs()
    await loadClientConfig()
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  window.addEventListener('keydown', onGlobalKey)
})
onUnmounted(() => {
  stopLogStream()
  window.removeEventListener('keydown', onGlobalKey)
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
.log-toolbar { margin-bottom: 10px; display: flex; align-items: center; }
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
  background: #fdf6ec; border-radius: 6px; padding: 6px 8px;
  border: 1px dashed #e6a23c; max-height: 120px; overflow-y: auto;
}
.chat-streaming {
  color: #409eff; font-size: 18px; animation: blink 1s infinite;
}
@keyframes blink { 50% { opacity: 0.2; } }
.chat-controls { display: flex; align-items: center; gap: 8px; }
</style>
