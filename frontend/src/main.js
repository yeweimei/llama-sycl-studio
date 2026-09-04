import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./views/Login.vue'), meta: { title: '登录', public: true } },
    { path: '/', redirect: '/chat' },
    { path: '/chat', component: () => import('./views/Chat.vue'), meta: { title: '对话' } },
    { path: '/services', component: () => import('./views/Services.vue'), meta: { title: '服务管理' } },
    { path: '/services/:id', component: () => import('./views/ServiceDetail.vue'), meta: { title: '服务详情' } },
    { path: '/models', component: () => import('./views/Models.vue'), meta: { title: '模型中心' } },
    { path: '/downloads', component: () => import('./views/Downloads.vue'), meta: { title: '模型下载' } },
    { path: '/monitor', component: () => import('./views/Monitor.vue'), meta: { title: '系统监控' } },
    { path: '/stats', component: () => import('./views/Stats.vue'), meta: { title: 'API统计' } },
    { path: '/settings', component: () => import('./views/Settings.vue'), meta: { title: '设置' } },
    { path: '/help', component: () => import('./views/Help.vue'), meta: { title: '帮助中心' } },
  ],
})

// 全局路由守卫：未认证跳 /login
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  const token = localStorage.getItem('auth_token')
  if (!token) {
    return '/login'
  }
  return true
})

const app = createApp(App)
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
