<template>
  <div class="settings-view">
    <div v-if="notice" class="notice" :class="noticeType">{{ notice }}</div>

    <section class="panel">
      <header class="panel-head">
        <h2 class="panel-title">模型端点</h2>
        <span
          v-for="(m, k) in models"
          :key="k"
          class="probe"
          :class="probeClass(m)"
        >
          {{ probeText(m) }}
        </span>
      </header>

      <div v-for="(m, key) in models" :key="key" class="model-block">
        <h3 class="block-title">{{ key }}</h3>
        <div class="grid">
          <label class="field">
            <span>base_url</span>
            <input
              v-model.trim="m.base_url"
              type="text"
              placeholder="http://…"
            />
          </label>
          <label class="field">
            <span>model_name</span>
            <input v-model.trim="m.model_name" type="text" placeholder="模型名" />
          </label>
          <label class="field">
            <span>api_key</span>
            <input v-model="m.api_key" type="password" placeholder="API Key" />
          </label>
          <label class="field">
            <span>modality</span>
            <select v-model="m.modality">
              <option value="text">text</option>
              <option value="vision">vision</option>
              <option value="multimodal">multimodal</option>
            </select>
          </label>
          <label class="field check">
            <input v-model="m.enabled" type="checkbox" />
            <span>启用</span>
          </label>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2 class="panel-title">视觉路由</h2>
      <label class="field inline">
        <span>视觉路线策略</span>
        <select v-model="visionPolicy">
          <option value="prefer_vision_model">prefer_vision_model</option>
          <option value="prefer_main_if_multimodal">
            prefer_main_if_multimodal
          </option>
          <option value="vision_model_only">vision_model_only</option>
        </select>
      </label>
    </section>

    <section class="panel">
      <h2 class="panel-title">上下文压缩</h2>
      <div class="grid">
        <label class="field">
          <span>threshold</span>
          <input v-model.number="compress.threshold" type="number" />
        </label>
        <label class="field">
          <span>context_window</span>
          <input v-model.number="compress.context_window" type="number" />
        </label>
        <label class="field">
          <span>keep_recent_turns</span>
          <input v-model.number="compress.keep_recent_turns" type="number" />
        </label>
        <label class="field">
          <span>strategy</span>
          <select v-model="compress.strategy">
            <option value="summarize">summarize</option>
            <option value="truncate">truncate</option>
            <option value="drop_oldest">drop_oldest</option>
          </select>
        </label>
        <label class="field full">
          <span>prompt</span>
          <textarea
            v-model="compress.prompt"
            rows="3"
            placeholder="压缩提示词"
          ></textarea>
        </label>
      </div>
    </section>

    <section class="panel">
      <h2 class="panel-title">执行环境</h2>
      <div class="grid">
        <label class="field">
          <span>mode</span>
          <select v-model="sandbox.mode">
            <option value="docker">docker</option>
            <option value="local">local</option>
          </select>
        </label>
      </div>

      <h3 v-if="sandbox.mode === 'docker'" class="block-title">docker</h3>
      <div v-if="sandbox.mode === 'docker'" class="grid">
        <label
          v-for="(v, k) in sandbox.docker || {}"
          :key="k"
          class="field"
        >
          <span>{{ k }}</span>
          <input v-model.trim="sandbox.docker[k]" type="text" />
        </label>
      </div>

      <h3 v-if="sandbox.mode !== 'docker'" class="block-title">local</h3>
      <div v-if="sandbox.mode !== 'docker'" class="grid">
        <label v-for="(v, k) in sandbox.local || {}" :key="k" class="field">
          <span>{{ k }}</span>
          <input
            v-if="sandbox.local[k] === true || sandbox.local[k] === false"
            v-model="sandbox.local[k]"
            type="checkbox"
          />
          <input
            v-else
            v-model.trim="sandbox.local[k]"
            type="text"
          />
        </label>
      </div>
    </section>

    <footer class="footer">
      <button class="primary" :disabled="saving" @click="saveAll">
        {{ saving ? '保存中…' : '保存' }}
      </button>
      <button :disabled="loading" @click="loadAll">刷新</button>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { getModels, saveModels, getSandbox, saveSandbox } from '../api'

const notice = ref('')
const noticeType = ref('ok')
const saving = ref(false)
const loading = ref(false)

const visionPolicy = ref('prefer_vision_model')

const models = reactive({
  main: {
    base_url: '',
    model_name: '',
    api_key: '',
    enabled: true,
    modality: 'text',
    available: false,
    detected_modality: ''
  },
  vision: {
    base_url: '',
    model_name: '',
    api_key: '',
    enabled: false,
    modality: 'vision',
    available: false,
    detected_modality: ''
  },
  compressor: {
    base_url: '',
    model_name: '',
    api_key: '',
    enabled: false,
    modality: 'text',
    available: false,
    detected_modality: ''
  }
})

const compress = reactive({
  threshold: 0,
  context_window: 0,
  keep_recent_turns: 0,
  strategy: 'summarize',
  prompt: ''
})

const sandbox = reactive({
  mode: 'local',
  docker: {},
  local: {}
})

function setNotice(msg, type = 'ok') {
  notice.value = msg
  noticeType.value = type
  setTimeout(() => {
    notice.value = ''
  }, 3000)
}

function probeClass(m) {
  if (m.available) return 'probe-ok'
  if (m.detected_modality) return 'probe-warn'
  return 'probe-na'
}

function probeText(m) {
  if (m.available) return `✓ available${m.detected_modality ? ' · ' + m.detected_modality : ''}`
  if (m.detected_modality) return `◔ ${m.detected_modality}`
  return '○ 未探测'
}

function applyModels(data) {
  for (const key of ['main', 'vision', 'compressor']) {
    const src = data[key] || data[`${key}_model`] || {}
    models[key].base_url = src.base_url || ''
    models[key].model_name = src.model_name || src.model || ''
    models[key].api_key = src.api_key || ''
    models[key].enabled = !!src.enabled
    models[key].modality = src.modality || 'text'
    models[key].available = !!src.available
    models[key].detected_modality = src.detected_modality || ''
  }
  // 兼容顶层的 vision_policy
  const p =
    data.vision_policy ||
    data.vision_policy_value ||
    visionPolicy.value
  visionPolicy.value = p
}

function applyCompress(data) {
  compress.threshold = data.threshold ?? 0
  compress.context_window = data.context_window ?? 0
  compress.keep_recent_turns = data.keep_recent_turns ?? 0
  compress.strategy = data.strategy || 'summarize'
  compress.prompt = data.prompt || ''
}

async function loadModels() {
  const data = await getModels()
  if (data?.vision_policy) visionPolicy.value = data.vision_policy
  applyModels(data)
  // 压缩配置可能也在模型响应中，或独立字段
  if (data?.compression || data?.compress) {
    applyCompress(data.compression || data.compress)
  }
}

async function loadSandbox() {
  const data = await getSandbox()
  if (data.mode) sandbox.mode = data.mode
  sandbox.docker = { ...(data.docker || {}) }
  sandbox.local = { ...(data.local || {}) }
}

async function loadAll() {
  loading.value = true
  setNotice('')
  try {
    await Promise.all([loadModels(), loadSandbox()])
    setNotice('已刷新')
  } catch (e) {
    setNotice('读取失败：' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function saveAll() {
  saving.value = true
  try {
    const m = JSON.parse(JSON.stringify(models))
    const modelsPayload = {
      main: m.main,
      vision: m.vision,
      compressor: m.compressor,
      vision_policy: visionPolicy.value,
      compression: {
        threshold: compress.threshold,
        context_window: compress.context_window,
        keep_recent_turns: compress.keep_recent_turns,
        strategy: compress.strategy,
        prompt: compress.prompt
      }
    }
    await saveModels(modelsPayload)
    await saveSandbox({
      mode: sandbox.mode,
      docker: sandbox.docker,
      local: sandbox.local
    })
    setNotice('✓ 保存成功')
  } catch (e) {
    setNotice('保存失败：' + e.message, 'error')
  } finally {
    saving.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.settings-view {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 20px;
  background: var(--bg);
  max-width: 860px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.panel-title {
  margin: 0 0 14px;
  font-size: 15px;
  font-weight: 600;
}

.panel-head .panel-title {
  margin: 0;
}

.block-title {
  margin: 16px 0 8px;
  font-size: 13px;
  font-weight: 600;
  text-transform: capitalize;
  color: var(--text-weak);
}

.probe {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
  color: var(--text-weak);
}

.probe-ok {
  color: var(--ok);
  border-color: var(--ok);
}

.probe-warn {
  color: var(--warn);
  border-color: var(--warn);
}

.probe-na {
  color: var(--text-weak);
}

.model-block {
  margin-top: 12px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-weak);
}

.field.check {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  align-self: end;
  padding-bottom: 8px;
  color: var(--text);
}

.field.inline {
  flex-direction: row;
  align-items: center;
  gap: 10px;
}

.field.full {
  grid-column: 1 / -1;
}

.label-span {
  color: var(--text-weak);
}

textarea {
  resize: vertical;
  min-height: 60px;
}

.footer {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  padding-bottom: 24px;
}

.notice {
  position: sticky;
  top: 0;
  z-index: 10;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
  border: 1px solid var(--border);
  background: var(--bg-panel);
}

.notice.ok {
  color: var(--ok);
  border-color: var(--ok);
}

.notice.error {
  color: var(--err);
  border-color: var(--err);
}
</style>