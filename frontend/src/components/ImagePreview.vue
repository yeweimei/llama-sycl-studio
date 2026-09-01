<template>
  <!-- 图片预览：全屏深色 + CSS transform 缩放/旋转（无第三方库） -->
  <el-dialog
    :model-value="modelValue"
    fullscreen
    top="0"
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    modal-class="img-preview-modal"
    class="img-preview-dialog"
    @update:model-value="onDialogUpdate"
    destroy-on-close
  >
    <template #header>
      <div class="img-preview-toolbar">
        <el-button-group>
          <el-button size="small" :disabled="scale <= minScale" @click="zoomOut" :title="`缩小（-）`"><el-icon><ZoomOut /></el-icon></el-button>
          <el-button size="small" :disabled="scale >= maxScale" @click="zoomIn" :title="`放大（+）`"><el-icon><ZoomIn /></el-icon></el-button>
          <el-button size="small" @click="rotate" :title="`旋转（R）`"><el-icon><RefreshLeft /></el-icon></el-button>
          <el-button size="small" @click="reset" :title="`重置（0）`"><el-icon><RefreshRight /></el-icon></el-button>
          <el-button size="small" @click="download" :title="`下载`"><el-icon><Download /></el-icon></el-button>
          <el-button size="small" @click="close" :title="`关闭（Esc）`"><el-icon><Close /></el-icon></el-button>
        </el-button-group>
        <span class="img-preview-info">{{ Math.round(scale * 100) }}% · 滚轮缩放 · Esc 关闭</span>
      </div>
    </template>
    <div ref="stageRef" class="img-preview-stage" @wheel.prevent="onWheel">
      <img
        :src="src"
        class="img-preview-img"
        :style="{ transform: `scale(${scale}) rotate(${rotation}deg)` }"
        alt="预览图片"
        @dblclick="reset"
      />
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { ZoomIn, ZoomOut, RefreshLeft, RefreshRight, Download, Close } from '@element-plus/icons-vue'

// v-model：modelValue 控制显隐，src 为图片地址（dataURL 或 http(s)）
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  src: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const scale = ref(1)
const rotation = ref(0)
const minScale = 0.1
const maxScale = 5

function close() { emit('update:modelValue', false) }
function onDialogUpdate(v) { emit('update:modelValue', v) }

function zoomIn() { scale.value = Math.min(maxScale, +(scale.value * 1.2).toFixed(2)) }
function zoomOut() { scale.value = Math.max(minScale, +(scale.value / 1.2).toFixed(2)) }
function rotate() { rotation.value = (rotation.value + 90) % 360 }
function reset() { scale.value = 1; rotation.value = 0 }
function onWheel(e) { if (e.deltaY < 0) zoomIn(); else zoomOut() }

function download() {
  // src 可能是 dataURL，也可能是 http(s) 图片；fallback 用新窗口打开兜底
  try {
    const a = document.createElement('a')
    a.href = props.src
    a.download = 'image-preview.png'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } catch (e) { /* ignore */ }
}

// 打开时重置视图
watch(() => props.modelValue, (v) => { if (v) reset() })

// 键盘快捷键：Esc 关闭、+/- 缩放、R 旋转、0 重置
function onKeydown(e) {
  if (!props.modelValue) return
  if (e.key === 'Escape') close()
  else if (e.key === '+' || e.key === '=') zoomIn()
  else if (e.key === '-') zoomOut()
  else if (e.key === 'r' || e.key === 'R') rotate()
  else if (e.key === '0') reset()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.img-preview-toolbar {
  display: flex; align-items: center; gap: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  padding-bottom: 8px;
}
.img-preview-info { font-size: 12px; color: #909399; }
.img-preview-stage {
  display: flex; align-items: center; justify-content: center;
  flex: 1; min-height: 0; overflow: auto; user-select: none;
}
.img-preview-img {
  max-width: 90vw; max-height: 82vh; object-fit: contain;
  border-radius: 4px; cursor: zoom-in; transition: transform 0.1s ease;
  transform-origin: center center;
}
</style>

<style>
/* 全局：el-dialog 全屏深色背景（scoped 无法穿透 dialog body，用非 scoped 处理） */
.img-preview-modal { background: rgba(0, 0, 0, 0.85); }
.img-preview-dialog { background: transparent; }
.img-preview-dialog .el-dialog {
  background: #1a1a1a; color: #fff; box-shadow: none;
  display: flex; flex-direction: column;
}
.img-preview-dialog .el-dialog__header { margin: 0; padding: 12px 16px; }
.img-preview-dialog .el-dialog__body { flex: 1; overflow: hidden; padding: 8px 16px 16px; display: flex; }
.img-preview-dialog .el-button { color: #fff; background: transparent; border-color: rgba(255, 255, 255, 0.25); }
.img-preview-dialog .el-button:hover { color: #409eff; border-color: #409eff; background: rgba(64, 158, 255, 0.1); }
.img-preview-dialog .el-button.is-disabled { color: rgba(255, 255, 255, 0.3); background: transparent; border-color: rgba(255, 255, 255, 0.1); }
</style>