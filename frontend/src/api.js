// 基于 fetch 的轻量 API 封装，自动附加可选的 Authorization Bearer token。
// 支持的接口：
//   GET/PUT  /api/models    模型端点配置
//   GET/PUT  /api/sandbox   执行环境配置
//   GET      /api/sessions  会话列表
//   POST     /api/sessions  新建会话
//   DELETE   /api/sessions/:id  删除会话
//   POST     /api/upload    上传图片，返回 { object_id, ... }
//   POST     /api/compress  手动触发上下文压缩
//   GET      /api/skills    可用技能列表

const token = localStorage.getItem('ai_token') || ''

async function request(method, url, body) {
  const headers = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const options = { method, headers }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }

  const res = await fetch(url, options)
  if (!res.ok) {
    let detail = ''
    try {
      detail = await res.text()
    } catch (e) {
      /* ignore */
    }
    throw new Error(`${method} ${url} -> ${res.status} ${detail}`)
  }
  // DELETE 或 204 可能无返回体
  if (res.status === 204) return null
  const text = await res.text()
  return text ? JSON.parse(text) : null
}

const get = (url) => request('GET', url)
const put = (url, body) => request('PUT', url, body)
const post = (url, body) => request('POST', url, body)
const del = (url) => request('DELETE', url)

export function getModels() {
  return get('/api/models')
}

export function saveModels(payload) {
  return put('/api/models', payload)
}

export function getSandbox() {
  return get('/api/sandbox')
}

export function saveSandbox(payload) {
  return put('/api/sandbox', payload)
}

export function getSessions() {
  return get('/api/sessions')
}

export function createSession() {
  return post('/api/sessions')
}

export function deleteSession(id) {
  return del(`/api/sessions/${encodeURIComponent(id)}`)
}

// 上传图片文件，返回后端给出的 { object_id, ... }
export function uploadImage(file) {
  const form = new FormData()
  form.append('file', file)
  const headers = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  return fetch('/api/upload', { method: 'POST', headers, body: form }).then(
    async (res) => {
      if (!res.ok) throw new Error(`POST /api/upload -> ${res.status}`)
      return res.json()
    }
  )
}

export function triggerCompress(sessionId) {
  return post('/api/compress', { session_id: sessionId })
}

export function getSkills() {
  return get('/api/skills')
}

export default {
  getModels,
  saveModels,
  getSandbox,
  saveSandbox,
  getSessions,
  createSession,
  deleteSession,
  uploadImage,
  triggerCompress,
  getSkills
}