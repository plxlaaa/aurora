<template>
  <div class="msg" :class="'role-' + message.role">
    <!-- 用户消息（含文本协议工具结果包装：渲染为工具卡片而非用户气泡） -->
    <template v-if="message.role === 'user' && toolResultInfo">
      <div class="row tool-row">
        <span class="avatar tool-avatar">⚙</span>
        <div class="body-col">
          <div class="status-row">
            <span class="state done">✓</span>
            <span class="s-tool">{{ toolResultInfo.name || '工具' }}</span>
            <span class="state-text done">结果回填</span>
          </div>
          <div class="tool-result">
            <pre class="tool-result-body">{{ toolResultInfo.text }}</pre>
          </div>
        </div>
      </div>
    </template>
    <template v-else-if="message.role === 'user'">
      <div class="row user-row">
        <div class="body-col">
          <div v-if="message.attachments && message.attachments.length" class="att-thumbs">
            <a
              v-for="(a, i) in message.attachments"
              :key="i"
              :href="a.object_id ? fileUrl(a.object_id) : undefined"
              target="_blank"
              rel="noopener"
            >
              <img
                :src="a.object_id ? fileUrl(a.object_id) : a.preview"
                class="att-thumb"
                alt="附件"
              />
            </a>
          </div>
          <div v-if="message.content" class="bubble user-bubble">{{ message.content }}</div>
        </div>
        <span class="avatar user-avatar">我</span>
      </div>
    </template>

    <!-- 思考过程（可折叠；空内容不渲染，避免空气泡） -->
    <template v-else-if="message.role === 'thinking' && (message.content || '').trim()">
      <div class="row">
        <span class="avatar ai-avatar">AI</span>
        <div class="body-col">
          <div class="thinking-bubble" @click="showThinking = !showThinking">
            <span class="tag">思考</span>
            <span class="chev">{{ showThinking ? '▾' : '▸' }}</span>
            <span v-if="!showThinking" class="thinking-preview">{{ preview }}</span>
          </div>
          <div v-if="showThinking" class="thinking-body">{{ message.content }}</div>
        </div>
      </div>
    </template>

    <!-- 工具调用状态 -->
    <template v-else-if="message.role === 'tool_call'">
      <div class="row tool-row">
        <span class="avatar tool-avatar">⚙</span>
        <div class="body-col">
          <div class="status-row">
            <span class="state" :class="message.status">
              {{ stateSymbol(message.status) }}
            </span>
            <span class="s-tool">{{ message.name || '工具' }}</span>
            <span v-if="message.status === 'running'" class="state-text running">运行中</span>
            <span v-else class="state-text" :class="message.status">
              {{ message.status === 'done' ? '完成' : message.status === 'error' ? '失败' : '待处理' }}
            </span>
          </div>
          <div v-if="hasResult" class="tool-result">
            <!-- JSON 对象渲染为键值表，其余保持原文 -->
            <div v-if="resultRows" class="tool-result-body kv">
              <div v-for="(row, i) in resultRows" :key="i" class="kv-row">
                <span class="kv-key">{{ row.k }}</span>
                <span class="kv-val" :title="row.v">{{ row.v }}</span>
              </div>
            </div>
            <pre v-else class="tool-result-body">{{ resultText }}</pre>
          </div>
        </div>
      </div>
    </template>

    <!-- 工具结果 -->
    <template v-else-if="message.role === 'tool'">
      <div class="row tool-row">
        <span class="avatar tool-avatar">⚙</span>
        <div class="body-col">
          <div class="status-row tool-result-row">
            <span class="s-tool">{{ message.name || '工具结果' }}</span>
            <span v-if="message.content" class="s-detail">{{ trimResult(message.content) }}</span>
          </div>
        </div>
      </div>
    </template>

    <!-- 系统提示 -->
    <template v-else-if="message.role === 'info'">
      <div class="info-row">{{ message.content }}</div>
    </template>

    <!-- 错误（人性化文案 + 重试入口） -->
    <template v-else-if="message.role === 'error'">
      <div class="error-card">
        <div class="error-main">
          <span class="error-icon">⚠</span>
          <span>{{ friendlyError }}</span>
        </div>
        <button class="retry-btn" :disabled="streaming" @click="$emit('regenerate')">
          ↻ 重试
        </button>
      </div>
    </template>

    <!-- 助手消息 -->
    <template v-else>
      <div class="row">
        <span class="avatar ai-avatar">AI</span>
        <div class="body-col">
          <!-- 等待首字：打字指示器，避免空气泡像卡死 -->
          <div v-if="!assistantText && streaming" class="bubble assistant-bubble typing">
            <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          </div>
          <div v-else class="bubble assistant-bubble">
            <MarkdownText :text="assistantText" :streaming="streaming" />
            <span v-if="streaming" class="cursor">▍</span>
          </div>
          <!-- 消息操作 -->
          <div class="msg-actions">
            <button class="action-btn" title="复制" @click="copyMessage">📋</button>
            <button
              class="action-btn"
              :class="{ active: feedback === 'up' }"
              title="有帮助"
              @click="setFeedback('up')"
            >
              ▲
            </button>
            <button
              class="action-btn"
              :class="{ active: feedback === 'down' }"
              title="没帮助"
              @click="setFeedback('down')"
            >
              ▼
            </button>
            <button
              v-if="regenerable"
              class="action-btn"
              title="重新生成"
              :disabled="streaming"
              @click="$emit('regenerate')"
            >
              ↻
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { fileUrl } from '../api'
import MarkdownText from './MarkdownText.vue'

const props = defineProps({
  message: { type: Object, required: true },
  streaming: { type: Boolean, default: false },
  regenerable: { type: Boolean, default: false }
})

defineEmits(['regenerate'])

const showThinking = ref(true)
const feedback = ref('')

function stateSymbol(status) {
  if (status === 'done' || status === 'ok' || status === 'success') return '✓'
  if (status === 'error' || status === 'failed') return '✕'
  if (status === 'running' || status === 'streaming') return '◔'
  return '○'
}

const preview = computed(() => {
  const t = props.message.content || ''
  return t.length > 60 ? t.slice(0, 60) + '…' : t
})

// 文本协议工具结果回填：user 消息内容为 <tool_result name="x">...</tool_result> 时识别为工具卡片
const toolResultInfo = computed(() => {
  if (props.message.role !== 'user') return null
  const m = /^<tool_result\s+name="([^"]*)">([\s\S]*?)<\/tool_result>/.exec(
    props.message.content || ''
  )
  return m ? { name: m[1], text: m[2].trim() } : null
})

// 助手正文剔除文本协议调用标记（调用过程已由工具卡片呈现，避免 JSON 原文漏出）
const assistantText = computed(() =>
  (props.message.content || '').replace(
    /<\s*tool_?call\s*>[\s\S]*?<\s*\/\s*tool_?call\s*>/gi,
    ''
  )
)

const hasResult = computed(() => {
  const r = props.message.result
  return r !== undefined && r !== null && r !== ''
})

const resultText = computed(() => {
  const r = props.message.result
  if (typeof r === 'string') return r
  try {
    return JSON.stringify(r, null, 2)
  } catch {
    return String(r)
  }
})

// 扁平 JSON 对象 → 键值行（嵌套/数组/超长文本回退原文渲染）
const resultRows = computed(() => {
  const r = props.message.result
  if (!r || typeof r !== 'object' || Array.isArray(r)) return null
  const entries = Object.entries(r)
  if (!entries.length || entries.length > 12) return null
  const rows = []
  for (const [k, v] of entries) {
    const s = typeof v === 'string' ? v : JSON.stringify(v)
    if (s.length > 120) return null // 含长文本则回退原文，避免表格里截断关键信息
    rows.push({ k, v: s })
  }
  return rows
})

// 错误文案人性化：技术报错翻译成人话，原文放 title 供排查
const friendlyError = computed(() => {
  const raw = String(props.message.content || '未知错误')
  const lower = raw.toLowerCase()
  let text = raw
  if (lower.includes('all connection attempts failed') || lower.includes('econnrefused')) {
    text = '连接不上模型服务，请检查网络后重试'
  } else if (lower.includes('502') || lower.includes('503') || lower.includes('所有渠道均失败')) {
    text = '上游服务暂时不可用，稍等片刻再重试'
  } else if (lower.includes('403') || lower.includes('限流') || lower.includes('429')) {
    text = '请求被限流或拒绝，稍等片刻再重试'
  } else if (lower.includes('timeout') || lower.includes('超时')) {
    text = '请求超时，请重试'
  } else if (lower.includes('未授权') || lower.includes('401')) {
    text = '登录状态已失效，请重新登录'
  }
  return text
})

function trimResult(text) {
  const s = String(text || '')
  return s.length > 120 ? s.slice(0, 120) + '…' : s
}

async function copyMessage() {
  const text = props.message.content || ''
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    /* ignore */
  }
}

function setFeedback(v) {
  feedback.value = feedback.value === v ? '' : v
}
</script>

<style scoped>
.msg {
  max-width: 100%;
  animation: msg-in 0.25s ease;
}

@keyframes msg-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.user-row {
  flex-direction: row-reverse;
}

.avatar {
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  user-select: none;
  box-shadow: 0 1px 4px var(--shadow);
}

.ai-avatar {
  background: var(--gradient);
}

.user-avatar {
  background: #10b981;
}

.tool-avatar {
  background: var(--warn);
  font-size: 14px;
}

.body-col {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
  max-width: 78%;
}

.user-row .body-col {
  align-items: flex-end;
}

/* 附件 */
.att-thumbs {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.att-thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--border);
}

/* 气泡 */
.bubble {
  border-radius: 12px;
  padding: 10px 14px;
  white-space: pre-wrap;
  word-break: break-word;
}

.user-bubble {
  background: var(--gradient);
  color: #fff;
  border-radius: 12px 12px 2px 12px;
}

.assistant-bubble {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 12px 12px 12px 2px;
  box-shadow: 0 1px 2px var(--shadow);
  min-width: 40px;
}

/* 思考 */
.thinking-bubble {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  color: var(--text-weak);
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 999px;
  cursor: pointer;
}

.thinking-bubble:hover {
  border-color: var(--accent);
}

.tag {
  font-weight: 600;
  color: var(--accent);
}

.chev {
  color: var(--text-faint);
}

.thinking-preview {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.thinking-body {
  background: var(--bg-soft);
  border-left: 3px solid var(--accent);
  border-radius: 4px;
  padding: 8px 12px;
  color: var(--text-weak);
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
  max-width: 100%;
}

/* 光标 */
.cursor {
  color: var(--accent);
  animation: blink 1s steps(1) infinite;
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

/* 消息操作 */
.msg-actions {
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.body-col:hover .msg-actions {
  opacity: 1;
}

.action-btn {
  width: 26px;
  height: 26px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-faint);
}

.action-btn:hover {
  background: var(--bg-hover);
  color: var(--text);
}

.action-btn.active {
  color: var(--accent);
  background: var(--accent-weak);
  border-color: var(--accent);
}

/* 工具状态 */
.status-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 13px;
  color: var(--text);
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

.state-text.running {
  color: var(--warn);
}

.tool-result {
  margin-top: 2px;
}

.tool-result-body {
  margin: 0;
  max-width: 100%;
  max-height: 240px;
  overflow: auto;
  background: var(--bg-code);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.info-row {
  align-self: center;
  text-align: center;
  color: var(--text-weak);
  font-size: 12px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 4px 12px;
  display: inline-block;
  margin: 0 auto;
  width: fit-content;
}

.info-row.error {
  color: var(--err);
  background: var(--err-bg);
  border-color: var(--err);
}

/* 错误卡片：人性化文案 + 重试 */
.error-card {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  background: var(--err-bg);
  border: 1px solid var(--err);
  border-radius: 10px;
  padding: 6px 12px;
  margin: 0 auto;
}

.error-main {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--err);
  font-size: 12.5px;
}

.error-icon {
  font-size: 13px;
}

.retry-btn {
  border: 1px solid var(--err);
  background: transparent;
  color: var(--err);
  font-size: 12px;
  border-radius: 6px;
  padding: 3px 10px;
  cursor: pointer;
  white-space: nowrap;
}

.retry-btn:hover:not(:disabled) {
  background: var(--err);
  color: #fff;
}

.retry-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 打字指示器：三点跳动 */
.typing {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 12px 16px;
}

.typing .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-faint);
  animation: dot-bounce 1.2s ease-in-out infinite;
}

.typing .dot:nth-child(2) {
  animation-delay: 0.15s;
}

.typing .dot:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes dot-bounce {
  0%, 60%, 100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

/* 工具结果键值表 */
.tool-result-body.kv {
  padding: 4px 0;
  white-space: normal;
}

.kv-row {
  display: flex;
  gap: 8px;
  padding: 2px 10px;
  font-size: 12px;
  line-height: 1.6;
}

.kv-row + .kv-row {
  border-top: 1px dashed var(--border);
}

.kv-key {
  flex: 0 0 auto;
  min-width: 72px;
  color: var(--text-weak);
  font-weight: 500;
}

.kv-val {
  color: var(--text);
  word-break: break-all;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 移动端适配 */
@media (max-width: 720px) {
  .body-col {
    max-width: 100%;
  }

  .msg-actions {
    opacity: 1;
  }
}
</style>
