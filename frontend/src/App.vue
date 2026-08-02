<template>
  <!-- 登录页：独立全屏，不带侧边栏布局 -->
  <router-view v-if="isLoginPage" />

  <el-container v-else class="layout">
    <!-- 桌面端侧边栏（≥768px） -->
    <el-aside width="200px" class="aside desktop-only">
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

    <!-- 移动端抽屉侧边栏（<768px） -->
    <el-drawer
      v-model="drawerVisible"
      direction="ltr"
      :with-header="false"
      size="240px"
      class="mobile-drawer"
    >
      <div class="logo">
        <span class="logo-icon">⬢</span>
        <span>LLM Studio</span>
      </div>
      <el-menu :default-active="$route.path" router class="menu" @select="drawerVisible = false">
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
    </el-drawer>

    <el-container>
      <el-header class="header">
        <!-- 移动端汉堡按钮 -->
        <el-button class="hamburger-btn" text @click="drawerVisible = true">
          <el-icon size="22"><Expand /></el-icon>
        </el-button>
        <div class="header-title">{{ $route.meta.title || 'LLM 推理服务管理台' }}</div>
        <div class="header-right">
          <el-tag size="small" type="success" class="desktop-only">NUC12</el-tag>
          <span class="header-time desktop-only">{{ now }}</span>
          <el-button size="small" text @click="doLogout">
            <el-icon><SwitchButton /></el-icon>
            <span class="desktop-only">退出</span>
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
import { Service, Files, Download, Monitor, Setting, SwitchButton, Expand } from '@element-plus/icons-vue'
import { authLogout } from './api'

const router = useRouter()
const now = ref(new Date().toLocaleString('zh-CN'))
const drawerVisible = ref(false)
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
  gap: 8px;
}
.header-title { font-size: 16px; font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.header-right { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.header-time { color: #909399; font-size: 13px; }
.hamburger-btn { display: none; padding: 4px 8px; }
.main { background: #f5f7fa; padding: 0; }

/* 移动端抽屉样式 */
:deep(.mobile-drawer .el-drawer__body) {
  padding: 0;
}

/* 响应式：<768px 隐藏桌面侧边栏，显示汉堡按钮 */
@media (max-width: 767px) {
  .desktop-only { display: none !important; }
  .hamburger-btn { display: inline-flex; }
  .header { padding: 0 12px; }
}
</style>
