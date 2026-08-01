<template>
  <div class="page-container">
    <el-row :gutter="16">
      <el-col :span="8">
        <el-card shadow="never">
          <div class="card-title"><span>内存</span></div>
          <div class="big-num">{{ sys.memory_avail_gb }} <small>GB 可用</small></div>
          <el-progress :percentage="memPercent" :stroke-width="14" :color="'#409eff'" />
          <div class="sub-info">总 {{ sys.memory_total_gb }} GB</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="card-title"><span>磁盘</span></div>
          <div class="big-num">{{ sys.disk_free_gb }} <small>GB 可用</small></div>
          <el-progress :percentage="diskPercent" :stroke-width="14" :color="'#67c23a'" />
          <div class="sub-info">总 {{ sys.disk_total_gb }} GB</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="card-title"><span>模型目录</span></div>
          <div class="big-num">{{ sys.model_dir_size_gb }} <small>GB</small></div>
          <div class="sub-info mono" style="word-break:break-all">{{ sys.model_dir }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title">
        <span>GPU 状态</span>
        <el-button size="small" style="margin-left:auto" @click="loadGpu">刷新</el-button>
      </div>
      <pre class="gpu-view">{{ gpu.raw || '（未获取到 GPU 信息，需安装 xpu-smi）' }}</pre>
      <div v-if="gpu.devices?.length" class="sub-info">
        检测到设备：{{ gpu.devices.map(d => d.pci_id).join(', ') }}
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { systemStatus, gpuStatus } from '../api'

const sys = ref({})
const gpu = ref({})

const memPercent = computed(() => {
  if (!sys.value.memory_total_gb) return 0
  return Math.round((1 - sys.value.memory_avail_gb / sys.value.memory_total_gb) * 100)
})
const diskPercent = computed(() => {
  if (!sys.value.disk_total_gb) return 0
  return Math.round((1 - sys.value.disk_free_gb / sys.value.disk_total_gb) * 100)
})

async function loadGpu() {
  gpu.value = await gpuStatus()
}
async function loadSys() {
  sys.value = await systemStatus()
}

onMounted(() => { loadSys(); loadGpu() })
</script>

<style scoped>
.big-num { font-size: 32px; font-weight: 700; color: #303133; margin-bottom: 12px; }
.big-num small { font-size: 14px; font-weight: 400; color: #909399; }
.sub-info { color: #909399; font-size: 12px; margin-top: 8px; }
.gpu-view {
  background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 6px;
  font-size: 12px; font-family: 'JetBrains Mono', Consolas, monospace;
  max-height: 400px; overflow: auto; white-space: pre-wrap;
}
</style>
