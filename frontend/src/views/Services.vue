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
        <el-table-column prop="model_path" label="模型" min-width="200" show-overflow-tooltip />
        <el-table-column label="端口" width="80">
          <template #default="{ row }"><span class="mono">{{ row.port }}</span></template>
        </el-table-column>
        <el-table-column label="API Key" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.api_key" size="small" type="warning">已设置</el-tag>
            <el-tag v-else size="small" type="info">无</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="330">
          <template #default="{ row }">
            <el-button v-if="row.status !== 'running'" type="success" size="small" @click="doStart(row)">
              启动
            </el-button>
            <el-button v-else type="warning" size="small" @click="doStop(row)">停止</el-button>
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" @click="$router.push('/services/' + row.id)">详情</el-button>
            <el-dropdown trigger="click" @command="(cmd) => onMore(cmd, row)">
              <el-button size="small">更多<el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="restart" :disabled="row.status !== 'running'">重启</el-dropdown-item>
                  <el-dropdown-item command="clone">克隆</el-dropdown-item>
                  <el-dropdown-item command="delete" divided style="color:#f56c6c">删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && services.length === 0" description="还没有服务，点击「新建服务」开始" />
    </el-card>

    <!-- 新建服务对话框 -->
    <el-dialog v-model="createVisible" title="新建推理服务" width="860px" top="5vh">
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
        <el-form-item label="推理显卡">
          <el-select v-model="form.gpu_id" style="width:100%">
            <el-option v-for="g in gpus" :key="g.id" :label="g.name" :value="g.id">
              <span>{{ g.name }}</span>
              <span style="float:right;color:#909399;font-size:12px">{{ g.id }}</span>
            </el-option>
          </el-select>
          <div class="form-tip">默认 A770M 独显；Iris Xe 核显可用于轻量任务（如 embedding）</div>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.api_key" placeholder="可选，设置后需带 Authorization: Bearer 访问" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="form.port" :min="8000" :max="8999" placeholder="留空自动分配" />
        </el-form-item>

        <el-divider content-position="left">推理参数</el-divider>
        <ParamForm v-model="form.args" />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑服务对话框 -->
    <el-dialog v-model="editVisible" title="编辑服务" width="760px" top="5vh">
      <el-form :model="editForm" label-width="130px">
        <el-form-item label="服务名" required>
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="模型" required>
          <el-select v-model="editForm.model_path" filterable placeholder="选择已下载的模型" style="width:100%">
            <el-option
              v-for="m in models.filter(m => m.kind === 'gguf')"
              :key="m.path"
              :label="`${m.name} (${m.quantization || '?'}, ${m.size_human})`"
              :value="m.path"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="推理显卡">
          <el-select v-model="editForm.gpu_id" style="width:100%">
            <el-option v-for="g in gpus" :key="g.id" :label="g.name" :value="g.id">
              <span>{{ g.name }}</span>
              <span style="float:right;color:#909399;font-size:12px">{{ g.id }}</span>
            </el-option>
          </el-select>
          <div class="form-tip">A770M 独显性能最强；Iris Xe 核显可用于轻量任务或腾出独显</div>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="editForm.api_key" placeholder="留空则不鉴权" />
          <div class="form-tip">修改后需重启服务生效</div>
        </el-form-item>
        <el-form-item label="端口" required>
          <el-input-number v-model="editForm.port" :min="8000" :max="8999" />
          <div class="form-tip">端口修改会重建容器，请先停止服务</div>
        </el-form-item>

        <el-divider content-position="left">推理参数</el-divider>
        <ParamForm v-model="editForm.args" />
        <div class="form-tip" style="color:#e6a23c">⚠️ 修改推理参数需重启服务生效</div>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSaveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, ArrowDown } from '@element-plus/icons-vue'
import ParamForm from '../components/ParamForm.vue'
import {
  listServices, createService, updateService, startService, stopService,
  restartService, cloneService, deleteService, listModels, gpuOptions,
} from '../api'

const services = ref([])
const models = ref([])
const gpus = ref([])
const loading = ref(false)
const createVisible = ref(false)
const creating = ref(false)
const editVisible = ref(false)
const saving = ref(false)
const form = ref({ name: '', model_path: '', api_key: '', port: null, args: {} })
const editForm = ref({
  id: null, name: '', model_path: '', api_key: '', port: null,
  gpu_id: '', args: {},
})

const statusText = (s) => ({ running: '运行中', stopped: '已停止', error: '异常' }[s] || s)

async function refresh() {
  loading.value = true
  try {
    services.value = await listServices()
    models.value = await listModels()
    try { gpus.value = (await gpuOptions()).gpus } catch (e) { gpus.value = [] }
  } finally {
    loading.value = false
  }
}

function openCreate() {
  // 默认选独显（A770M），找不到则第一个
  const defaultGpu = gpus.value.find(g => g.name.includes('A770')) || gpus.value[0] || null
  form.value = { name: '', model_path: '', api_key: '', port: null, gpu_id: defaultGpu?.id || '', args: {} }
  createVisible.value = true
}

async function doCreate() {
  if (!form.value.name || !form.value.model_path) {
    ElMessage.warning('请填写服务名并选择模型')
    return
  }
  creating.value = true
  try {
    const payload = { ...form.value }
    const gpu = gpus.value.find(g => g.id === form.value.gpu_id)
    if (gpu) payload.gpu_devices = gpu.devices
    delete payload.gpu_id
    // args 为空时后端会用默认参数
    await createService(payload)
    ElMessage.success('服务已创建')
    createVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  } finally {
    creating.value = false
  }
}

// ---------- 编辑 ----------
function openEdit(row) {
  // 根据 gpu_devices 反推 gpu_id（设备列表第一项对应 cardX）
  let gpu_id = ''
  const devs = row.gpu_devices || []
  if (devs.length) {
    const m = devs[0].match(/card(\d+)/)
    if (m) gpu_id = `card${m[1]}`
  }
  editForm.value = {
    id: row.id,
    name: row.name,
    model_path: row.model_path,
    api_key: row.api_key || '',
    port: row.port,
    gpu_id,
    args: { ...(row.args || {}) },
  }
  editVisible.value = true
}

async function doSaveEdit() {
  if (!editForm.value.name || !editForm.value.model_path) {
    ElMessage.warning('请填写服务名并选择模型')
    return
  }
  saving.value = true
  try {
    // gpu_id -> devices 列表
    const gpu = gpus.value.find(g => g.id === editForm.value.gpu_id)
    const payload = {
      name: editForm.value.name,
      model_path: editForm.value.model_path,
      api_key: editForm.value.api_key || null,
      port: editForm.value.port,
      args: editForm.value.args,
    }
    if (gpu) payload.gpu_devices = gpu.devices
    await updateService(editForm.value.id, payload)
    ElMessage.success('已保存（重启服务生效）')
    editVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

// ---------- 更多操作 ----------
async function onMore(cmd, row) {
  if (cmd === 'restart') {
    try {
      await restartService(row.id)
      ElMessage.success('重启中')
      setTimeout(refresh, 3000)
    } catch (e) {
      ElMessage.error(e.response?.data?.detail || '重启失败')
    }
  } else if (cmd === 'clone') {
    try {
      const { value } = await ElMessageBox.prompt(
        '克隆为新的服务名（新服务自动分配新端口，不会启动）',
        `克隆 ${row.name}`,
        { confirmButtonText: '克隆', cancelButtonText: '取消', inputValue: `${row.name}-copy` }
      )
      if (value) {
        await cloneService(row.id, value)
        ElMessage.success('克隆成功')
        refresh()
      }
    } catch (e) {
      if (e !== 'cancel') ElMessage.error(e.response?.data?.detail || '克隆失败')
    }
  } else if (cmd === 'delete') {
    ElMessageBox.confirm(`确认删除服务「${row.name}」？`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    }).then(async () => {
      await deleteService(row.id)
      ElMessage.success('已删除')
      refresh()
    }).catch(() => {})
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
