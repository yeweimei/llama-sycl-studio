<template>
  <!-- 登录页：独立全屏，不带侧边栏布局 -->
  <router-view v-if="isLoginPage" />

  <el-container v-else class="layout">
    <!-- 桌面端侧边栏（≥768px） -->
    <el-aside width="210px" class="aside desktop-only">
      <div class="logo">
        <span class="logo-icon">⬢</span>
        <span>LLM Studio</span>
      </div>
      <el-menu :default-active="$route.path" router class="menu">
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>对话</span>
        </el-menu-item>
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
        <el-menu-item index="/stats">
          <el-icon><DataLine /></el-icon>
          <span>API统计</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
        <el-menu-item index="/help">
          <el-icon><HelpFilled /></el-icon>
          <span>帮助中心</span>
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
        <el-menu-item index="/stats">
          <el-icon><DataLine /></el-icon>
          <span>API统计</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
        <el-menu-item index="/help">
          <el-icon><HelpFilled /></el-icon>
          <span>帮助中心</span>
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
          <el-button size="small" type="danger" plain class="desktop-only" :loading="restartingAll" @click="doRestartAll">
            <el-icon style="margin-right:4px"><RefreshRight /></el-icon>重启全部
          </el-button>
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
import { Service, Files, Download, Monitor, Setting, SwitchButton, Expand, DataLine, ChatDotRound, HelpFilled, RefreshRight } from '@element-plus/icons-vue'
import { authLogout, restartAllServices } from './api'
import { ElMessage, ElMessageBox } from 'element-plus'

const router = useRouter()
const now = ref(new Date().toLocaleString('zh-CN'))
const drawerVisible = ref(false)
const restartingAll = ref(false)
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

async function doRestartAll() {
  try {
    await ElMessageBox.confirm(
      '将重启所有已加载的模型实例，推理服务会短暂中断，确认继续？',
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

onMounted(() => { timer = setInterval(() => { now.value = new Date().toLocaleString('zh-CN') }, 1000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.layout {
  min-height: 100vh;
  height: 100vh;
  overflow: hidden;
}
.aside {
  height: 100vh;
  overflow-y: auto;
  overflow-x: hidden;
  background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
  border-right: none;
  box-shadow: 2px 0 12px rgba(15, 23, 42, 0.12);
}
/* 内层容器（header + main）占满剩余高度，禁止页面级滚动 */
.layout > .el-container {
  height: 100vh;
  overflow: hidden;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 18px;
  font-weight: 700;
  color: #60a5fa;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  letter-spacing: 0.5px;
}
.logo-icon { font-size: 22px; }
.menu {
  border-right: none;
  background: transparent;
  padding: 8px;
}
.menu :deep(.el-menu-item) {
  color: rgba(226, 232, 240, 0.85);
  border-radius: 8px;
  margin-bottom: 2px;
  height: 44px;
  line-height: 44px;
}
.menu :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.menu :deep(.el-menu-item.is-active) {
  background: linear-gradient(90deg, #2563eb, #3b82f6);
  color: #fff;
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35);
}
.header {
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  padding: 0 20px;
}
.header-title { font-size: 16px; font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.header-right { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.header-time { color: #909399; font-size: 13px; }
.hamburger-btn { display: none; padding: 4px 8px; }
.main {
  background: linear-gradient(180deg, #f1f5f9 0%, #f8fafc 100%);
  padding: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

/* 移动端抽屉样式 */
:deep(.mobile-drawer .el-drawer__body) {
  padding: 0;
  overflow-y: auto;
}
:deep(.mobile-drawer) {
  background: #0f172a;
}
:deep(.mobile-drawer .logo) {
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
:deep(.mobile-drawer .el-menu) {
  background: transparent;
}

/* 响应式：<768px 隐藏桌面侧边栏，显示汉堡按钮 */
@media (max-width: 767px) {
  .desktop-only { display: none !important; }
  .hamburger-btn { display: inline-flex; }
  .header { padding: 0 12px; }
}
</style>
