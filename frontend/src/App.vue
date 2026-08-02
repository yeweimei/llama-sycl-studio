<template>
  <!-- 登录页：独立全屏，不带侧边栏布局 -->
  <router-view v-if="isLoginPage" />

  <el-container v-else class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">
        <span class="logo-icon">⬢</span>
        <span>LLM Studio</span>
      </div>
      <el-menu :default-active="$route.path" router class="menu">
        <el-menu-item index="/services">
          <el-icon><Service /></el-icon>
          <span>服务管理</span>
        </el-menu-item>
        <el-menu-item index="/models">
          <el-icon><Files /></el-icon>
          <span>模型中心</span>
        </el-menu-item>
        <el-menu-item index="/downloads">
          <el-icon><Download /></el-icon>
          <span>模型下载</span>
        </el-menu-item>
        <el-menu-item index="/monitor">
          <el-icon><Monitor /></el-icon>
          <span>系统监控</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">{{ $route.meta.title || 'LLM 推理服务管理台' }}</div>
        <div class="header-right">
          <el-tag size="small" type="success">NUC12</el-tag>
          <span class="header-time">{{ now }}</span>
          <el-button size="small" text @click="doLogout">
            <el-icon><SwitchButton /></el-icon>
            退出
          </el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
        </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Service, Files, Download, Monitor, Setting, SwitchButton } from '@element-plus/icons-vue'
import { authLogout } from './api'

const router = useRouter()
const now = ref(new Date().toLocaleString('zh-CN'))
let timer = null

// 登录页不显示侧边栏布局
const isLoginPage = computed(() => router.currentRoute.value.path === '/login')

async function doLogout() {
  try {
    await authLogout()
  } catch {
    // ignore
  }
  localStorage.removeItem('auth_token')
  router.push('/login')
}

onMounted(() => { timer = setInterval(() => { now.value = new Date().toLocaleString('zh-CN') }, 1000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.layout { min-height: 100vh; }
.aside {
  background: #fff;
  border-right: 1px solid #e4e7ed;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 18px;
  font-weight: 700;
  color: #409eff;
  border-bottom: 1px solid #e4e7ed;
}
.logo-icon { font-size: 22px; }
.menu { border-right: none; }
.header {
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-title { font-size: 16px; font-weight: 600; }
.header-right { display: flex; align-items: center; gap: 12px; }
.header-time { color: #909399; font-size: 13px; }
.main { background: #f5f7fa; padding: 0; }
</style>
