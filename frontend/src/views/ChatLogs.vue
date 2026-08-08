<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="card-title">
        <span>对话日志</span>
        <div style="display:flex;gap:8px;align-items:center">
          <el-select v-model="filterModel" placeholder="全部模型" size="small" clearable style="width:180px" @change="load">
            <el-option v-for="m in models" :key="m" :label="m" :value="m" />
          </el-select>
          <el-select v-model="filterStatus" placeholder="全部状态" size="small" clearable style="width:120px" @change="load">
            <el-option label="进行中" value="running" />
            <el-option label="已完成" value="done" />
            <el-option label="失败" value="error" />
          </el-select>
          <el-switch v-model="autoRefresh" active-text="自动刷新" />
          <el-button size="small" @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
          <el-button size="small" type="danger" plain @click="clearAll">清空</el-button>
        </div>
      </div>

      <!-- 并发日志卡片流 -->
      <div v-loading="loading" class="chat-log-list">
        <el-empty v-if="!loading && filteredLogs.length === 0" description="暂无对话日志" />
        <el-card
          v-for="log in filteredLogs"
          :key="log.id"
          shadow="never"
          class="chat-log-card"
          :class="{ 'is-running': log.status === 'running', 'is-error': log.status === 'error' }"
        >
          <!-- 卡片头 -->
          <div class="log-header" @click="toggleExpand(log)">
            <div class="log-title">
              <el-tag size="small" :type="statusTagType(log.status)" effect="dark">
                {{ statusLabel(log.status) }}
              </el-tag>
              <span class="log-id">#{{ log.id }}</span>
              <span class="log-model">{{ log.model_name }}</span>
              <el-tag v-if="log.stream" size="small" type="info" effect="plain">流式</el-tag>
            </div>
            <div class="log-meta">
              <template v-if="log.status !== 'running'">
                <span v-if="log.total_ms">{{ (log.total_ms / 1000).toFixed(1) }}s</span>
                <span v-if="log.completion_tokens">{{ log.completion_tokens }} tok</span>
                <span v-if="log.prompt_tokens">{{ log.prompt_tokens }} in</span>
              </template>
              <template v-else>
                <span class="running-hint">生成中...</span>
              </template>
              <span class="log-time">{{ fmtTime(log.created_at) }}</span>
              <el-icon class="expand-icon"><ArrowDown v-if="!expanded.has(log.id)" /><ArrowUp v-else /></el-icon>
            </div>
          </div>

          <!-- 展开内容 -->
          <div v-if="expanded.has(log.id)" class="log-body">
            <div v-if="log.error" class="log-error">错误: {{ log.error }}</div>
            <div class="log-block">
              <div class="block-label">用户输入</div>
              <pre class="block-content user">{{ log.user_message || '(空)' }}</pre>
            </div>
            <div v-if="log.thinking" class="log-block">
              <div class="block-label thinking-label" @click="toggleThinking(log)">
                <el-icon><CaretRight v-if="!thinkingOpen.has(log.id)" /><CaretBottom v-else /></el-icon>
                思考过程 ({{ log.thinking.length }} 字符)
              </div>
              <pre v-if="thinkingOpen.has(log.id)" class="block-content thinking">{{ log.thinking }}</pre>
            </div>
            <div class="log-block">
              <div class="block-label">模型输出</div>
              <pre class="block-content response">{{ log.response || (log.status === 'running' ? '(等待输出...)' : '(空)') }}</pre>
            </div>
          </div>
        </el-card>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, ArrowDown, ArrowUp, CaretRight, CaretBottom } from '@element-plus/icons-vue'
import api from '../api'

const logs = ref([])
const loading = ref(false)
const expanded = ref(new Set())
const thinkingOpen = ref(new Set())
const autoRefresh = ref(true)
const filterModel = ref('')
const filterStatus = ref('')
let timer = null

const models = computed(() => {
  const s = new Set(logs.value.map(l => l.model_name).filter(Boolean))
  return [...s]
})

const filteredLogs = computed(() => {
  let arr = logs.value
  if (filterModel.value) arr = arr.filter(l => l.model_name === filterModel.value)
  if (filterStatus.value) arr = arr.filter(l => l.status === filterStatus.value)
  return arr
})

function statusTagType(s) {
  if (s === 'running') return 'primary'
  if (s === 'error') return 'danger'
  return 'success'
}
function statusLabel(s) {
  if (s === 'running') return '进行中'
  if (s === 'error') return '失败'
  return '完成'
}
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = n => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function toggleExpand(log) {
  const s = new Set(expanded.value)
  if (s.has(log.id)) s.delete(log.id)
  else s.add(log.id)
  expanded.value = s
}
function toggleThinking(log) {
  const s = new Set(thinkingOpen.value)
  if (s.has(log.id)) s.delete(log.id)
  else s.add(log.id)
  thinkingOpen.value = s
}

async function load() {
  loading.value = true
  try {
    const data = await api.get('/stats/chat-logs', { params: { limit: 100 } })
    const items = (data && (data.items || data.data?.items)) || []
    // 默认展开 running 的
    const s = new Set(expanded.value)
    for (const it of items) {
      if (it.status === 'running') s.add(it.id)
    }
    expanded.value = s
    logs.value = items
  } catch (e) {
    // 静默失败
  } finally {
    loading.value = false
  }
}

async function clearAll() {
  try {
    await ElMessageBox.confirm('确定清空所有对话日志？', '清空确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete('/stats/chat-logs')
    ElMessage.success('已清空')
    load()
  } catch {
    ElMessage.error('清空失败')
  }
}

onMounted(() => {
  load()
  timer = setInterval(() => { if (autoRefresh.value) load() }, 1500)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
watch(autoRefresh, v => { if (v) load() })
</script>

<style scoped>
.chat-log-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
  max-height: calc(100vh - 220px);
  overflow-y: auto;
  padding: 4px 2px;
}
.chat-log-card {
  border-left: 3px solid #dcdfe6;
}
.chat-log-card.is-running {
  border-left-color: #409eff;
  background: #f5f9ff;
}
.chat-log-card.is-error {
  border-left-color: #f56c6c;
}
.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  user-select: none;
}
.log-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.log-id { color: #909399; font-size: 12px; }
.log-model { font-weight: 600; font-size: 13px; }
.log-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #909399;
  font-size: 12px;
}
.running-hint { color: #409eff; font-weight: 500; animation: pulse 1.2s infinite; }
@keyframes pulse { 0%,100% {opacity:1} 50% {opacity:.4} }
.expand-icon { margin-left: 4px; }
.log-body { margin-top: 10px; }
.log-block { margin-bottom: 10px; }
.block-label { font-size: 12px; color: #909399; margin-bottom: 4px; font-weight: 500; }
.thinking-label { cursor: pointer; display: flex; align-items: center; gap: 4px; color: #b37feb; }
.block-content {
  margin: 0;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 300px;
  overflow-y: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
.block-content.user { background: #f4f4f5; }
.block-content.response { background: #f0f9eb; }
.block-content.thinking { background: #f9f0ff; color: #7b3fbf; }
.log-error { color: #f56c6c; font-size: 13px; margin-bottom: 8px; padding: 6px 10px; background: #fef0f0; border-radius: 4px; }
</style>
