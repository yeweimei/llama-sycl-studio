<template>
  <div class="page-container">
    <!-- 顶部：模型选择 -->
    <el-card shadow="never" style="margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-size:14px;font-weight:600">对话模型</span>
        <el-select v-model="currentSid" placeholder="选择模型" style="width:320px" filterable @change="onModelChange">
          <el-option
            v-for="s in chatModels"
            :key="s.id"
            :value="s.id"
            :label="`${s.name}${s.loaded ? ' ✓已加载' : '（未加载）'}${s.supports_chat === false ? ' ⚠️不支持对话' : ''}`"
            :disabled="s.supports_chat === false"
          />
        </el-select>
        <el-tag v-if="currentService" size="small" :type="currentService.loaded ? 'success' : 'info'">
          {{ currentService.loaded ? '已加载' : '未加载' }}
        </el-tag>
        <el-button v-if="currentService && !currentService.loaded" size="small" type="primary" :loading="loadingModel" @click="loadCurrentModel">
          加载模型
        </el-button>
        <el-button v-if="currentService && currentService.loaded" size="small" type="warning" :loading="loadingModel" @click="unloadCurrentModel">
          卸载模型
        </el-button>
        <el-button size="small" @click="refreshServices">刷新列表</el-button>
        <span v-if="currentService && currentService.loaded && currentService.device_label" style="font-size:12px;color:#909399">
          {{ currentService.device_label }}
        </span>
      </div>
    </el-card>

    <!-- 聊天区 -->
    <el-card shadow="never" v-if="currentSid">
      <div class="chat-layout">
        <!-- 会话侧栏 -->
        <div class="session-sidebar">
          <div class="session-header">
            <span style="font-size:13px;font-weight:600">会话</span>
            <el-button size="small" link @click="createNewSession"><el-icon><Plus /></el-icon></el-button>
          </div>
          <div class="session-list">
            <div
              v-for="s in sessions"
              :key="s.id"
              class="session-item"
              :class="{ active: s.id === currentSessionId }"
              @click="switchSession(s.id)"
            >
              <span class="session-title" @click.stop="startRenameSession(s)" :title="s.title">{{ s.title }}</span>
              <span class="session-meta">{{ s.msg_count || 0 }} 条</span>
              <el-button v-if="s.id !== 0" size="small" link class="session-del" @click.stop="removeSession(s)"><el-icon><Delete /></el-icon></el-button>
            </div>
          </div>
        </div>

        <!-- 聊天主区 -->
        <div class="chat-panel">
          <div class="chat-messages" ref="chatView">
            <div v-if="!messages.length" class="chat-empty">输入消息开始对话（需模型已加载）</div>
            <div v-for="(m, i) in messages" :key="i" class="chat-msg" :class="m.role">
              <div class="chat-avatar" :class="m.role">{{ m.role === 'user' ? '🧑' : '🤖' }}</div>
              <div class="chat-bubble-wrap">
                <div class="chat-bubble">
                  <div v-if="m.role === 'assistant'" class="chat-content markdown-body" v-html="renderMarkdown(m.content)"></div>
                  <div v-else class="chat-content" style="white-space:pre-wrap">{{ m.content }}</div>
                  <div v-if="m.thinking" class="chat-thinking">
                    <div class="thinking-header" @click="toggleThinking(i)">
                      <span>🤔 思考过程</span>
                      <el-icon class="thinking-arrow" :class="{ collapsed: !thinkingExpanded[i] }"><ArrowDown /></el-icon>
                    </div>
                    <div v-show="thinkingExpanded[i]" class="thinking-body" :ref="el => setThinkingRef(el, i)">{{ m.thinking }}</div>
                  </div>
                </div>
                <div class="chat-meta">
                  <span class="chat-time">{{ fmtTime(m.created_at) }}</span>
                  <div class="chat-actions" v-if="!chatLoading">
                    <el-button link size="small" @click="copyMessage(m)">复制</el-button>
                    <el-button v-if="m.role === 'assistant'" link size="small" @click="regenerate(i)">重新生成</el-button>
                    <el-button link size="small" style="color:#f56c6c" @click="deleteMessage(i)">删除</el-button>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="chatLoading" class="chat-msg assistant">
              <div class="chat-avatar assistant">🤖</div>
              <div class="chat-bubble-wrap"><div class="chat-bubble"><div class="chat-streaming">▋</div></div></div>
            </div>
          </div>
          <div class="chat-controls">
            <el-checkbox v-model="chatThinking">思考模式</el-checkbox>
            <span style="margin-left:12px;font-size:13px;color:#909399">max_tokens</span>
            <el-input-number v-model="chatMaxTokens" :min="32" :max="maxTokensLimit" :step="64" size="small" style="width:130px" />
            <el-upload
              :show-file-list="false"
              :before-upload="handleFileUpload"
              accept=".txt,.md,.pdf"
              style="margin-left:8px"
            >
              <el-button size="small" :loading="fileParsing">上传文件</el-button>
            </el-upload>
            <el-upload
              v-if="isVisionModel"
              :show-file-list="false"
              :before-upload="handleImageUpload"
              accept="image/png,image/jpeg"
              style="margin-left:4px"
            >
              <el-button size="small">图片</el-button>
            </el-upload>
            <el-button v-if="!chatLoading" size="small" type="primary" style="margin-left:auto" :disabled="!canChat" @click="sendChat">发送</el-button>
            <el-button v-else size="small" type="danger" style="margin-left:auto" @click="stopChat">⏹ 停止</el-button>
            <el-button size="small" @click="clearChat">清空</el-button>
          </div>
          <div v-if="pendingImage" style="margin-bottom:4px">
            <el-tag closable @close="pendingImage = null">📷 图片已附加</el-tag>
          </div>
          <div class="chat-input-area">
            <el-input
              v-model="chatInput"
              type="textarea"
              :autosize="{ minRows: 2, maxRows: 6 }"
              placeholder="输入消息，Enter 发送 / Shift+Enter 换行"
              @keydown.enter.exact.prevent="sendChat"
              maxlength="4096"
              show-word-limit
            />
          </div>
        </div>
      </div>
    </el-card>

    <el-empty v-else description="请选择对话模型" />
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Delete } from '@element-plus/icons-vue'
import {
  listServices, startService, stopService, listPresets,
  listSessions, createSession, renameSession, deleteSession,
  getChatHistory, addChatHistory, clearChatHistory, deleteHistoryItem,
  parsePdf,
} from '../api'
import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'

// ---------- 模型选择 ----------
const services = ref([])
const currentSid = ref(null)
const loadingModel = ref(false)
const presets = ref([])

// 当前模型可用上下文（决定 max_tokens 上限）
// llama.cpp 机制：总 ctx(--ctx-size) 按 parallel(slot) 均分，meta.n_ctx 即每 slot 上下文；
// max_tokens 上限 = 每 slot 上下文 × 0.75（预留 25% 给对话历史 prompt）
const maxTokensLimit = computed(() => {
  const svc = currentService.value
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

async function refreshPresets() {
  try { presets.value = await listPresets() } catch (e) { presets.value = [] }
}

// 可对话模型（supports_chat !== false）
const chatModels = computed(() => services.value.filter(s => s.supports_chat !== false))
const currentService = computed(() => services.value.find(s => s.id === currentSid.value))

async function refreshServices() {
  try {
    services.value = await listServices()
    // 默认选第一个已加载的对话模型，否则第一个
    if (!currentSid.value || !services.value.some(s => s.id === currentSid.value)) {
      const loaded = services.value.find(s => s.supports_chat !== false && s.loaded)
      const first = services.value.find(s => s.supports_chat !== false)
      currentSid.value = (loaded || first || services.value[0] || null)?.id ?? null
    }
  } catch (e) { /* ignore */ }
}

function onModelChange() {
  // 切换模型：重置会话与历史
  currentSessionId.value = 0
  messages.value = []
  thinkingExpanded.value = {}
  thinkingUserToggled.value = {}
  thinkingRefs.value = {}
  // max_tokens 超过新模型上下文时自动收敛
  if (chatMaxTokens.value > maxTokensLimit.value) {
    chatMaxTokens.value = maxTokensLimit.value
  }
  loadSessions().then(() => loadHistory())
}

async function loadCurrentModel() {
  if (!currentSid.value) return
  loadingModel.value = true
  try {
    await startService(currentSid.value)
    ElMessage.success('模型加载中…')
    await refreshServices()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载失败')
  } finally {
    loadingModel.value = false
  }
}

async function unloadCurrentModel() {
  if (!currentSid.value) return
  loadingModel.value = true
  try {
    await stopService(currentSid.value)
    ElMessage.success('已卸载')
    await refreshServices()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '卸载失败')
  } finally {
    loadingModel.value = false
  }
}

// ---------- 聊天 ----------
const messages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
// 聊天设置持久化（按模型分开存）
const CHAT_SET_KEY = computed(() => `chat-settings-${currentSid.value}`)
const chatThinking = ref(false)
const chatMaxTokens = ref(512)
const chatView = ref(null)
const fileParsing = ref(false)
const pendingImage = ref(null)
// 会话
const sessions = ref([])
const currentSessionId = ref(0)

// 加载设置（切模型时恢复该模型的设置）
function loadChatSettings() {
  const saved = localStorage.getItem(CHAT_SET_KEY.value)
  const parsed = saved ? JSON.parse(saved) : {}
  chatThinking.value = parsed.thinking ?? false
  chatMaxTokens.value = parsed.maxTokens ?? 512
}
watch(CHAT_SET_KEY, loadChatSettings)

watch([chatThinking, chatMaxTokens], () => {
  localStorage.setItem(CHAT_SET_KEY.value, JSON.stringify({ thinking: chatThinking.value, maxTokens: chatMaxTokens.value }))
})

async function loadSessions() {
  try {
    sessions.value = await listSessions(currentSid.value)
  } catch (e) { /* ignore */ }
}

async function createNewSession() {
  try {
    const s = await createSession(currentSid.value, { title: `新会话 ${sessions.value.length}` })
    sessions.value.unshift(s)
    await switchSession(s.id)
  } catch (e) { ElMessage.error('创建会话失败') }
}

async function switchSession(sessionId) {
  currentSessionId.value = sessionId
  messages.value = []
  thinkingExpanded.value = {}
  thinkingUserToggled.value = {}
  thinkingRefs.value = {}
  await loadHistory()
}

async function startRenameSession(s) {
  if (s.id === 0) return
  try {
    const { value } = await ElMessageBox.prompt('会话标题', '重命名会话', {
      inputValue: s.title, confirmButtonText: '保存', cancelButtonText: '取消',
    })
    if (value && value.trim()) {
      await renameSession(currentSid.value, s.id, { title: value.trim() })
      s.title = value.trim()
    }
  } catch (e) { /* cancel */ }
}

async function removeSession(s) {
  try {
    await ElMessageBox.confirm(`确认删除会话「${s.title}」及其历史记录？`, '删除确认', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
    await deleteSession(currentSid.value, s.id)
    sessions.value = sessions.value.filter(x => x.id !== s.id)
    if (currentSessionId.value === s.id) await switchSession(0)
  } catch (e) { /* cancel */ }
}

// 打断控制器
let chatAbort = null

function stopChat() {
  if (chatAbort) chatAbort.abort()
}

const canChat = computed(() => currentService.value?.loaded)
const isVisionModel = computed(() => !!currentService.value?.has_mmproj)

function stripThink(text) {
  if (!text) return text
  return text.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '').trim()
}

// thinking 折叠状态
const thinkingExpanded = ref({})
const thinkingUserToggled = ref({})
const thinkingRefs = ref({})

function setThinkingRef(el, index) {
  if (el) thinkingRefs.value[index] = el
}

function scrollThinking(index) {
  const el = thinkingRefs.value[index]
  if (!el) return
  nextTick(() => { el.scrollTop = el.scrollHeight })
}

function toggleThinking(index) {
  thinkingExpanded.value[index] = !thinkingExpanded.value[index]
  thinkingUserToggled.value[index] = true
  if (thinkingExpanded.value[index]) {
    setTimeout(() => scrollThinking(index), 50)
  }
}

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text || chatLoading.value) return
  if (!currentSid.value) { ElMessage.warning('请先选择模型'); return }
  chatLoading.value = true
  let userContent = text
  if (pendingImage.value) {
    userContent = [
      { type: 'text', text },
      { type: 'image_url', image_url: { url: pendingImage.value } },
    ]
  }
  messages.value.push({ role: 'user', content: text })
  try {
    const r = await addChatHistory(currentSid.value, { role: 'user', content: text, session_id: currentSessionId.value })
    if (r.id) messages.value[messages.value.length - 1].history_id = r.id
  } catch (e) { /* ignore */ }
  chatInput.value = ''
  pendingImage.value = null
  messages.value.push({ role: 'assistant', content: '', thinking: '' })
  const aiMsg = messages.value[messages.value.length - 1]
  scrollChat()
  const controller = new AbortController()
  chatAbort = controller
  try {
    const payload = {
      messages: messages.value
        .filter(m => m.role !== 'thinking')
        .filter((m, i, arr) => !(m.role === 'assistant' && !m.content.trim() && i === arr.length - 1))
        .map((m, idx, arr) => {
          if (m.role === 'user' && idx === arr.length - 1 && Array.isArray(userContent)) {
            return { role: 'user', content: userContent }
          }
          return { role: m.role, content: m.role === 'assistant' ? stripThink(m.content) : m.content }
        }),
      max_tokens: chatMaxTokens.value,
      temperature: 0.7,
      stream: true,
    }
    if (chatThinking.value) payload.chat_template_kwargs = { enable_thinking: true }
    else payload.chat_template_kwargs = { enable_thinking: false }

    const resp = await fetch(`/api/services/${currentSid.value}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('auth_token')}`,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
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
            const firstThinking = !aiMsg.thinking
            aiMsg.thinking += delta.reasoning_content
            const idx = messages.value.indexOf(aiMsg)
            if (idx >= 0 && firstThinking && !thinkingUserToggled.value[idx]) {
              thinkingExpanded.value[idx] = true
            }
            if (idx >= 0) scrollThinking(idx)
          }
          if (delta.content) {
            aiMsg.content += delta.content
            if (aiMsg.content.includes('<think')) {
              const m = aiMsg.content.match(/<think\b[^>]*>([\s\S]*?)(?:<\/think>|$)/i)
              if (m) {
                if (!aiMsg.thinking) aiMsg.thinking = m[1] || ''
                aiMsg.content = aiMsg.content.replace(/<think\b[^>]*>[\s\S]*?(?:<\/think>|$)/i, '').trim()
              }
            }
          }
          scrollChat()
          highlightCode()
        } catch (e) { /* ignore */ }
      }
    }
    if (!aiMsg.content && !aiMsg.thinking) {
      aiMsg.content = '（模型未返回内容：可能是思考模式未产出正式回答，或 max_tokens 在思考阶段被截断。可尝试关闭思考模式或调大 max_tokens）'
      aiMsg.isError = true
    } else if (!aiMsg.content && aiMsg.thinking) {
      aiMsg.content = '（模型仅返回了思考内容，未生成正式回答）'
    } else {
      const idx = messages.value.indexOf(aiMsg)
      if (idx >= 0 && aiMsg.thinking && !thinkingUserToggled.value[idx]) {
        thinkingExpanded.value[idx] = false
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      if (!aiMsg.content && aiMsg.thinking) aiMsg.content = '（已停止：仅输出了思考内容）'
    } else {
      aiMsg.content = `❌ 调用失败: ${e.message || e}`
    }
  } finally {
    chatLoading.value = false
    chatAbort = null
    scrollChat()
    highlightCode()
    const hasReal = aiMsg.content && !aiMsg.content.startsWith('（') && !aiMsg.content.startsWith('❌')
    if (hasReal) {
      try {
        const r = await addChatHistory(currentSid.value, { role: 'assistant', content: aiMsg.content, thinking: aiMsg.thinking || '', session_id: currentSessionId.value })
        if (r.id) aiMsg.history_id = r.id
      } catch (e) { /* ignore */ }
    }
    try { await loadSessions() } catch (e) { /* ignore */ }
  }
}

async function clearChat() {
  messages.value = []
  thinkingExpanded.value = {}
  thinkingUserToggled.value = {}
  thinkingRefs.value = {}
  try { await clearChatHistory(currentSid.value, currentSessionId.value) } catch (e) { /* ignore */ }
  try { await loadSessions() } catch (e) { /* ignore */ }
}

function scrollChat() {
  nextTick(() => {
    const el = chatView.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// ---------- 消息操作 ----------
async function copyMessage(m) {
  const text = m.content || m.thinking || ''
  if (await copyText(text)) ElMessage.success('已复制')
  else ElMessage.error('复制失败')
}

async function copyText(text) {
  if (!text) return false
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (e) { /* 降级 */ }
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

async function deleteMessage(i) {
  const m = messages.value[i]
  if (!m) return
  if (m.history_id) {
    try { await deleteHistoryItem(currentSid.value, m.history_id) } catch (e) { /* ignore */ }
  }
  messages.value.splice(i, 1)
  try { await loadSessions() } catch (e) { /* ignore */ }
}

async function regenerate(i) {
  const m = messages.value[i]
  if (!m) return
  // 删除本条 assistant + 前一条 user，重新生成
  messages.value.splice(i, 1)
  if (messages.value[i - 1]?.role === 'user') messages.value.splice(i - 1, 1)
  await sendChat()
}

// ---------- Markdown / 高亮 ----------
marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text) {
  if (!text) return ''
  try {
    return marked.parse(text)
  } catch (e) {
    return text
  }
}

function highlightCode() {
  nextTick(() => {
    document.querySelectorAll('.chat-content pre code').forEach(block => {
      if (!block.dataset.highlighted) {
        try { hljs.highlightElement(block) } catch (e) { /* ignore */ }
        block.dataset.highlighted = '1'
        const pre = block.parentElement
        if (pre && !pre.querySelector('.code-copy-btn')) {
          const btn = document.createElement('button')
          btn.className = 'code-copy-btn'
          btn.textContent = '复制'
          btn.onclick = async () => {
            const ok = await copyText(block.textContent)
            btn.textContent = ok ? '✓' : '✗'
            setTimeout(() => { btn.textContent = '复制' }, 1500)
          }
          pre.style.position = 'relative'
          pre.appendChild(btn)
        }
      }
    })
  })
}

// ---------- 文件上传 ----------
async function handleFileUpload(file) {
  fileParsing.value = true
  try {
    if (file.name.endsWith('.pdf')) {
      const resp = await parsePdf(currentSid.value, file)
      chatInput.value = (chatInput.value ? chatInput.value + '\n' : '') + resp.text
    } else {
      const text = await file.text()
      chatInput.value = (chatInput.value ? chatInput.value + '\n' : '') + text.slice(0, 8000)
    }
  } catch (e) {
    ElMessage.error('文件解析失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    fileParsing.value = false
  }
  return false
}

async function handleImageUpload(file) {
  const reader = new FileReader()
  reader.onload = () => { pendingImage.value = reader.result }
  reader.readAsDataURL(file)
  return false
}

// ---------- 历史 ----------
async function loadHistory() {
  try {
    const list = await getChatHistory(currentSid.value, currentSessionId.value)
    if (list.length) {
      const cleaned = []
      let lastKey = null
      for (const h of list) {
        const content = (h.content || '').trim()
        if (!content) continue
        if (content.startsWith('（') || content.startsWith('❌')) continue
        const key = `${h.role}:${content}`
        if (h.role === 'user' && key === lastKey) continue
        lastKey = key
        cleaned.push({ role: h.role, content: h.content, thinking: h.thinking || '', history_id: h.id, created_at: h.created_at })
      }
      messages.value = cleaned
    }
  } catch (e) { /* ignore */ }
}

// ---------- 工具 ----------
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  if (d.toDateString() === now.toDateString()) return `${hh}:${mm}`
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${hh}:${mm}`
}

// ---------- 生命周期 ----------
onMounted(async () => {
  await refreshPresets()
  await refreshServices()
  if (currentSid.value) {
    loadChatSettings()
    loadSessions().then(() => loadHistory())
  }
})

onUnmounted(() => {
  if (chatAbort) chatAbort.abort()
})
</script>

<style scoped>
.chat-layout { display: flex; gap: 12px; height: calc(100vh - 260px); min-height: 420px; }
.session-sidebar { width: 200px; flex-shrink: 0; border-right: 1px solid #ebeef5; display: flex; flex-direction: column; }
.session-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 4px; border-bottom: 1px solid #f0f0f0; }
.session-list { flex: 1; overflow-y: auto; }
.session-item { display: flex; align-items: center; gap: 4px; padding: 6px 8px; cursor: pointer; border-radius: 4px; font-size: 13px; }
.session-item:hover { background: #f5f7fa; }
.session-item.active { background: #ecf5ff; color: #409eff; }
.session-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.session-meta { font-size: 11px; color: #c0c4cc; flex-shrink: 0; }
.session-del { opacity: 0; flex-shrink: 0; }
.session-item:hover .session-del { opacity: 1; }
.chat-panel { flex: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.chat-messages {
  flex: 1; overflow-y: auto; background: #fafafa; border-radius: 8px;
  padding: 16px; border: 1px solid #ebeef5;
}
.chat-empty { color: #c0c4cc; text-align: center; margin-top: 120px; font-size: 14px; }
.chat-msg { margin-bottom: 16px; display: flex; gap: 8px; }
.chat-msg.user { flex-direction: row-reverse; }
.chat-avatar { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.chat-avatar.user { background: #ecf5ff; }
.chat-avatar.assistant { background: #f0f0f0; }
.chat-bubble-wrap { max-width: 75%; display: flex; flex-direction: column; }
.chat-msg.user .chat-bubble-wrap { align-items: flex-end; }
.chat-bubble {
  padding: 10px 14px; border-radius: 12px;
  background: #fff; border: 1px solid #e4e7ed; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.chat-msg.user .chat-bubble { background: #ecf5ff; border-color: #d9ecff; }
.chat-meta { display: flex; align-items: center; gap: 8px; margin-top: 2px; }
.chat-time { font-size: 11px; color: #c0c4cc; }
.chat-actions { display: none; gap: 2px; }
.chat-bubble-wrap:hover .chat-actions { display: flex; }
.chat-actions .el-button { padding: 2px 4px; font-size: 11px; height: auto; }
.chat-content { font-size: 14px; line-height: 1.6; }
.chat-content.markdown-body :deep(p) { margin: 4px 0; }
.chat-content.markdown-body :deep(pre) { background: #1e1e1e; color: #d4d4d4; padding: 10px 14px; border-radius: 6px; overflow-x: auto; font-size: 13px; position: relative; }
.chat-content.markdown-body :deep(code) { background: #f0f0f0; padding: 1px 4px; border-radius: 3px; font-size: 13px; }
.chat-content.markdown-body :deep(pre code) { background: none; padding: 0; }
.chat-content.markdown-body :deep(ul), .chat-content.markdown-body :deep(ol) { padding-left: 20px; margin: 4px 0; }
.chat-content.markdown-body :deep(table) { border-collapse: collapse; }
.chat-content.markdown-body :deep(th), .chat-content.markdown-body :deep(td) { border: 1px solid #ddd; padding: 4px 8px; }
:deep(.code-copy-btn) { position: absolute; top: 4px; right: 4px; font-size: 11px; padding: 2px 8px; border-radius: 4px; border: 1px solid #444; background: #333; color: #ccc; cursor: pointer; opacity: 0; transition: opacity 0.2s; }
:deep(pre:hover .code-copy-btn) { opacity: 1; }
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
.chat-streaming { color: #409eff; font-size: 18px; animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0.2; } }
.chat-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.chat-input-area { flex-shrink: 0; }
.chat-input-area :deep(.el-textarea__inner) { resize: none; }

@media (max-width: 767px) {
  .chat-layout { flex-direction: column; height: auto; }
  .session-sidebar { width: 100%; border-right: none; border-bottom: 1px solid #ebeef5; max-height: 120px; }
  .chat-messages { height: 320px; padding: 10px; }
  .chat-bubble-wrap { max-width: 90%; }
  .chat-actions { display: flex !important; }
}
</style>
