<template>
  <div class="msg" :class="'role-' + message.role">
    <!-- 用户消息 -->
    <template v-if="message.role === 'user'">
      <div class="user-msg">
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
        <div v-if="message.content" class="bubble user-bubble">
          {{ message.content }}
        </div>
      </div>
    </template>

    <!-- 思考过程 -->
    <template v-else-if="message.role === 'thinking'">
      <div class="bubble thinking-bubble">
        <span class="tag">思考</span>
        <span class="thinking-text">{{ message.content || '…' }}</span>
      </div>
    </template>

    <!-- 工具调用状态 -->
    <template v-else-if="message.role === 'tool_call'">
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
        <pre class="tool-result-body">{{ resultText }}</pre>
      </div>
    </template>

    <!-- 工具结果 -->
    <template v-else-if="message.role === 'tool'">
      <div class="status-row tool-result-row">
        <span class="s-tool">{{ message.name || '工具结果' }}</span>
        <span v-if="message.content" class="s-detail">{{ trimResult(message.content) }}</span>
      </div>
    </template>

    <!-- 系统提示 -->
    <template v-else-if="message.role === 'info'">
      <div class="info-row">{{ message.content }}</div>
    </template>

    <!-- 错误 -->
    <template v-else-if="message.role === 'error'">
      <div class="info-row error">[错误] {{ message.content }}</div>
    </template>

    <!-- 助手消息（含流式光标） -->
    <template v-else>
      <div class="bubble assistant-bubble">
        <span v-if="streaming" class="cursor">▍</span>
        <span class="content">{{ message.content || '' }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fileUrl } from '../api'

const props = defineProps({
  message: { type: Object, required: true },
  streaming: { type: Boolean, default: false }
})

function stateSymbol(status) {
  if (status === 'done' || status === 'ok' || status === 'success') return '✓'
  if (status === 'error' || status === 'failed') return '✕'
  if (status === 'running' || status === 'streaming') return '◔'
  return '○'
}

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

function trimResult(text) {
  const s = String(text || '')
  return s.length > 120 ? s.slice(0, 120) + '…' : s
}
</script>

<style scoped>
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

.cursor {
  color: var(--accent);
  animation: blink 1s steps(1) infinite;
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
  margin-top: 4px;
}

.tool-result-body {
  margin: 0;
  max-width: 100%;
  max-height: 240px;
  overflow: auto;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
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
  background: var(--bg);
  border-radius: 10px;
  padding: 4px 12px;
  display: inline-block;
}

.info-row.error {
  color: var(--err);
  background: #fdefeb;
}
</style>
