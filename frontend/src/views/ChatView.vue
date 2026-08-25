<template>
  <div class="chat-view">
    <div v-if="notice.text" class="notice" :class="notice.type">{{ notice.text }}</div>
    <!-- 会话侧栏 -->
    <aside class="sidebar">
      <button class="primary new-btn" @click="createNewSession">＋ 新建会话</button>

      <div class="conn-status">
        <span class="dot" :class="{ connected: wsState === 'connected' }"></span>
        <span class="label">{{ wsText }}</span>
      </div>

      <ul class="session-list">
        <li
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentSessionId }"
          @click="switchSession(s.id)"
        >
          <span class="s-name">{{ s.title || '未命名会话' }}</span>
          <span v-if="s.status === 'running'" class="s-status" title="处理中">◔</span>
          <button
            class="s-action"
            title="归档会话"
            @click.stop="archive(s.id)"
          >
            📦
          </button>
          <button class="s-action danger" title="删除会话" @click.stop="remove(s.id)">
            ✕
          </button>
        </li>
      </ul>

      <button
        class="compress-btn"
        :disabled="!currentSessionId || streaming"
        @click="compressNow"
      >
        ⤷ 压缩上下文
      </button>
    </aside>

    <!-- 主对话区 -->
    <section class="main">
      <!-- 工具栏：模型 / 思考强度 / 连接 -->
      <div class="chat-toolbar">
        <label class="tool-item">
          <span>模型</span>
          <select v-model="currentModel" :disabled="!currentModelOptions.length" @change="onSwitchModel">
            <option v-for="opt in currentModelOptions" :key="opt" :value="opt">
              {{ opt }}
            </option>
          </select>
        </label>
        <label class="tool-item">
          <span>思考强度</span>
          <select v-model="thinkingLevel" @change="onSetThinking">
            <option value="low">低</option>
            <option value="normal">中</option>
            <option value="high">高</option>
          </select>
        </label>
        <div class="toolbar-spacer"></div>
        <span class="conn-state" :class="wsState">
          {{ wsState === 'connected' ? '●' : wsState === 'connecting' ? '◔' : '○' }}
          {{ wsText }}
        </span>
      </div>

      <!-- 消息流 -->
      <div ref="scroller" class="messages">
        <div v-if="!messages.length" class="empty">
          <p>在下方输入内容开始对话，可附带图片。</p>
          <p class="hint">支持视觉理解、上下文压缩、Skills 与 MCP 工具。</p>
        </div>

        <MessageItem
          v-for="m in messages"
          :key="m.id"
          :message="m"
          :streaming="streaming && m.id === lastMessageId"
        />
      </div>

      <!-- 输入区 -->
      <div class="composer">
        <div v-if="pendingAttachments.length" class="pending-att">
          <div v-for="(a, i) in pendingAttachments" :key="i" class="pending-thumb-wrap">
            <img :src="a.preview" class="att-thumb" alt="待发送" />
            <button class="remove-att" title="移除" @click="removeAttachment(i)">✕</button>
          </div>
        </div>

        <div class="composer-row">
          <button
            class="attach-btn"
            :disabled="streaming"
            title="添加图片"
            @click="pickImage"
          >
            ＋
          </button>
          <input
            type="file"
            ref="fileInput"
            accept="image/*"
            hidden
            @change="onFileChange"
          />
          <textarea
            v-model="input"
            class="input-area"
            rows="1"
            placeholder="输入消息，回车发送"
            @keydown.enter.exact.prevent="send"
            @input="autoGrow"
          ></textarea>
          <button
            class="primary send-btn"
            :disabled="streaming || (!input.trim() && !pendingAttachments.length)"
            @click="send"
          >
            {{ streaming ? '生成中…' : '发送' }}
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import {
  getSessions,
  createSession,
  getSession,
  deleteSession,
  archiveSession,
  uploadImage,
  triggerCompress,
  getModels,
  fileUrl
} from '../api'
import { getToken } from '../api/http'
import MessageItem from '../components/MessageItem.vue'

const messages = ref([])
const sessions = ref([])
const currentSessionId = ref(null)
const input = ref('')
const pendingAttachments = ref([])
const streaming = ref(false)
const wsState = ref('disconnected')
const currentModel = ref('')
const thinkingLevel = ref('normal')
const currentModelOptions = ref([])

const fileInput = ref(null)
const scroller = ref(null)

let socket = null
let msgSeq = 0
let socketSessionId = null
let manualClose = false
let reconnectTimer = null

const lastMessageId = computed(() =>
  messages.value.length ? messages.value[messages.value.length - 1].id : null
)

const wsText = computed(() => {
  if (wsState.value === 'connected') return '已连接'
  if (wsState.value === 'connecting') return '连接中'
  return '未连接'
})

const wsUrl = computed(() => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = getToken()
  return `${proto}://${location.host}/api/chat?token=${encodeURIComponent(token)}`
})

function nextId() {
  msgSeq += 1
  return `m-${msgSeq}`
}

function scrollToBottom() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

function autoGrow(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 140) + 'px'
}

/* ---------- 历史消息归一化 ---------- */
function normalizeHistory(raw) {
  const out = []
  for (const m of raw || []) {
    const role = m.role
    if (role === 'assistant_delta') {
      const last = out[out.length - 1]
      const text = m.content || ''
      if (last && last.role === 'assistant') last.content += text
      else if (text) out.push({ id: nextId(), role: 'assistant', content: text })
    } else if (role === 'tool_call') {
      out.push({
        id: nextId(),
        role: 'tool_call',
        name: m.name || '工具',
        status: m.status || 'done',
        arguments: m.arguments,
        result: m.result
      })
    } else if (role === 'tool') {
      out.push({ id: nextId(), role: 'tool', name: m.name || '工具', content: m.content })
    } else if (role === 'user') {
      const attachments = (m.attachments || [])
        .filter((a) => typeof a === 'string')
        .map((id) => ({ object_id: id }))
      out.push({ id: nextId(), role: 'user', content: m.content || '', attachments })
    } else if (role === 'assistant' || role === 'thinking') {
      out.push({ id: nextId(), role, content: m.content || '' })
    } else {
      out.push({ id: nextId(), role: 'assistant', content: m.content || '' })
    }
  }
  return out
}

/* ---------- WebSocket ---------- */
function connectWS(sessionId) {
  if (socket && socket.readyState === WebSocket.OPEN && socketSessionId === sessionId) return
  closeSocket()
  manualClose = false
  socketSessionId = sessionId
  wsState.value = 'connecting'
  socket = new WebSocket(`${wsUrl.value}&session_id=${encodeURIComponent(sessionId)}`)
  socket.onopen = () => {
    wsState.value = 'connected'
  }
  socket.onclose = () => {
    wsState.value = 'disconnected'
    streaming.value = false
    socketSessionId = null
    socket = null
    if (!manualClose) {
      reconnectTimer = setTimeout(() => {
        if (currentSessionId.value) connectWS(currentSessionId.value)
      }, 3000)
    }
  }
  socket.onerror = () => {
    wsState.value = 'disconnected'
  }
  socket.onmessage = (ev) => handleMessage(ev.data)
}

function closeSocket() {
  manualClose = true
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (socket) {
    try {
      socket.onclose = null
      socket.close()
    } catch (e) {
      /* ignore */
    }
    socket = null
  }
  socketSessionId = null
}

function handleMessage(raw) {
  let data
  try {
    data = JSON.parse(raw)
  } catch (e) {
    appendDelta(raw)
    return
  }
  if (!data || typeof data !== 'object') return
  const type = data.type || 'delta'
  switch (type) {
    case 'delta':
      appendDelta(data.content || data.text || '')
      break
    case 'assistant_message':
      messages.value.push({ id: nextId(), role: 'assistant', content: data.content || '' })
      break
    case 'thinking':
      appendThinking(data.text || data.content || '')
      break
    case 'tool_call':
      upsertToolCall(data)
      break
    case 'tool':
      upsertToolResult(data)
      break
    case 'info':
      messages.value.push({ id: nextId(), role: 'info', content: data.text || '' })
      break
    case 'error':
      streaming.value = false
      messages.value.push({ id: nextId(), role: 'error', content: data.message || '未知错误' })
      break
    case 'done':
    case 'end':
    case 'finished':
      streaming.value = false
      break
    default:
      if (data.content && data.role !== 'user') appendDelta(data.content)
      break
  }
  scrollToBottom()
}

function appendDelta(text) {
  if (!text) return
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant' && streaming.value) {
    last.content += text
  } else {
    messages.value.push({ id: nextId(), role: 'assistant', content: text })
  }
  streaming.value = true
}

function appendThinking(text) {
  if (!text) return
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'thinking') last.content += text
  else messages.value.push({ id: nextId(), role: 'thinking', content: text })
}

function upsertToolCall(data) {
  const name = data.name || '工具'
  const existing = messages.value.find(
    (m) => m.role === 'tool_call' && m.name === name && m.status === 'running'
  )
  if (existing) {
    existing.status = data.status || existing.status
    if (data.arguments !== undefined) existing.arguments = data.arguments
    if (data.result !== undefined) existing.result = data.result
  } else {
    messages.value.push({
      id: nextId(),
      role: 'tool_call',
      name,
      status: data.status || 'running',
      arguments: data.arguments,
      result: data.result
    })
  }
}

function upsertToolResult(data) {
  const id = data.tool_call_id
  const existing = messages.value.find(
    (m) => m.role === 'tool_call' && (m.tool_call_id === id || (id && m.name === data.name))
  )
  if (existing) {
    existing.status = 'done'
    if (data.content !== undefined) existing.result = data.content
  } else {
    messages.value.push({
      id: nextId(),
      role: 'tool',
      name: data.name || '工具',
      content: data.content || ''
    })
  }
}

/* ---------- 会话管理 ---------- */
async function loadSessions() {
  try {
    const res = await getSessions()
    sessions.value = res?.sessions || []
  } catch (e) {
    sessions.value = []
    notify(e.message, 'error')
  }
}

async function createNewSession() {
  try {
    const res = await createSession()
    const id = res.id || res.session_id
    await loadSessions()
    await switchSession(id)
  } catch (e) {
    notify('新建会话失败：' + e.message, 'error')
  }
}

async function switchSession(id) {
  currentSessionId.value = id
  streaming.value = false
  messages.value = []
  try {
    const res = await getSession(id)
    messages.value = normalizeHistory(res?.messages || [])
  } catch (e) {
    messages.value = []
  }
  connectWS(id)
  scrollToBottom()
}

async function remove(id) {
  if (!window.confirm('确定删除该会话？')) return
  try {
    await deleteSession(id)
    await loadSessions()
    if (currentSessionId.value === id) {
      currentSessionId.value = null
      messages.value = []
      closeSocket()
    }
  } catch (e) {
    notify('删除会话失败：' + e.message, 'error')
  }
}

async function archive(id) {
  try {
    await archiveSession(id)
    await loadSessions()
    if (currentSessionId.value === id) {
      currentSessionId.value = null
      messages.value = []
      closeSocket()
    }
  } catch (e) {
    notify('归档失败：' + e.message, 'error')
  }
}

async function compressNow() {
  if (!currentSessionId.value) return
  try {
    await triggerCompress(currentSessionId.value)
    notify('上下文已压缩', 'ok')
    await switchSession(currentSessionId.value)
  } catch (e) {
    notify('压缩失败：' + e.message, 'error')
  }
}

/* ---------- 模型与思考强度 ---------- */
async function loadModelsMeta() {
  try {
    const data = await getModels()
    const endpoints = data?.endpoints || {}
    const opts = []
    for (const [key, ep] of Object.entries(endpoints)) {
      if (ep.enabled && ep.model_name) opts.push(ep.model_name)
    }
    currentModelOptions.value = [...new Set(opts)]
    const main = endpoints.main
    currentModel.value = main?.model_name || currentModelOptions.value[0] || ''
  } catch (e) {
    currentModelOptions.value = []
  }
}

function onSwitchModel() {
  if (!currentModel.value) return
  ensureSocketAndSend({ type: 'switch_model', model_name: currentModel.value })
}

function onSetThinking() {
  ensureSocketAndSend({ type: 'set_thinking', level: thinkingLevel.value })
}

/* ---------- 附件与发送 ---------- */
function pickImage() {
  fileInput.value?.click()
}

async function onFileChange(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  try {
    const res = await uploadImage(file)
    const objectId = res?.object_id || res?.id
    if (!objectId) throw new Error('上传未返回 object_id')
    pendingAttachments.value.push({
      object_id: objectId,
      preview: URL.createObjectURL(file),
      name: file.name
    })
  } catch (err) {
    notify('上传图片失败：' + err.message, 'error')
  }
}

function removeAttachment(i) {
  pendingAttachments.value.splice(i, 1)
}

function ensureSocketAndSend(payload) {
  if (!currentSessionId.value) return
  if (!socket || socket.readyState !== WebSocket.OPEN || socketSessionId !== currentSessionId.value) {
    connectWS(currentSessionId.value)
  }
  socket.send(JSON.stringify(payload))
}

async function send() {
  const content = input.value.trim()
  const attachmentIds = pendingAttachments.value.map((a) => a.object_id)
  if (!content && !attachmentIds.length) return
  if (!currentSessionId.value) {
    try {
      const res = await createSession()
      await loadSessions()
      currentSessionId.value = res.id || res.session_id
    } catch (e) {
      notify('创建会话失败：' + e.message, 'error')
      return
    }
  }

  messages.value.push({
    id: nextId(),
    role: 'user',
    content,
    attachments: pendingAttachments.value.map((a) => ({ object_id: a.object_id, preview: a.preview }))
  })
  scrollToBottom()

  input.value = ''
  pendingAttachments.value = []

  ensureSocketAndSend({
    type: 'chat',
    session_id: currentSessionId.value,
    content,
    attachments: attachmentIds
  })
}

/* ---------- 提示 ---------- */
let noticeTimer = null
const notice = ref({ text: '', type: 'ok' })
function notify(text, type = 'ok') {
  notice.value = { text, type }
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => {
    notice.value = { text: '', type: 'ok' }
  }, 3000)
}

watch(messages, scrollToBottom, { deep: true })

onMounted(async () => {
  await Promise.all([loadSessions(), loadModelsMeta()])
  // 默认打开最近会话（列表首条）
  if (sessions.value.length) {
    await switchSession(sessions.value[0].id)
  }
})

onUnmounted(() => {
  closeSocket()
})
</script>

<style scoped>
.chat-view {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  background: var(--bg-panel);
  position: relative;
}

.notice {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 14px;
  font-size: 13px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.notice.ok {
  color: var(--ok);
  border-color: var(--ok);
}

.notice.err {
  color: var(--err);
  border-color: var(--err);
}

/* 侧栏 */
.sidebar {
  width: 260px;
  flex: 0 0 auto;
  border-right: 1px solid var(--border);
  background: var(--bg);
  display: flex;
  flex-direction: column;
  padding: 12px;
  gap: 10px;
}

.new-btn {
  width: 100%;
}

.conn-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-weak);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c6ccd2;
}

.dot.connected {
  background: var(--ok);
}

.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
  flex: 1 1 auto;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  color: var(--text);
}

.session-item:hover {
  background: var(--bg-hover);
}

.session-item.active {
  background: var(--accent-weak);
  border-color: var(--accent);
}

.s-name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.s-status {
  color: var(--warn);
  font-size: 12px;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}

.s-action {
  border: none;
  background: transparent;
  color: var(--text-weak);
  padding: 0 3px;
  font-size: 12px;
  flex: 0 0 auto;
}

.s-action:hover {
  color: var(--warn);
  background: transparent;
}

.s-action.danger:hover {
  color: var(--err);
}

.compress-btn {
  width: 100%;
  color: var(--accent);
}

/* 主区 */
.main {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-toolbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
  font-size: 12px;
}

.tool-item {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-weak);
}

.tool-item select {
  padding: 4px 8px;
  font-size: 12px;
  max-width: 220px;
}

.toolbar-spacer {
  flex: 1 1 auto;
}

.conn-state {
  color: var(--text-weak);
  white-space: nowrap;
}

.conn-state.connected {
  color: var(--ok);
}

.conn-state.connecting {
  color: var(--warn);
}

.messages {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.empty {
  align-self: center;
  margin-top: 60px;
  text-align: center;
  color: var(--text-weak);
  font-size: 13px;
}

.empty p {
  margin: 4px 0;
}

.empty .hint {
  color: #aab2ba;
  font-size: 12px;
}

.composer {
  flex: 0 0 auto;
  border-top: 1px solid var(--border);
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--bg-panel);
}

.pending-att {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pending-thumb-wrap {
  position: relative;
}

.att-thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--border);
}

.remove-att {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  line-height: 1;
  padding: 0;
  font-size: 10px;
  border-radius: 50%;
  background: var(--err);
  color: #fff;
  border: none;
}

.composer-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.attach-btn {
  width: 32px;
  height: 32px;
  padding: 0;
  border-radius: 6px;
  font-size: 16px;
  line-height: 1;
}

.input-area {
  flex: 1 1 auto;
  resize: none;
  min-height: 36px;
  max-height: 140px;
  line-height: 1.5;
  overflow-y: auto;
}

.send-btn {
  flex: 0 0 auto;
}
</style>
