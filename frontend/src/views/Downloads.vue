<template>
  <div class="page-container">
    <el-card shadow="never" style="margin-bottom:16px">
      <div class="card-title"><span>下载新模型</span></div>
      <el-form :inline="true" :model="form">
        <el-form-item label="来源">
          <el-select v-model="form.source" style="width:140px">
            <el-option v-for="s in sources" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="仓库 ID" style="width:320px">
          <el-input v-model="form.repo_id" placeholder="如 unsloth/Qwen3.5-9B-GGUF" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="listing" @click="fetchFiles">列出文件</el-button>
        </el-form-item>
      </el-form>

      <template v-if="files.length">
        <el-divider content-position="left">选择文件（{{ files.length }} 个 GGUF）</el-divider>
        <el-table :data="files" stripe size="small" max-height="300">
          <el-table-column prop="filename" label="文件" min-width="300" show-overflow-tooltip />
          <el-table-column label="大小" width="100">
            <template #default="{ row }">{{ row.size ? fmtSize(row.size) : '未知' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="140">
            <template #default="{ row }">
              <el-button type="primary" size="small" :loading="downloadingFile === row.filename" @click="doDownload(row)">
                下载
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <el-card shadow="never">
      <div class="card-title"><span>下载任务</span></div>
      <el-table :data="tasks" stripe size="small">
        <el-table-column prop="repo_id" label="仓库" min-width="200" show-overflow-tooltip />
        <el-table-column prop="filename" label="文件" min-width="200" show-overflow-tooltip />
        <el-table-column label="进度" width="220">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress || 0"
              :status="row.status === 'done' ? 'success' : row.status === 'error' ? 'exception' : undefined"
            />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button v-if="row.status === 'downloading'" size="small" type="danger" @click="cancel(row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!tasks.length" description="暂无下载任务" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listSources, listRepoFiles, startDownload, listTasks, taskProgress, cancelTask } from '../api'

const form = ref({ source: 'huggingface', repo_id: 'unsloth/Qwen3.5-9B-GGUF' })
const sources = ref([])
const files = ref([])
const listing = ref(false)
const downloadingFile = ref('')
const tasks = ref([])
let pollTimer = null

async function fetchFiles() {
  if (!form.value.repo_id) { ElMessage.warning('请输入仓库 ID'); return }
  listing.value = true
  try {
    files.value = await listRepoFiles(form.value)
    ElMessage.success(`找到 ${files.value.length} 个 GGUF 文件`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '获取文件列表失败')
    files.value = []
  } finally {
    listing.value = false
  }
}

async function doDownload(row) {
  downloadingFile.value = row.filename
  try {
    const t = await startDownload({ ...form.value, filename: row.filename })
    ElMessage.success('下载已开始')
    loadTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '下载启动失败')
  } finally {
    downloadingFile.value = ''
  }
}

async function loadTasks() {
  tasks.value = await listTasks()
  // 轮询进行中的任务
  for (const t of tasks.value) {
    if (t.status === 'downloading') {
      try {
        const p = await taskProgress(t.id)
        t.progress = p.progress
        t.status = p.status
        if (p.status === 'done') loadTasks()
      } catch (e) { /* 忽略 */ }
    }
  }
}

async function cancel(row) {
  await cancelTask(row.id)
  ElMessage.info('已取消')
  loadTasks()
}

function statusText(s) {
  return { downloading: '下载中', done: '完成', error: '失败' }[s] || s
}
function statusType(s) {
  return { downloading: 'primary', done: 'success', error: 'danger' }[s] || 'info'
}
function fmtSize(n) {
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(1) + units[i]
}

onMounted(async () => {
  sources.value = await listSources()
  loadTasks()
  pollTimer = setInterval(loadTasks, 3000)
})
onUnmounted(() => clearInterval(pollTimer))
</script>
