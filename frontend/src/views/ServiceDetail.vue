<template>
  <div class="page-container" v-loading="loading">
    <el-page-header @back="$router.back()" :content="service?.name || '服务详情'" style="margin-bottom:16px">
      <template #extra>
        <el-button v-if="service?.status !== 'running'" type="success" size="small" @click="doStart">启动</el-button>
        <el-button v-else type="warning" size="small" @click="doStop">停止</el-button>
        <el-button size="small" @click="doRestart">重启</el-button>
      </template>
    </el-page-header>

    <el-row :gutter="16">
      <!-- 左：参数配置 -->
      <el-col :span="14">
        <el-card shadow="never">
          <div class="card-title">
            <span>推理参数</span>
            <el-select v-model="selectedTemplate" size="small" placeholder="套用模板" style="width:180px;margin-left:auto" @change="applyTemplate">
              <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
            </el-select>
            <el-button size="small" @click="saveAsTemplate">存为模板</el-button>
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

      <!-- 右：命令行 + 信息 -->
      <el-col :span="10">
        <el-card shadow="never" style="margin-bottom:16px">
          <div class="card-title"><span>启动命令</span></div>
          <el-input
            v-model="commandText"
            type="textarea"
            :rows="12"
            class="mono-area"
            placeholder="编辑命令行（双向同步）"
            @input="commandEdited"
          />
          <div class="form-tip">改动命令行会自动同步到左侧表单；也可直接编辑后「应用命令」</div>
          <el-button size="small" type="primary" style="margin-top:8px" @click="applyCommand">应用命令行</el-button>
          <el-button size="small" style="margin-top:8px" @click="copyCommand">复制</el-button>
        </el-card>

        <el-card shadow="never">
          <div class="card-title"><span>服务信息</span></div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="模型">{{ service?.model_path }}</el-descriptions-item>
            <el-descriptions-item label="端口">
              <span class="mono">{{ service?.port }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="API 端点">
              <code class="mono">{{ apiEndpoint }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="API Key">
              <el-tag v-if="service?.api_key" size="small" type="warning">已设置</el-tag>
              <el-tag v-else size="small" type="info">未设置（无鉴权）</el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <!-- 日志 -->
    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title">
        <span>运行日志</span>
        <el-button size="small" style="margin-left:auto" @click="refreshLogs">刷新</el-button>
      </div>
      <pre class="log-view">{{ logs || '（无日志，启动后显示）' }}</pre>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getService, updateService, startService, stopService, restartService,
  getServiceLogs, getParamSchema, listTemplates, createTemplate,
} from '../api'

const route = useRoute()
const sid = route.params.id
const service = ref(null)
const args = ref({})
const loading = ref(true)
const logs = ref('')
const templates = ref([])
const selectedTemplate = ref(null)
const kvTypes = ['f16', 'bf16', 'q8_0', 'q4_0', 'q4_1', 'iq4_nl', 'f32']

// 命令行生成
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
    if (a[k] !== undefined && a[k] !== null && a[k] !== '') {
      parts.push(flag, String(a[k]))
    }
  }
  if (a.flash_attn) parts.push('--flash-attn')
  if (a.jinja) parts.push('--jinja')
  if (a.no_webui) parts.push('--no-webui')
  if (a.cache_type_k) parts.push('--cache-type-k', a.cache_type_k)
  if (a.cache_type_v) parts.push('--cache-type-v', a.cache_type_v)
  return parts.join(' ')
}

watch(args, () => { commandText.value = buildCommand() }, { deep: true })

// 从命令行解析回表单
function commandEdited(text) {
  // 只更新命令文本，不实时回写（避免循环）
}

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
  ElMessage.success('参数已保存（重启服务生效）')
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

async function refreshLogs() {
  const d = await getServiceLogs(sid, 200)
  logs.value = d.logs
}

async function doStart() { await startService(sid); ElMessage.success('启动中'); setTimeout(load, 3000) }
async function doStop() { await stopService(sid); ElMessage.success('已停止'); load() }
async function doRestart() { await restartService(sid); ElMessage.success('重启中'); setTimeout(load, 3000) }

function copyCommand() {
  navigator.clipboard.writeText(commandText.value)
  ElMessage.success('已复制')
}

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
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.mono-area :deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
.log-view {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.form-tip { font-size: 12px; color: #909399; margin-top: 6px; }
</style>
