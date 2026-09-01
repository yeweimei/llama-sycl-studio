<template>
  <div class="page-container">
    <!-- 顶部：模型选择 -->
    <el-card shadow="never" style="margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-size:14px;font-weight:600">对话模型</span>
        <el-select v-model="currentSid" placeholder="选择模型" style="width:320px" filterable @change="onModelChange">
          <el-option
            v-for="s in chatModels"
            :key="s.id"
            :value="s.id"
            :label="`${s.name}${s.loaded ? ' ✓已加载' : '（未加载）'}${s.supports_chat === false ? ' ⚠️不支持对话' : ''}`"
            :disabled="s.supports_chat === false"
          />
        </el-select>
        <el-tag v-if="currentService" size="small" :type="currentService.loaded ? 'success' : 'info'">
          {{ currentService.loaded ? '已加载' : '未加载' }}
        </el-tag>
        <el-button v-if="currentService && !currentService.loaded" size="small" type="primary" :loading="loadingModel" @click="loadCurrentModel">
          加载模型
        </el-button>
        <el-button v-if="currentService && currentService.loaded" size="small" type="warning" :loading="loadingModel" @click="unloadCurrentModel">
          卸载模型
        </el-button>
        <el-button size="small" @click="refreshServices">刷新列表</el-button>
        <span v-if="currentService && currentService.loaded && currentService.device_label" style="font-size:12px;color:#909399">
          {{ currentService.device_label }}
        </span>
      </div>
    </el-card>

    <!-- 聊天区：共享对话组件（状态存于 pinia chatStore，切页面不中断流式） -->
    <el-card shadow="never" v-if="currentSid">
      <ChatPanel
        :key="currentSid"
        :service-id="currentSid"
        :model-loaded="!!currentService?.loaded"
        :is-vision="!!currentService?.has_mmproj"
        :max-tokens-limit="maxTokensLimit"
        :service-loaded-at="currentService?.loaded_at || 0"
        height="calc(100vh - 260px)"
      />
    </el-card>

    <el-empty v-else description="请选择对话模型" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import ChatPanel from '../components/ChatPanel.vue'
import { listServices, startService, stopService, listPresets } from '../api'

// ---------- 模型选择 ----------
const services = ref([])
const currentSid = ref(null)
const loadingModel = ref(false)
const presets = ref([])

// 当前模型可用上下文（决定 max_tokens 上限）
// llama.cpp 机制：总 ctx(--ctx-size) 按 parallel(slot) 均分，meta.n_ctx 即每 slot 上下文；
// max_tokens 上限 = 每 slot 上下文 × 0.75（预留 25% 给对话历史 prompt）
const maxTokensLimit = computed(() => {
  const svc = currentService.value
  if (!svc) return 8192
  const preset = presets.value.find(p => p.model_name === svc.name)
  // 每 slot 上下文：加载后 meta.n_ctx 最准（已均分），否则 ctx/parallel 推算
  let perSlot = svc.loaded_info?.meta?.n_ctx
  if (!perSlot) {
    const ctx = preset?.ctx_size || svc.loaded_info?.ctx_size || 8192
    // parallel：预设优先，loaded_info args 里 --parallel 兜底
    let parallel = preset?.parallel
    if (!parallel && svc.loaded_info?.args) {
      const args = svc.loaded_info.args
      const pi = Array.isArray(args) ? args.indexOf('--parallel') : -1
      if (pi >= 0 && pi + 1 < args.length) parallel = parseInt(args[pi + 1])
    }
    perSlot = Math.floor(ctx / Math.max(1, parallel || 1))
  }
  return Math.max(512, Math.floor(perSlot * 0.75))
})

async function refreshPresets() {
  try { presets.value = await listPresets() } catch (e) { presets.value = [] }
}

// 可对话模型（supports_chat !== false）
const chatModels = computed(() => services.value.filter(s => s.supports_chat !== false))
const currentService = computed(() => services.value.find(s => s.id === currentSid.value))

async function refreshServices() {
  try {
    services.value = await listServices()
    // 默认选第一个已加载的对话模型，否则第一个
    if (!currentSid.value || !services.value.some(s => s.id === currentSid.value)) {
      const loaded = services.value.find(s => s.supports_chat !== false && s.loaded)
      const first = services.value.find(s => s.supports_chat !== false)
      currentSid.value = (loaded || first || services.value[0] || null)?.id ?? null
    }
  } catch (e) { /* ignore */ }
}

// 切换模型：ChatPanel 以 :key=currentSid 重新挂载，按 sid 从 store 恢复/初始化对话
function onModelChange() { /* 状态隔离由 chatStore 按 sid 管理 */ }

async function loadCurrentModel() {
  if (!currentSid.value) return
  loadingModel.value = true
  try {
    await startService(currentSid.value)
    ElMessage.success('模型加载中…')
    await refreshServices()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载失败')
  } finally {
    loadingModel.value = false
  }
}

async function unloadCurrentModel() {
  if (!currentSid.value) return
  loadingModel.value = true
  try {
    await stopService(currentSid.value)
    ElMessage.success('已卸载')
    await refreshServices()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '卸载失败')
  } finally {
    loadingModel.value = false
  }
}

// ---------- 生命周期 ----------
onMounted(async () => {
  await refreshPresets()
  await refreshServices()
})
</script>
