<template>
  <el-form :model="model" label-width="110px" size="small">
    <el-row :gutter="16">
      <el-col :span="12">
        <el-form-item label="上下文长度">
          <el-input-number v-model="model.ctx_size" :min="512" :max="262144" :step="1024" controls-position="right" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="GPU 层数">
          <el-input-number v-model="model.n_gpu_layers" :min="0" :max="999" controls-position="right" style="width:100%" />
        </el-form-item>
      </el-col>
    </el-row>
    <el-row :gutter="16">
      <el-col :span="12">
        <el-form-item label="线程数">
          <el-input-number v-model="model.threads" :min="1" :max="64" controls-position="right" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="批大小">
          <el-input-number v-model="model.batch_size" :min="32" :max="8192" :step="512" controls-position="right" style="width:100%" />
        </el-form-item>
      </el-col>
    </el-row>
    <el-row :gutter="16">
      <el-col :span="12">
        <el-form-item label="ubatch">
          <el-input-number v-model="model.ubatch_size" :min="32" :max="4096" :step="256" controls-position="right" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="并行数">
          <el-input-number v-model="model.parallel" :min="1" :max="64" controls-position="right" style="width:100%" />
        </el-form-item>
      </el-col>
    </el-row>
    <el-row :gutter="16">
      <el-col :span="12">
        <el-form-item label="温度">
          <el-input-number v-model="model.temp" :min="0" :max="2" :step="0.1" :precision="2" controls-position="right" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="KV 缓存 K">
          <el-select v-model="model.cache_type_k" style="width:100%">
            <el-option v-for="t in kvTypes" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
      </el-col>
    </el-row>
    <el-row :gutter="16">
      <el-col :span="12">
        <el-form-item label="KV 缓存 V">
          <el-select v-model="model.cache_type_v" style="width:100%">
            <el-option v-for="t in kvTypes" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="Flash Attn">
          <el-switch v-model="model.flash_attn" />
        </el-form-item>
      </el-col>
    </el-row>
    <el-row :gutter="16">
      <el-col :span="12">
        <el-form-item label="Jinja 模板">
          <el-switch v-model="model.jinja" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="mmap">
          <el-switch v-model="model.mmap" />
        </el-form-item>
      </el-col>
    </el-row>
  </el-form>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Object, required: true },
})
const emit = defineEmits(['update:modelValue'])

const kvTypes = ['f16', 'bf16', 'q8_0', 'q4_0', 'q4_1', 'iq4_nl', 'f32']

const DEFAULT_ARGS = {
  ctx_size: 8192,
  temp: 0.7,
  threads: 8,
  batch_size: 2048,
  ubatch_size: 512,
  parallel: 4,
  cache_type_k: 'q8_0',
  cache_type_v: 'q8_0',
  flash_attn: true,
  jinja: true,
  n_gpu_layers: 99,
  mmap: true,
}

const model = ref({ ...DEFAULT_ARGS, ...props.modelValue })

watch(
  () => props.modelValue,
  (v) => { model.value = { ...DEFAULT_ARGS, ...v } },
  { deep: true }
)
watch(
  model,
  (v) => { emit('update:modelValue', { ...v }) },
  { deep: true }
)
</script>
