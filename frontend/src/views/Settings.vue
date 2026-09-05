<template>
  <div class="page-container">
    <el-row :gutter="16">
      <!-- 左列 -->
      <el-col :xs="24" :sm="12">
        <!-- API 密钥 -->
        <el-card shadow="never">
          <div class="card-title">
            <span>API 密钥</span>
            <el-button type="primary" size="small" style="margin-left:auto" @click="createKeyDialog = true">
              生成新密钥
            </el-button>
          </div>
          <el-table :data="keys" size="small" stripe>
            <el-table-column prop="name" label="名称" width="120" />
            <el-table-column label="密钥" min-width="200">
              <template #default="{ row }">
                <span class="mono">{{ maskKey(row.key) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-switch :model-value="row.enabled === 1" @change="toggle(row)" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{ row }">
                <el-button size="small" type="danger" @click="removeKey(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 参数模板 -->
        <el-card shadow="never" style="margin-top:16px">
          <div class="card-title"><span>参数模板</span></div>
          <el-table :data="templates" size="small" stripe>
            <el-table-column prop="name" label="模板名" />
            <el-table-column label="操作" width="80">
              <template #default="{ row }">
                <el-button size="small" type="danger" @click="removeTemplate(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!templates.length" description="暂无模板" :image-size="60" />
        </el-card>
      </el-col>

      <!-- 右列 -->
      <el-col :xs="24" :sm="12">
        <!-- 容器信息 -->
        <el-card shadow="never">
          <div class="card-title"><span>📦 容器架构</span></div>
          <el-descriptions :column="1" size="small" border style="margin-bottom:12px">
            <el-descriptions-item label="架构模式">
              <el-tag size="small" type="success">单容器一体化</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="Router URL">
              <span class="mono">{{ containerInfoData.router_url || '-' }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="Router 状态">
              <el-tag size="small" :type="containerInfoData.router_healthy ? 'success' : 'danger'">
                {{ containerInfoData.router_healthy ? '在线' : '离线' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="模型目录">{{ containerInfoData.model_dir || '-' }}</el-descriptions-item>
            <el-descriptions-item label="最大驻留">{{ containerInfoData.models_max || '-' }}</el-descriptions-item>
            <el-descriptions-item label="全局上下文">
              <span class="mono">{{ routerCtx || '-' }}</span>
              <el-tag size="small" type="warning" style="margin-left:6px">所有模型共用</el-tag>
            </el-descriptions-item>
          </el-descriptions>
          <el-alert type="info" :closable="false" show-icon title="上下文说明"
            description="当前策略：每个模型使用自身 GGUF 默认上下文（如 9B≈131K、小模型≈8K），互不影响。llama.cpp router 模式下模型预设里的'上下文长度'不参与实际加载；如需统一钳制所有模型，部署时设置 ROUTER_CTX（如 ROUTER_CTX=32768）并重启容器。"
            style="margin-top:8px" />
        </el-card>

        <!-- 告警配置（M7 飞书通知） -->
        <el-card shadow="never" style="margin-top:16px">
          <div class="card-title"><span>🔔 告警通知（飞书）</span></div>
          <el-alert type="info" :closable="false" show-icon style="margin-bottom:8px;font-size:12px"
            title="模型异常自动重启/自愈失败时推送飞书"
            description="在飞书群添加「自定义机器人」，复制 Webhook 地址填入下方。留空则关闭告警。" />
          <el-form label-width="90px" style="max-width:520px">
            <el-form-item label="Webhook">
              <el-input v-model="alertCfg.webhook" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." clearable />
            </el-form-item>
            <el-form-item label="启用">
              <el-switch v-model="alertCfg.enabled" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" size="small" :loading="savingAlert" @click="saveAlert">保存</el-button>
              <el-button size="small" :loading="testingAlert" @click="testAlert">发送测试</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 模型预设 -->
        <el-card shadow="never" style="margin-top:16px">
          <div class="card-title">
            <span>⚙️ 模型预设</span>
            <el-button type="primary" size="small" style="margin-left:auto" @click="openPresetDialog">新增预设</el-button>
            <el-button size="small" @click="generateConfig">生成 config.ini</el-button>
          </div>
          <el-alert type="warning" :closable="false" show-icon style="margin-bottom:8px;font-size:12px">
            预设保存后需生成 config.ini 并重启容器生效
          </el-alert>
          <el-table :data="presets" size="small" stripe>
            <el-table-column prop="model_name" label="模型名" min-width="140" />
            <el-table-column prop="ctx_size" label="上下文" width="80" />
            <el-table-column prop="temp" label="温度" width="60" />
            <el-table-column label="Flash Attn" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.flash_attn ? 'success' : 'info'">{{ row.flash_attn ? 'ON' : 'OFF' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button size="small" @click="editPreset(row)">编辑</el-button>
                <el-button size="small" type="danger" @click="removePreset(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!presets.length" description="暂无预设" :image-size="60" />
        </el-card>

        <!-- 引擎管理 -->
        <el-card shadow="never" style="margin-top:16px">
          <div class="card-title">
            <span>🔧 引擎管理</span>
            <el-button size="small" type="danger" plain @click="doCleanupOld"><el-icon><Delete /></el-icon>&nbsp;清理旧版本</el-button>
            <el-button size="small" @click="loadEngineBackends"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
          </div>

          <!-- 推理后端统一开关 -->
          <div style="margin-bottom:12px;padding:12px;background:#f8f9fb;border-radius:8px">
            <div style="font-size:13px;font-weight:600;margin-bottom:8px">推理后端</div>
            <el-radio-group v-model="selectedFlavor" @change="onBackendChange" :disabled="switchingBackend">
              <el-radio-button v-for="b in engineBackends?.backends || []" :key="b.flavor" :value="b.flavor">
                {{ b.label }}<template v-if="b.active">（当前）</template>
              </el-radio-button>
            </el-radio-group>
            <div class="form-tip" style="margin-top:6px">
              切换后端会切换 llama.cpp 推理引擎构建（SYCL / Vulkan 各存一套版本与推理参数），<b>切换后需重启全部推理服务生效</b>。
            </div>
          </div>

          <!-- 选中后端状态 -->
          <el-descriptions v-if="currentBackend" :column="3" size="small" border style="margin-bottom:12px">
            <el-descriptions-item label="当前后端">
              <el-tag :type="currentBackend.flavor === 'vulkan' ? 'warning' : 'info'">{{ currentBackend.label }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="激活版本">
              <el-tag type="success">{{ currentBackend.active_version || '-' }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="已安装版本">{{ currentBackend.installed?.length || 0 }} 个</el-descriptions-item>
          </el-descriptions>

          <div style="font-size:13px;font-weight:600;margin-bottom:8px">已安装版本（{{ currentBackend?.label }}，可回滚）</div>
          <el-table :data="currentBackend?.installed || []" size="small" stripe style="margin-bottom:16px">
            <el-table-column prop="version" label="版本" width="130" />
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag v-if="row.active" size="small" type="success">当前</el-tag>
                <span v-else style="color:#909399">已备份</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button v-if="!row.active" size="small" link @click="doRollback(row.dir)">回滚</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div style="font-size:13px;font-weight:600;margin-bottom:8px">可用升级（{{ currentBackend?.label }}，GitHub Release）</div>
          <el-table :data="currentBackend?.available || []" size="small" stripe v-loading="engineLoading" style="margin-bottom:12px">
            <el-table-column prop="version" label="版本" width="120" />
            <el-table-column prop="size_human" label="大小" width="100" />
            <el-table-column prop="published_at" label="发布时间" min-width="150" />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button size="small" type="primary" link :loading="engineUpgrading === selectedFlavor + '-' + row.version" @click="doUpgrade(row.version, selectedFlavor)">安装</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 网络代理 -->
        <el-card shadow="never" style="margin-top:16px">
          <div class="card-title"><span>🌐 网络代理</span></div>
          <el-form :model="proxyForm" label-width="120px" size="small">
            <el-form-item label="启用代理">
              <el-switch v-model="proxyForm.proxy_enabled" />
            </el-form-item>
            <el-form-item label="代理地址">
              <el-input v-model="proxyForm.proxy_url" placeholder="如 http://192.168.3.232:7897" />
            </el-form-item>
            <el-form-item label="HF 镜像">
              <el-input v-model="proxyForm.hf_mirror" placeholder="如 https://hf-mirror.com（可选）" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingProxy" @click="saveProxy">保存</el-button>
              <el-button size="small" style="margin-left:8px" @click="testProxy">测试</el-button>
            </el-form-item>
          </el-form>
          <el-alert v-if="proxyTest" :type="proxyTestOk ? 'success' : 'error'" :closable="false" show-icon style="margin-top:8px">
            {{ proxyTest }}
          </el-alert>
        </el-card>

        <!-- 修改密码 -->
        <el-card shadow="never" style="margin-top:16px">
          <div class="card-title"><span>🔒 修改密码</span></div>
          <el-form :model="pwdForm" label-width="80px" size="small">
            <el-form-item label="原密码">
              <el-input v-model="pwdForm.old_password" type="password" show-password />
            </el-form-item>
            <el-form-item label="新密码">
              <el-input v-model="pwdForm.new_password" type="password" show-password placeholder="至少 4 位" />
            </el-form-item>
            <el-form-item label="确认">
              <el-input v-model="pwdForm.confirm" type="password" show-password />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="changingPwd" @click="doChangePassword">修改</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
    </el-row>

    <!-- 生成密钥对话框 -->
    <el-dialog v-model="createKeyDialog" title="生成 API 密钥" width="420px" class="responsive-dialog">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="newKeyName" placeholder="如 openclaw、doclab" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createKeyDialog = false">取消</el-button>
        <el-button type="primary" @click="doCreateKey">生成</el-button>
      </template>
    </el-dialog>

    <!-- 新密钥展示 -->
    <el-dialog v-model="showKeyDialog" title="新密钥已生成（请立即保存）" width="520px" class="responsive-dialog">
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px">
        此密钥只显示一次，关闭后无法再次查看
      </el-alert>
      <el-input :model-value="generatedKey" readonly>
        <template #append>
          <el-button @click="copyKey">复制</el-button>
        </template>
      </el-input>
      <template #footer>
        <el-button type="primary" @click="showKeyDialog = false">我已保存</el-button>
      </template>
    </el-dialog>

    <!-- 预设编辑对话框 -->
    <el-dialog v-model="presetDialog" :title="editingPreset.id ? '编辑预设' : '新增预设'" width="640px" class="responsive-dialog">
      <el-form :model="editingPreset" label-width="120px" size="small">
        <el-form-item label="模型名" required>
          <el-input v-model="editingPreset.model_name" placeholder="如 Qwen3.5-9B-Q6_K" :disabled="!!editingPreset.id" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :xs="24" :sm="12">
            <el-form-item label="上下文长度">
              <el-input-number v-model="editingPreset.ctx_size" :min="512" :max="262144" :step="1024" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="GPU 层数">
              <div style="display:flex;gap:8px;align-items:center;width:100%">
                <el-input-number v-if="!nglAuto" v-model="editingPreset.n_gpu_layers" :min="0" :max="999" style="flex:1" />
                <span v-else style="flex:1;font-size:13px;color:#909399">auto（按显存自动）</span>
                <el-switch v-model="nglAuto" @change="nglAutoChange" />
                <span style="font-size:12px;color:#909399;white-space:nowrap">{{ nglAuto ? '自动' : '手动' }}</span>
              </div>
              <div v-if="nglAuto" style="display:flex;gap:8px;align-items:center;width:100%;margin-top:6px">
                <span style="font-size:12px;color:#909399;white-space:nowrap">显存余量</span>
                <el-input-number v-model="editingPreset.fit_target_mib" :min="0" :max="8192" :step="256" style="flex:1" />
                <span style="font-size:12px;color:#909399;white-space:nowrap">MiB</span>
              </div>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="温度">
              <el-input-number v-model="editingPreset.temp" :min="0" :max="2" :step="0.1" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="线程数">
              <el-input-number v-model="editingPreset.threads" :min="1" :max="64" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="批大小">
              <el-input-number v-model="editingPreset.batch_size" :min="32" :max="8192" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="并发槽位">
              <el-input-number v-model="editingPreset.parallel" :min="1" :max="64" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="KV 缓存 K">
              <el-select v-model="editingPreset.cache_type_k" style="width:100%">
                <el-option v-for="t in ['f16','bf16','q8_0','q4_0','q4_1','iq4_nl','f32']" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="KV 缓存 V">
              <el-select v-model="editingPreset.cache_type_v" style="width:100%">
                <el-option v-for="t in ['f16','bf16','q8_0','q4_0','q4_1','iq4_nl','f32']" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="Flash Attention">
          <el-switch v-model="editingPreset.flash_attn" />
        </el-form-item>
        <el-form-item label="Jinja 模板">
          <el-switch v-model="editingPreset.jinja" />
        </el-form-item>
        <el-form-item label="mmap">
          <el-switch v-model="editingPreset.mmap" />
        </el-form-item>
        <el-form-item label="MoE 专家 CPU">
          <el-switch v-model="editingPreset.cpu_moe" />
          <span style="margin-left:8px;color:#909399;font-size:12px">专家权重放 CPU（MoE 模型提速）</span>
        </el-form-item>
        <el-form-item v-if="editingPreset.cpu_moe" label="CPU MoE 层数">
          <el-input-number v-model="editingPreset.cpu_moe_layers" :min="0" :step="1" style="width:100%" placeholder="全部" />
          <span style="margin-left:8px;color:#909399;font-size:12px">0=全部专家层；N&gt;0=仅前 N 层（--n-cpu-moe N）</span>
        </el-form-item>
        <el-form-item label="MTP 投机解码">
          <el-switch v-model="editingPreset.mtp" />
          <span style="margin-left:8px;color:#909399;font-size:12px">多 token 预测加速</span>
        </el-form-item>
        <el-form-item v-if="editingPreset.mtp" label="MTP 预测长度">
          <el-input-number v-model="editingPreset.mtp_n_max" :min="1" :max="16" style="width:100%" />
        </el-form-item>
        <el-form-item v-if="editingPreset.mtp" label="MTP 模型">
          <el-input v-model="editingPreset.mtp_model" placeholder="如 mtp-gemma-4-12B-it.gguf（留空=自投机）" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="presetDialog = false">取消</el-button>
        <el-button type="primary" @click="savePreset">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listApiKeys, createApiKey, deleteApiKey, toggleApiKey,
  listTemplates, deleteTemplate, containerInfo, getRouterCtx,
  getProxySettings, saveProxySettings, authChangePassword,
  listPresets, createPreset, updatePreset, deletePreset, generateConfigIni,
  getEngineVersion, getEngineUpgrades, getEngineBackends, switchEngine,
  upgradeEngine, rollbackEngine, cleanupEngine, restartAllServices,
  getAlertConfig, saveAlertConfig, testAlert as sendTestAlert,
} from '../api'
import { Refresh, Delete } from '@element-plus/icons-vue'

const keys = ref([])
const templates = ref([])
const presets = ref([])
const containerInfoData = ref({})
const alertCfg = ref({ webhook: '', enabled: true })
const savingAlert = ref(false)
const testingAlert = ref(false)
const routerCtx = ref(null)
const createKeyDialog = ref(false)
const showKeyDialog = ref(false)
const newKeyName = ref('')
const generatedKey = ref('')
const proxyForm = ref({ proxy_enabled: false, proxy_url: '', hf_mirror: '' })
const savingProxy = ref(false)
const proxyTest = ref('')
const proxyTestOk = ref(false)
const pwdForm = ref({ old_password: '', new_password: '', confirm: '' })
const changingPwd = ref(false)
const presetDialog = ref(false)
const editingPreset = ref({})
// GPU 层数：-1 = 引擎自动适配（--ngl auto + --fit 预留 1GB）
const nglAuto = ref(false)
const nglAutoBackup = ref(99)
function nglAutoChange(v) {
  if (v) {
    nglAutoBackup.value = editingPreset.value.n_gpu_layers >= 0 ? editingPreset.value.n_gpu_layers : 99
    editingPreset.value.n_gpu_layers = -1
  } else {
    editingPreset.value.n_gpu_layers = nglAutoBackup.value
  }
}

// ---------- 引擎管理 ----------
const engineBackends = ref(null)
const selectedFlavor = ref('sycl-fp16')
const switchingBackend = ref(false)
const engineLoading = ref(false)
const engineUpgrading = ref('')
const FLAVOR_LABELS = { 'sycl-fp16': 'SYCL', vulkan: 'Vulkan' }
const currentBackend = computed(() =>
  (engineBackends.value?.backends || []).find(b => b.flavor === selectedFlavor.value) || null
)

async function loadEngineBackends() {
  engineLoading.value = true
  try {
    const data = await getEngineBackends()
    engineBackends.value = data
    selectedFlavor.value = data.current || 'sycl-fp16'
  } catch (e) {
    ElMessage.error('引擎信息加载失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    engineLoading.value = false
  }
}

async function onBackendChange(flavor) {
  const target = (engineBackends.value?.backends || []).find(b => b.flavor === flavor)
  const label = target?.label || flavor
  // 已是当前后端：仅查看，不动作
  if (target?.active) {
    ElMessage.info(`当前已是 ${label} 后端`)
    return
  }
  // 该后端没有已安装版本：引导先安装
  if (!target?.installed?.some(v => !v.active)) {
    ElMessage.warning(`${label} 后端尚未安装任何版本，请先在下方向导选择版本安装`)
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认将推理后端切换到 ${label}？\n\n切换将替换 llama.cpp 引擎构建，并需要重启全部推理服务生效（推理参数将使用 ${label} 模板）。`,
      '切换推理后端', { confirmButtonText: '切换', cancelButtonText: '取消', type: 'warning' }
    )
  } catch (e) { return }
  switchingBackend.value = true
  try {
    const r = await switchEngine(flavor)
    ElMessage.success(r.message || `已切换到 ${label}`)
    await loadEngineBackends()
    // 提示重启全部推理服务
    try {
      await ElMessageBox.confirm(
        `引擎构建已切换为 ${label}，需要重启全部推理服务才能生效。是否现在重启？`,
        '重启推理服务', { confirmButtonText: '立即重启', cancelButtonText: '稍后', type: 'warning' }
      )
      await restartAllServices()
      ElMessage.success('已重启全部推理服务')
    } catch (e) { /* 用户取消重启 */ }
  } catch (e) {
    ElMessage.error('切换失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    switchingBackend.value = false
  }
}

async function doUpgrade(version, flavor = 'sycl-fp16') {
  const label = FLAVOR_LABELS[flavor] || flavor
  try {
    await ElMessageBox.confirm(
      `确认安装 llama.cpp ${label} ${version}？安装后需重启服务生效。`,
      '安装确认', { confirmButtonText: '安装', cancelButtonText: '取消', type: 'warning' }
    )
  } catch (e) { return }
  engineUpgrading.value = `${flavor}-${version}`
  try {
    const r = await upgradeEngine(version, flavor)
    ElMessage.success(r.message || `已安装 ${label} ${version}`)
    await loadEngineBackends()
  } catch (e) {
    ElMessage.error('升级失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    engineUpgrading.value = ''
  }
}

async function doRollback(version) {
  try {
    await ElMessageBox.confirm(
      `确认回滚到 ${version}？回滚后需重启容器生效。`,
      '回滚确认', { confirmButtonText: '回滚', cancelButtonText: '取消', type: 'warning' }
    )
  } catch (e) { return }
  try {
    const r = await rollbackEngine(version)
    ElMessage.success(r.message || `已回滚到 ${version}`)
    await loadEngineBackends()
  } catch (e) {
    ElMessage.error('回滚失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function doCleanupOld() {
  // 先 dry-run 预览将删除的版本，确认后再执行
  let preview
  try {
    preview = await cleanupEngine(3, true)
  } catch (e) {
    ElMessage.error('预览失败: ' + (e.response?.data?.detail || e.message))
    return
  }
  if (!preview.deleted || preview.deleted.length === 0) {
    ElMessage.info('没有可清理的旧版本')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将删除 ${preview.deleted.length} 个旧版本备份：\n${preview.deleted.join('、')}\n\n保留：${preview.kept.join('、')}`,
      '清理旧版本', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch (e) { return }
  try {
    const r = await cleanupEngine(3, false)
    ElMessage.success(`已清理 ${r.deleted.length} 个旧版本：${r.deleted.join('、')}`)
    await loadEngineBackends()
  } catch (e) {
    ElMessage.error('清理失败: ' + (e.response?.data?.detail || e.message))
  }
}

const defaultPreset = {
  model_name: '', ctx_size: 8192, temp: 0.7, threads: 8, batch_size: 2048,
  ubatch_size: 512, parallel: 4, cache_type_k: 'q8_0', cache_type_v: 'q8_0',
  flash_attn: true, jinja: true, n_gpu_layers: 99, fit_target_mib: 1024, mmap: true,
  cpu_moe: false, cpu_moe_layers: 0, mtp: false, mtp_model: '', mtp_n_max: 3,
}

async function doChangePassword() {
  if (!pwdForm.value.old_password) { ElMessage.warning('请输入原密码'); return }
  if (!pwdForm.value.new_password || pwdForm.value.new_password.length < 4) { ElMessage.warning('新密码至少 4 位'); return }
  if (pwdForm.value.new_password !== pwdForm.value.confirm) { ElMessage.warning('两次输入不一致'); return }
  changingPwd.value = true
  try {
    await authChangePassword(pwdForm.value.old_password, pwdForm.value.new_password)
    ElMessage.success('密码已修改')
    pwdForm.value = { old_password: '', new_password: '', confirm: '' }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '修改失败')
  } finally {
    changingPwd.value = false
  }
}

function maskKey(k) {
  if (!k) return ''
  return k.slice(0, 10) + '••••' + k.slice(-4)
}

async function loadKeys() { keys.value = await listApiKeys() }
async function loadTemplates() { templates.value = await listTemplates() }
async function loadContainerInfo() {
  containerInfoData.value = await containerInfo()
  try { const r = await getRouterCtx(); routerCtx.value = r.router_ctx } catch (e) { routerCtx.value = null }
}
async function loadPresets() { presets.value = await listPresets() }
async function loadProxy() { proxyForm.value = await getProxySettings() }
async function loadAlert() {
  try { alertCfg.value = await getAlertConfig() } catch (e) { /* ignore */ }
}
async function saveAlert() {
  savingAlert.value = true
  try {
    await saveAlertConfig(alertCfg.value)
    ElMessage.success('告警配置已保存')
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    savingAlert.value = false
  }
}
async function testAlert() {
  testingAlert.value = true
  try {
    await sendTestAlert()
    ElMessage.success('测试告警已发送，请查看飞书')
  } catch (e) {
    ElMessage.error('发送失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    testingAlert.value = false
  }
}

async function saveProxy() {
  savingProxy.value = true
  try {
    proxyForm.value = await saveProxySettings(proxyForm.value)
    ElMessage.success('代理设置已保存')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingProxy.value = false
  }
}

async function testProxy() {
  proxyTest.value = '测试中...'
  try {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 12000)
    const resp = await fetch('https://huggingface.co/api/models?search=qwen&limit=1', { signal: ctrl.signal })
    clearTimeout(timer)
    proxyTestOk.value = resp.ok
    proxyTest.value = resp.ok ? '✅ 连接正常' : `❌ HTTP ${resp.status}`
  } catch (e) {
    proxyTestOk.value = false
    proxyTest.value = `❌ ${e.name === 'AbortError' ? '超时' : e.message}`
  }
}

async function doCreateKey() {
  if (!newKeyName.value) { ElMessage.warning('请输入名称'); return }
  const r = await createApiKey(newKeyName.value)
  generatedKey.value = r.key
  newKeyName.value = ''
  createKeyDialog.value = false
  showKeyDialog.value = true
  loadKeys()
}

function copyKey() {
  navigator.clipboard.writeText(generatedKey.value)
  ElMessage.success('已复制')
}

async function toggle(row) { await toggleApiKey(row.id); loadKeys() }
async function removeKey(row) { await deleteApiKey(row.id); loadKeys() }
async function removeTemplate(row) { await deleteTemplate(row.id); loadTemplates() }

// ---------- 预设 ----------
function openPresetDialog() {
  editingPreset.value = { ...defaultPreset }
  nglAuto.value = (editingPreset.value.n_gpu_layers ?? 99) < 0
  presetDialog.value = true
}

function editPreset(row) {
  editingPreset.value = { ...row }
  nglAuto.value = (editingPreset.value.n_gpu_layers ?? 99) < 0
  presetDialog.value = true
}

async function savePreset() {
  if (!editingPreset.value.model_name) { ElMessage.warning('请输入模型名'); return }
  try {
    if (editingPreset.value.id) {
      await updatePreset(editingPreset.value.id, editingPreset.value)
    } else {
      await createPreset(editingPreset.value)
    }
    ElMessage.success('预设已保存')
    presetDialog.value = false
    loadPresets()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}

async function removePreset(row) {
  await deletePreset(row.id)
  ElMessage.success('已删除')
  loadPresets()
}

async function generateConfig() {
  try {
    const r = await generateConfigIni()
    if (r.ok) {
      ElMessage.success('config.ini 已生成，重启容器后生效')
    } else {
      ElMessage.warning('生成失败: ' + (r.error || '未知错误'))
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '生成失败')
  }
}

onMounted(() => {
  loadKeys(); loadTemplates(); loadContainerInfo(); loadProxy(); loadPresets(); loadEngineBackends(); loadAlert()
})
</script>

<style scoped>
.form-tip { font-size: 12px; color: #909399; margin-top: 8px; }

/* 移动端：双列堆叠为单列 */
@media (max-width: 767px) {
  .el-col + .el-col { margin-top: 12px; }
  .responsive-dialog { width: 92% !important; }
}
</style>
