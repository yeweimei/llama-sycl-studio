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
              @click="toggleChatLog(log)"
            >
              <div class="chatlog-head">
                <el-tag size="small" :type="chatLogStatusType(log.status)" effect="dark" class="chatlog-status">{{ chatLogStatusLabel(log.status) }}</el-tag>
                <span class="chatlog-model">{{ log.model_name }}</span>
                <span class="chatlog-time">{{ fmtChatLogTime(log.created_at) }}</span>
                <span v-if="log.completion_tokens" class="chatlog-tok">{{ log.completion_tokens }} tok</span>
                <span v-if="log.status === 'running'" class="chatlog-running">生成中...</span>
                <el-icon class="chatlog-arrow"><ArrowDown v-if="!chatLogExpanded.has(log.id)" /><ArrowUp v-else /></el-icon>
              </div>
              <div v-if="chatLogExpanded.has(log.id)" class="chatlog-body" @click.stop>
                <div v-if="log.error" class="chatlog-error">{{ log.error }}</div>
                <div class="chatlog-block">
                  <div class="chatlog-label">用户输入</div>
                  <pre class="chatlog-pre user">{{ log.user_message || '(空)' }}</pre>
                </div>
                <div v-if="log.thinking" class="chatlog-block">
                  <div class="chatlog-label thinking" @click.stop="toggleChatLogThinking(log)">
                    <el-icon><CaretRight v-if="!chatLogThinking.has(log.id)" /><CaretBottom v-else /></el-icon>
                    思考过程 ({{ log.thinking.length }} 字符)
                  </div>
                  <pre v-if="chatLogThinking.has(log.id)" class="chatlog-pre thinking">{{ log.thinking }}</pre>
                </div>
                <div class="chatlog-block">
                  <div class="chatlog-label">模型输出</div>
                  <pre class="chatlog-pre response">{{ log.response || (log.status === 'running' ? '(等待输出...)' : '(空)') }}</pre>
                </div>
              </div>
            </div>
          </div>
          <div v-if="!chatLogsLoading && chatLogs.length === 0" class="chatlog-empty">暂无对话日志</div>
        </div>
      </el-tab-pane>

      <!-- ================= 聊天测试台 ================= -->
      <el-tab-pane v-if="service?.supports_chat !== false" label="💬 聊天测试台" name="chat">
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
                <span class="session-title" @dblclick.stop="startRenameSession(s)" :title="s.title">{{ s.title }}</span>
                <span class="session-meta">{{ s.msg_count || 0 }} 条</span>
                <el-button v-if="s.id !== 0" size="small" link class="session-rename" @click.stop="startRenameSession(s)"><el-icon><Edit /></el-icon></el-button>
                <el-button v-if="s.id !== 0" size="small" link class="session-del" @click.stop="removeSession(s)"><el-icon><Delete /></el-icon></el-button>
              </div>
            </div>
          </div>
          <!-- 聊天主区 -->
          <div class="chat-panel">
          <div class="chat-messages" ref="chatView">
            <div v-if="!messages.length" class="chat-empty">输入消息开始测试（需模型已加载）</div>
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
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, Plus, Delete, Edit, ArrowUp, CaretRight, CaretBottom } from '@element-plus/icons-vue'
import {
  getService, startService, stopService, getServiceLogs,
  chatProxy, clientConfig, listPresets,
  getChatHistory, addChatHistory, clearChatHistory, parsePdf,
  listSessions, createSession, renameSession, deleteSession,
  deleteHistoryItem,
  getChatLogs, clearChatLogs,
} from '../api'
import { marked } from 'marked'
import hljs from 'highlight.js/lib/common'
import 'highlight.js/styles/github-dark.css'

marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text) {
  if (!text) return ''
  try {
    const html = marked.parse(text)
    return html
  } catch { return text }
}

// 代码高亮 + 复制按钮：在 DOM 更新后 post-process
function highlightCode() {
  nextTick(() => {
    const el = chatView.value
    if (!el) return
    el.querySelectorAll('pre code').forEach(block => {
      if (!block.dataset.highlighted) {
        try { hljs.highlightElement(block) } catch (e) { /* ignore */ }
        block.dataset.highlighted = '1'
        // 添加复制按钮
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

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  if (d.toDateString() === now.toDateString()) return `${hh}:${mm}`
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${hh}:${mm}`
}

async function copyMessage(m) {
  const text = m.content || m.thinking || ''
  if (await copyText(text)) {
    ElMessage.success('已复制')
  } else {
    ElMessage.error('复制失败')
  }
}

// 通用复制：优先 Clipboard API，非 HTTPS 环境降级 textarea+execCommand
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

async function deleteMessage(i) {
  const m = messages.value[i]
  if (!m) return
  if (m.history_id) {
    try { await deleteHistoryItem(sid, m.history_id) } catch (e) { /* ignore */ }
  }
  messages.value.splice(i, 1)
  try { await loadSessions() } catch (e) { /* ignore */ }
}

async function regenerate(i) {
  // 找到 assistant 消息之前的最后一条 user 消息
  if (chatLoading.value) return
  // 删除该 assistant 消息（含历史）
  const m = messages.value[i]
  if (m?.history_id) {
    try { await deleteHistoryItem(sid, m.history_id) } catch (e) { /* ignore */ }
  }
  messages.value.splice(i, 1)
  // 找到最后一条 user 消息
  let lastUserIdx = -1
  for (let j = messages.value.length - 1; j >= 0; j--) {
    if (messages.value[j].role === 'user') { lastUserIdx = j; break }
  }
  if (lastUserIdx < 0) return
  // 恢复输入并重新发送
  chatInput.value = messages.value[lastUserIdx].content
  // 删除该 user 消息（避免重复）
  if (messages.value[lastUserIdx]?.history_id) {
    try { await deleteHistoryItem(sid, messages.value[lastUserIdx].history_id) } catch (e) { /* ignore */ }
  }
  messages.value.splice(lastUserIdx, 1)
  await sendChat()
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

// ---------- 对话日志（虚拟滚动）----------
const CHATLOG_ROW_H = 52           // 每行固定高度（折叠态）
const CHATLOG_OVERSCAN = 6         // 可视区外预渲染行数
const chatLogs = ref([])
const chatLogsLoading = ref(false)
const chatLogAutoRefresh = ref(true)
const chatLogExpanded = ref(new Set())
const chatLogThinking = ref(new Set())
const chatLogViewport = ref(null)
const chatLogScrollTop = ref(0)
const chatLogViewportH = ref(480)
let chatLogTimer = null

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
function toggleChatLog(log) {
  const s = new Set(chatLogExpanded.value)
  if (s.has(log.id)) s.delete(log.id)
  else s.add(log.id)
  chatLogExpanded.value = s
}
function toggleChatLogThinking(log) {
  const s = new Set(chatLogThinking.value)
  if (s.has(log.id)) s.delete(log.id)
  else s.add(log.id)
  chatLogThinking.value = s
}

async function refreshChatLogs() {
  chatLogsLoading.value = true
  try {
    const modelName = service.value?.name || ''
    const d = await getChatLogs(modelName, 300)
    const items = (d && (d.items || d.data?.items)) || []
    // 新 running 自动展开
    const s = new Set(chatLogExpanded.value)
    for (const it of items) if (it.status === 'running') s.add(it.id)
    chatLogExpanded.value = s
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

// ---------- 聊天 ----------
const messages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
// 聊天设置持久化（按服务分开存）
const CHAT_SET_KEY = `chat-settings-${sid}`
const chatThinking = ref(localStorage.getItem(CHAT_SET_KEY) ? JSON.parse(localStorage.getItem(CHAT_SET_KEY)).thinking ?? false : false)
const chatMaxTokens = ref(localStorage.getItem(CHAT_SET_KEY) ? JSON.parse(localStorage.getItem(CHAT_SET_KEY)).maxTokens ?? 512 : 512)
const chatView = ref(null)
const fileParsing = ref(false)
const pendingImage = ref(null) // base64 data URL

// ---------- 会话管理 ----------
const sessions = ref([])
const currentSessionId = ref(0)

async function loadSessions() {
  try {
    sessions.value = await listSessions(sid)
  } catch (e) { /* ignore */ }
}

async function createNewSession() {
  try {
    const s = await createSession(sid, { title: `新会话 ${sessions.value.length}` })
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

let renamingSession = false

async function startRenameSession(s) {
  if (s.id === 0) return // 默认会话不可重命名
  if (renamingSession) return  // 防重复打开叠加
  renamingSession = true
  try {
    const { value } = await ElMessageBox.prompt('会话标题', '重命名会话', {
      inputValue: s.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    if (value && value.trim()) {
      await renameSession(sid, s.id, { title: value.trim() })
      s.title = value.trim()
    }
  } catch (e) { /* cancel */ } finally {
    renamingSession = false
  }
}

async function removeSession(s) {
  try {
    await ElMessageBox.confirm(`确认删除会话「${s.title}」及其历史记录？`, '删除确认', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
    await deleteSession(sid, s.id)
    sessions.value = sessions.value.filter(x => x.id !== s.id)
    if (currentSessionId.value === s.id) {
      await switchSession(0)
    }
  } catch (e) { /* cancel */ }
}
// 打断控制器
let chatAbort = null

watch([chatThinking, chatMaxTokens], () => {
  localStorage.setItem(CHAT_SET_KEY, JSON.stringify({ thinking: chatThinking.value, maxTokens: chatMaxTokens.value }))
})

function stopChat() {
  if (chatAbort) chatAbort.abort()
}

const canChat = computed(() => service.value?.loaded)
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
// 图片上传能力：以后端实际检测为准（模型目录有 mmproj 才显示）
const isVisionModel = computed(() => !!service.value?.has_mmproj)

function stripThink(text) {
  if (!text) return text
  return text.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '').trim()
}

// thinking 折叠状态
const thinkingExpanded = ref({})
// 用户手动折叠/展开过的消息索引（流式过程中尊重用户操作，不再强制展开）
const thinkingUserToggled = ref({})
// thinking 滚动容器 refs
const thinkingRefs = ref({})

function setThinkingRef(el, index) {
  if (el) thinkingRefs.value[index] = el
}

// 思考框滚动到底部（内容增长时跟随最新思考）
function scrollThinking(index) {
  const el = thinkingRefs.value[index]
  if (!el) return
  // Vue DOM 更新异步：等渲染完成再滚动，避免 scrollHeight 读到旧值
  nextTick(() => {
    el.scrollTop = el.scrollHeight
  })
}

function toggleThinking(index) {
  thinkingExpanded.value[index] = !thinkingExpanded.value[index]
  thinkingUserToggled.value[index] = true
  // 展开后滚动到底部
  if (thinkingExpanded.value[index]) {
    setTimeout(() => scrollThinking(index), 50)
  }
}

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text || chatLoading.value) return
  // 同步置位防重入（必须在任何 await 之前，否则双击/连按会重复发送）
  chatLoading.value = true
  // 构建消息内容（多模态图片）
  let userContent = text
  if (pendingImage.value) {
    userContent = [
      { type: 'text', text },
      { type: 'image_url', image_url: { url: pendingImage.value } },
    ]
  }
  messages.value.push({ role: 'user', content: text })
  // 持久化用户消息（仅一次）
  try { const r = await addChatHistory(sid, { role: 'user', content: text, session_id: currentSessionId.value }); if (r.id) messages.value[messages.value.length - 1].history_id = r.id } catch (e) { /* ignore */ }
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
        // 剔除末尾空 assistant 占位（仅前端显示用，不发模型，避免末尾连续 assistant 校验失败）
        .filter((m, i, arr) => !(m.role === 'assistant' && !m.content.trim() && i === arr.length - 1))
        .map((m, idx, arr) => {
          // 最后一条用户消息用多模态内容
          if (m.role === 'user' && idx === arr.length - 1 && Array.isArray(userContent)) {
            return { role: 'user', content: userContent }
          }
          return {
            role: m.role,
            content: m.role === 'assistant' ? stripThink(m.content) : m.content,
          }
        }),
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
            // 首次出现思考内容时自动展开；之后尊重用户手动折叠状态
            const idx = messages.value.indexOf(aiMsg)
            if (idx >= 0 && firstThinking && !thinkingUserToggled.value[idx]) {
              thinkingExpanded.value[idx] = true
            }
            // 思考内容增长时滚动到底部（跟随最新思考）
            if (idx >= 0) scrollThinking(idx)
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
          highlightCode()
        } catch (e) { /* 忽略解析错误 */ }
      }
    }
    if (!aiMsg.content && !aiMsg.thinking) {
      aiMsg.content = '（模型未返回内容：可能是思考模式未产出正式回答，或 max_tokens 在思考阶段被截断。可尝试关闭思考模式或调大 max_tokens）'
      aiMsg.isError = true
    } else if (!aiMsg.content && aiMsg.thinking) {
      aiMsg.content = '（模型仅返回了思考内容，未生成正式回答）'
    } else {
      // 流式结束后默认折叠思考内容（用户手动操作过的保持原状）
      const idx = messages.value.indexOf(aiMsg)
      if (idx >= 0 && aiMsg.thinking && !thinkingUserToggled.value[idx]) {
        thinkingExpanded.value[idx] = false
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      // 用户主动停止：保留已生成内容
      if (!aiMsg.content && aiMsg.thinking) aiMsg.content = '（已停止：仅输出了思考内容）'
    } else {
      aiMsg.content = `❌ 调用失败: ${e.message || e}`
    }
  } finally {
    chatLoading.value = false
    chatAbort = null
    scrollChat()
    highlightCode()
    // 持久化助手回复（跳过占位提示/错误/空回复；仅思考无正式回答也不存）
    const hasReal = aiMsg.content && !aiMsg.content.startsWith('（') && !aiMsg.content.startsWith('❌')
    if (hasReal) {
      try { const r = await addChatHistory(sid, { role: 'assistant', content: aiMsg.content, thinking: aiMsg.thinking || '', session_id: currentSessionId.value }); if (r.id) aiMsg.history_id = r.id } catch (e) { /* ignore */ }
    }
    // 刷新会话列表（消息数实时更新）
    try { await loadSessions() } catch (e) { /* ignore */ }
  }
}

async function clearChat() {
  messages.value = []
  thinkingExpanded.value = {}
  thinkingUserToggled.value = {}
  thinkingRefs.value = {}
  try { await clearChatHistory(sid, currentSessionId.value) } catch (e) { /* ignore */ }
  try { await loadSessions() } catch (e) { /* ignore */ }
}

function scrollChat() {
  nextTick(() => {
    const el = chatView.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// ---------- 文件上传 ----------
async function handleFileUpload(file) {
  fileParsing.value = true
  try {
    if (file.name.endsWith('.pdf')) {
      const resp = await parsePdf(sid, file)
      chatInput.value = (chatInput.value ? chatInput.value + '\n' : '') + resp.text
    } else {
      // txt/md 直接读文本
      const text = await file.text()
      chatInput.value = (chatInput.value ? chatInput.value + '\n' : '') + text.slice(0, 8000)
    }
  } catch (e) {
    ElMessage.error('文件解析失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    fileParsing.value = false
  }
  return false // 阻止 el-upload 默认上传
}

async function handleImageUpload(file) {
  // 转 base64 data URL
  const reader = new FileReader()
  reader.onload = () => {
    pendingImage.value = reader.result
  }
  reader.readAsDataURL(file)
  return false
}

// ---------- 加载历史 ----------
async function loadHistory() {
  try {
    const list = await getChatHistory(sid, currentSessionId.value)
    if (list.length) {
      // 过滤占位提示/错误消息，并去重连续重复的 user 消息
      const cleaned = []
      let lastKey = null
      for (const h of list) {
        const content = (h.content || '').trim()
        if (!content) continue
        if (content.startsWith('（') || content.startsWith('❌')) continue
        const key = `${h.role}:${content}`
        if (h.role === 'user' && key === lastKey) continue // 去重
        lastKey = key
        cleaned.push({ role: h.role, content: h.content, thinking: h.thinking || '', history_id: h.id, created_at: h.created_at })
      }
      messages.value = cleaned
      highlightCode()
    }
  } catch (e) { /* ignore */ }
}

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
  loadSessions().then(() => loadHistory())
  window.addEventListener('keydown', onGlobalKey)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKey)
  stopLogPolling()
  stopChatLogPolling()
})

function onGlobalKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && activeTab.value === 'chat' && document.activeElement?.tagName === 'TEXTAREA') {
    e.preventDefault()
    if (!chatLoading.value) sendChat()
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
/* 对话日志（虚拟滚动） */
.chatlog-toolbar { display: flex; align-items: center; margin-bottom: 8px; }
.chatlog-count { font-size: 13px; color: #909399; }
.chatlog-vlist {
  background: #1e1e1e; border-radius: 6px;
  height: 480px; overflow-y: auto; position: relative;
}
.chatlog-item {
  position: absolute; left: 0; right: 0; top: 0;
  height: 52px; padding: 6px 12px; box-sizing: border-box;
  cursor: pointer; border-bottom: 1px solid #2a2a2a;
  background: transparent; transition: background .15s;
}
.chatlog-item:hover { background: #2a2a2a; }
.chatlog-item.is-running { background: rgba(64,158,255,.12); }
.chatlog-item.is-error { background: rgba(245,108,108,.12); }
.chatlog-head { display: flex; align-items: center; gap: 8px; height: 100%; }
.chatlog-status { flex-shrink: 0; }
.chatlog-model { color: #e8e8e8; font-weight: 600; font-size: 12px; flex-shrink: 0; }
.chatlog-time { color: #888; font-size: 11px; margin-left: auto; }
.chatlog-tok { color: #888; font-size: 11px; }
.chatlog-running { color: #409eff; font-size: 11px; animation: pulse 1.2s infinite; }
.chatlog-arrow { color: #888; }
.chatlog-body { background: #161616; padding: 10px 12px; border-radius: 4px; margin-top: 4px; }
.chatlog-block { margin-bottom: 8px; }
.chatlog-block:last-child { margin-bottom: 0; }
.chatlog-label { font-size: 11px; color: #909399; margin-bottom: 4px; }
.chatlog-label.thinking { color: #b37feb; cursor: pointer; display: flex; align-items: center; gap: 4px; }
.chatlog-pre {
  margin: 0; padding: 8px 10px; border-radius: 4px; font-size: 12px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-word; max-height: 200px; overflow-y: auto;
  font-family: 'JetBrains Mono', Consolas, monospace; color: #d4d4d4;
}
.chatlog-pre.user { background: #232323; }
.chatlog-pre.response { background: #1d2b1d; color: #a6e3a1; }
.chatlog-pre.thinking { background: #231d2b; color: #c9a6e3; }
.chatlog-error { color: #f56c6c; font-size: 12px; padding: 6px 10px; background: #2b1d1d; border-radius: 4px; margin-bottom: 8px; }
.chatlog-empty { color: #666; text-align: center; padding: 40px 0; font-size: 13px; }
@keyframes pulse { 0%,100% {opacity:1} 50% {opacity:.4} }
.form-tip { font-size: 12px; color: #909399; margin-top: 6px; }

.chat-layout { display: flex; gap: 12px; height: 540px; }
.session-sidebar { width: 200px; flex-shrink: 0; border-right: 1px solid #ebeef5; display: flex; flex-direction: column; }
.session-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 4px; border-bottom: 1px solid #f0f0f0; }
.session-list { flex: 1; overflow-y: auto; }
.session-item { display: flex; align-items: center; gap: 4px; padding: 6px 8px; cursor: pointer; border-radius: 4px; font-size: 13px; }
.session-item:hover { background: #f5f7fa; }
.session-item.active { background: #ecf5ff; color: #409eff; }
.session-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.session-meta { font-size: 11px; color: #c0c4cc; flex-shrink: 0; }
.session-rename { opacity: 0; flex-shrink: 0; }
.session-del { opacity: 0; flex-shrink: 0; }
.session-item:hover .session-rename,
.session-item:hover .session-del { opacity: 1; }
.chat-panel { flex: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.chat-messages {
  flex: 1; overflow-y: auto; background: #fafafa; border-radius: 8px;
  padding: 16px; border: 1px solid #ebeef5;
}
.chat-empty { color: #c0c4cc; text-align: center; margin-top: 180px; font-size: 14px; }
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
.chat-streaming {
  color: #409eff; font-size: 18px; animation: blink 1s infinite;
}
@keyframes blink { 50% { opacity: 0.2; } }
.chat-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.chat-input-area { flex-shrink: 0; }
.chat-input-area :deep(.el-textarea__inner) { resize: none; }

/* 移动端适配 */
@media (max-width: 767px) {
  .log-toolbar > * { margin-bottom: 4px; }
  .log-toolbar :deep(.el-date-editor) { width: 100% !important; }
  .chat-messages { height: 320px; padding: 10px; }
  .chat-bubble-wrap { max-width: 90%; }
  .chat-layout { flex-direction: column; height: auto; }
  .session-sidebar { width: 100%; border-right: none; border-bottom: 1px solid #ebeef5; max-height: 120px; }
  .chat-actions { display: flex !important; }
  .el-col + .el-col { margin-top: 12px; }
}
</style>
