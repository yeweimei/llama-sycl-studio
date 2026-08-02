<template>
  <div class="login-page">
    <!-- 装饰背景光斑 -->
    <div class="bg-blob blob-1"></div>
    <div class="bg-blob blob-2"></div>
    <div class="bg-blob blob-3"></div>

    <div class="login-card">
      <div class="brand">
        <div class="brand-logo">
          <span class="logo-mark">⬢</span>
        </div>
        <h1 class="brand-name">LLM Studio</h1>
        <p class="brand-sub">llama.cpp SYCL 推理服务管理台</p>
      </div>

      <!-- 未设置密码：首次设置 -->
      <template v-if="!authConfigured">
        <div class="form-head">
          <h2 class="form-title">初始化管理员</h2>
          <p class="form-desc">首次使用，请设置管理密码</p>
        </div>
        <el-form @submit.prevent="doSetup">
          <el-form-item>
            <el-input
              v-model="password"
              type="password"
              placeholder="设置密码（至少 4 位）"
              size="large"
              show-password
              autofocus
              @keyup.enter="doSetup"
            />
          </el-form-item>
          <el-form-item>
            <el-input
              v-model="confirmPassword"
              type="password"
              placeholder="确认密码"
              size="large"
              show-password
              @keyup.enter="doSetup"
            />
          </el-form-item>
          <el-button type="primary" size="large" class="submit-btn" :loading="loading" @click="doSetup">
            设置并登录
          </el-button>
        </el-form>
      </template>

      <!-- 已设置密码：登录 -->
      <template v-else>
        <div class="form-head">
          <h2 class="form-title">欢迎回来</h2>
          <p class="form-desc">请输入管理员密码继续</p>
        </div>
        <el-form @submit.prevent="doLogin">
          <el-form-item>
            <el-input
              v-model="password"
              type="password"
              placeholder="管理员密码"
              size="large"
              show-password
              autofocus
              @keyup.enter="doLogin"
            />
          </el-form-item>
          <el-button type="primary" size="large" class="submit-btn" :loading="loading" @click="doLogin">
            登 录
          </el-button>
        </el-form>
      </template>

      <div class="login-footer">
        <span class="dot"></span>
        <span>NUC12 · A770M · 内网服务</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { authStatus, authSetup, authLogin } from '../api'

const router = useRouter()
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const authConfigured = ref(true)

onMounted(async () => {
  try {
    const s = await authStatus()
    authConfigured.value = s.configured
    if (s.authenticated) {
      router.push('/')
    }
  } catch {
    // ignore
  }
})

async function doSetup() {
  if (!password.value || password.value.length < 4) {
    ElMessage.warning('密码至少 4 位')
    return
  }
  if (password.value !== confirmPassword.value) {
    ElMessage.warning('两次密码不一致')
    return
  }
  loading.value = true
  try {
    const res = await authSetup(password.value)
    localStorage.setItem('auth_token', res.token)
    ElMessage.success('设置成功')
    router.push('/')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '设置失败')
  } finally {
    loading.value = false
  }
}

async function doLogin() {
  if (!password.value) {
    ElMessage.warning('请输入密码')
    return
  }
  loading.value = true
  try {
    const res = await authLogin(password.value)
    localStorage.setItem('auth_token', res.token)
    ElMessage.success('登录成功')
    router.push('/')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  overflow: hidden;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 45%, #0f172a 100%);
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

/* 背景光斑 */
.bg-blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  opacity: 0.35;
  pointer-events: none;
}
.blob-1 {
  width: 420px; height: 420px;
  background: #2563eb;
  top: -120px; left: -100px;
  animation: float 12s ease-in-out infinite;
}
.blob-2 {
  width: 380px; height: 380px;
  background: #7c3aed;
  bottom: -140px; right: -80px;
  animation: float 15s ease-in-out infinite reverse;
}
.blob-3 {
  width: 260px; height: 260px;
  background: #0891b2;
  top: 50%; left: 55%;
  opacity: 0.2;
  animation: float 18s ease-in-out infinite;
}
@keyframes float {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(30px, -30px) scale(1.08); }
}

/* 卡片 */
.login-card {
  position: relative;
  z-index: 1;
  width: 400px;
  padding: 44px 40px 28px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.07);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.45);
  animation: cardIn 0.5s ease-out;
}
@keyframes cardIn {
  from { opacity: 0; transform: translateY(24px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 品牌区 */
.brand {
  text-align: center;
  margin-bottom: 30px;
}
.brand-logo {
  width: 64px; height: 64px;
  margin: 0 auto 14px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 16px;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  box-shadow: 0 8px 24px rgba(59, 130, 246, 0.35);
}
.logo-mark {
  font-size: 32px;
  color: #fff;
  line-height: 1;
}
.brand-name {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 1px;
}
.brand-sub {
  margin: 6px 0 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.55);
}

/* 表单 */
.form-head { margin-bottom: 20px; }
.form-title {
  margin: 0 0 6px;
  font-size: 18px;
  font-weight: 600;
  color: #fff;
}
.form-desc {
  margin: 0;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.5);
}

:deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.08);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.15) inset;
  border-radius: 8px;
}
:deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.3) inset;
}
:deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #3b82f6 inset, 0 0 0 3px rgba(59, 130, 246, 0.25);
}
:deep(.el-input__inner) {
  color: #fff;
}
:deep(.el-input__inner::placeholder) {
  color: rgba(255, 255, 255, 0.4);
}
:deep(.el-input__password) {
  color: rgba(255, 255, 255, 0.6);
}

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 4px;
  border-radius: 8px;
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  border: none;
  box-shadow: 0 6px 20px rgba(59, 130, 246, 0.35);
  transition: transform 0.15s, box-shadow 0.15s;
}
.submit-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 26px rgba(59, 130, 246, 0.45);
}

/* 底部 */
.login-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 26px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.35);
}
.dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: #22c55e;
}
</style>
