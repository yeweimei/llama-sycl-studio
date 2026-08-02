<template>
  <div class="page-container">
    <el-row :gutter="16">
      <!-- API Keys -->
      <el-col :span="12">
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
          <div class="form-tip">密钥用于保护推理服务的 OpenAI 兼容端点（启动服务时填入 --api-key）</div>
        </el-card>

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

      <!-- 镜像管理 -->
      <el-col :span="12">
        <el-card shadow="never">
          <div class="card-title"><span>llama.cpp 镜像</span></div>
          <el-descriptions :column="1" size="small" border style="margin-bottom:12px">
            <el-descriptions-item label="当前镜像">
              <span class="mono">{{ currentImage }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <el-table :data="images" size="small" stripe>
            <el-table-column prop="tag" label="Tag" min-width="200" />
            <el-table-column label="大小" width="90">
              <template #default="{ row }">{{ fmtSize(row.size) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!images.length" description="无本地镜像" :image-size="60" />
        </el-card>

        <el-card shadow="never" style="margin-top:16px">
          <div class="card-title"><span>🌐 网络代理（模型搜索/下载）</span></div>
          <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px">
            用于解决模型搜索/下载被墙的问题（如 NUC12 直连 HuggingFace 超时）
          </el-alert>
          <el-form :model="proxyForm" label-width="120px" size="small">
            <el-form-item label="启用代理">
              <el-switch v-model="proxyForm.proxy_enabled" />
            </el-form-item>
            <el-form-item label="代理地址">
              <el-input v-model="proxyForm.proxy_url" placeholder="如 http://192.168.3.232:7897" />
              <div class="form-tip">宿主机 clash 代理示例：http://192.168.3.232:7897</div>
            </el-form-item>
            <el-form-item label="HF 镜像">
              <el-input v-model="proxyForm.hf_mirror" placeholder="如 https://hf-mirror.com（可选）" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingProxy" @click="saveProxy">保存代理设置</el-button>
              <el-button size="small" style="margin-left:8px" @click="testProxy">测试连接</el-button>
            </el-form-item>
          </el-form>
          <el-alert v-if="proxyTest" :type="proxyTestOk ? 'success' : 'error'" :closable="false" show-icon style="margin-top:8px">
            {{ proxyTest }}
          </el-alert>
        </el-card>
      </el-col>
    </el-row>

    <!-- 生成密钥对话框 -->
    <el-dialog v-model="createKeyDialog" title="生成 API 密钥" width="420px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="newKeyName" placeholder="如 doclab、openclaw" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createKeyDialog = false">取消</el-button>
        <el-button type="primary" @click="doCreateKey">生成</el-button>
      </template>
    </el-dialog>

    <!-- 新密钥展示 -->
    <el-dialog v-model="showKeyDialog" title="新密钥已生成（请立即保存）" width="520px">
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listApiKeys, createApiKey, deleteApiKey, toggleApiKey,
  listTemplates, deleteTemplate, listImages, imageVersions,
  getProxySettings, saveProxySettings,
} from '../api'

const keys = ref([])
const templates = ref([])
const images = ref([])
const currentImage = ref('')
const createKeyDialog = ref(false)
const showKeyDialog = ref(false)
const newKeyName = ref('')
const generatedKey = ref('')
const proxyForm = ref({ proxy_enabled: false, proxy_url: '', hf_mirror: '' })
const savingProxy = ref(false)
const proxyTest = ref('')
const proxyTestOk = ref(false)

function maskKey(k) {
  if (!k) return ''
  return k.slice(0, 10) + '••••' + k.slice(-4)
}

async function loadKeys() { keys.value = await listApiKeys() }
async function loadTemplates() { templates.value = await listTemplates() }
async function loadImages() {
  images.value = await listImages()
  const v = await imageVersions()
  currentImage.value = v.current
}

async function loadProxy() {
  proxyForm.value = await getProxySettings()
}

async function saveProxy() {
  savingProxy.value = true
  try {
    proxyForm.value = await saveProxySettings(proxyForm.value)
    ElMessage.success('代理设置已保存（搜索/下载立即生效）')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingProxy.value = false
  }
}

async function testProxy() {
  // 前端直接试连 HF，验证代理是否通
  proxyTest.value = '测试中...'
  try {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 12000)
    const resp = await fetch('https://huggingface.co/api/models?search=qwen&limit=1', {
      signal: ctrl.signal,
    })
    clearTimeout(timer)
    proxyTestOk.value = resp.ok
    proxyTest.value = resp.ok ? '✅ 连接正常（直连）' : `❌ 直连失败（HTTP ${resp.status}）`
  } catch (e) {
    proxyTestOk.value = false
    proxyTest.value = `❌ 直连失败：${e.name === 'AbortError' ? '超时（建议启用代理）' : e.message}`
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

async function toggle(row) {
  await toggleApiKey(row.id)
  loadKeys()
}
async function removeKey(row) {
  await deleteApiKey(row.id)
  loadKeys()
}
async function removeTemplate(row) {
  await deleteTemplate(row.id)
  loadTemplates()
}

function fmtSize(n) {
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(1) + units[i]
}

onMounted(() => { loadKeys(); loadTemplates(); loadImages(); loadProxy() })
</script>

<style scoped>
.form-tip { font-size: 12px; color: #909399; margin-top: 8px; }
</style>
