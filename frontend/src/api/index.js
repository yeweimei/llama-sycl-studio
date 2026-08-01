import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

// ---------- 服务 ----------
export const listServices = () => api.get('/services').then(r => r.data)
export const getService = (id) => api.get(`/services/${id}`).then(r => r.data)
export const createService = (data) => api.post('/services', data).then(r => r.data)
export const updateService = (id, data) => api.put(`/services/${id}`, data).then(r => r.data)
export const startService = (id) => api.post(`/services/${id}/start`).then(r => r.data)
export const stopService = (id) => api.post(`/services/${id}/stop`).then(r => r.data)
export const restartService = (id) => api.post(`/services/${id}/restart`).then(r => r.data)
export const cloneService = (id, name) => api.post(`/services/${id}/clone`, null, { params: { name } }).then(r => r.data)
export const deleteService = (id) => api.delete(`/services/${id}`).then(r => r.data)
export const getServiceLogs = (id, tail = 200) => api.get(`/services/${id}/logs`, { params: { tail } }).then(r => r.data)
export const getParamSchema = () => api.get('/services/params/schema').then(r => r.data)
export const chatProxy = (id, data) => api.post(`/services/${id}/chat`, data).then(r => r.data)
export const clientConfig = (id) => api.get(`/services/${id}/client-config`).then(r => r.data)

// ---------- 模型 ----------
export const listModels = () => api.get('/models').then(r => r.data)
export const deleteModel = (path) => api.delete('/models', { params: { path } }).then(r => r.data)

// ---------- 下载 ----------
export const listSources = () => api.get('/downloads/sources').then(r => r.data)
export const listRepoFiles = (data) => api.post('/downloads/list-files', data).then(r => r.data)
export const startDownload = (data) => api.post('/downloads', data).then(r => r.data)
export const listTasks = () => api.get('/downloads/tasks').then(r => r.data)
export const taskProgress = (tid) => api.get(`/downloads/tasks/${tid}/progress`).then(r => r.data)
export const cancelTask = (tid) => api.delete(`/downloads/tasks/${tid}`).then(r => r.data)

// ---------- 监控 ----------
export const gpuStatus = () => api.get('/gpu').then(r => r.data)
export const systemStatus = () => api.get('/gpu/system').then(r => r.data)

// ---------- 设置 ----------
export const listApiKeys = () => api.get('/settings/api-keys').then(r => r.data)
export const createApiKey = (name) => api.post('/settings/api-keys', { name }).then(r => r.data)
export const deleteApiKey = (id) => api.delete(`/settings/api-keys/${id}`).then(r => r.data)
export const toggleApiKey = (id) => api.post(`/settings/api-keys/${id}/toggle`).then(r => r.data)
export const listTemplates = () => api.get('/settings/templates').then(r => r.data)
export const createTemplate = (data) => api.post('/settings/templates', data).then(r => r.data)
export const deleteTemplate = (id) => api.delete(`/settings/templates/${id}`).then(r => r.data)
export const listImages = () => api.get('/settings/images').then(r => r.data)
export const imageVersions = () => api.get('/settings/image-versions').then(r => r.data)
export const pullImage = (tag) => api.post('/settings/images/pull', null, { params: { tag } }).then(r => r.data)

export default api
