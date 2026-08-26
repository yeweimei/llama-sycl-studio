import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 120000 })

// ---------- 请求拦截器：自动带 token ----------
api.interceptors.request.use(config => {
  const token = localStorage.getItem('auth_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ---------- 响应拦截器：401 跳登录（带防抖守卫） ----------
let redirecting = false
api.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('auth_token')
      // 防抖：避免多个并发请求同时触发跳转
      if (!redirecting && window.location.pathname !== '/login') {
        redirecting = true
        window.location.href = '/login'
      }
      // 额外延迟重置，防止跳转过程中排队的请求再次触发
      setTimeout(() => { redirecting = false }, 2000)
    }
    return Promise.reject(err)
  }
)

// ---------- 认证 ----------
export const authStatus = () => api.get('/auth/status').then(r => r.data)
export const authSetup = (password) => api.post('/auth/setup', { password }).then(r => r.data)
export const authLogin = (password) => api.post('/auth/login', { password }).then(r => r.data)
export const authLogout = () => api.post('/auth/logout').then(r => r.data)
export const authChangePassword = (oldPassword, newPassword) => api.post('/auth/change-password', { old_password: oldPassword, new_password: newPassword }).then(r => r.data)

// ---------- 模型池管理 ----------
export const listServices = () => api.get('/services').then(r => r.data)
export const getService = (id) => api.get(`/services/${id}`).then(r => r.data)
export const createService = (data) => api.post('/services', data).then(r => r.data)
export const updateService = (id, data) => api.put(`/services/${id}`, data).then(r => r.data)
export const startService = (id) => api.post(`/services/${id}/start`).then(r => r.data)
export const stopService = (id) => api.post(`/services/${id}/stop`).then(r => r.data)
export const deleteService = (id) => api.delete(`/services/${id}`).then(r => r.data)
export const restartService = (id) => api.post(`/services/${id}/restart`).then(r => r.data)
export const getServiceLogs = (id, tail = 200, since = null, until = null) => {
  const params = { tail }
  if (since) params.since = since
  if (until) params.until = until
  return api.get(`/services/${id}/logs`, { params }).then(r => r.data)
}
// 对话日志（chat_api_logs）
export const getChatLogs = (model, limit = 200) => {
  const params = { limit }
  if (model) params.model = model
  return api.get('/stats/chat-logs', { params }).then(r => r.data)
}
export const getChatLogModels = () => api.get('/stats/chat-log-models').then(r => r.data)
export const clearChatLogs = (model) => {
  const params = {}
  if (model) params.model = model
  return api.delete('/stats/chat-logs', { params }).then(r => r.data)
}
export const getParamSchema = () => api.get('/services/params/schema').then(r => r.data)
export const chatProxy = (id, data) => api.post(`/services/${id}/chat`, data).then(r => r.data)
export const clientConfig = (id) => api.get(`/services/${id}/client-config`).then(r => r.data)
export const routerStatus = () => api.get('/services/router/status').then(r => r.data)

// ---------- 模型 ----------
export const listModels = () => api.get('/models').then(r => r.data)
export const deleteModel = (path) => api.delete('/models', { params: { path } }).then(r => r.data)

// ---------- 下载 ----------
export const listSources = () => api.get('/downloads/sources').then(r => r.data)
export const searchModels = (data) => api.post('/downloads/search', data).then(r => r.data)
export const listRepoFiles = (data) => api.post('/downloads/list-files', data).then(r => r.data)
export const startDownload = (data) => api.post('/downloads', data).then(r => r.data)
export const listTasks = () => api.get('/downloads/tasks').then(r => r.data)
export const taskProgress = (tid) => api.get(`/downloads/tasks/${tid}/progress`).then(r => r.data)
export const cancelTask = (tid) => api.delete(`/downloads/tasks/${tid}`).then(r => r.data)
export const pauseTask = (tid) => api.post(`/downloads/tasks/${tid}/pause`).then(r => r.data)
export const resumeTask = (tid) => api.post(`/downloads/tasks/${tid}/resume`).then(r => r.data)
export const retryTask = (tid) => api.post(`/downloads/tasks/${tid}/retry`).then(r => r.data)
export const deleteTask = (tid) => api.delete(`/downloads/tasks/${tid}`).then(r => r.data)

// ---------- 监控 ----------
export const gpuStatus = () => api.get('/gpu').then(r => r.data)
export const getSelectableGpus = () => api.get('/gpu/selectable').then(r => r.data)
export const systemStatus = () => api.get('/gpu/system').then(r => r.data)
export const gatewayHealth = () => api.get('/services/gateway/health').then(r => r.data)
export const getAlertConfig = () => api.get('/settings/alert').then(r => r.data)
export const saveAlertConfig = (cfg) => api.put('/settings/alert', cfg).then(r => r.data)
export const testAlert = () => api.post('/settings/alert/test').then(r => r.data)
export const estimateMemory = (payload) => api.post('/presets/estimate-memory', payload).then(r => r.data)

// ---------- 设置 ----------
export const listApiKeys = () => api.get('/settings/api-keys').then(r => r.data)
export const createApiKey = (name) => api.post('/settings/api-keys', { name }).then(r => r.data)
export const deleteApiKey = (id) => api.delete(`/settings/api-keys/${id}`).then(r => r.data)
export const toggleApiKey = (id) => api.post(`/settings/api-keys/${id}/toggle`).then(r => r.data)
export const listTemplates = () => api.get('/settings/templates').then(r => r.data)
export const createTemplate = (data) => api.post('/settings/templates', data).then(r => r.data)
export const deleteTemplate = (id) => api.delete(`/settings/templates/${id}`).then(r => r.data)
export const getProxySettings = () => api.get('/settings/proxy').then(r => r.data)
export const saveProxySettings = (data) => api.put('/settings/proxy', data).then(r => r.data)
export const containerInfo = () => api.get('/settings/container-info').then(r => r.data)
export const getRouterCtx = () => api.get('/settings/router-ctx').then(r => r.data)

// ---------- 模型预设 ----------
export const listPresets = () => api.get('/presets').then(r => r.data)
export const createPreset = (data) => api.post('/presets', data).then(r => r.data)
export const updatePreset = (id, data) => api.put(`/presets/${id}`, data).then(r => r.data)
export const deletePreset = (id) => api.delete(`/presets/${id}`).then(r => r.data)
export const generateConfigIni = () => api.post('/presets/generate-config').then(r => r.data)

// ---------- 模型标签 ----------
export const listModelTags = () => api.get('/model-tags').then(r => r.data)
export const getModelTags = (name) => api.get(`/model-tags/${encodeURIComponent(name)}`).then(r => r.data)
export const updateModelTags = (name, data) => api.put(`/model-tags/${encodeURIComponent(name)}`, data).then(r => r.data)
export const autoModelTags = (name) => api.post(`/model-tags/${encodeURIComponent(name)}/auto`).then(r => r.data)

// ---------- API 统计 ----------
export const getStats = () => api.get('/stats').then(r => r.data)
export const getStatsTrends = (hours = 24, bucketMinutes = 60) => api.get('/stats/trends', { params: { hours, bucket_minutes: bucketMinutes } }).then(r => r.data)
export const getEndpointStats = (hours = 0) => api.get('/stats/endpoints', { params: { hours } }).then(r => r.data)
export const getRecentRequests = (limit = 50) => api.get('/stats/requests', { params: { limit } }).then(r => r.data)

// ---------- 聊天历史 ----------
export const getChatHistory = (sid, sessionId = 0) => api.get(`/services/${sid}/history`, { params: { session_id: sessionId } }).then(r => r.data)
export const addChatHistory = (sid, data) => api.post(`/services/${sid}/history`, data).then(r => r.data)
export const clearChatHistory = (sid, sessionId = 0) => api.delete(`/services/${sid}/history`, { params: { session_id: sessionId } }).then(r => r.data)
export const deleteHistoryItem = (sid, historyId) => api.delete(`/services/${sid}/history/${historyId}`).then(r => r.data)

// ---------- 聊天会话 ----------
export const listSessions = (sid) => api.get(`/services/${sid}/sessions`).then(r => r.data)
export const createSession = (sid, data = {}) => api.post(`/services/${sid}/sessions`, data).then(r => r.data)
export const renameSession = (sid, sessionId, data) => api.patch(`/services/${sid}/sessions/${sessionId}`, data).then(r => r.data)
export const deleteSession = (sid, sessionId) => api.delete(`/services/${sid}/sessions/${sessionId}`).then(r => r.data)
export const parsePdf = (sid, file) => {
  const formData = new FormData()
  formData.append('file', file)
  return api.post(`/services/${sid}/parse-pdf`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}

// ---------- 引擎管理 ----------
export const getEngineVersion = () => api.get('/engine/version').then(r => r.data)
export const getEngineUpgrades = () => api.get('/engine/upgrades').then(r => r.data)
export const upgradeEngine = (version, flavor = 'sycl-fp16') => api.post('/engine/upgrade', { version, flavor }).then(r => r.data)
export const rollbackEngine = (version) => api.post('/engine/rollback', { version }).then(r => r.data)
export const cleanupEngine = (keep = 3, dryRun = false) => api.post('/engine/cleanup', { keep, dry_run: dryRun }).then(r => r.data)
export const restartAllServices = () => api.post('/services/restart-all').then(r => r.data)

export default api
