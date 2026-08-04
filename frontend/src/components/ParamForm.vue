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

    <!-- 高级采样参数（存入 extra_args 透传 llama.cpp） -->
    <el-divider content-position="left">
      <span style="cursor:pointer;user-select:none" @click="samplingOpen = !samplingOpen">
        {{ samplingOpen ? '▾' : '▸' }} 采样参数（top_p / repeat_penalty 等）
      </span>
    </el-divider>
    <div v-show="samplingOpen">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="Top-K">
            <el-input-number v-model="model.sampling.top_k" :min="0" :max="200" controls-position="right" style="width:100%" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Top-P">
            <el-input-number v-model="model.sampling.top_p" :min="0" :max="1" :step="0.05" :precision="2" controls-position="right" style="width:100%" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="Min-P">
            <el-input-number v-model="model.sampling.min_p" :min="0" :max="1" :step="0.05" :precision="2" controls-position="right" style="width:100%" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Typical-P">
            <el-input-number v-model="model.sampling.typical_p" :min="0" :max="1" :step="0.05" :precision="2" controls-position="right" style="width:100%" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="重复惩罚">
            <el-input-number v-model="model.sampling.repeat_penalty" :min="1" :max="2" :step="0.05" :precision="2" controls-position="right" style="width:100%" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="存在惩罚">
            <el-input-number v-model="model.sampling.presence_penalty" :min="0" :max="2" :step="0.1" :precision="2" controls-position="right" style="width:100%" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="频率惩罚">
            <el-input-number v-model="model.sampling.frequency_penalty" :min="0" :max="2" :step="0.1" :precision="2" controls-position="right" style="width:100%" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="随机种子">
            <el-input-number v-model="model.sampling.seed" :min="-1" :max="2147483647" controls-position="right" style="width:100%" />
            <div class="form-tip" style="margin-top:2px">-1 = 每次随机</div>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="Mirostat">
            <el-select v-model="model.sampling.mirostat" style="width:100%">
              <el-option :value="0" label="关闭 (0)" />
              <el-option :value="1" label="v1 (1)" />
              <el-option :value="2" label="v2 (2)" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Mirostat LR">
            <el-input-number v-model="model.sampling.mirostat_lr" :min="0" :max="1" :step="0.01" :precision="3" controls-position="right" style="width:100%" />
          </el-form-item>
        </el-col>
      </el-row>
    </div>
  </el-form>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Object, required: true },
})
const emit = defineEmits(['update:modelValue'])

const kvTypes = ['f16', 'bf16', 'q8_0', 'q4_0', 'q4_1', 'iq4_nl', 'f32']
const samplingOpen = ref(false)

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

// 采样参数默认值（与 llama.cpp 默认一致；0/1.0 表示禁用）
const DEFAULT_SAMPLING = {
  top_k: 40,
  top_p: 0.95,
  min_p: 0.05,
  typical_p: 1.0,
  repeat_penalty: 1.0,
  presence_penalty: 0.0,
  frequency_penalty: 0.0,
  seed: -1,
  mirostat: 0,
  mirostat_lr: 0.1,
  mirostat_ent: 5.0,
}

function normalize(v) {
  const base = { ...DEFAULT_ARGS }
  const src = v && typeof v === 'object' ? v : {}
  for (const k of Object.keys(DEFAULT_ARGS)) {
    if (src[k] !== undefined && src[k] !== null) base[k] = src[k]
  }
  // sampling 从 extra_args.sampling 读（旧数据没有则用默认）
  const extra = (src.extra_args && typeof src.extra_args === 'object') ? src.extra_args : {}
  base.sampling = { ...DEFAULT_SAMPLING, ...(extra.sampling || {}) }
  // 兼容旧字段（之前存在 model 顶层的采样参数）
  for (const k of Object.keys(DEFAULT_SAMPLING)) {
    if (src[k] !== undefined && src[k] !== null && base.sampling[k] === DEFAULT_SAMPLING[k]) {
      base.sampling[k] = src[k]
    }
  }
  return base
}

const model = ref(normalize(props.modelValue))

watch(
  () => props.modelValue,
  (v) => {
    const merged = normalize(v)
    if (JSON.stringify(merged) !== JSON.stringify(model.value)) {
      model.value = merged
    }
  },
  { deep: true }
)
watch(
  model,
  (v) => {
    const out = { ...v }
    // 把 sampling 打平进 extra_args（后端透传 llama.cpp）
    out.extra_args = { ...(v.extra_args || {}), sampling: { ...v.sampling } }
    emit('update:modelValue', out)
  },
  { deep: true }
)
</script>
