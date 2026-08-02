<template>
  <div class="login-container">
    <div class="login-card">
      <div class="login-logo">
        <span class="logo-icon">⬢</span>
        <span>LLM Studio</span>
      </div>

      <!-- 未设置密码：显示设置密码表单 -->
      <template v-if="!authConfigured">
        <h3 class="login-title">设置管理员密码</h3>
        <p class="login-subtitle">首次使用，请设置管理密码</p>
        <el-form @submit.prevent="doSetup">
          <el-form-item>
            <el-input
              v-model="password"
              type="password"
              placeholder="设置密码（至少 4 位）"
              size="large"
              show-password
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
          <el-form-item>
            <el-button type="primary" size="large" style="width:100%" :loading="loading" @click="doSetup">
              设置并登录
            </el-button>
          </el-form-item>
        </el-form>
      </template>

      <!-- 已设置密码：显示登录表单 -->
      <template v-else>
        <h3 class="login-title">登录</h3>
        <p class="login-subtitle">请输入管理员密码</p>
        <el-form @submit.prevent="doLogin">
          <el-form-item>
            <el-input
              v-model="password"
              type="password"
              placeholder="密码"
              size="large"
              show-password
              @keyup.enter="doLogin"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" size="large" style="width:100%" :loading="loading" @click="doLogin">
              登录
            </el-button>
          </el-form-item>
        </el-form>
      </template>
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
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: #f5f7fa;
}
.login-card {
  width: 380px;
  padding: 40px 32px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 16px rgba(0, 0, 0, 0.08);
}
.login-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 24px;
  font-weight: 700;
  color: #409eff;
  margin-bottom: 24px;
}
.logo-icon { font-size: 28px; }
.login-title {
  text-align: center;
  margin: 0 0 8px;
  font-size: 18px;
  font-weight: 600;
}
.login-subtitle {
  text-align: center;
  color: #909399;
  font-size: 13px;
  margin-bottom: 24px;
}
</style>
