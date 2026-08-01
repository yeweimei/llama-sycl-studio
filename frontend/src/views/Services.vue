<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="card-title">
        <span>推理服务</span>
        <el-button type="primary" size="small" @click="openCreate" style="margin-left:auto">
          <el-icon><Plus /></el-icon>&nbsp;新建服务
        </el-button>
        <el-button size="small" @click="refresh"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
      </div>

      <el-table :data="services" v-loading="loading" stripe>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <span class="status-dot" :class="'status-' + row.status"></span>
            {{ statusText(row.status) }}
          </template>
        </el-table-column>
        <el-table-column prop="name" label="服务名" width="160">
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push('/services/' + row.id)">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="model_path" label="模型" min-width="220" show-overflow-tooltip />
        <el-table-column label="端口" width="80">
          <template #default="{ row }"><span class="mono">{{ row.port }}</span></template>
        </el-table-column>
        <el-table-column label="API Key" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.api_key" size="small" type="warning">已设置</el-tag>
            <el-tag v-else size="small" type="info">未设置</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260">
          <template #default="{ row }">
            <el-button v-if="row.status !== 'running'" type="success" size="small" @click="doStart(row)">
              启动
            </el-button>
            <el-button v-else type="warning" size="small" @click="doStop(row)">停止</el-button>
            <el-button size="small" @click="$router.push('/services/' + row.id)">详情</el-button>
            <el-popconfirm title="确认删除该服务？" @confirm="doDelete(row)">
              <template #reference>
                <el-button type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && services.length === 0" description="还没有服务，点击「新建服务」开始" />
    </el-card>

    <!-- 新建服务对话框 -->
    <el-dialog v-model="createVisible" title="新建推理服务" width="720px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="服务名" required>
          <el-input v-model="form.name" placeholder="如 qwen3.5-9b，将用作容器名" />
        </el-form-item>
        <el-form-item label="模型" required>
          <el-select v-model="form.model_path" filterable placeholder="选择已下载的模型" style="width:100%">
            <el-option
              v-for="m in models.filter(m => m.kind === 'gguf')"
              :key="m.path"
              :label="`${m.name} (${m.quantization || '?'}, ${m.size_human})`"
              :value="m.path"
            />
          </el-select>
          <div class="form-tip">模型不存在？先去「模型中心」或「模型下载」</div>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.api_key" placeholder="可选，设置后需带 Authorization: Bearer 访问" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="form.port" :min="8000" :max="8999" placeholder="留空自动分配" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { listServices, createService, startService, stopService, deleteService, listModels } from '../api'

const services = ref([])
const models = ref([])
const loading = ref(false)
const createVisible = ref(false)
const creating = ref(false)
const form = ref({ name: '', model_path: '', api_key: '', port: null })

const statusText = (s) => ({ running: '运行中', stopped: '已停止', error: '异常' }[s] || s)

async function refresh() {
  loading.value = true
  try {
    services.value = await listServices()
    models.value = await listModels()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = { name: '', model_path: '', api_key: '', port: null }
  createVisible.value = true
}

async function doCreate() {
  if (!form.value.name || !form.value.model_path) {
    ElMessage.warning('请填写服务名并选择模型')
    return
  }
  creating.value = true
  try {
    await createService(form.value)
    ElMessage.success('服务已创建')
    createVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  } finally {
    creating.value = false
  }
}

async function doStart(row) {
  try {
    await startService(row.id)
    ElMessage.success(`${row.name} 启动中`)
    setTimeout(refresh, 3000)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启动失败')
  }
}

async function doStop(row) {
  await stopService(row.id)
  ElMessage.success(`${row.name} 已停止`)
  refresh()
}

async function doDelete(row) {
  await deleteService(row.id)
  ElMessage.success('已删除')
  refresh()
}

onMounted(refresh)
</script>

<style scoped>
.form-tip { font-size: 12px; color: #909399; margin-top: 4px; }
</style>
