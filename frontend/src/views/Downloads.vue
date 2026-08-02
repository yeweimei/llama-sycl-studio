<template>
  <div class="page-container">
    <!-- 搜索区 -->
    <el-card shadow="never" style="margin-bottom:16px">
      <div class="search-bar">
        <el-select v-model="form.source" style="width:150px">
          <el-option v-for="s in sources" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-input
          v-model="form.repo_id"
          placeholder="搜索模型，如 qwen3.5 gguf / embedding / 9b"
          clearable
          size="large"
          @keyup.enter="doSearch"
          @clear="results = []"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
          <template #append>
            <el-button :loading="searching" @click="doSearch">搜索</el-button>
          </template>
        </el-input>
        <el-button size="large" @click="showHot = !showHot">🔥 热门模型</el-button>
      </div>

      <!-- 热门模型快捷入口 -->
      <div v-if="showHot" class="hot-models">
        <el-tag
          v-for="m in hotModels"
          :key="m.repo_id"
          class="hot-tag"
          :type="m.source === form.source ? 'primary' : ''"
          @click="loadRepo(m)"
        >
          {{ m.label }}
        </el-tag>
      </div>
      <div v-if="!showHot" class="search-hint">输入关键词搜索 HuggingFace / ModelScope 上的 GGUF 模型，点击结果查看文件</div>
    </el-card>

    <!-- 搜索结果 -->
    <el-card v-if="results.length" shadow="never" style="margin-bottom:16px">
      <div class="card-title"><span>搜索结果（{{ results.length }}）</span></div>
      <el-table :data="results" stripe size="small" @row-click="selectRepo" style="cursor:pointer">
        <el-table-column prop="name" label="模型" min-width="180">
          <template #default="{ row }">
            <span class="repo-name">{{ row.name }}</span>
            <span class="repo-author">@{{ row.author }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="260" show-overflow-tooltip />
        <el-table-column label="下载量" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.downloads) }}</template>
        </el-table-column>
        <el-table-column label="点赞" width="70" align="right">
          <template #default="{ row }">{{ fmtNum(row.likes) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click.stop="selectRepo(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 文件选择（选中仓库后） -->
    <el-card v-if="selectedRepo" shadow="never" style="margin-bottom:16px">
      <div class="card-title">
        <el-icon><Folder /></el-icon>
        <span class="repo-name">{{ selectedRepo.repo_id }}</span>
        <el-button size="small" style="margin-left:8px" @click="selectedRepo = null">← 返回</el-button>
      </div>
      <div v-loading="listingFiles" style="min-height:60px">
        <el-table v-if="files.length" :data="files" stripe size="small">
          <el-table-column prop="filename" label="文件" min-width="300" show-overflow-tooltip />
          <el-table-column label="量化" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="quantType(row.filename)">{{ quantName(row.filename) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="大小" width="110" align="right">
            <template #default="{ row }">{{ row.size ? fmtSize(row.size) : '-' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button type="primary" size="small" :loading="downloadingFile === row.filename" @click="doDownload(row)">
                下载
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!listingFiles && !files.length" description="该仓库没有 GGUF 文件" :image-size="60" />
      </div>
    </el-card>

    <!-- 下载任务 -->
    <el-card shadow="never">
      <div class="card-title">
        <span>下载任务</span>
        <el-button size="small" style="margin-left:auto" @click="loadTasks">刷新</el-button>
      </div>
      <el-table :data="tasks" stripe size="small">
        <el-table-column prop="filename" label="文件" min-width="220" show-overflow-tooltip />
        <el-table-column label="进度" width="240">
          <template #default="{ row }">
            <el-progress
              :percentage="Math.round(row.progress || 0)"
              :status="row.status === 'done' ? 'success' : row.status === 'error' ? 'exception' : undefined"
            />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <!-- downloading -> 暂停 / 取消 -->
            <template v-if="row.status === 'downloading'">
              <el-button size="small" @click="pauseDownload(row)">暂停</el-button>
              <el-button size="small" type="danger" @click="cancelDownload(row)">取消</el-button>
            </template>
            <!-- paused -> 继续 / 取消 -->
            <template v-else-if="row.status === 'paused'">
              <el-button size="small" type="primary" @click="resumeDownload(row)">继续</el-button>
              <el-button size="small" type="danger" @click="cancelDownload(row)">取消</el-button>
            </template>
            <!-- error -> 重试 / 删除 -->
            <template v-else-if="row.status === 'error'">
              <el-button size="small" type="warning" @click="retryDownload(row)">重试</el-button>
              <el-button size="small" type="danger" @click="deleteHistory(row)">删除</el-button>
            </template>
            <!-- done -> 删除 -->
            <template v-else-if="row.status === 'done'">
              <el-button size="small" type="success" @click="refreshModels()">刷新模型</el-button>
              <el-button size="small" type="danger" @click="deleteHistory(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!tasks.length" description="暂无下载任务" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Folder } from '@element-plus/icons-vue'
import {
  listSources, searchModels, listRepoFiles, startDownload,
  listTasks, taskProgress, cancelTask, pauseTask, resumeTask, retryTask, deleteTask,
  listModels
} from '../api'

const form = ref({ source: 'huggingface', repo_id: '' })
const sources = ref([])
const results = ref([])
const searching = ref(false)
const selectedRepo = ref(null)
const files = ref([])
const listingFiles = ref(false)
const downloadingFile = ref('')
const tasks = ref([])
const showHot = ref(true)
const isAuthed = ref(true)
let pollTimer = null

// 热门模型快捷入口
const hotModels = [
  { label: 'Qwen3.5-9B GGUF', repo_id: 'unsloth/Qwen3.5-9B-GGUF', source: 'huggingface' },
  { label: 'Qwen3.5-4B GGUF', repo_id: 'unsloth/Qwen3.5-4B-GGUF', source: 'huggingface' },
  { label: 'Qwen3-8B GGUF', repo_id: 'unsloth/Qwen3-8B-GGUF', source: 'huggingface' },
  { label: 'Qwen3-Embedding', repo_id: 'Qwen/Qwen3-Embedding-0.6B', source: 'huggingface' },
  { label: 'Qwen2.5-7B GGUF', repo_id: 'unsloth/Qwen2.5-7B-Instruct-GGUF', source: 'huggingface' },
  { label: 'Qwen3.5-27B GGUF', repo_id: 'unsloth/Qwen3.5-27B-GGUF', source: 'huggingface' },
]

async function doSearch() {
  if (!form.value.repo_id.trim()) { ElMessage.warning('请输入搜索关键词'); return }
  searching.value = true
  try {
    results.value = await searchModels({ ...form.value, repo_id: form.value.repo_id.trim() })
    if (!results.value.length) ElMessage.info('没有找到 GGUF 模型，试试其他关键词')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '搜索失败')
    results.value = []
  } finally {
    searching.value = false
  }
}

async function loadRepo(m) {
  form.value.source = m.source || form.value.source
  form.value.repo_id = m.repo_id
  await selectRepo(m)
}

async function selectRepo(row) {
  selectedRepo.value = { repo_id: row.repo_id }
  listingFiles.value = true
  files.value = []
  try {
    files.value = await listRepoFiles({ source: form.value.source, repo_id: row.repo_id })
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '获取文件失败')
  } finally {
    listingFiles.value = false
  }
}

async function doDownload(row) {
  downloadingFile.value = row.filename
  try {
    await startDownload({ source: form.value.source, repo_id: selectedRepo.value.repo_id, filename: row.filename })
    ElMessage.success(`开始下载 ${row.filename}`)
    loadTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '下载启动失败')
  } finally {
    downloadingFile.value = ''
  }
}

async function loadTasks() {
  if (!isAuthed.value) return
  try {
    tasks.value = await listTasks()
  } catch (e) {
    if (e.response?.status === 401) {
      isAuthed.value = false
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    }
    return
  }
  for (const t of tasks.value) {
    if (t.status === 'downloading' || t.status === 'paused') {
      try {
        const p = await taskProgress(t.id)
        t.progress = p.progress
        t.status = p.status
        if (p.status === 'done' && isAuthed.value) loadTasks()
      } catch (e) {
        if (e.response?.status === 401) {
          isAuthed.value = false
          if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
        } else if (e.response?.status === 404) {
          t.status = 'error'
        }
      }
    }
  }
}

async function pauseDownload(row) {
  try {
    await pauseTask(row.id)
    ElMessage.info('已暂停')
    loadTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '暂停失败')
  }
}

async function resumeDownload(row) {
  try {
    await resumeTask(row.id)
    ElMessage.success('已继续')
    loadTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '继续失败')
  }
}

async function retryDownload(row) {
  try {
    await retryTask(row.id)
    ElMessage.success('正在重试...')
    loadTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重试失败')
  }
}

async function cancelDownload(row) {
  try {
    await ElMessageBox.confirm('确定取消此下载任务？', '提示', { type: 'warning' })
  } catch { return }
  try {
    await cancelTask(row.id)
    ElMessage.info('已取消')
    loadTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '取消失败')
  }
}

async function deleteHistory(row) {
  try {
    await ElMessageBox.confirm('确定删除此任务记录？', '提示', { type: 'warning' })
  } catch { return }
  try {
    await deleteTask(row.id)
    ElMessage.info('已删除')
    loadTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

async function refreshModels() {
  try {
    await listModels()
    ElMessage.success('模型列表已刷新')
  } catch {
    // ignore
  }
}

// ---------- 格式化 ----------
function fmtSize(n) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(1) + units[i]
}
function fmtNum(n) {
  if (!n) return '0'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return String(n)
}
function quantName(fn) {
  const up = fn.toUpperCase()
  for (const q of ['Q6_K_XL', 'Q5_K_XL', 'Q4_K_XL', 'Q8_0', 'Q6_K', 'Q5_K_M', 'Q5_K_S', 'Q4_K_M', 'Q4_K_S', 'Q3_K_M', 'Q3_K_S', 'Q2_K', 'IQ4_XS', 'IQ4_NL', 'IQ3_XXS', 'IQ2_XS', 'BF16', 'F16']) {
    if (up.includes(q)) return q
  }
  return 'GGUF'
}
function quantType(fn) {
  const q = quantName(fn)
  if (q.includes('Q6') || q.includes('Q8') || q.includes('BF16') || q.includes('F16')) return 'success'
  if (q.includes('Q4') || q.includes('Q5')) return 'warning'
  return 'info'
}
function statusText(s) {
  return { downloading: '下载中', done: '完成', error: '失败', paused: '已暂停', cancelled: '已取消' }[s] || s
}
function statusType(s) {
  return { downloading: 'primary', done: 'success', error: 'danger', paused: 'warning', cancelled: 'info' }[s] || 'info'
}

onMounted(async () => {
  sources.value = await listSources()
  loadTasks()
  pollTimer = setInterval(() => { if (isAuthed.value) loadTasks() }, 3000)
})
onUnmounted(() => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } })
</script>

<style scoped>
.search-bar {
  display: flex;
  gap: 10px;
  align-items: stretch;
}
.search-bar .el-input { flex: 1; }
.hot-models {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.hot-tag { cursor: pointer; margin-right: 0; }
.search-hint { color: #909399; font-size: 13px; margin-top: 10px; }
.repo-name { font-weight: 600; }
.repo-author { color: #909399; font-size: 12px; margin-left: 6px; }
.card-title { display: flex; align-items: center; gap: 8px; }
</style>
