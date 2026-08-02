<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="card-title">
        <span>本地模型（{{ models.length }}）</span>
        <el-button size="small" style="margin-left:auto" @click="load">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
        <el-button type="primary" size="small" @click="$router.push('/downloads')">
          <el-icon><Download /></el-icon>&nbsp;下载模型
        </el-button>
      </div>

      <el-table :data="models" v-loading="loading" stripe class="mobile-table">
        <el-table-column prop="name" label="模型" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <el-icon v-if="row.kind === 'hf-dir'"><Folder /></el-icon>
            <el-icon v-else><Document /></el-icon>
            {{ row.name }}
          </template>
        </el-table-column>
        <el-table-column label="量化" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.quantization" size="small">{{ row.quantization }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="architecture" label="架构" width="180" show-overflow-tooltip />
        <el-table-column prop="size_human" label="大小" width="90" />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.kind === 'gguf' ? 'success' : 'info'">
              {{ row.kind === 'gguf' ? 'GGUF' : 'HF 目录' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-popconfirm title="确认删除该模型文件？" @confirm="doDelete(row)">
              <template #reference>
                <el-button v-if="row.kind === 'gguf'" type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && models.length === 0" description="暂无模型，去下载一个吧" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Download, Document, Folder } from '@element-plus/icons-vue'
import { listModels, deleteModel } from '../api'

const models = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    models.value = await listModels()
  } finally {
    loading.value = false
  }
}

async function doDelete(row) {
  await deleteModel(row.path)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>
