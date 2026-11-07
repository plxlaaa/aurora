<template>
  <div class="settings-view">
    <div v-if="notice.text" class="notice" :class="notice.type">{{ notice.text }}</div>

    <!-- 模型端点 -->
    <section class="card">
      <header class="card-head">
        <h2 class="card-title">模型端点</h2>
        <div class="card-actions">
          <span
            v-for="(ep, key) in endpoints"
            :key="key"
            class="badge"
            :class="ep.available ? 'ok' : 'na'"
            :title="`检测模态: ${ep.detected_modality || '未知'}`"
          >
            {{ key }} · {{ ep.available ? '可用' : '未探测' }}
          </span>
        </div>
      </header>

      <div v-for="(ep, key) in endpoints" :key="key" class="model-block">
        <h3 class="block-title">{{ key }}</h3>
        <div class="grid">
          <label class="field">
            <span>base_url</span>
            <input v-model.trim="ep.base_url" type="text" placeholder="http://…/v1" />
          </label>
          <label class="field">
            <span>model_name</span>
            <input v-model.trim="ep.model_name" type="text" placeholder="模型名" />
          </label>
          <label class="field">
            <span>api_key（留空保持不变）</span>
            <input v-model="ep.api_key" type="password" placeholder="仅修改时填写" />
          </label>
          <label class="field">
            <span>modality</span>
            <select v-model="ep.modality">
              <option value="auto">auto</option>
              <option value="multimodal">multimodal</option>
              <option value="text">text</option>
            </select>
          </label>
          <label class="field check">
            <input v-model="ep.enabled" type="checkbox" />
            <span>启用</span>
          </label>
          <div class="field probe-note">
            <span>能力探测：{{ ep.detected_modality || '未知' }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 视觉路由 -->
    <section class="card">
      <h2 class="card-title">视觉路由</h2>
      <label class="field inline">
        <span>策略</span>
        <select v-model="visionPolicy" class="wide-select">
          <option value="prefer_vision_model">prefer_vision_model（优先独立视觉模型）</option>
          <option value="prefer_main_if_multimodal">prefer_main_if_multimodal（主模型多模态则复用）</option>
          <option value="vision_model_only">vision_model_only（仅用视觉模型）</option>
        </select>
      </label>
    </section>

    <!-- 上下文压缩 -->
    <section class="card">
      <h2 class="card-title">上下文压缩</h2>
      <div class="grid">
        <label class="field check">
          <input v-model="compress.enabled" type="checkbox" />
          <span>启用自动压缩</span>
        </label>
        <label class="field">
          <span>threshold</span>
          <input v-model.number="compress.threshold" type="number" step="0.05" min="0" max="1" />
        </label>
        <label class="field">
          <span>context_window（token）</span>
          <input v-model.number="compress.context_window" type="number" min="1" />
        </label>
        <label class="field">
          <span>keep_recent_turns（保留最近轮数）</span>
          <input v-model.number="compress.keep_recent_turns" type="number" min="0" />
        </label>
        <label class="field">
          <span>strategy</span>
          <select v-model="compress.strategy">
            <option value="map_reduce">map_reduce（分块摘要合并）</option>
            <option value="truncate">truncate（裁剪）</option>
          </select>
        </label>
        <label class="field full">
          <span>prompt</span>
          <textarea v-model="compress.prompt" rows="3" placeholder="摘要提示词"></textarea>
        </label>
      </div>
    </section>

    <!-- 执行环境 -->
    <section class="card">
      <h2 class="card-title">执行环境</h2>
      <label class="field inline">
        <span>mode</span>
        <select v-model="sandbox.mode" class="wide-select">
          <option value="sandbox">sandbox（Docker 隔离）</option>
          <option value="local">local（宿主执行，需 allow=true）</option>
        </select>
      </label>
      <div class="grid json-block">
        <label class="field full">
          <span>docker 配置（JSON）</span>
          <textarea v-model="dockerText" rows="8" spellcheck="false"></textarea>
        </label>
        <label class="field full">
          <span>local 配置（JSON）</span>
          <textarea v-model="localText" rows="4" spellcheck="false"></textarea>
        </label>
      </div>
    </section>

    <!-- 模型目录 -->
    <section class="card">
      <div class="card-head">
        <h2 class="card-title">模型目录</h2>
        <button class="small" :disabled="saving" @click="saveCatalog">保存目录</button>
      </div>
      <label class="field full">
        <span>catalogs（JSON，可选扩展信息）</span>
        <textarea v-model="catalogText" rows="6" spellcheck="false"></textarea>
      </label>
    </section>

    <!-- 工具中心 -->
    <ToolCenter />

    <!-- 代码执行 -->
    <section class="card">
      <h2 class="card-title">代码执行测试</h2>
      <p class="hint">在 sandbox / local 执行环境中运行 Python 代码。</p>
      <textarea
        v-model="codeInput"
        rows="5"
        spellcheck="false"
        placeholder="print('hello')"
      ></textarea>
      <div class="run-row">
        <button class="primary" :disabled="running || !codeInput.trim()" @click="runCode">
          {{ running ? '运行中…' : '运行' }}
        </button>
      </div>
      <div v-if="codeResult" class="run-result">
        <div class="run-meta">
          <span>exit_code: {{ codeResult.exit_code }}</span>
          <span v-if="codeResult.timed_out">超时</span>
          <span>mode: {{ codeResult.mode }}</span>
        </div>
        <div v-if="codeResult.stdout" class="run-block">
          <div class="run-label">stdout</div>
          <pre class="run-pre">{{ codeResult.stdout }}</pre>
        </div>
        <div v-if="codeResult.stderr" class="run-block">
          <div class="run-label err">stderr</div>
          <pre class="run-pre err">{{ codeResult.stderr }}</pre>
        </div>
      </div>
    </section>

    <section class="card">
      <h2 class="card-title">关于与更新</h2>
      <div class="update-row">
        <span class="ver-badge">{{ updateInfo.version || '…' }}</span>
        <span class="ver-meta" v-if="updateInfo.repo">{{ updateInfo.repo }}</span>
        <span class="ver-meta" v-if="updateInfo.token_configured === false" style="color: var(--text-weak)">
          （私有仓库建议配置 GITHUB_TOKEN）
        </span>
      </div>
      <div class="update-row" v-if="updateInfo.mirrors && updateInfo.mirrors.length">
        <span class="run-label">下载渠道（加速镜像优先，失败自动切换，官方兜底）：</span>
      </div>
      <div class="channel-list" v-if="updateInfo.mirrors && updateInfo.mirrors.length">
        <span class="channel" v-for="(m, i) in updateInfo.mirrors" :key="m">优先级{{ i + 1 }}：{{ m }}</span>
        <span class="channel">兜底：{{ updateInfo.official }}</span>
      </div>
      <div v-if="updateCheck" class="update-result">
        <template v-if="updateCheck.error">
          <span class="run-label err">检查失败：{{ updateCheck.error }}</span>
        </template>
        <template v-else>
          <div class="update-row">
            <span>最新版本 <b>{{ updateCheck.latest }}</b></span>
            <span class="ver-badge ok" v-if="updateCheck.has_update">可更新</span>
            <span class="ver-badge" v-else>已是最新</span>
          </div>
          <div class="run-label" v-if="updateCheck.asset">
            资产：{{ updateCheck.asset.name }}（{{ Math.round((updateCheck.asset.size || 0) / 1048576 * 10) / 10 }} MB）
          </div>
          <details v-if="updateCheck.notes"><summary>更新日志</summary>
            <pre class="notes">{{ updateCheck.notes }}</pre>
          </details>
        </template>
      </div>
      <div v-if="applyReport" class="update-result">
        <div class="update-row">
          <span class="run-label" :class="{ err: !applyReport.ok }">
            {{ applyReport.ok ? (applyReport.noop ? applyReport.message : '更新完成') : ('更新失败：' + applyReport.error) }}
          </span>
        </div>
        <ol class="steps">
          <li v-for="(s, i) in applyReport.steps || []" :key="i">{{ s }}</li>
        </ol>
        <div v-if="applyReport.channels && applyReport.channels.length" class="run-label">
          渠道尝试：
          <span v-for="c in applyReport.channels" :key="c.channel" class="channel" :class="{ fail: !c.ok }">
            {{ c.channel }}（{{ c.ok ? '成功 ' + c.seconds + 's' : '失败' }}）
          </span>
        </div>
        <div class="run-label" v-if="applyReport.restart">{{ applyReport.restart }}</div>
      </div>
      <footer class="footer">
        <button :disabled="updating" @click="doCheckUpdate">{{ checking ? '检查中…' : '检查更新' }}</button>
        <button class="primary" :disabled="checking || !updateCheck || !updateCheck.has_update"
                @click="doApplyUpdate">
          {{ updating ? '更新中，请勿关闭页面…' : '一键更新' }}
        </button>
      </footer>
    </section>

    <footer class="footer">
      <button class="primary" :disabled="saving" @click="saveAll">
        {{ saving ? '保存中…' : '保存全部' }}
      </button>
      <button :disabled="loading" @click="loadAll">刷新</button>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import ToolCenter from '../components/ToolCenter.vue'
import {
  getModels,
  saveModels,
  getSandbox,
  saveSandbox,
  getModelCatalog,
  saveModelCatalog,
  runCode as execCode,
  getUpdateInfo,
  checkUpdate,
  applyUpdate
} from '../api'

const notice = ref({ text: '', type: 'ok' })
const saving = ref(false)
const loading = ref(false)
const running = ref(false)

const endpoints = reactive({})
const visionPolicy = ref('prefer_vision_model')
const compress = reactive({
  enabled: true,
  threshold: 0.8,
  context_window: 8192,
  keep_recent_turns: 6,
  strategy: 'map_reduce',
  prompt: ''
})
const sandbox = reactive({ mode: 'sandbox' })
const dockerText = ref('')
const localText = ref('')
const catalogText = ref('')
const codeInput = ref('')
const codeResult = ref(null)

let noticeTimer = null
function notify(text, type = 'ok') {
  notice.value = { text, type }
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => {
    notice.value = { text: '', type: 'ok' }
  }, 3000)
}

/* ---------- 加载 ---------- */
function applyModels(data) {
  const eps = data?.endpoints || {}
  // 清空并重建，保留 api_key 输入状态仅用于编辑
  for (const key of Object.keys(endpoints)) delete endpoints[key]
  for (const [key, ep] of Object.entries(eps)) {
    endpoints[key] = {
      base_url: ep.base_url || '',
      model_name: ep.model_name || '',
      api_key: '',
      modality: ep.modality || 'auto',
      enabled: ep.enabled !== false,
      available: !!ep.available,
      detected_modality: ep.detected_modality || ''
    }
  }
  const vr = data?.vision_routing || {}
  visionPolicy.value = vr.policy || 'prefer_vision_model'
  const cp = data?.compress || {}
  compress.enabled = cp.enabled !== false
  compress.threshold = cp.threshold ?? 0.8
  compress.context_window = cp.context_window ?? 8192
  compress.keep_recent_turns = cp.keep_recent_turns ?? 6
  compress.strategy = cp.strategy || 'map_reduce'
  compress.prompt = cp.prompt || ''
}

function applySandbox(data) {
  sandbox.mode = data.mode || 'sandbox'
  dockerText.value = JSON.stringify(data.docker || {}, null, 2)
  localText.value = JSON.stringify(data.local || {}, null, 2)
}

async function loadModels() {
  const data = await getModels()
  applyModels(data)
}

async function loadSandboxCfg() {
  const data = await getSandbox()
  applySandbox(data)
}

async function loadCatalog() {
  const data = await getModelCatalog()
  catalogText.value = JSON.stringify(data?.catalogs || {}, null, 2)
}

async function loadAll() {
  loading.value = true
  notify('')
  try {
    await Promise.all([loadModels(), loadSandboxCfg(), loadCatalog()])
    notify('已刷新')
  } catch (e) {
    notify('读取失败：' + e.message, 'err')
  } finally {
    loading.value = false
  }
}

/* ---------- 保存 ---------- */
function buildEndpointsPayload() {
  const out = {}
  for (const [key, ep] of Object.entries(endpoints)) {
    const item = {
      base_url: ep.base_url,
      model_name: ep.model_name,
      modality: ep.modality,
      enabled: ep.enabled
    }
    // api_key 留空时不发送，后端保留原值
    if (ep.api_key) item.api_key = ep.api_key
    out[key] = item
  }
  return out
}

function parseJson(text, label) {
  const trimmed = text.trim()
  if (!trimmed) return {}
  try {
    return JSON.parse(trimmed)
  } catch (e) {
    throw new Error(`${label} JSON 解析失败：${e.message}`)
  }
}

async function saveAll() {
  saving.value = true
  try {
    await saveModels({
      endpoints: buildEndpointsPayload(),
      vision_routing: { policy: visionPolicy.value },
      compress: {
        enabled: compress.enabled,
        threshold: compress.threshold,
        context_window: compress.context_window,
        keep_recent_turns: compress.keep_recent_turns,
        strategy: compress.strategy,
        prompt: compress.prompt
      }
    })
    await saveSandbox({
      mode: sandbox.mode,
      docker: parseJson(dockerText.value, 'docker'),
      local: parseJson(localText.value, 'local')
    })
    notify('✓ 保存成功')
  } catch (e) {
    notify('保存失败：' + e.message, 'err')
  } finally {
    saving.value = false
  }
}

async function saveCatalog() {
  saving.value = true
  try {
    const catalogs = parseJson(catalogText.value, 'catalogs')
    await saveModelCatalog({ catalogs })
    notify('✓ 目录已保存')
  } catch (e) {
    notify('保存目录失败：' + e.message, 'err')
  } finally {
    saving.value = false
  }
}

/* ---------- 代码执行 ---------- */
async function runCode() {
  running.value = true
  codeResult.value = null
  try {
    codeResult.value = await execCode(codeInput.value)
  } catch (e) {
    notify('执行失败：' + e.message, 'err')
  } finally {
    running.value = false
  }
}

/* ---------- 关于与更新 ---------- */
const updateInfo = reactive({ version: '', repo: '', mirrors: [], official: '', backup_dir: '', token_configured: true })
const updateCheck = ref(null)
const applyReport = ref(null)
const checking = ref(false)
const updating = ref(false)

async function loadUpdateInfo() {
  try {
    const d = await getUpdateInfo()
    Object.assign(updateInfo, d)
  } catch { /* 静默：更新器信息非关键路径 */ }
}

async function doCheckUpdate() {
  checking.value = true
  applyReport.value = null
  try {
    updateCheck.value = await checkUpdate()
  } catch (e) {
    updateCheck.value = { error: e.message }
  } finally {
    checking.value = false
  }
}

async function doApplyUpdate() {
  if (!confirm(`确认更新到 ${updateCheck.value?.latest} ？更新前会自动备份，过程约 1-3 分钟，请勿关闭页面。`)) return
  updating.value = true
  applyReport.value = null
  try {
    applyReport.value = await applyUpdate(updateCheck.value?.tag || '', '')
    if (applyReport.value.ok && !applyReport.value.noop) notify('更新完成，服务已重启')
    else if (applyReport.value.ok) notify(applyReport.value.message || '已是最新版本')
    else notify('更新失败：' + applyReport.value.error, 'err')
  } catch (e) {
    applyReport.value = { ok: false, error: e.message, steps: [] }
    notify('更新失败：' + e.message, 'err')
  } finally {
    updating.value = false
  }
}

onMounted(() => { loadAll(); loadUpdateInfo() })
</script>

<style scoped>
.settings-view {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 20px;
  background: var(--bg);
  max-width: 980px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  position: relative;
}

.notice {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 14px;
  font-size: 13px;
}

.notice.ok {
  color: var(--ok);
  border-color: var(--ok);
}

.notice.err {
  color: var(--err);
  border-color: var(--err);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.card-head .card-title {
  margin: 0;
}

.card-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.model-block {
  margin-top: 12px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}

.block-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-weak);
}

.hint {
  margin: 0 0 12px;
  color: var(--text-weak);
  font-size: 12px;
}

.probe-note {
  justify-content: flex-end;
  padding-bottom: 8px;
}

.wide-select {
  min-width: 320px;
}

.json-block {
  margin-top: 12px;
}

/* 代码执行 */
.run-row {
  margin-top: 10px;
}

.run-result {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  padding: 10px 12px;
}

.run-meta {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--text-weak);
  margin-bottom: 6px;
}

.run-pre {
  margin: 4px 0 0;
  padding: 8px 10px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow: auto;
}

.run-pre.err {
  color: var(--err);
  border-color: var(--err);
}

.run-label {
  font-size: 11px;
  color: var(--text-weak);
  margin-top: 8px;
}

.run-label.err {
  color: var(--err);
}

button.small {
  padding: 4px 10px;
  font-size: 12px;
}

.footer {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  padding-bottom: 24px;
}

/* ---------- 关于与更新 ---------- */
.update-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 6px 0;
}

.ver-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  font-size: 12px;
  font-weight: 600;
}

.ver-badge.ok {
  color: var(--ok, #2e9e5b);
  border-color: var(--ok, #2e9e5b);
}

.ver-meta {
  font-size: 12px;
  color: var(--text-weak);
}

.channel-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 6px 0 10px;
}

.channel {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 8px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-weak);
}

.channel.fail {
  color: var(--err);
  border-color: var(--err);
}

.update-result {
  margin: 10px 0;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-panel);
}

.steps {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: 12px;
  color: var(--text-weak);
}

.notes {
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 200px;
  overflow: auto;
}
</style>
