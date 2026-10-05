<template>
  <div class="chat-view">
    <div v-if="notice.text" class="notice" :class="notice.type">{{ notice.text }}</div>

    <!-- 会话侧栏 -->
    <aside class="sidebar" :class="{ collapsed: sidebarCollapsed }">
      <button class="primary new-btn" @click="createNewSession">＋ 新建会话</button>

      <div class="session-search">
        <input
          v-model="sessionQuery"
          type="text"
          placeholder="搜索会话…"
          spellcheck="false"
        />
      </div>

      <div class="conn-status">
        <span class="dot" :class="{ connected: wsState === 'connected' }"></span>
        <span class="label">{{ wsText }}</span>
      </div>

      <ul class="session-list">
        <li
          v-for="s in filteredSessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentSessionId }"
        >
          <template v-if="renamingId === s.id">
            <input
              ref="renameInputEl"
              v-model="renameInput"
              class="rename-input"
              spellcheck="false"
              @click.stop
              @keyup.enter="confirmRename"
              @keyup.esc="cancelRename"
              @blur="confirmRename"
            />
          </template>
          <template v-else>
            <span class="s-name" :title="s.title" @click="switchSession(s.id)">
              {{ s.title || '未命名会话' }}
            </span>
            <span v-if="s.status === 'running'" class="s-status" title="处理中">◔</span>
            <button class="s-action" title="重命名" @click.stop="startRename(s)">✎</button>
            <button class="s-action" title="导出 Markdown" @click.stop="exportSession(s.id, 'md')">⤓M</button>
            <button class="s-action" title="导出 JSON" @click.stop="exportSession(s.id, 'json')">⤓J</button>
            <button class="s-action" title="归档会话" @click.stop="archive(s.id)">⇱</button>
            <button class="s-action danger" title="删除会话" @click.stop="remove(s.id)">✕</button>
          </template>
        </li>
        <li v-if="!filteredSessions.length" class="no-result">
          {{ sessionQuery ? '无匹配会话' : '暂无会话' }}
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
        <button class="toggle-side" :title="sidebarCollapsed ? '展开会话栏' : '收起会话栏'" @click="sidebarCollapsed = !sidebarCollapsed">
          {{ sidebarCollapsed ? '☰' : '⟨' }}
        </button>
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
      <div ref="scroller" class="messages" @scroll.passive="onScroll">
        <div v-if="!messages.length" class="empty">
          <div class="empty-icon">✦</div>
          <p class="empty-title">{{ currentSessionTitle || '开始一段新对话' }}</p>
          <p class="hint">支持视觉理解、上下文压缩、Skills 与 MCP 工具。</p>
          <div class="suggestions">
            <button
              v-for="s in suggestions"
              :key="s"
              class="suggestion"
              @click="useSuggestion(s)"
            >
              {{ s }}
            </button>
          </div>
        </div>

        <MessageItem
          v-for="m in messages"
          :key="m.id"
          :message="m"
          :streaming="streaming && m.id === lastMessageId"
          :regenerable="regenerableOf(m)"
          @regenerate="regenerate"
        />
      </div>

      <!-- 输入区 -->
      <div class="composer">
        <button v-if="!atBottom" class="to-bottom" title="回到底部" @click="scrollToBottom(true)">
          ↓ 回到最新
        </button>

        <div v-if="pendingAttachments.length" class="pending-att">
          <div v-for="(a, i) in pendingAttachments" :key="i" class="pending-thumb-wrap">
            <img :src="a.preview" class="att-thumb" alt="待发送" />
            <button class="remove-att" title="移除" @click="removeAttachment(i)">✕</button>
          </div>
        </div>

        <div class="composer-row">
          <button
            class="attach-btn"
            :disabled="uploading"
            title="添加图片"
            @click="pickImage"
          >
            {{ uploading ? '…' : '＋' }}
          </button>
          <input
            type="file"
            ref="fileInput"
            accept="image/*"
            multiple
            hidden
            @change="onFileChange"
          />
          <textarea
            ref="inputEl"
            v-model="input"
            class="input-area"
            rows="1"
            :placeholder="streaming ? '生成中，可继续输入…' : '输入消息，回车发送，Shift+回车换行'"
            @keydown="onKeydown"
            @compositionstart="composing = true"
            @compositionend="composing = false"
            @paste="onPaste"
            @input="autoGrow"
          ></textarea>
          <button
            v-if="streaming"
            class="stop-btn"
            title="停止生成"
            @click="stop"
          >
            ■ 停止
          </button>
          <button
            v-else
            class="primary send-btn"
            :disabled="!canSend"
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
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import {
  getSessions,
  createSession,
  getSession,
  deleteSession,
  archiveSession,
  renameSession,
  exportSessionUrl,
  uploadImage,
  triggerCompress,
  getModels
} from '../api'
import { getToken, downloadFile } from '../api/http'
import MessageItem from '../components/MessageItem.vue'

const suggestions = [
  '用通俗的语言解释什么是大语言模型',
  '写一段 Python 快速排序代码',
  '帮我总结三篇经典科幻小说的共同点'
]

const messages = ref([])
const sessions = ref([])
const currentSessionId = ref(null)
const input = ref('')
const pendingAttachments = ref([])
const streaming = ref(false)
const uploading = ref(false)
const wsState = ref('disconnected')
const currentModel = ref('')
const thinkingLevel = ref('normal')
const currentModelOptions = ref([])
const sessionQuery = ref('')
const renamingId = ref(null)
const renameInput = ref('')
const sidebarCollapsed = ref(false)
const atBottom = ref(true)
const composing = ref(false)

const fileInput = ref(null)
const scroller = ref(null)
const renameInputEl = ref(null)
const inputEl = ref(null)

let socket = null
let msgSeq = 0
let socketSessionId = null
let manualClose = false
let reconnectTimer = null
let reconnectDelay = 1000
// 连接尚未就绪时暂存的待发送指令
let outbox = []

// 打字机队列：网络分块先入缓冲，定时器匀速吐字，缓冲越大吐字越快以便追赶
let typeBuffer = ''
let typeTimer = null
let typingMsg = null
let typeEnded = false
const TYPE_TICK_MS = 24

const lastMessageId = computed(() =>
  messages.value.length ? messages.value[messages.value.length - 1].id : null
)

const canSend = computed(
  () => !uploading.value && (!!input.value.trim() || pendingAttachments.value.length > 0)
)

const currentSessionTitle = computed(() => {
  const s = sessions.value.find((x) => x.id === currentSessionId.value)
  return s?.title || ''
})

const filteredSessions = computed(() => {
  const q = sessionQuery.value.trim().toLowerCase()
  if (!q) return sessions.value
  return sessions.value.filter((s) => (s.title || '').toLowerCase().includes(q))
})

const wsText = computed(() => {
  if (wsState.value === 'connected') return '已连接'
  if (wsState.value === 'connecting') return '连接中'
  return '未连接'
})

const wsUrl = computed(() => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = getToken()
  return `${proto}://${location.host}/api/chat?token=${encodeURIComponent(token || '')}`
})

function nextId() {
  msgSeq += 1
  return `m-${msgSeq}`
}

/* 距底部 80px 内视为「贴底」，超出则不再自动滚动，避免打断用户回看 */
function onScroll() {
  const el = scroller.value
  if (!el) return
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

function scrollToBottom(force = false) {
  if (!force && !atBottom.value) return
  nextTick(() => {
    const el = scroller.value
    if (!el) return
    el.scrollTop = el.scrollHeight
    atBottom.value = true
  })
}

function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 140) + 'px'
}

/* 回车发送：输入法组合中与 Shift/Ctrl 组合键不触发 */
function onKeydown(e) {
  if (e.key !== 'Enter') return
  if (composing.value || e.isComposing || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return
  e.preventDefault()
  send()
}

function useSuggestion(text) {
  input.value = text
  nextTick(() => {
    inputEl.value?.focus()
    autoGrow()
  })
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
  if (socket && socketSessionId === sessionId &&
      (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return
  }
  closeSocket()
  manualClose = false
  socketSessionId = sessionId
  wsState.value = 'connecting'
  socket = new WebSocket(`${wsUrl.value}&session_id=${encodeURIComponent(sessionId)}`)
  socket.onopen = () => {
    wsState.value = 'connected'
    reconnectDelay = 1000
    // 连接就绪后补发排队指令
    const queued = outbox
    outbox = []
    for (const payload of queued) socket.send(JSON.stringify(payload))
  }
  socket.onclose = () => {
    wsState.value = 'disconnected'
    streaming.value = false
    resetTyping()
    socketSessionId = null
    socket = null
    if (!manualClose && currentSessionId.value) {
      // 指数退避重连，上限 15s
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        if (currentSessionId.value) connectWS(currentSessionId.value)
      }, reconnectDelay)
      reconnectDelay = Math.min(reconnectDelay * 2, 15000)
    }
  }
  socket.onerror = () => {
    wsState.value = 'disconnected'
  }
  socket.onmessage = (ev) => handleMessage(ev.data)
}

function closeSocket() {
  manualClose = true
  outbox = []
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (socket) {
    try {
      socket.onclose = null
      socket.onmessage = null
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
    case 'pong':
      return
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
      notify(data.text || '', 'ok')
      break
    case 'error':
      endStreaming()
      finishPendingTools()
      messages.value.push({ id: nextId(), role: 'error', content: data.message || '未知错误' })
      break
    case 'cancelled':
      endStreaming()
      finishPendingTools()
      messages.value.push({ id: nextId(), role: 'info', content: '已停止生成' })
      loadSessions()
      break
    case 'done':
    case 'end':
    case 'finished':
      endStreaming()
      finishPendingTools()
      loadSessions()
      break
    default:
      if (data.content && data.role !== 'user') appendDelta(data.content)
      break
  }
  scrollToBottom()
}

/* 生成结束时把仍在 running 的工具卡片收尾，避免永久转圈 */
function finishPendingTools() {
  for (const m of messages.value) {
    if (m.role === 'tool_call' && m.status === 'running') m.status = 'done'
  }
}

function appendDelta(text) {
  if (!text) return
  streaming.value = true
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant') {
    typingMsg = last
  } else {
    typingMsg = { id: nextId(), role: 'assistant', content: '' }
    messages.value.push(typingMsg)
  }
  typeBuffer += text
  if (!typeTimer) {
    typeEnded = false
    typeTimer = setInterval(typeTick, TYPE_TICK_MS)
  }
}

function stopTypeTimer() {
  if (typeTimer) {
    clearInterval(typeTimer)
    typeTimer = null
  }
}

/* 排空缓冲后正常收尾 */
function finishTyping() {
  stopTypeTimer()
  typeBuffer = ''
  typingMsg = null
  typeEnded = false
  streaming.value = false
}

/* 丢弃缓冲（切换会话/连接断开时调用） */
function resetTyping() {
  stopTypeTimer()
  typeBuffer = ''
  typingMsg = null
  typeEnded = false
}

/* 生成结束：等缓冲排空再收尾，避免结尾文字瞬间蹦出 */
function endStreaming() {
  typeEnded = true
  if (!typeTimer || !typeBuffer) finishTyping()
}

function typeTick() {
  // 目标消息已被切换会话/重新生成移除时直接终止
  if (!typingMsg || !messages.value.includes(typingMsg)) {
    resetTyping()
    return
  }
  if (!typeBuffer) {
    if (typeEnded) finishTyping()
    else stopTypeTimer()
    return
  }
  const n = Math.max(2, Math.ceil(typeBuffer.length / 20))
  typingMsg.content += typeBuffer.slice(0, n)
  typeBuffer = typeBuffer.slice(n)
  scrollToBottom()
}

function appendThinking(text) {
  if (!text) return
  streaming.value = true
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'thinking') last.content += text
  else messages.value.push({ id: nextId(), role: 'thinking', content: text })
}

function upsertToolCall(data) {
  const name = data.name || '工具'
  const callId = data.id || data.tool_call_id || null
  // 优先按后端下发的 call id 匹配，回退到同名 running 卡片
  const existing = messages.value.find((m) => {
    if (m.role !== 'tool_call') return false
    if (callId && m.callId) return m.callId === callId
    return m.name === name && m.status === 'running'
  })
  if (existing) {
    if (callId) existing.callId = callId
    existing.status = data.status || existing.status
    if (data.arguments !== undefined) existing.arguments = data.arguments
    if (data.result !== undefined) existing.result = data.result
  } else {
    messages.value.push({
      id: nextId(),
      callId,
      role: 'tool_call',
      name,
      status: data.status || 'running',
      arguments: data.arguments,
      result: data.result
    })
  }
}

function upsertToolResult(data) {
  const callId = data.tool_call_id || data.id || null
  const existing = messages.value.find(
    (m) =>
      m.role === 'tool_call' &&
      ((callId && m.callId === callId) || (!callId && m.name === data.name))
  )
  if (existing) {
    existing.status = existing.status === 'error' ? 'error' : 'done'
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

/* ---------- 停止 / 重新生成 ---------- */
function stop() {
  // 交由服务端中断本轮生成，不再直接断开连接
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'cancel' }))
  } else {
    streaming.value = false
    resetTyping()
  }
}

function regenerableOf(m) {
  if (m.role !== 'assistant' || streaming.value) return false
  return m.id === lastMessageId.value
}

async function regenerate() {
  if (!currentSessionId.value || streaming.value) return
  let lastUserIdx = -1
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') {
      lastUserIdx = i
      break
    }
  }
  if (lastUserIdx < 0) return
  messages.value.splice(lastUserIdx + 1)
  streaming.value = true
  ensureSocketAndSend({ type: 'regenerate' })
  scrollToBottom(true)
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
    // 后端已即时落盘，直接乐观插入列表，避免等待刷新造成的空窗
    if (id && !sessions.value.some((s) => s.id === id)) {
      sessions.value.unshift({ id, title: '新会话', message_count: 0, status: 'idle' })
    }
    await switchSession(id)
    loadSessions()
  } catch (e) {
    notify('新建会话失败：' + e.message, 'error')
  }
}

async function switchSession(id) {
  if (id === currentSessionId.value) return
  closeSocket()
  currentSessionId.value = id
  streaming.value = false
  resetTyping()
  messages.value = []
  atBottom.value = true
  try {
    const res = await getSession(id)
    messages.value = normalizeHistory(res?.messages || [])
  } catch (e) {
    notify('加载会话失败：' + e.message, 'error')
  }
  connectWS(id)
  scrollToBottom(true)
  if (window.innerWidth <= 720) sidebarCollapsed.value = true
}

/* 当前会话被移除后，自动切到列表中的下一个会话 */
async function fallbackAfterRemoval(removedId) {
  if (currentSessionId.value !== removedId) return
  closeSocket()
  currentSessionId.value = null
  messages.value = []
  const next = sessions.value[0]
  if (next) await switchSession(next.id)
}

async function remove(id) {
  if (!window.confirm('确定删除该会话？删除后不可恢复。')) return
  try {
    await deleteSession(id)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    await fallbackAfterRemoval(id)
    loadSessions()
  } catch (e) {
    notify('删除会话失败：' + e.message, 'error')
  }
}

async function exportSession(id, format) {
  try {
    const s = sessions.value.find((x) => x.id === id)
    const name = (s?.title || id).replace(/[^\w\u4e00-\u9fa5-]+/g, '_').slice(0, 40) || id
    await downloadFile(exportSessionUrl(id, format), `${name}.${format}`)
    notify(`已导出 ${format.toUpperCase()}`, 'ok')
  } catch (e) {
    notify('导出失败：' + e.message, 'error')
  }
}

async function archive(id) {
  if (!window.confirm('归档该会话？归档后将从列表中移除。')) return
  try {
    await archiveSession(id)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    await fallbackAfterRemoval(id)
    loadSessions()
    notify('会话已归档', 'ok')
  } catch (e) {
    notify('归档失败：' + e.message, 'error')
  }
}

async function startRename(s) {
  renamingId.value = s.id
  renameInput.value = s.title || ''
  await nextTick()
  const el = Array.isArray(renameInputEl.value) ? renameInputEl.value[0] : renameInputEl.value
  el?.focus()
  el?.select()
}

async function confirmRename() {
  if (!renamingId.value) return
  const id = renamingId.value
  renamingId.value = null
  const title = renameInput.value.trim()
  const target = sessions.value.find((s) => s.id === id)
  if (target && target.title === title) return
  try {
    await renameSession(id, title)
    if (target) target.title = title || '新会话'
  } catch (e) {
    notify('重命名失败：' + e.message, 'error')
    loadSessions()
  }
}

function cancelRename() {
  renamingId.value = null
}

async function compressNow() {
  if (!currentSessionId.value || streaming.value) return
  try {
    const res = await triggerCompress(currentSessionId.value)
    messages.value = normalizeHistory(res?.messages || [])
    notify('上下文已压缩', 'ok')
    scrollToBottom(true)
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
const MAX_ATTACHMENTS = 4

function pickImage() {
  fileInput.value?.click()
}

async function uploadOne(file) {
  const res = await uploadImage(file)
  const objectId = res?.object_id || res?.id
  if (!objectId) throw new Error('上传未返回 object_id')
  pendingAttachments.value.push({
    object_id: objectId,
    preview: URL.createObjectURL(file),
    name: file.name || '图片'
  })
}

async function addFiles(files) {
  const list = Array.from(files || []).filter((f) => f.type.startsWith('image/'))
  if (!list.length) return
  const room = MAX_ATTACHMENTS - pendingAttachments.value.length
  if (room <= 0) {
    notify(`最多添加 ${MAX_ATTACHMENTS} 张图片`, 'error')
    return
  }
  uploading.value = true
  try {
    for (const file of list.slice(0, room)) {
      try {
        await uploadOne(file)
      } catch (err) {
        notify('上传图片失败：' + err.message, 'error')
      }
    }
  } finally {
    uploading.value = false
  }
}

async function onFileChange(e) {
  const files = e.target.files
  await addFiles(files)
  e.target.value = ''
}

/* 支持直接粘贴剪贴板中的图片 */
async function onPaste(e) {
  const files = Array.from(e.clipboardData?.files || []).filter((f) => f.type.startsWith('image/'))
  if (!files.length) return
  e.preventDefault()
  await addFiles(files)
}

function removeAttachment(i) {
  const [removed] = pendingAttachments.value.splice(i, 1)
  if (removed?.preview) URL.revokeObjectURL(removed.preview)
}

/* 连接未就绪时先入队，onopen 后自动补发 */
function ensureSocketAndSend(payload) {
  if (!currentSessionId.value) return
  if (socket && socket.readyState === WebSocket.OPEN && socketSessionId === currentSessionId.value) {
    socket.send(JSON.stringify(payload))
    return
  }
  outbox.push(payload)
  connectWS(currentSessionId.value)
}

async function send() {
  if (streaming.value) {
    notify('正在生成，请先停止或等待完成', 'error')
    return
  }
  const content = input.value.trim()
  const atts = pendingAttachments.value.slice()
  if (!content && !atts.length) return
  if (uploading.value) {
    notify('图片仍在上传中，请稍候', 'error')
    return
  }

  if (!currentSessionId.value) {
    try {
      const res = await createSession()
      currentSessionId.value = res.id || res.session_id
      loadSessions()
    } catch (e) {
      notify('创建会话失败：' + e.message, 'error')
      return
    }
  }

  messages.value.push({
    id: nextId(),
    role: 'user',
    content,
    attachments: atts.map((a) => ({ object_id: a.object_id, preview: a.preview }))
  })

  input.value = ''
  pendingAttachments.value = []
  streaming.value = true
  nextTick(() => {
    // 清空后复位输入框高度，否则会保留多行时撑开的尺寸
    const el = inputEl.value
    if (el) {
      el.style.height = 'auto'
      el.focus()
    }
  })
  scrollToBottom(true)

  ensureSocketAndSend({
    type: 'message',
    session_id: currentSessionId.value,
    content,
    attachments: atts.map((a) => a.object_id)
  })
}

/* ---------- 提示 ---------- */
let noticeTimer = null
const notice = ref({ text: '', type: 'ok' })
function notify(text, type = 'ok') {
  if (!text) return
  notice.value = { text, type }
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => {
    notice.value = { text: '', type: 'ok' }
  }, 3000)
}

// 流式追加内容时保持贴底（用户上滚查看历史时不打断）
watch(() => messages.value.length, () => scrollToBottom())
watch(
  () => messages.value[messages.value.length - 1]?.content,
  () => scrollToBottom()
)

onMounted(async () => {
  if (window.innerWidth <= 720) sidebarCollapsed.value = true
  await Promise.all([loadSessions(), loadModelsMeta()])
  // 默认打开最近会话（列表首条）
  if (sessions.value.length) {
    await switchSession(sessions.value[0].id)
  }
})

onUnmounted(() => {
  closeSocket()
  if (noticeTimer) clearTimeout(noticeTimer)
  for (const a of pendingAttachments.value) {
    if (a.preview) URL.revokeObjectURL(a.preview)
  }
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
  box-shadow: 0 2px 8px var(--shadow);
}

.notice.ok {
  color: var(--ok);
  border-color: var(--ok);
}

.notice.err {
  color: var(--err);
  border-color: var(--err);
}

.notice.error {
  color: var(--err);
  border-color: var(--err);
}

/* 侧栏 */
.sidebar {
  width: 268px;
  flex: 0 0 auto;
  border-right: 1px solid var(--border);
  background: var(--bg);
  display: flex;
  flex-direction: column;
  padding: 12px;
  gap: 10px;
  transition: width 0.18s ease, padding 0.18s ease;
}

.sidebar.collapsed {
  width: 0;
  padding: 12px 0;
  overflow: hidden;
  border-right: none;
}

.new-btn {
  width: 100%;
}

.session-search input {
  width: 100%;
  padding: 6px 10px;
  font-size: 12px;
  border-radius: 8px;
}

.conn-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-weak);
  padding: 0 2px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-faint);
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
  gap: 2px;
  padding: 7px 8px;
  border-radius: 8px;
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
  color: var(--text-faint);
  padding: 0 3px;
  font-size: 12px;
  flex: 0 0 auto;
  opacity: 0;
  transition: opacity 0.15s ease, color 0.15s ease;
}

.session-item:hover .s-action {
  opacity: 1;
}

.s-action:hover {
  color: var(--accent);
  background: transparent;
}

.s-action.danger:hover {
  color: var(--err);
}

.rename-input {
  flex: 1 1 auto;
  font-size: 13px;
  padding: 3px 8px;
  border-radius: 6px;
  min-width: 0;
}

.no-result {
  padding: 12px;
  text-align: center;
  color: var(--text-faint);
  font-size: 12px;
  list-style: none;
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

.toggle-side {
  width: 28px;
  height: 26px;
  padding: 0;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1;
  flex: 0 0 auto;
}

.to-bottom {
  position: absolute;
  top: -40px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 5;
  font-size: 12px;
  padding: 5px 12px;
  border-radius: 14px;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-weak);
  box-shadow: 0 2px 8px var(--shadow);
}

.to-bottom:hover {
  color: var(--accent);
  border-color: var(--accent);
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
  gap: 14px;
}

.empty {
  align-self: center;
  margin-top: 40px;
  text-align: center;
  color: var(--text-weak);
  font-size: 13px;
  max-width: 480px;
}

.empty-icon {
  width: 56px;
  height: 56px;
  margin: 0 auto 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  color: #fff;
  border-radius: 16px;
  background: var(--gradient);
  box-shadow: 0 6px 20px rgba(74, 125, 255, 0.35);
}

.empty-title {
  margin: 6px 0 4px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}

.empty p {
  margin: 4px 0;
}

.empty .hint {
  color: var(--text-faint);
  font-size: 12px;
}

.suggestions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 18px;
}

.suggestion {
  font-size: 13px;
  text-align: left;
  padding: 9px 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text);
}

.suggestion:hover {
  border-color: var(--accent);
  background: var(--accent-weak);
  color: var(--accent);
}

.composer {
  flex: 0 0 auto;
  position: relative;
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
  border-radius: 8px;
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
  border-radius: 8px;
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

.stop-btn {
  flex: 0 0 auto;
  background: var(--err-bg);
  border-color: var(--err);
  color: var(--err);
}

.stop-btn:hover {
  background: var(--err);
  color: #fff;
}

/* 移动端适配 */
@media (max-width: 720px) {
  .sidebar {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 20;
    width: 230px;
    box-shadow: 2px 0 12px var(--shadow);
  }

  .sidebar.collapsed {
    width: 0;
    box-shadow: none;
  }

  .s-action {
    opacity: 1;
  }

  .tool-item {
    gap: 4px;
  }

  .tool-item select {
    max-width: 120px;
  }

  .messages {
    padding: 14px;
  }
}
</style>
