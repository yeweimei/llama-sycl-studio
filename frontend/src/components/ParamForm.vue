<template>
  <el-form :model="model" label-width="110px" size="small">
    <el-row :gutter="16">
      <el-col :span="12">
        <el-form-item label="上下文长度">
          <el-input-number v-model="model.ctx_size" :min="512" :max="262144" :step="1024" controls-position="right" style="width:100%" />
          <div class="form-tip" style="width:100%">当前策略：模型使用自身 GGUF 默认上下文；此项为展示值（见设置页说明）</div>
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="GPU 层数">
          <el-input-number v-model="model.n_gpu_layers" :min="0" :max="999" controls-position="right" style="width:100%" />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="MoE 专家 CPU">
          <el-switch v-model="model.cpu_moe" />
          <div class="form-tip" style="width:100%">专家权重放 CPU、attention 全 GPU（MoE 模型提速，实测 +24%）</div>
        </el-form-item>
      </el-col>
    </el-row>
    <el-row :gutter="16">
      <el-col :span="12">
        <el-form-item label="MTP 投机解码">
          <el-switch v-model="model.mtp" />
          <div class="form-tip" style="width:100%">多 token 预测加速（需 MTP 模型文件）</div>
        </el-form-item>
      </el-col>
      <el-col :span="12" v-if="model.mtp">
        <el-form-item label="MTP 模型">
          <el-input v-model="model.mtp_model" placeholder="如 Qwen3.6-35B-A3B-MTP.gguf（放 /models 下）" clearable />
        </el-form-item>
      </el-col>
    </el-row>
    <el-row :gutter="16" v-if="model.mtp">
      <el-col :span="12">
        <el-form-item label="预测长度">
          <el-input-number v-model="model.mtp_n_max" :min="1" :max="16" controls-position="right" style="width:100%" />
          <div class="form-tip" style="width:100%">每次投机预测的 token 数（默认 3，越大加速越多但接受率下降）</div>
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
          <div class="form-tip" style="width:100%">并发上限：同时最多处理 N 个请求，超出自动排队（llama.cpp 内部排队）</div>
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

    <!-- 长上下文缩放（YaRN） -->
    <el-divider content-position="left">
      <span style="cursor:pointer;user-select:none" @click="yarnOpen = !yarnOpen">
        {{ yarnOpen ? '▾' : '▸' }} 长上下文缩放（YaRN / RoPE）
      </span>
    </el-divider>
    <div v-show="yarnOpen">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="长上下文缩放">
            <el-switch v-model="model.rope_enabled" @change="onYarnSwitch" />
            <div class="form-tip" style="width:100%">Qwen 社区建议：超过 32K 长上下文必须启用 YaRN 缩放，不能只加大 ctx-size</div>
          </el-form-item>
        </el-col>
        <el-col :span="12" v-if="model.rope_enabled">
          <el-form-item label="缩放方法">
            <el-select v-model="model.rope_scaling" style="width:100%">
              <el-option value="yarn" label="yarn（YaRN，推荐）" />
              <el-option value="linear" label="linear（线性）" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16" v-if="model.rope_enabled">
        <el-col :span="12">
          <el-form-item label="缩放因子">
            <el-input-number v-model="model.rope_scale" :min="0.5" :max="10" :step="0.1" :precision="2" controls-position="right" style="width:100%" />
            <div class="form-tip" style="width:100%">YaRN 建议 = 目标上下文 / 原始上下文，如 128K/32K = 4.0</div>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="原始上下文">
            <el-input-number v-model="model.yarn_orig_ctx" :min="1024" :max="262144" :step="1024" controls-position="right" style="width:100%" />
            <div class="form-tip" style="width:100%">模型训练时的原始上下文长度，如 32768</div>
          </el-form-item>
        </el-col>
      </el-row>
    </div>

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
            <span class="param-help-btn" title="查看说明" @click="showHelp('top_k')">❓</span>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Top-P">
            <el-input-number v-model="model.sampling.top_p" :min="0" :max="1" :step="0.05" :precision="2" controls-position="right" style="width:100%" />
            <span class="param-help-btn" title="查看说明" @click="showHelp('top_p')">❓</span>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="Min-P">
            <el-input-number v-model="model.sampling.min_p" :min="0" :max="1" :step="0.05" :precision="2" controls-position="right" style="width:100%" />
            <span class="param-help-btn" title="查看说明" @click="showHelp('min_p')">❓</span>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Typical-P">
            <el-input-number v-model="model.sampling.typical_p" :min="0" :max="1" :step="0.05" :precision="2" controls-position="right" style="width:100%" />
            <span class="param-help-btn" title="查看说明" @click="showHelp('typical_p')">❓</span>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="重复惩罚">
            <el-input-number v-model="model.sampling.repeat_penalty" :min="1" :max="2" :step="0.05" :precision="2" controls-position="right" style="width:100%" />
            <span class="param-help-btn" title="查看说明" @click="showHelp('repeat_penalty')">❓</span>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="存在惩罚">
            <el-input-number v-model="model.sampling.presence_penalty" :min="0" :max="2" :step="0.1" :precision="2" controls-position="right" style="width:100%" />
            <span class="param-help-btn" title="查看说明" @click="showHelp('presence_penalty')">❓</span>
          </el-form-item>
        </el-col>
      </el-row>
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="频率惩罚">
            <el-input-number v-model="model.sampling.frequency_penalty" :min="0" :max="2" :step="0.1" :precision="2" controls-position="right" style="width:100%" />
            <span class="param-help-btn" title="查看说明" @click="showHelp('frequency_penalty')">❓</span>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="随机种子">
            <el-input-number v-model="model.sampling.seed" :min="-1" :max="2147483647" controls-position="right" style="width:100%" />
            <span class="param-help-btn" title="查看说明" @click="showHelp('seed')">❓</span>
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
            <span class="param-help-btn" title="查看说明" @click="showHelp('mirostat')">❓</span>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="Mirostat LR">
            <el-input-number v-model="model.sampling.mirostat_lr" :min="0" :max="1" :step="0.01" :precision="3" controls-position="right" style="width:100%" />
            <span class="param-help-btn" title="查看说明" @click="showHelp('mirostat_lr')">❓</span>
          </el-form-item>
        </el-col>
      </el-row>
    </div>

    <!-- 参数说明弹窗 -->
    <el-dialog v-model="helpVisible" :title="helpData?.name || '参数说明'" width="420px" append-to-body>
      <div v-if="helpData" style="line-height:1.8">
        <p style="margin:0 0 8px;color:#606266">{{ helpData.desc }}</p>
        <div style="background:#f5f7fa;border-radius:6px;padding:10px 12px;font-size:13px">
          <div><b>怎么调：</b>{{ helpData.tip }}</div>
          <div style="margin-top:6px"><b>推荐值：</b>{{ helpData.recommend }}</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="helpVisible = false">知道了</el-button>
      </template>
    </el-dialog>

    <!-- 显存估算（M9） -->
    <el-divider style="margin:12px 0" />
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <el-button type="primary" size="small" plain :loading="estimating" @click="estimateMem">
        📊 估算显存占用
      </el-button>
      <span v-if="estResult" class="form-tip">基于当前参数（ctx={{ model.ctx_size }} / batch={{ model.batch_size }} / {{ model.cache_type_k }}+{{ model.cache_type_v }} KV）</span>
    </div>
    <el-card v-if="estResult" shadow="never" style="margin-top:10px;background:#f5f7fa" size="small">
      <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
        <span style="font-size:20px;font-weight:700;color:#409eff">{{ estResult.total_gib }} GiB</span>
        <span style="font-size:13px;color:#606266">预估总占用</span>
        <el-tag size="small" :type="estFit === 'ok' ? 'success' : (estFit === 'tight' ? 'warning' : 'danger')">
          {{ estFit === 'ok' ? '✅ 可加载' : (estFit === 'tight' ? '⚠️ 显存紧张' : '❌ 超出显存') }}
        </el-tag>
      </div>
      <el-descriptions :column="3" size="small" style="margin-top:8px">
        <el-descriptions-item label="模型权重">{{ estResult.parts.model_weights }} GiB</el-descriptions-item>
        <el-descriptions-item label="KV Cache">{{ estResult.parts.kv_cache }} GiB</el-descriptions-item>
        <el-descriptions-item label="计算图">{{ estResult.parts.compute_graph }} GiB</el-descriptions-item>
        <el-descriptions-item label="mmproj">{{ estResult.parts.mmproj }} GiB</el-descriptions-item>
        <el-descriptions-item label="系统余量">{{ estResult.parts.overhead }} GiB</el-descriptions-item>
        <el-descriptions-item label="设备显存">{{ gpuInfo }}</el-descriptions-item>
      </el-descriptions>
      <div class="form-tip" style="margin-top:6px">{{ estResult.formula_note }}</div>
    </el-card>
  </el-form>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { estimateMemory } from '../api'

const props = defineProps({
  modelValue: { type: Object, required: true },
  modelPath: { type: String, default: '' },
  mmprojPath: { type: String, default: '' },
  gpuTotalGiB: { type: Number, default: 0 },
})
const emit = defineEmits(['update:modelValue'])

const kvTypes = ['f16', 'bf16', 'q8_0', 'q4_0', 'q4_1', 'iq4_nl', 'f32']
const samplingOpen = ref(false)
const yarnOpen = ref(false)

// 关闭 YaRN 开关时联动清空缩放因子/原始上下文，避免残留脏值保存到 DB
function onYarnSwitch(val) {
  if (!val) {
    model.value.rope_scale = null
    model.value.yarn_orig_ctx = null
    model.value.rope_scaling = ''
  }
}

// 参数说明数据（name/desc/tip/recommend）
const HELP_MAP = {
  top_k: {
    name: 'Top-K（候选词截断）',
    desc: '每次生成时，模型只从概率最高的前 K 个词里选一个。K 越小，回答越保守、越可预测；K 越大，越有创意但也越容易跑偏。',
    tip: '想稳定可控就调小（10~30）；想多样有趣就调大（60~100）；0 表示完全关闭这个限制。',
    recommend: '40（默认）｜代码/数学任务 10~30，创意写作 60~100',
  },
  top_p: {
    name: 'Top-P（核采样）',
    desc: '累计概率达到 P 的词才会被候选。相当于"从最可能的一批词里选"，是 Top-K 的另一种筛选方式。两者可同时用。',
    tip: '越小越保守（0.7~0.8），越大越自由（0.95~1.0）。1.0 表示不限制。',
    recommend: '0.95（默认）｜严谨任务 0.8，创意任务 0.9~0.95',
  },
  min_p: {
    name: 'Min-P（最低概率过滤）',
    desc: '把概率低于"最高概率 × Min-P"的词全部排除。比如最高词概率 0.5、Min-P=0.05，则概率低于 0.025 的词都被淘汰。',
    tip: '一种较新的防跑偏手段，配合 Top-P 用效果不错。0 表示关闭。一般 0.02~0.1 之间微调即可。',
    recommend: '0.05（默认）｜想要更稳可试 0.1',
  },
  typical_p: {
    name: 'Typical-P（典型采样）',
    desc: '只选"信息量符合预期"的词，避免模型选太意外或太无聊的词。适合让文本更自然。',
    tip: '1.0 表示关闭。调低到 0.9 左右会让输出更"典型"，减少跳跃。和 Top-P 二选一用即可，不用都开。',
    recommend: '1.0（默认，即关闭）｜需要时可试 0.9',
  },
  repeat_penalty: {
    name: '重复惩罚',
    desc: '对已经出现过的词降权，数值越大越不愿意重复。能明显减少"复读机"现象（比如一直说同一句话）。',
    tip: '1.0 = 不惩罚。聊天模型 1.05~1.15 效果较好；太高（>1.3）会让句子变得生硬、不自然。',
    recommend: '1.0~1.1（默认 1.0）｜中文对话推荐 1.05~1.15',
  },
  presence_penalty: {
    name: '存在惩罚',
    desc: '只要某个词"出现过"就给它降权，不管出现多少次。鼓励模型谈论新话题、换着花样表达。',
    tip: '0 = 不惩罚。想要话题多样、避免车轱辘话可以调到 0.2~0.6。',
    recommend: '0.0（默认）｜需要多样化输出时 0.3~0.6',
  },
  frequency_penalty: {
    name: '频率惩罚',
    desc: '词"出现得越频繁"惩罚越重。跟存在惩罚类似，但更针对反复刷屏的词，能有效压住啰嗦重复。',
    tip: '0 = 不惩罚。和存在惩罚二选一用即可；两者都开容易让回答过于简短。',
    recommend: '0.0（默认）｜压复读 0.3~0.5',
  },
  seed: {
    name: '随机种子',
    desc: '生成随机数的"起点"。固定同一个种子，相同输入会得到几乎一样的输出，方便复现和对比实验。',
    tip: '-1 = 每次随机（默认）。调试时固定一个数（比如 42），改参数对比效果更公平。',
    recommend: '-1（默认，每次随机）｜实验对比时固定 42',
  },
  mirostat: {
    name: 'Mirostat（自适应采样）',
    desc: '一种"智能"采样算法，会动态调整生成策略，让输出始终维持在一个合适的"惊喜度"，减少重复又不至于太乱。',
    tip: '0 = 关闭（推荐大多数情况）。追求更流畅自然的输出可开 v2（2）。开启后建议配合下方 LR 和 Ent 使用。',
    recommend: '0（默认，关闭）｜追求自然可试 v2',
  },
  mirostat_lr: {
    name: 'Mirostat LR（学习率）',
    desc: 'Mirostat 调整策略的"反应速度"。越大调整越快、输出变化越剧烈；越小越平稳。',
    tip: '仅在 Mirostat 开启时有意义。一般保持默认即可，感觉输出太跳可调小到 0.05。',
    recommend: '0.1（默认）',
  },
  mirostat_ent: {
    name: 'Mirostat Ent（目标熵）',
    desc: 'Mirostat 想要维持的"惊喜度"目标值。越大输出越多样，越小越保守。',
    tip: '仅在 Mirostat 开启时有意义。输出太重复就调大（5.5~6），太发散就调小（3~4）。',
    recommend: '5.0（默认）',
  },
}

const helpVisible = ref(false)
const helpData = ref(null)

function showHelp(key) {
  helpData.value = HELP_MAP[key] || null
  helpVisible.value = true
}

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
  cpu_moe: false,
  mtp: false,
  mtp_model: '',
  mtp_n_max: 3,
  rope_scaling: '',
  rope_enabled: false,
  rope_scale: null,
  yarn_orig_ctx: null,
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
  // rope_enabled 从 rope_scaling 推导（后端只存 rope_scaling）
  base.rope_enabled = !!base.rope_scaling
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
    // rope_enabled → rope_scaling（后端只认 rope_scaling: ''/yarn/linear）
    if (!v.rope_enabled) {
      out.rope_scaling = ''
    } else if (!v.rope_scaling) {
      out.rope_scaling = 'yarn'
    }
    delete out.rope_enabled
    // 把 sampling 打平进 extra_args（后端透传 llama.cpp）
    out.extra_args = { ...(v.extra_args || {}), sampling: { ...v.sampling } }
    emit('update:modelValue', out)
  },
  { deep: true }
)

// ---------- 显存估算（M9） ----------
const estimating = ref(false)
const estResult = ref(null)

const estFit = computed(() => {
  if (!estResult.value) return ''
  const total = estResult.value.total_gib
  const gpu = props.gpuTotalGiB
  if (!gpu) return 'ok'
  if (total <= gpu * 0.85) return 'ok'
  if (total <= gpu * 0.95) return 'tight'
  return 'over'
})

const gpuInfo = computed(() => {
  return props.gpuTotalGiB ? `${props.gpuTotalGiB} GiB（目标设备）` : '未知（未选设备）'
})

async function estimateMem() {
  if (!props.modelPath) {
    ElMessage.warning('请先选择模型文件')
    return
  }
  estimating.value = true
  try {
    const payload = {
      model_path: props.modelPath,
      ctx_size: model.value.ctx_size || 8192,
      batch_size: model.value.batch_size || 2048,
      ubatch_size: model.value.ubatch_size || 512,
      parallel: model.value.parallel || 4,
      cache_type_k: model.value.cache_type_k || 'q8_0',
      cache_type_v: model.value.cache_type_v || 'q8_0',
      n_gpu_layers: model.value.n_gpu_layers ?? 99,
      flash_attn: !!model.value.flash_attn,
      mmproj: props.mmprojPath || '',
    }
    estResult.value = await estimateMemory(payload)
  } catch (e) {
    ElMessage.error('估算失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    estimating.value = false
  }
}
</script>

<style scoped>
.param-help-btn {
  cursor: pointer;
  color: #909399;
  margin-left: 4px;
  font-size: 13px;
  user-select: none;
  vertical-align: middle;
}
.param-help-btn:hover {
  color: #409eff;
}
</style>
