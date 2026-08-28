// 后端 API 统一入口。所有端点与 backend/app/routers/api.py 对应。

import { http } from './http'

const base = (path) => `/api${path}`

// ---- 鉴权 ----
export const login = (password = '', apiKey = '') =>
  http.post(base('/auth/login'), { password, api_key: apiKey })

// ---- 模型端点 / 视觉路由 / 压缩 ----
export const getModels = () => http.get(base('/models'))
export const saveModels = (payload) => http.put(base('/models'), payload)

export const getModelCatalog = () => http.get(base('/models/catalog'))
export const saveModelCatalog = (payload) => http.put(base('/models/catalog'), payload)

// ---- 执行环境 ----
export const getSandbox = () => http.get(base('/sandbox'))
export const saveSandbox = (payload) => http.put(base('/sandbox'), payload)

// ---- Skills ----
export const getSkills = () => http.get(base('/skills'))
export const toggleSkill = (name, enabled) => http.put(base(`/skills/${name}`), { enabled })
export const deleteSkill = (name) => http.del(base(`/skills/${encodeURIComponent(name)}`))
export const uploadSkillZip = (file) => http.upload(base('/skills/upload'), file)
export const getSkillFiles = (name) => http.get(base(`/skills/${encodeURIComponent(name)}/files`))
export const readSkillFile = (name, path) =>
  http.get(base(`/skills/${encodeURIComponent(name)}/files/${encodePath(path)}`))
export const writeSkillFile = (name, path, content) =>
  http.put(base(`/skills/${encodeURIComponent(name)}/files/${encodePath(path)}`), { content })

// ---- MCP ----
export const getMcpServers = () => http.get(base('/mcp'))
export const getMcpServerConfigs = () => http.get(base('/mcp/servers'))
export const createMcpServer = (cfg) => http.post(base('/mcp/servers'), cfg)
export const updateMcpServer = (name, cfg) =>
  http.put(base(`/mcp/servers/${encodeURIComponent(name)}`), cfg)
export const deleteMcpServer = (name) => http.del(base(`/mcp/servers/${encodeURIComponent(name)}`))
export const reconnectMcpServer = (name) =>
  http.post(base(`/mcp/servers/${encodeURIComponent(name)}/reconnect`))

// ---- 工具中心 ----
export const getToolsSnapshot = () => http.get(base('/tools'))
export const getCustomTools = () => http.get(base('/tools/custom'))
export const getCustomTool = (name) => http.get(base(`/tools/custom/${encodeURIComponent(name)}`))
export const createCustomTool = (tool) => http.post(base('/tools/custom'), tool)
export const updateCustomTool = (name, tool) =>
  http.put(base(`/tools/custom/${encodeURIComponent(name)}`), tool)
export const deleteCustomTool = (name) => http.del(base(`/tools/custom/${encodeURIComponent(name)}`))
export const toggleCustomTool = (name, enabled) =>
  http.put(base(`/tools/custom/${encodeURIComponent(name)}/enabled`), { enabled })
export const testCustomTool = (name, args) =>
  http.post(base(`/tools/custom/${encodeURIComponent(name)}/test`), { arguments: args })

// 文件路径按段编码，保留分隔符
function encodePath(path) {
  return String(path)
    .split('/')
    .map((seg) => encodeURIComponent(seg))
    .join('/')
}

// ---- 附件 ----
export const uploadImage = (file) => http.upload(base('/upload'), file)
export const fileUrl = (objectId) => base(`/files/${encodeURIComponent(objectId)}`)

// ---- 代码执行 ----
export const runCode = (code) => http.post(base('/run-code'), { code })

// ---- 压缩 ----
export const triggerCompress = (sessionId) =>
  http.post(base('/compress'), { session_id: sessionId })

// ---- 会话 ----
export const getSessions = () => http.get(base('/sessions'))
export const createSession = () => http.post(base('/sessions'))
export const getSession = (id) => http.get(base(`/sessions/${encodeURIComponent(id)}`))
export const deleteSession = (id) => http.del(base(`/sessions/${encodeURIComponent(id)}`))
export const archiveSession = (id) =>
  http.post(base(`/sessions/${encodeURIComponent(id)}/archive`))
export const renameSession = (id, title) =>
  http.post(base(`/sessions/${encodeURIComponent(id)}/rename`), { title })

export default {
  login,
  getModels,
  saveModels,
  getModelCatalog,
  saveModelCatalog,
  getSandbox,
  saveSandbox,
  getSkills,
  toggleSkill,
  deleteSkill,
  uploadSkillZip,
  getSkillFiles,
  readSkillFile,
  writeSkillFile,
  getMcpServers,
  getMcpServerConfigs,
  createMcpServer,
  updateMcpServer,
  deleteMcpServer,
  reconnectMcpServer,
  getToolsSnapshot,
  getCustomTools,
  createCustomTool,
  updateCustomTool,
  deleteCustomTool,
  toggleCustomTool,
  testCustomTool,
  uploadImage,
  fileUrl,
  runCode,
  triggerCompress,
  getSessions,
  createSession,
  getSession,
  deleteSession,
  archiveSession,
  renameSession
}
