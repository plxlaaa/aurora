<template>
  <div class="chat-view">
    <!-- 会话侧栏 -->
    <aside class="sidebar">
      <button class="primary new-btn" @click="createSession">＋ 新建会话</button>

      <div class="conn-status">
        <span
          class="dot"
          :class="{ connected: wsState === 'connected' }"
        ></span>
        <span class="label">{{ wsText }}</span>
      </div>

      <ul class="session-list">
        <li
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentSessionId }"
          @click="switchSession(s)"
        >
          <span class="s-name">{{ s.title || '未命名会话' }}</span>
          <button class="del" title="删除会话" @click.stop="removeSession(s.id)">
            ✕
          </button>
        </li>
      </ul>

      <button class="compress-btn" :disabled="!currentSessionId" @click="compressNow">
        ⤷ 压缩上下文
      </button>
    </aside>

    <!-- 主对话区 -->
    <section class="main">
      <div ref="scroller" class="messages">
        <div v-if="!messages.length" class="empty">
          在下方输入内容开始对话，可附带图片。
        </div>

        <div
          v-for="(m, idx) in messages"
          :key="m.id"
          class="msg"
          :class="'role-' + m.role"
        >
          <!-- 用户消息 -->
          <template v-if="m.role === 'user'">
            <div class="user-msg">
              <div v-if="m.attachments && m.attachments.length" class="att-thumbs">
                <img
                  v-for="(a, i) in m.attachments"
                  :key="i"
                  :src="a.preview || ('data:image/png;base64,' + a.base64)"
                  class="att-thumb"
                  alt="附件"
                />
              </div>
              <div v-if="m.content" class="bubble user-bubble">
                {{ m.content }}
              </div>
            </div>
          </template>

          <!-- 思考分区 -->
          <template v-else-if="m.role === 'thinking'">
            <div class="bubble thinking-bubble">
              <span class="tag">思考</span>
              <span class="thinking-text">{{ m.content || '…' }}</span>
            </div>
          </template>

          <!-- 工具调用状态行 -->
          <template v-else-if="m.role === 'tool_call'">
            <div class="status-row">
              <span class="state" :class="m.status">
                {{ stateSymbol(m.status) }}
              </span>
              <span class="s-tool">{{ m.tool || '工具' }}</span>
              <span v-if="m.detail" class="s-detail">{{ m.detail }}</span>
              <span class="state-text" :class="m.status">{{ stateText(m.status) }}</span>
            </div>
          </template>

          <!-- 压缩状态行 -->
          <template v-else-if="m.role === 'compression'">
            <div class="status-row compression">
              <span class="state" :class="m.status">
                {{ stateSymbol(m.status) }}
              </span>
              <span class="s-tool">上下文压缩</span>
              <span v-if="m.detail" class="s-detail">{{ m.detail }}</span>
              <span class="state-text" :class="m.status">{{ stateText(m.status) }}</span>
            </div>
          </template>

          <!-- 助手/assistant 流式内容 -->
          <template v-else>
            <div class="bubble assistant-bubble">
              <span class="typing" v-if="streaming && idx === messages.length - 1">
                <span class="cursor">▍</span>
              </span>
              <span class="content">{{ m.content || '' }}</span>
            </div>
          </template>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="composer">
        <div v-if="attachments.length" class="pending-att">
          <img
            v-for="(a, i) in attachments"
            :key="i"
            :src="a.preview"
            class="att-thumb"
            alt="待发送"
          />
        </div>

        <div class="composer-row">
          <button
            class="attach-btn"
            :disabled="streaming"
            title="添加图片"
            @click="pickImage"
          >
            +
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
          ></textarea>
          <button
            class="primary send-btn"
            :disabled="streaming || (!input.trim() && !attachments.length)"
            @click="send"
          >
            发送
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import {
  getSessions,
  createSession,
  deleteSession,
  uploadImage,
  triggerCompress
} from '../api'

const messages = ref([])
const sessions = ref([])
const currentSessionId = ref(null)
const input = ref('')
const attachments = ref([])
const streaming = ref(false)
const wsState = ref('disconnected')

const fileInput = ref(null)
const scroller = ref(null)

let socket = null
let msgSeq = 0
let assistantSeq = 1

const wsText = computed(() => {
  if (wsState.value === 'connected') return '● 已连接'
  if (wsState.value === 'connecting') return '◔ 连接中'
  return '○ 未连接'
})

const wsUrl = computed(
  () =>
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/chat`
)

function newId(prefix) {
  msgSeq += 1
  return `${prefix}-${msgSeq}`
}

function stateSymbol(status) {
  if (status === 'done' || status === 'ok' || status === 'success') return '✓'
  if (status === 'error' || status === 'failed') return '✕'
  if (status === 'running' || status === 'streaming') return '◔'
  return '○'
}

function stateText(status) {
  if (status === 'done' || status === 'ok' || status === 'success') return '完成'
  if (status === 'error' || status === 'failed') return '失败'
  if (status === 'running' || status === 'streaming') return '运行中'
  if (status === 'queued') return '排队中'
  return '待处理'
}

function scrollToBottom() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

/* ---------- WebSocket ---------- */
function connectWS() {
  if (socket && socket.readyState === WebSocket.OPEN) return
  wsState.value = 'connecting'
  socket = new WebSocket(wsUrl.value)
  socket.onopen = () => {
    wsState.value = 'connected'
  }
  socket.onclose = () => {
    wsState.value = 'disconnected'
    if (streaming.value) {
      streaming.value = false
    }
  }
  socket.onerror = () => {
    wsState.value = 'disconnected'
  }
  socket.onmessage = (ev) => {
    handleMessage(ev.data)
  }
}

function handleMessage(raw) {
  let data = raw
  if (typeof raw === 'string') {
    try {
      data = JSON.parse(raw)
    } catch (e) {
      // 纯文本：当作 assistant delta
      appendAssistantDelta(raw)
      return
    }
  }
  if (!data || typeof data !== 'object') return
  const type = data.type || 'delta'
  switch (type) {
    case 'delta':
      appendAssistantDelta(data.content)
      break
    case 'message':
      handleFullMessage(data)
      break
    case 'thinking':
      handleThinking(data)
      break
    case 'tool_call':
      handleToolCall(data)
      break
    case 'compression':
      handleCompression(data)
      break
    case 'error':
      appendAssistantDelta(`\n[错误] ${data.message || '未知错误'}`)
      break
    case 'done':
    case 'end':
    case 'finished':
      streaming.value = false
      break
    default:
      // fallback：若带 content 且是 assistant 则追加
      if (data.content && data.role !== 'user') {
        appendAssistantDelta(data.content)
      }
      break
  }
  scrollToBottom()
}

function appendAssistantDelta(text) {
  if (!text) return
  let last = messages.value[messages.value.length - 1]
  if (!last || last.role !== 'assistant') {
    last = { id: newId('assistant'), role: 'assistant', content: '' }
    messages.value.push(last)
  }
  if (streaming.value === false) streaming.value = true
  last.content += text
  scrollToBottom()
}

function handleFullMessage(data) {
  const role = data.role || 'assistant'
  if (role === 'user') {
    messages.value.push({
      id: newId('user-msg'),
      role: 'user',
      content: data.content || '',
      attachments: data.attachments || []
    })
  } else if (role === 'thinking') {
    handleThinking(data)
  } else if (role === 'tool_call') {
    handleToolCall(data)
  } else {
    messages.value.push({
      id: newId('assistant'),
      role: 'assistant',
      content: data.content || data.text || ''
    })
  }
  scrollToBottom()
}

function handleThinking(data) {
  // 若最后一条是 thinking 则追加，否则新建
  let last = messages.value[messages.value.length - 1]
  const content = data.content || data.text || ''
  if (last && last.role === 'thinking') {
    last.content += content
  } else if (content) {
    messages.value.push({ id: newId('thinking'), role: 'thinking', content })
  }
}

function handleToolCall(data) {
  const name = data.name || data.tool || data.tool_name || '工具'
  // 更新已存在的同名工具行
  const existing = messages.value.find(
    (m) => m.role === 'tool_call' && m.tool === name
  )
  const status = data.status || 'running'
  const detail = data.detail || data.arguments || ''
  if (existing) {
    existing.status = status
    if (detail) existing.detail = detail
    if (status === 'done' || status === 'error') streaming.value = false
  } else {
    messages.value.push({
      id: newId('toolcall'),
      role: 'tool_call',
      tool: name,
      status,
      detail
    })
  }
}

function handleCompression(data) {
  const status = data.status || (data.started ? 'running' : 'done')
  const detail = data.detail || data.message || ''
  const existing = messages.value.find((m) => m.role === 'compression')
  if (existing) {
    existing.status = status
    if (detail) existing.detail = detail
  } else {
    messages.value.push({
      id: newId('compress'),
      role: 'compression',
      status,
      detail
    })
  }
}

/* ---------- 会话管理 ---------- */
async function loadSessions() {
  try {
    const list = await getSessions()
    sessions.value = Array.isArray(list) ? list : list?.sessions || []
  } catch (e) {
    sessions.value = []
  }
}

async function newSession() {
  try {
    const s = await createSession()
    const session = {
      id: s.id || s.session_id,
      title: s.title || '新会话'
    }
    sessions.value.push(session)
    await switchSession(session)
  } catch (e) {
    alert('新建会话失败：' + e.message)
  }
}

async function switchSession(s) {
  currentSessionId.value = s.id || s.session_id
  messages.value = []
  streaming.value = false
  scrollToBottom()
}

async function removeSession(id) {
  if (!confirm('确定删除该会话？')) return
  try {
    await deleteSession(id)
    sessions.value = sessions.value.filter((s) => (s.id || s.session_id) !== id)
    if ((currentSessionId.value || '') === String(id)) {
      currentSessionId.value = null
      messages.value = []
    }
  } catch (e) {
    alert('删除会话失败：' + e.message)
  }
}

async function compressNow() {
  if (!currentSessionId.value) return
  try {
    handleCompression({ status: 'running', detail: '正在压缩…' })
    const r = await triggerCompress(currentSessionId.value)
    handleCompression({ status: 'done', detail: r?.message || '已完成' })
  } catch (e) {
    handleCompression({ status: 'error', detail: e.message })
  }
  scrollToBottom()
}

/* ---------- 附件与发送 ---------- */
function pickImage() {
  fileInput.value?.click()
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      // 移除 data URL 前缀，仅保留 base64
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function onFileChange(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  try {
    const upload = await uploadImage(file)
    const objectId = upload?.object_id || upload?.id
    const preview = URL.createObjectURL(file)
    const base64 = await readAsBase64(file)
    attachments.value.push({
      object_id: objectId,
      base64,
      preview,
      name: file.name
    })
    scrollToBottom()
  } catch (err) {
    alert('上传图片失败：' + err.message)
  }
}

async function send() {
  const content = input.value.trim()
  const attachmentPayload = attachments.value.map((a) => ({
    object_id: a.object_id,
    base64: a.base64
  }))

  if (!content && !attachmentPayload.length) return
  if (streaming.value && content) return

  // 推送用户消息到界面
  messages.value.push({
    id: newId('user'),
    role: 'user',
    content,
    attachments: attachmentPayload.map((a, i) => ({
      ...a,
      preview: attachments.value[i].preview
    }))
  })
  scrollToBottom()

  input.value = ''
  attachments.value = []

  connectWS()
  const payload = {
    type: 'chat',
    session_id: currentSessionId.value,
    content,
    attachments: attachmentPayload
  }
  try {
    socket.send(JSON.stringify(payload))
    streaming.value = true
  } catch (e) {
    streaming.value = false
    appendAssistantDelta('\n[发送失败] 连接尚未建立，请重试。')
  }
}

onMounted(() => {
  loadSessions()
})

onUnmounted(() => {
  socket?.close()
})
</script>

<style scoped>
.chat-view {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  background: var(--bg-panel);
}

/* 侧栏 */
.sidebar {
  width: 240px;
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
  justify-content: space-between;
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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.del {
  border: none;
  background: transparent;
  color: var(--text-weak);
  padding: 0 4px;
  font-size: 12px;
  flex: 0 0 auto;
}

.del:hover {
  color: var(--err);
  background: transparent;
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
  margin-top: 40px;
  color: var(--text-weak);
  font-size: 13px;
}

.msg {
  max-width: 100%;
}

/* 用户消息 */
.user-msg {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}

.att-thumbs {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.att-thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--border);
}

.bubble {
  border-radius: 10px;
  padding: 9px 13px;
  max-width: 78%;
  white-space: pre-wrap;
  word-break: break-word;
}

.user-bubble {
  background: var(--accent);
  color: #fff;
}

.assistant-bubble {
  background: var(--bg);
  border: 1px solid var(--border);
}

/* thinking */
.thinking-bubble {
  background: var(--accent-weak);
  color: var(--text-weak);
  font-size: 13px;
  border-left: 3px solid var(--accent);
  border-radius: 4px;
}

.tag {
  font-weight: 600;
  margin-right: 6px;
}

.thinking-text {
  color: var(--text);
}

.typing .cursor {
  animation: blink 1s steps(1) infinite;
  color: var(--accent);
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

/* 状态行 */
.status-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 13px;
  color: var(--text);
}

.status-row.compression {
  border-style: dashed;
}

.state {
  width: 18px;
  text-align: center;
  color: var(--text-weak);
}

.state.done {
  color: var(--ok);
}

.state.error {
  color: var(--err);
}

.state.running {
  color: var(--warn);
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}

.s-tool {
  font-weight: 500;
}

.s-detail {
  color: var(--text-weak);
}

.state-text {
  color: var(--text-weak);
  font-size: 12px;
}

.state-text.done {
  color: var(--ok);
}

.state-text.error {
  color: var(--err);
}

/* 输入区 */
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
}

.send-btn {
  flex: 0 0 auto;
}
</style>