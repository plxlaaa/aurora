// 基于 fetch 的轻量 API 封装：自动附加 Bearer token，统一错误处理。

const TOKEN_KEY = 'ai_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

// 401 回调（由 App 层注册，用于跳转登录）
let onUnauthorized = null
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}

async function request(method, url, body, formData = false) {
  const headers = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (body !== undefined && !formData) {
    headers['Content-Type'] = 'application/json'
  }

  const options = { method, headers }
  if (body !== undefined) {
    options.body = formData ? body : JSON.stringify(body)
  }

  const res = await fetch(url, options)
  if (res.status === 401 && onUnauthorized) {
    onUnauthorized()
  }
  if (!res.ok) {
    let detail = ''
    try {
      const parsed = await res.json()
      detail = parsed.detail || JSON.stringify(parsed)
    } catch (e) {
      detail = await res.text().catch(() => '')
    }
    throw new ApiError(detail || `${method} ${url} -> ${res.status}`, res.status)
  }
  if (res.status === 204) return null
  const text = await res.text()
  return text ? JSON.parse(text) : null
}

export const http = {
  get: (url) => request('GET', url),
  put: (url, body) => request('PUT', url, body),
  post: (url, body) => request('POST', url, body),
  del: (url) => request('DELETE', url),
  upload: (url, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('POST', url, fd, true)
  }
}
