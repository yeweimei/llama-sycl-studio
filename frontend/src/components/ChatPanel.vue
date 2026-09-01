<template>
  <div class="chat-layout" :style="layoutStyle">
    <!-- 会话侧栏 -->
    <div class="session-sidebar">
      <div class="session-header">
        <span style="font-size:13px;font-weight:600">会话</span>
        <el-button size="small" link @click="store.createNewSession(serviceId)"><el-icon><Plus /></el-icon></el-button>
      </div>
      <div class="session-list">
        <div
          v-for="s in st.sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === st.currentSessionId }"
          @click="store.switchSession(serviceId, s.id)"
        >
          <span class="session-title" @dblclick.stop="store.startRenameSession(serviceId, s)" :title="s.title">{{ s.title }}</span>
          <span class="session-meta">{{ s.msg_count || 0 }} 条</span>
          <el-button v-if="s.id !== 0" size="small" link class="session-rename" @click.stop="store.startRenameSession(serviceId, s)"><el-icon><Edit /></el-icon></el-button>
          <el-button v-if="s.id !== 0" size="small" link class="session-del" @click.stop="store.removeSession(serviceId, s)"><el-icon><Delete /></el-icon></el-button>
        </div>
      </div>
    </div>

    <!-- 聊天主区 -->
    <div class="chat-panel">
      <div class="chat-messages" ref="chatView">
        <div v-if="!st.messages.length" class="chat-empty">{{ emptyText }}</div>
        <div v-for="(m, i) in st.messages" :key="i" class="chat-msg" :class="m.role">
          <div class="chat-avatar" :class="m.role">{{ m.role === 'user' ? '🧑' : '🤖' }}</div>
          <div class="chat-bubble-wrap">
            <div class="chat-bubble">
              <div v-if="m.role === 'assistant'" class="chat-content markdown-body" v-html="renderMarkdown(m.content)"></div>
              <div v-else class="chat-content" style="white-space:pre-wrap">
                <!-- 已发送消息附带的文档文件名 -->
                <div v-if="m.files && m.files.length" class="chat-files">
                  <el-tag v-for="(f, fi) in m.files" :key="fi" size="small" type="info" class="chat-file-tag">📄 {{ f }}</el-tag>
                </div>
                <div v-if="userImages(m).length" class="chat-images">
                  <img
                    v-for="(img, j) in userImages(m)"
                    :key="j"
                    :src="img"
                    class="chat-image-thumb"
                    alt="对话图片"
                    title="点击查看大图"
                    @click="openImagePreview(img)"
                  />
                </div>
                <template v-if="userText(m)">{{ userText(m) }}</template>
              </div>
              <div v-if="m.thinking" class="chat-thinking">
                <div class="thinking-header" @click="store.toggleThinking(serviceId, i)">
                  <span>🤔 思考过程</span>
                  <el-icon class="thinking-arrow" :class="{ collapsed: !st.thinkingExpanded[i] }"><ArrowDown /></el-icon>
                </div>
                <div v-show="st.thinkingExpanded[i]" class="thinking-body" :ref="el => store.setThinkingRef(serviceId, el, i)">{{ m.thinking }}</div>
              </div>
              <div v-if="m.role === 'assistant' && m.metrics && (m.metrics.prefill_tps || m.metrics.decode_tps)" class="chat-metrics">
                <template v-if="m.metrics.prefill_tps">prefill {{ m.metrics.prefill_tps }} t/s</template><template v-if="m.metrics.prefill_tps && m.metrics.decode_tps"> · </template><template v-if="m.metrics.decode_tps">输出 {{ m.metrics.decode_tps }} t/s</template><template v-if="m.metrics.mtp_accept"> · MTP 接受率 {{ m.metrics.mtp_accept }}%</template>
              </div>
            </div>
            <div class="chat-meta">
              <span class="chat-time">{{ fmtTime(m.created_at) }}</span>
              <div class="chat-actions" v-if="!st.chatLoading">
                <el-button link size="small" @click="copyMessage(m)">复制</el-button>
                <el-button v-if="m.role === 'assistant'" link size="small" @click="store.regenerate(serviceId, i)">重新生成</el-button>
                <el-button link size="small" style="color:#f56c6c" @click="store.deleteMessage(serviceId, i)">删除</el-button>
              </div>
            </div>
          </div>
        </div>
        <div v-if="st.chatLoading" class="chat-msg assistant">
          <div class="chat-avatar assistant">🤖</div>
          <div class="chat-bubble-wrap"><div class="chat-bubble"><div class="chat-streaming">▋</div></div></div>
        </div>
      </div>

      <!-- 待发送附件（文档 chip + 图片 chip） -->
      <div v-if="st.pendingFiles.length || st.pendingImage" class="chat-attach-bar">
        <el-tag
          v-for="(f, fi) in st.pendingFiles"
          :key="'f' + fi"
          closable
          type="warning"
          class="attach-chip"
          @close="store.removePendingFile(serviceId, fi)"
        >
          📄 {{ f.name }}（{{ f.chars }} 字）
        </el-tag>
        <el-tag v-if="st.pendingImage" closable type="success" class="attach-chip" @close="store.clearPendingImage(serviceId)">
          📷 图片已附加
        </el-tag>
      </div>

      <div class="chat-controls">
        <el-checkbox v-model="st.chatThinking">思考模式</el-checkbox>
        <span style="margin-left:12px;font-size:13px;color:#909399">max_tokens</span>
        <el-input-number v-model="st.chatMaxTokens" :min="32" :max="maxTokensLimit" :step="64" size="small" style="width:130px" />
        <el-upload
          :show-file-list="false"
          :before-upload="(file) => store.handleFileUpload(serviceId, file)"
          accept=".txt,.md,.pdf,.docx,.xlsx"
          style="margin-left:8px"
        >
          <el-button size="small" :loading="st.fileParsing">上传文件</el-button>
        </el-upload>
        <el-upload
          v-if="isVision"
          :show-file-list="false"
          :before-upload="(file) => store.handleImageUpload(serviceId, file)"
          accept="image/png,image/jpeg"
          style="margin-left:4px"
        >
          <el-button size="small">图片</el-button>
        </el-upload>
        <el-button v-if="!st.chatLoading" size="small" type="primary" style="margin-left:auto" :disabled="!canChat" @click="onSend">发送</el-button>
        <el-button v-else size="small" type="danger" style="margin-left:auto" @click="store.stopChat(serviceId)">⏹ 停止</el-button>
        <el-button size="small" @click="store.clearChat(serviceId)">清空</el-button>
      </div>
      <div class="chat-input-area">
        <el-input
          v-model="st.chatInput"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          placeholder="输入消息，Enter 发送 / Shift+Enter 换行"
          @keydown.enter.exact.prevent="onSend"
          maxlength="4096"
          show-word-limit
        />
      </div>
    </div>

    <!-- 图片预览组件 -->
    <ImagePreview
      :model-value="!!previewImage"
      :src="previewImage || ''"
      @update:model-value="onPreviewClose"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Delete, Edit, ArrowDown } from '@element-plus/icons-vue'
import ImagePreview from './ImagePreview.vue'
import { useChatStore } from '../stores/chat'
import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'

const props = defineProps({
  // 服务（模型）id
  serviceId: { type: [String, Number], required: true },
  // 模型是否已加载（决定能否发送）
  modelLoaded: { type: Boolean, default: false },
  // 是否视觉模型（显示图片上传按钮）
  isVision: { type: Boolean, default: false },
  // max_tokens 上限（由外层按模型上下文算好传入）
  maxTokensLimit: { type: Number, default: 8192 },
  // 模型加载完成时间戳（秒），用于首次推理预热提示；不传则不提示
  serviceLoadedAt: { type: Number, default: 0 },
  // 空列表提示文案
  emptyText: { type: String, default: '输入消息开始对话（需模型已加载）' },
  // 布局高度（Chat 页 / 详情页 tab 高度不同）
  height: { type: String, default: 'calc(100vh - 260px)' },
})

const store = useChatStore()
// 该 sid 的响应式 state：setup 时 ensure 建好，避免在 computed（render 阶段）里 mutate store
store.ensure(props.serviceId)
const st = computed(() => store.states[props.serviceId])

const chatView = ref(null)
// 图片预览：当前预览的图片地址（null 表示未打开）
const previewImage = ref(null)

const layoutStyle = computed(() => ({ height: props.height, minHeight: '420px' }))
const canChat = computed(() => props.modelLoaded)

marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text) {
  if (!text) return ''
  try {
    return marked.parse(text)
  } catch (e) {
    return text
  }
}

// 从用户消息提取纯文本（兼容字符串与 OpenAI content 数组）
function userText(m) {
  const c = m.content
  if (Array.isArray(c)) {
    return c.filter(p => p.type === 'text').map(p => p.text || '').join('\n')
  }
  return typeof c === 'string' ? c : ''
}
// 从用户消息提取图片列表（发送时存 m.images；历史里可能是 content 数组）
function userImages(m) {
  if (m.images && m.images.length) return m.images
  const c = m.content
  if (Array.isArray(c)) {
    return c.filter(p => p.type === 'image_url' && p.image_url?.url).map(p => p.image_url.url)
  }
  return []
}

function openImagePreview(src) { previewImage.value = src }
function onPreviewClose(v) { if (!v) previewImage.value = null }

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  if (d.toDateString() === now.toDateString()) return `${hh}:${mm}`
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${hh}:${mm}`
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

async function copyMessage(m) {
  const text = m.content || m.thinking || ''
  if (await copyText(text)) ElMessage.success('已复制')
  else ElMessage.error('复制失败')
}

function onSend() {
  store.sendMessage(props.serviceId, {
    serviceLoadedAt: props.serviceLoadedAt,
    modelLoaded: props.modelLoaded,
  })
}

// 滚动到底部
function scrollChat() {
  nextTick(() => {
    const el = chatView.value
    if (el) el.scrollTop = el.scrollHeight
    // 展开中的思考框跟随滚动
    const s = st.value
    for (const k in s.thinkingRefs) {
      if (s.thinkingExpanded[k]) {
        const tel = s.thinkingRefs[k]
        if (tel) tel.scrollTop = tel.scrollHeight
      }
    }
  })
}

// 代码高亮 + 复制按钮：DOM 更新后 post-process
function highlightCode() {
  nextTick(() => {
    const el = chatView.value
    if (!el) return
    el.querySelectorAll('pre code').forEach(block => {
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

// 流式帧驱动：滚动 + 高亮（store 流式循环不碰 DOM）
watch(() => st.value.streamTick, () => {
  scrollChat()
  highlightCode()
})

// 设置持久化（思考模式 / max_tokens，按 sid）
watch(() => [st.value.chatThinking, st.value.chatMaxTokens], () => {
  store.persistSettings(props.serviceId)
}, { deep: false })

// max_tokens 超过新模型上下文时自动收敛
watch(() => props.maxTokensLimit, (limit) => {
  if (st.value.chatMaxTokens > limit) st.value.chatMaxTokens = limit
})

onMounted(async () => {
  await store.initChat(props.serviceId)
  // 初始化后滚到底（历史消息）
  scrollChat()
  highlightCode()
})
</script>

<style scoped>
.chat-layout { display: flex; gap: 12px; min-height: 420px; }
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
.chat-images { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 4px; }
.chat-image-thumb { max-width: 220px; max-height: 220px; border-radius: 8px; cursor: zoom-in; border: 1px solid #e4e7ed; display: block; }
.chat-files { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; }
.chat-file-tag { margin: 0; }
.chat-attach-bar { display: flex; flex-wrap: wrap; gap: 6px; flex-shrink: 0; }
.attach-chip { margin: 0; }
.chat-metrics { margin-top: 6px; padding-top: 6px; border-top: 1px dashed #ebeef5; color: #909399; font-size: 12px; line-height: 1.4; }
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
  .chat-layout { flex-direction: column; height: auto !important; }
  .session-sidebar { width: 100%; border-right: none; border-bottom: 1px solid #ebeef5; max-height: 120px; }
  .chat-messages { height: 320px; padding: 10px; }
  .chat-bubble-wrap { max-width: 90%; }
  .chat-actions { display: flex !important; }
}
</style>
