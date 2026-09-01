import { ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listSessions, createSession, renameSession, deleteSession,
  getChatHistory, addChatHistory, clearChatHistory, deleteHistoryItem,
  parseDoc,
} from '../api'

// 按 serviceId（sid）维度的全局对话 store。
// 关键：AbortController / reader 流式循环都存活在本 store（模块级单例），
// 组件切页面/卸载不会中断流式请求，回到对话页时直接读取 state 即可看到继续输出。
export const useChatStore = defineStore('chat', () => {
  // state: { [sid]: ChatState }
  // ChatState:
  //   messages        消息列表（含 role/content/thinking/metrics/images/history_id/created_at/files）
  //   chatInput       输入框文字
  //   chatLoading     是否流式生成中
  //   pendingImage    待发送图片（base64 data URL）
  //   pendingFiles    待发送文档附件 [{ name, text, chars }]
  //   fileParsing     文档解析中 loading
  //   thinkingExpanded / thinkingUserToggled / thinkingRefs  思考折叠状态
  //   sessions        会话列表
  //   currentSessionId 当前会话 id（0=默认会话）
  //   chatThinking    思考模式开关
  //   chatMaxTokens   max_tokens 设置
  //   initialized     onMounted 初始化标记（避免重复拉历史）
  //   streamTick      流式帧计数（组件 watch 它来滚动/高亮）
  //   chatAbort       当前请求的 AbortController
  const states = ref({})

  // 非响应式：AbortController 不能放进 reactive（会被 proxy 化导致 abort 失效）
  const abortControllers = new Map()
  // 已提示过"首次推理预热"的 sid
  const warmWarned = new Set()

  function ensure(sid) {
    if (!states.value[sid]) {
      states.value[sid] = {
        messages: [],
        chatInput: '',
        chatLoading: false,
        pendingImage: null,
        pendingFiles: [],
        fileParsing: false,
        thinkingExpanded: {},
        thinkingUserToggled: {},
        thinkingRefs: {},
        sessions: [],
        currentSessionId: 0,
        chatThinking: false,
        chatMaxTokens: 512,
        initialized: false,
        streamTick: 0,
      }
    }
    return states.value[sid]
  }

  // ---------- 初始化（组件 onMounted 调用；已初始化直接跳过） ----------
  async function initChat(sid) {
    const st = ensure(sid)
    if (st.initialized) return
    st.initialized = true
    loadChatSettings(st)
    try {
      st.sessions = await listSessions(sid)
    } catch (e) { /* ignore */ }
    await loadHistory(sid)
  }

  // ---------- 聊天设置（localStorage 按 sid 持久化） ----------
  function loadChatSettings(st) {
    try {
      const saved = localStorage.getItem(`chat-settings-${_sidOf(st)}`)
      const parsed = saved ? JSON.parse(saved) : {}
      st.chatThinking = parsed.thinking ?? false
      st.chatMaxTokens = parsed.maxTokens ?? 512
    } catch (e) { /* ignore */ }
  }
  // st -> sid 反查（设置 key 需要）
  function _sidOf(st) {
    for (const k of Object.keys(states.value)) if (states.value[k] === st) return k
    return ''
  }
  function persistSettings(sid) {
    const st = ensure(sid)
    localStorage.setItem(`chat-settings-${sid}`, JSON.stringify({
      thinking: st.chatThinking, maxTokens: st.chatMaxTokens,
    }))
  }

  // ---------- 会话管理 ----------
  async function loadSessions(sid) {
    const st = ensure(sid)
    try {
      st.sessions = await listSessions(sid)
    } catch (e) { /* ignore */ }
  }

  async function createNewSession(sid) {
    const st = ensure(sid)
    try {
      const s = await createSession(sid, { title: `新会话 ${st.sessions.length}` })
      st.sessions.unshift(s)
      await switchSession(sid, s.id)
    } catch (e) { ElMessage.error('创建会话失败') }
  }

  async function switchSession(sid, sessionId) {
    const st = ensure(sid)
    st.currentSessionId = sessionId
    st.messages = []
    st.thinkingExpanded = {}
    st.thinkingUserToggled = {}
    st.thinkingRefs = {}
    await loadHistory(sid)
  }

  let renamingSession = false
  async function startRenameSession(sid, s) {
    if (s.id === 0) return // 默认会话不可重命名
    if (renamingSession) return  // 防重复打开叠加
    renamingSession = true
    try {
      const { value } = await ElMessageBox.prompt('会话标题', '重命名会话', {
        inputValue: s.title, confirmButtonText: '保存', cancelButtonText: '取消',
      })
      if (value && value.trim()) {
        await renameSession(sid, s.id, { title: value.trim() })
        s.title = value.trim()
      }
    } catch (e) { /* cancel */ } finally {
      renamingSession = false
    }
  }

  async function removeSession(sid, s) {
    const st = ensure(sid)
    try {
      await ElMessageBox.confirm(`确认删除会话「${s.title}」及其历史记录？`, '删除确认', {
        confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
      })
      await deleteSession(sid, s.id)
      st.sessions = st.sessions.filter(x => x.id !== s.id)
      if (st.currentSessionId === s.id) await switchSession(sid, 0)
    } catch (e) { /* cancel */ }
  }

  // ---------- 思考折叠 ----------
  function setThinkingRef(sid, el, index) {
    const st = ensure(sid)
    if (el) st.thinkingRefs[index] = el
  }
  function toggleThinking(sid, index) {
    const st = ensure(sid)
    st.thinkingExpanded[index] = !st.thinkingExpanded[index]
    st.thinkingUserToggled[index] = true
    if (st.thinkingExpanded[index]) {
      // 展开后滚动到底部（DOM 更新后）
      setTimeout(() => {
        const el = st.thinkingRefs[index]
        if (el) el.scrollTop = el.scrollHeight
      }, 50)
    }
  }

  function stripThink(text) {
    if (!text) return text
    return text.replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '').trim()
  }

  // ---------- 发送消息（流式；不随组件卸载中断） ----------
  async function sendMessage(sid, opts = {}) {
    const st = ensure(sid)
    const text = st.chatInput.trim()
    if (!text || st.chatLoading) return
    if (opts.modelLoaded === false) {
      ElMessage.warning('模型未加载，请先加载模型')
      return
    }
    // 首次推理预热提示：模型刚启动（加载完成 <5min）且本 sid 未提示过
    // 容器重建后首次推理需预热 ~90-100s，之后恢复 1s 级响应
    if (opts.serviceLoadedAt && !warmWarned.has(sid) && (Date.now() / 1000 - opts.serviceLoadedAt) < 300) {
      warmWarned.add(sid)
      ElMessage.info('模型刚启动，首次推理需预热约 1-2 分钟，请耐心等待（仅首次）')
    }
    st.chatLoading = true

    // 组装发给模型的用户消息内容（附件文档 + 文字 + 图片）
    const attachedText = st.pendingFiles.map(f => f.text).filter(Boolean).join('\n\n')
    const fileNames = st.pendingFiles.map(f => f.name)
    let displayText = text
    let modelText = text
    if (attachedText) {
      // 发送给模型：文档内容作为上下文拼在文字前面
      modelText = `以下是附件文档内容：\n\n${attachedText}\n\n---\n\n用户问题：${text}`
    }
    let userContent = modelText
    if (st.pendingImage) {
      userContent = [
        { type: 'text', text: modelText },
        { type: 'image_url', image_url: { url: st.pendingImage } },
      ]
    }
    // 本地展示用消息（输入框只放用户自己打的字，不撑爆；附件以文件名 chip 展示）
    st.messages.push({
      role: 'user',
      content: displayText,
      images: st.pendingImage ? [st.pendingImage] : [],
      files: fileNames,
    })
    // 历史落库：只存用户输入的文字（附件文档体积大且一次性，不进历史库）
    try {
      const r = await addChatHistory(sid, { role: 'user', content: displayText, session_id: st.currentSessionId })
      if (r.id) st.messages[st.messages.length - 1].history_id = r.id
    } catch (e) { /* ignore */ }
    st.chatInput = ''
    st.pendingImage = null
    st.pendingFiles = []

    st.messages.push({ role: 'assistant', content: '', thinking: '' })
    const aiMsg = st.messages[st.messages.length - 1]
    st.streamTick++

    const controller = new AbortController()
    abortControllers.set(sid, controller)
    try {
      const payload = {
        messages: st.messages
          .filter(m => m.role !== 'thinking')
          // 剔除末尾空 assistant 占位（仅前端显示用，不发模型）
          .filter((m, i, arr) => !(m.role === 'assistant' && !m.content.trim() && i === arr.length - 1))
          .map((m, idx, arr) => {
            // 最后一条用户消息用组装后的多模态/附件内容
            if (m.role === 'user' && idx === arr.length - 1) {
              // m.files/m.images 只用于展示；发送内容用闭包里的 userContent（仅最后一条带附件）
              return { role: 'user', content: userContent }
            }
            // 历史用户消息：content 为字符串
            return { role: m.role, content: m.role === 'assistant' ? stripThink(m.content) : m.content }
          }),
        max_tokens: st.chatMaxTokens,
        temperature: 0.7,
        stream: true,
      }
      if (st.chatThinking) payload.chat_template_kwargs = { enable_thinking: true }
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
            // 末次 chunk 带 timings → 指标行（prefill/decode tps、MTP 接受率）
            const timings = chunk.timings
            if (timings) {
              const mm = {}
              if (timings.prompt_per_second != null) mm.prefill_tps = Number(timings.prompt_per_second).toFixed(1)
              if (timings.predicted_per_second != null) mm.decode_tps = Number(timings.predicted_per_second).toFixed(1)
              if (timings.draft_acceptance != null) mm.mtp_accept = (Number(timings.draft_acceptance) * 100).toFixed(1)
              aiMsg.metrics = mm
            }
            if (delta.reasoning_content) {
              const firstThinking = !aiMsg.thinking
              aiMsg.thinking += delta.reasoning_content
              const idx = st.messages.indexOf(aiMsg)
              if (idx >= 0 && firstThinking && !st.thinkingUserToggled[idx]) {
                st.thinkingExpanded[idx] = true
              }
            }
            if (delta.content) {
              aiMsg.content += delta.content
              // 兼容 content 里内嵌 <think> 标签的模型
              if (aiMsg.content.includes('<think')) {
                const m = aiMsg.content.match(/<think\b[^>]*>([\s\S]*?)(?:<\/think>|$)/i)
                if (m) {
                  if (!aiMsg.thinking) aiMsg.thinking = m[1] || ''
                  aiMsg.content = aiMsg.content.replace(/<think\b[^>]*>[\s\S]*?(?:<\/think>|$)/i, '').trim()
                }
              }
            }
            st.streamTick++
          } catch (e) { /* 忽略解析错误 */ }
        }
      }
      if (!aiMsg.content && !aiMsg.thinking) {
        aiMsg.content = '（模型未返回内容：可能是思考模式未产出正式回答，或 max_tokens 在思考阶段被截断。可尝试关闭思考模式或调大 max_tokens）'
        aiMsg.isError = true
      } else if (!aiMsg.content && aiMsg.thinking) {
        aiMsg.content = '（模型仅返回了思考内容，未生成正式回答）'
      } else {
        const idx = st.messages.indexOf(aiMsg)
        if (idx >= 0 && aiMsg.thinking && !st.thinkingUserToggled[idx]) {
          st.thinkingExpanded[idx] = false
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        if (!aiMsg.content && aiMsg.thinking) aiMsg.content = '（已停止：仅输出了思考内容）'
      } else {
        aiMsg.content = `❌ 调用失败: ${e.message || e}`
      }
    } finally {
      st.chatLoading = false
      abortControllers.delete(sid)
      st.streamTick++
      // 持久化助手回复（跳过占位提示/错误/空回复）
      const hasReal = aiMsg.content && !aiMsg.content.startsWith('（') && !aiMsg.content.startsWith('❌')
      if (hasReal) {
        try {
          const r = await addChatHistory(sid, { role: 'assistant', content: aiMsg.content, thinking: aiMsg.thinking || '', session_id: st.currentSessionId })
          if (r.id) aiMsg.history_id = r.id
        } catch (e) { /* ignore */ }
      }
      try { await loadSessions(sid) } catch (e) { /* ignore */ }
    }
  }

  // 用户手动点"停止"才 abort（组件卸载不调用）
  function stopChat(sid) {
    abortControllers.get(sid)?.abort()
  }

  async function clearChat(sid) {
    const st = ensure(sid)
    st.messages = []
    st.thinkingExpanded = {}
    st.thinkingUserToggled = {}
    st.thinkingRefs = {}
    try { await clearChatHistory(sid, st.currentSessionId) } catch (e) { /* ignore */ }
    try { await loadSessions(sid) } catch (e) { /* ignore */ }
  }

  async function deleteMessage(sid, i) {
    const st = ensure(sid)
    const m = st.messages[i]
    if (!m) return
    if (m.history_id) {
      try { await deleteHistoryItem(sid, m.history_id) } catch (e) { /* ignore */ }
    }
    st.messages.splice(i, 1)
    try { await loadSessions(sid) } catch (e) { /* ignore */ }
  }

  // 重新生成：删掉该 assistant 及最后一条 user，把 user 的文字/图片恢复到待发送状态后重发
  async function regenerate(sid, i) {
    const st = ensure(sid)
    if (st.chatLoading) return
    const m = st.messages[i]
    if (m?.history_id) {
      try { await deleteHistoryItem(sid, m.history_id) } catch (e) { /* ignore */ }
    }
    st.messages.splice(i, 1)
    // 找最后一条 user 消息
    let lastUserIdx = -1
    for (let j = st.messages.length - 1; j >= 0; j--) {
      if (st.messages[j].role === 'user') { lastUserIdx = j; break }
    }
    if (lastUserIdx < 0) return
    const um = st.messages[lastUserIdx]
    st.chatInput = um.content || ''
    if (um.images?.length) st.pendingImage = um.images[0]
    if (um.history_id) {
      try { await deleteHistoryItem(sid, um.history_id) } catch (e) { /* ignore */ }
    }
    st.messages.splice(lastUserIdx, 1)
    await sendMessage(sid)
  }

  // ---------- 文件 / 图片上传（附件 chip 模式） ----------
  // txt/md 前端直接读文本；pdf/docx/xlsx 走后端 parseDoc
  async function handleFileUpload(sid, file) {
    const st = ensure(sid)
    st.fileParsing = true
    try {
      const name = file.name || ''
      const suffix = name.includes('.') ? name.split('.').pop().toLowerCase() : ''
      let text = ''
      if (['pdf', 'docx', 'xlsx'].includes(suffix)) {
        const resp = await parseDoc(sid, file)
        text = resp.text || ''
      } else {
        text = await file.text()
        text = text.slice(0, 8000)
      }
      st.pendingFiles.push({ name, text, chars: text.length })
    } catch (e) {
      ElMessage.error('文件解析失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      st.fileParsing = false
    }
    return false // 阻止 el-upload 默认上传
  }

  async function handleImageUpload(sid, file) {
    const st = ensure(sid)
    const reader = new FileReader()
    reader.onload = () => { st.pendingImage = reader.result }
    reader.readAsDataURL(file)
    return false
  }

  function removePendingFile(sid, idx) {
    const st = ensure(sid)
    st.pendingFiles.splice(idx, 1)
  }
  function clearPendingImage(sid) {
    ensure(sid).pendingImage = null
  }

  // ---------- 历史加载 ----------
  async function loadHistory(sid) {
    const st = ensure(sid)
    try {
      const list = await getChatHistory(sid, st.currentSessionId)
      if (list.length) {
        const cleaned = []
        let lastKey = null
        for (const h of list) {
          const content = (h.content || '').trim()
          if (!content) continue
          if (content.startsWith('（') || content.startsWith('❌')) continue
          const key = `${h.role}:${content}`
          if (h.role === 'user' && key === lastKey) continue // 去重连续重复 user
          lastKey = key
          // 兼容历史里存的 OpenAI content 数组（含 image_url）→ 拆文本 + 图片
          let displayContent = h.content
          let displayImages = []
          if (typeof h.content === 'string' && /^\s*\[/.test(h.content.trim())) {
            try {
              const parsed = JSON.parse(h.content)
              if (Array.isArray(parsed)) {
                displayImages = parsed.filter(p => p.type === 'image_url' && p.image_url?.url).map(p => p.image_url.url)
                displayContent = parsed.filter(p => p.type === 'text').map(p => p.text || '').join('\n')
              }
            } catch (e) { /* 非 JSON，按普通文本 */ }
          } else if (Array.isArray(h.content)) {
            displayImages = h.content.filter(p => p.type === 'image_url' && p.image_url?.url).map(p => p.image_url.url)
            displayContent = h.content.filter(p => p.type === 'text').map(p => p.text || '').join('\n')
          }
          cleaned.push({ role: h.role, content: displayContent, images: displayImages, thinking: h.thinking || '', history_id: h.id, created_at: h.created_at })
        }
        st.messages = cleaned
      }
    } catch (e) { /* ignore */ }
  }

  return {
    states,
    ensure,
    initChat,
    persistSettings,
    loadSessions,
    createNewSession,
    switchSession,
    startRenameSession,
    removeSession,
    setThinkingRef,
    toggleThinking,
    sendMessage,
    stopChat,
    clearChat,
    deleteMessage,
    regenerate,
    handleFileUpload,
    handleImageUpload,
    removePendingFile,
    clearPendingImage,
    loadHistory,
  }
})


