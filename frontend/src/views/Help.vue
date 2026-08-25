<template>
  <div class="page-container">
    <!-- 顶部：整体重启 + 快速概览 -->
    <div class="help-hero">
      <div class="hero-left">
        <div class="hero-title">💡 帮助中心</div>
        <div class="hero-sub">LLM Studio 常用操作速查 · 快捷命令 · 故障排查</div>
      </div>
      <div class="hero-right">
        <el-button type="danger" plain :loading="restartingAll" @click="doRestartAll">
          <el-icon style="margin-right:6px"><RefreshRight /></el-icon>重启全部服务
        </el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <!-- 左列 -->
      <el-col :xs="24" :md="12">
        <!-- 引擎与设备 -->
        <el-card shadow="never" class="help-card">
          <div class="card-title"><span>⚙️ 引擎与设备</span></div>
          <div class="cmd-block">
            <div class="cmd-title">查看引擎版本（容器内）</div>
            <code class="cmd">docker exec llama-studio /app/llama-server --version</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">列出 GPU 设备（SYCL）</div>
            <code class="cmd">docker exec llama-studio /app/llama-server --list-devices</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">OpenCL 设备详情</div>
            <code class="cmd">clinfo | grep -E "Device Name|Device Version"</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">查看当前激活引擎版本</div>
            <code class="cmd">docker exec llama-studio cat /root/.llama-studio/bin/active_version</code>
          </div>
        </el-card>

        <!-- 模型实例 -->
        <el-card shadow="never" class="help-card">
          <div class="card-title"><span>🚀 模型实例管理</span></div>
          <div class="cmd-block">
            <div class="cmd-title">查看运行中的实例</div>
            <code class="cmd">docker exec llama-studio ps aux | grep llama-server</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">查看实例日志（如 Qwen3-Embedding）</div>
            <code class="cmd">docker exec llama-studio tail -f /root/.llama-studio/instances/Qwen3-Embedding-0.6B-GGUF.log</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">WebUI / API 健康检查</div>
            <code class="cmd">curl -s http://192.168.3.246:9100/api/health</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">容器重启（升级/回滚引擎后需执行）</div>
            <code class="cmd">ssh nuc12 'sudo docker restart llama-studio'</code>
          </div>
        </el-card>

        <!-- API 调用 -->
        <el-card shadow="never" class="help-card">
          <div class="card-title"><span>🔌 API 调用示例</span></div>
          <div class="cmd-block">
            <div class="cmd-title">对话补全（Chat Completions）</div>
            <code class="cmd multi">curl http://192.168.3.246:9100/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3.8-9B-Distill-GGUF","messages":[{"role":"user","content":"你好"}]}'</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">Embedding（向量化）</div>
            <code class="cmd multi">curl http://192.168.3.246:9100/v1/embeddings \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-Embedding-0.6B-GGUF","input":"hello"}'</code>
          </div>
        </el-card>
      </el-col>

      <!-- 右列 -->
      <el-col :xs="24" :md="12">
        <!-- 引擎升级与回滚 -->
        <el-card shadow="never" class="help-card">
          <div class="card-title"><span>🔄 引擎升级 / 回滚</span></div>
          <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px">
            升级/回滚后<strong>必须重启容器</strong>才生效。建议升级前先备份当前版本（WebUI 自动备份）。
          </el-alert>
          <div class="cmd-block">
            <div class="cmd-title">WebUI 升级（推荐）：设置 → 引擎管理 → 可用升级 → 升级</div>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">命令行回滚到指定版本</div>
            <code class="cmd">docker exec llama-studio sh -c 'echo b10369 &gt; /root/.llama-studio/bin/active_version'</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">手动切换后重启生效</div>
            <code class="cmd">ssh nuc12 'sudo docker restart llama-studio'</code>
          </div>
          <div class="cmd-block">
            <div class="cmd-title">清理旧版本备份（WebUI 一键）</div>
            <code class="cmd">curl -X POST http://192.168.3.246:9100/api/engine/cleanup \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"keep": 3}'</code>
          </div>
        </el-card>

        <!-- 关键环境变量 -->
        <el-card shadow="never" class="help-card">
          <div class="card-title"><span>🧪 关键环境变量（SYCL）</span></div>
          <el-table :data="envVars" size="small" stripe>
            <el-table-column prop="name" label="变量" width="230" />
            <el-table-column prop="value" label="默认" width="90" />
            <el-table-column prop="desc" label="说明" />
          </el-table>
        </el-card>

        <!-- 故障排查 -->
        <el-card shadow="never" class="help-card">
          <div class="card-title"><span>🩺 常见问题</span></div>
          <el-collapse>
            <el-collapse-item title="核显加载模型报 UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY">
              <p style="line-height:1.8">新版 llama.cpp（b10488+）的 host pinned memory（#26789）在 iGPU 上触发 memcpy OOM。
              已默认通过 <code class="mono">GGML_SYCL_ENABLE_HOST_PINNED_MEM=0</code> 关闭，无需手动处理。
              若需临时开启：<code class="mono">docker exec llama-studio env GGML_SYCL_ENABLE_HOST_PINNED_MEM=1 ...</code></p>
            </el-collapse-item>
            <el-collapse-item title="升级后前端版本显示为 b0 / unknown">
              <p style="line-height:1.8">b10437+ 是 launcher 架构（<code class="mono">version: 0.1.0-dev (build NNNNN)</code>）。
              2026-08-25 已修复版本解析逻辑，若仍异常请刷新页面或重启容器。</p>
            </el-collapse-item>
            <el-collapse-item title="升级引擎后所有模型跑不起来">
              <p style="line-height:1.8">先回滚到上一个可用版本（设置 → 引擎管理 → 回滚），再重启容器。
              新版本建议先在 <code class="mono">--list-devices</code> + 单模型实测通过后再正式启用。</p>
            </el-collapse-item>
            <el-collapse-item title="实例状态 error / 端口被占">
              <p style="line-height:1.8">系统有自愈机制（退避重试 + 僵尸收割），等待 1-2 分钟自动恢复。
              若持续异常：查看实例日志定位，或使用「重启全部服务」总开关。</p>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshRight } from '@element-plus/icons-vue'
import { restartAllServices } from '../api'

const restartingAll = ref(false)

const envVars = [
  { name: 'GGML_SYCL_ENABLE_HOST_PINNED_MEM', value: '0', desc: 'host pinned memory（iGPU 需关，见故障排查）' },
  { name: 'ZES_ENABLE_SYSMAN', value: '1', desc: 'level-zero 系统管理（内存查询）' },
  { name: 'GGML_SYCL_ENABLE_FLASH_ATTN', value: '1', desc: 'Flash Attention 加速' },
  { name: 'GGML_SYCL_ENABLE_OPT', value: '1', desc: 'Intel GPU 优化特性（老显卡建议 0）' },
]

async function doRestartAll() {
  try {
    await ElMessageBox.confirm(
      '将重启所有已加载的模型实例（短暂中断推理服务），确认继续？',
      '重启全部服务', { confirmButtonText: '重启', cancelButtonText: '取消', type: 'warning' }
    )
  } catch (e) { return }
  restartingAll.value = true
  try {
    const r = await restartAllServices()
    const okCount = (r.restarted || []).filter(x => x.ok).length
    ElMessage.success(`已重启 ${okCount} 个服务` + (r.stopped?.length ? `（含 ${r.stopped.length} 个已停止）` : ''))
  } catch (e) {
    ElMessage.error('重启失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    restartingAll.value = false
  }
}
</script>

<style scoped>
.help-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%);
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 16px;
  color: #fff;
}
.hero-title { font-size: 20px; font-weight: 700; }
.hero-sub { font-size: 13px; opacity: 0.85; margin-top: 4px; }
.help-card { border-radius: 10px; margin-bottom: 16px; }
.cmd-block { margin-bottom: 12px; }
.cmd-title { font-size: 13px; color: #606266; margin-bottom: 6px; font-weight: 500; }
.cmd {
  display: block;
  font-family: 'JetBrains Mono', Consolas, 'Courier New', monospace;
  font-size: 12px;
  background: #0f172a;
  color: #a5f3fc;
  padding: 10px 12px;
  border-radius: 8px;
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
}
.cmd.multi { line-height: 1.7; }
.mono {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  background: #f4f4f5;
  padding: 2px 6px;
  border-radius: 4px;
}
</style>
