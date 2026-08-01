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
} from '../api'

const keys = ref([])
const templates = ref([])
const images = ref([])
const currentImage = ref('')
const createKeyDialog = ref(false)
const showKeyDialog = ref(false)
const newKeyName = ref('')
const generatedKey = ref('')

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

onMounted(() => { loadKeys(); loadTemplates(); loadImages() })
</script>

<style scoped>
.form-tip { font-size: 12px; color: #909399; margin-top: 8px; }
</style>
