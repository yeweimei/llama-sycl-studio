<template>
  <el-form :model="model" label-width="150px" size="small">
    <el-row :gutter="12">
      <el-col :span="12">
        <el-form-item label="GPU 层数 (-ngl)">
          <el-input-number v-model="model.n_gpu_layers" :min="0" :max="999" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="上下文长度 (-c)">
          <el-input-number v-model="model.ctx_size" :min="512" :max="262144" :step="1024" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="批大小 (-b)">
          <el-input-number v-model="model.batch_size" :min="32" :max="8192" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="微批大小 (--ubatch-size)">
          <el-input-number v-model="model.ubatch_size" :min="16" :max="4096" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="并发槽位 (-np)">
          <el-input-number v-model="model.parallel" :min="1" :max="64" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="Flash Attention">
          <el-switch v-model="model.flash_attn" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="KV 缓存类型 K">
          <el-select v-model="model.cache_type_k" style="width:100%">
            <el-option v-for="t in kvTypes" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="KV 缓存类型 V">
          <el-select v-model="model.cache_type_v" style="width:100%">
            <el-option v-for="t in kvTypes" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="Jinja 模板">
          <el-switch v-model="model.jinja" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="温度 (--temp)">
          <el-slider v-model="model.temp" :min="0" :max="2" :step="0.1" show-input />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="Top-K">
          <el-input-number v-model="model.top_k" :min="1" :max="100" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="Top-P">
          <el-slider v-model="model.top_p" :min="0.1" :max="1" :step="0.05" show-input />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="重复惩罚">
          <el-slider v-model="model.repeat_penalty" :min="1" :max="2" :step="0.05" show-input />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="线程数 (-t)">
          <el-input-number v-model="model.threads" :min="1" :max="64" style="width:100%" />
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

// 默认参数（9B 模型推荐）
const DEFAULT_ARGS = {
  n_gpu_layers: 99,
  ctx_size: 32768,
  batch_size: 2048,
  ubatch_size: 512,
  parallel: 4,
  flash_attn: true,
  cache_type_k: 'q8_0',
  cache_type_v: 'q8_0',
  jinja: true,
  temp: 0.7,
  top_k: 15,
  top_p: 0.95,
  repeat_penalty: 1.0,
  threads: 8,
}

// 用本地 ref 承载，双向同步到 modelValue
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
