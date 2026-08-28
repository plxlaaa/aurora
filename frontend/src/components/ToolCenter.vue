<template>
  <section class="card">
    <div class="tc-head">
      <h2 class="card-title">工具中心</h2>
      <div class="tabs">
        <button
          v-for="t in TABS"
          :key="t.key"
          class="tab"
          :class="{ active: tab === t.key }"
          @click="switchTab(t.key)"
        >
          {{ t.label }}
        </button>
      </div>
    </div>

    <!-- 总览 -->
    <div v-if="tab === 'overview'" class="pane">
      <p class="hint">
        模型可通过 function calling 或文本协议调用以下工具。内置 read_skill / run_skill /
        run_code 由后端执行。
      </p>
      <div class="tool-table">
        <div v-for="t in snapshotTools" :key="t.source + ':' + t.name" class="tool-row">
          <span class="tool-name">{{ t.name }}</span>
          <span class="badge" :class="SOURCE_CLASS[t.source] || ''">{{ t.source }}</span>
          <span class="tool-desc">{{ t.description || '—' }}</span>
          <span v-if="t.type" class="tool-type">{{ t.type }}</span>
          <span class="badge" :class="t.enabled ? 'ok' : 'na'">
            {{ t.enabled ? '启用' : '停用' }}
          </span>
        </div>
        <p v-if="!snapshotTools.length" class="hint">暂无工具。</p>
      </div>
    </div>

    <!-- Skills 管理 -->
    <div v-if="tab === 'skills'" class="pane">
      <div class="row">
        <label class="upload-btn">
          <input type="file" accept=".zip" @change="onUploadZip" />
          上传 Skill zip
        </label>
        <span v-if="uploading" class="hint">上传中…</span>
      </div>

      <div class="skill-list">
        <div
          v-for="s in skills"
          :key="s.name"
          class="item-row"
          :class="{ selected: selectedSkill === s.name }"
          @click="selectSkill(s.name)"
        >
          <span class="item-name">{{ s.name }}</span>
          <span class="item-desc">{{ s.description || '—' }}</span>
          <label class="switch" @click.stop>
            <input type="checkbox" :checked="!!s.enabled" @change="onToggleSkill(s)" />
            <span>{{ s.enabled ? '启用' : '停用' }}</span>
          </label>
          <button class="small danger" @click.stop="onDeleteSkill(s.name)">删除</button>
        </div>
        <p v-if="!skills.length" class="hint">暂无 Skill，可上传 zip 导入。</p>
      </div>

      <div v-if="selectedSkill" class="file-panel">
        <h3 class="panel-title">{{ selectedSkill }} 的文件</h3>
        <div class="file-list">
          <button
            v-for="f in skillFiles"
            :key="f.path"
            class="file-chip"
            :class="{ active: openedFile === f.path }"
            @click="openFile(f.path)"
          >
            {{ f.path }}
          </button>
        </div>
        <p v-if="!skillFiles.length" class="hint">无文本文件。</p>
        <div v-if="openedFile" class="editor">
          <div class="editor-head">
            <span class="editor-path">{{ openedFile }}</span>
            <button class="small" :disabled="savingFile" @click="saveFile">保存</button>
          </div>
          <textarea v-model="fileContent" rows="12" spellcheck="false"></textarea>
        </div>
      </div>
    </div>

    <!-- MCP 管理 -->
    <div v-if="tab === 'mcp'" class="pane">
      <div class="mcp-list">
        <div v-for="s in mcpServers" :key="s.name" class="item-row">
          <span class="item-name">{{ s.name }}</span>
          <span class="badge" :class="s.available ? 'ok' : 'err'">
            {{ s.available ? '已连接' : '未连接' }}
          </span>
          <span class="item-desc">
            {{ s.available ? `${s.tool_count} 个工具 · ${s.transport || ''}` : '—' }}
          </span>
          <button class="small" @click="onReconnectMcp(s.name)">重连</button>
          <button class="small" @click="editMcp(s)">编辑</button>
          <button class="small danger" @click="onDeleteMcp(s.name)">删除</button>
        </div>
        <p v-if="!mcpServers.length" class="hint">未配置 MCP server。</p>
      </div>

      <div class="editor">
        <div class="editor-head">
          <span class="editor-path">{{ mcpForm.name ? `编辑 ${mcpForm.name}` : '新增 MCP server' }}</span>
          <div class="editor-actions">
            <button class="small primary" :disabled="mcpSaving" @click="saveMcp">
              {{ mcpForm.name ? '更新' : '创建并连接' }}
            </button>
            <button v-if="mcpForm.name" class="small" @click="resetMcpForm">取消</button>
          </div>
        </div>
        <div class="grid">
          <label class="field">
            <span>name</span>
            <input v-model.trim="mcpForm.name" type="text" :disabled="!!mcpForm.name" />
          </label>
          <label class="field">
            <span>transport</span>
            <select v-model="mcpForm.transport">
              <option value="stdio">stdio</option>
              <option value="http">http</option>
              <option value="sse">sse</option>
            </select>
          </label>
          <label v-if="mcpForm.transport === 'stdio'" class="field">
            <span>command</span>
            <input v-model.trim="mcpForm.command" type="text" placeholder="python3 -m mcp_server" />
          </label>
          <label v-if="mcpForm.transport !== 'stdio'" class="field">
            <span>url</span>
            <input v-model.trim="mcpForm.url" type="text" placeholder="https://…/mcp" />
          </label>
          <label class="field check">
            <input v-model="mcpForm.enabled" type="checkbox" />
            <span>启用</span>
          </label>
          <label v-if="mcpForm.transport === 'stdio'" class="field full">
            <span>args（JSON 数组）</span>
            <textarea v-model="mcpForm.argsText" rows="2" spellcheck="false"></textarea>
          </label>
          <label v-if="mcpForm.transport === 'stdio'" class="field full">
            <span>env（JSON 对象）</span>
            <textarea v-model="mcpForm.envText" rows="2" spellcheck="false"></textarea>
          </label>
          <label v-if="mcpForm.transport !== 'stdio'" class="field full">
            <span>headers（JSON 对象）</span>
            <textarea v-model="mcpForm.headersText" rows="2" spellcheck="false"></textarea>
          </label>
        </div>
      </div>
    </div>

    <!-- 自定义工具 -->
    <div v-if="tab === 'custom'" class="pane">
      <div class="skill-list">
        <div v-for="t in customTools" :key="t.name" class="item-row">
          <span class="item-name">{{ t.name }}</span>
          <span class="badge" :class="t.type === 'http' ? 'info' : 'warn'">{{ t.type }}</span>
          <span class="item-desc">{{ t.description || '—' }}</span>
          <label class="switch">
            <input type="checkbox" :checked="!!t.enabled" @change="onToggleCustom(t)" />
            <span>{{ t.enabled ? '启用' : '停用' }}</span>
          </label>
          <button class="small" @click="editCustom(t.name)">编辑</button>
          <button class="small" @click="testCustom(t.name)">测试</button>
          <button class="small danger" @click="onDeleteCustom(t.name)">删除</button>
        </div>
        <p v-if="!customTools.length" class="hint">暂无自定义工具。</p>
      </div>

      <div class="editor">
        <div class="editor-head">
          <span class="editor-path">{{ customForm.isNew ? '新建自定义工具' : `编辑 ${customForm.name}` }}</span>
          <div class="editor-actions">
            <select v-if="customForm.isNew" v-model="customForm.template" class="small-select" @change="applyCustomTemplate">
              <option value="http">HTTP 模板</option>
              <option value="python">Python 模板</option>
            </select>
            <button class="small primary" :disabled="customSaving" @click="saveCustom">
              {{ customForm.isNew ? '创建' : '保存' }}
            </button>
          </div>
        </div>
        <label class="field full">
          <span>工具定义（JSON）</span>
          <textarea v-model="customForm.defText" rows="14" spellcheck="false"></textarea>
        </label>
      </div>

      <div v-if="testTarget" class="editor">
        <div class="editor-head">
          <span class="editor-path">测试 {{ testTarget }}</span>
          <button class="small primary" :disabled="testing" @click="runTest">运行测试</button>
        </div>
        <label class="field full">
          <span>arguments（JSON）</span>
          <textarea v-model="testArgsText" rows="3" spellcheck="false"></textarea>
        </label>
        <div v-if="testResult !== null" class="run-result">
          <div class="run-label">结果</div>
          <pre class="run-pre" :class="{ err: testIsError }">{{ testResult }}</pre>
        </div>
      </div>
    </div>

    <p v-if="notice" class="tc-notice" :class="noticeType">{{ notice }}</p>
  </section>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import {
  getToolsSnapshot,
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
  getCustomTools,
  getCustomTool,
  createCustomTool,
  updateCustomTool,
  deleteCustomTool,
  toggleCustomTool,
  testCustomTool
} from '../api'

const TABS = [
  { key: 'overview', label: '总览' },
  { key: 'skills', label: 'Skills' },
  { key: 'mcp', label: 'MCP' },
  { key: 'custom', label: '自定义工具' }
]

const SOURCE_CLASS = { builtin: 'info', skill: 'warn', custom: '', mcp: 'info' }

const HTTP_TEMPLATE = {
  name: 'my_tool',
  description: '工具说明',
  type: 'http',
  http: {
    url: 'https://api.example.com/{q}',
    method: 'GET',
    headers: {},
    timeout: 15
  },
  parameters: {
    type: 'object',
    properties: { q: { type: 'string', description: '查询词' } },
    required: ['q']
  }
}

const PYTHON_TEMPLATE = {
  name: 'my_tool',
  description: '工具说明',
  type: 'python',
  code: "def run(args):\n    return {'hello': args.get('name', 'world')}",
  parameters: {
    type: 'object',
    properties: { name: { type: 'string' } }
  }
}

const tab = ref('overview')
const notice = ref('')
const noticeType = ref('ok')
let noticeTimer = null

function notify(text, type = 'ok') {
  notice.value = text
  noticeType.value = type
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => (notice.value = ''), 3500)
}

/* ---------- 总览 ---------- */
const snapshotTools = ref([])

async function loadSnapshot() {
  const data = await getToolsSnapshot()
  snapshotTools.value = data?.tools || []
}

/* ---------- Skills ---------- */
const skills = ref([])
const uploading = ref(false)
const selectedSkill = ref('')
const skillFiles = ref([])
const openedFile = ref('')
const fileContent = ref('')
const savingFile = ref(false)

async function loadSkills() {
  const data = await getSkills()
  skills.value = data?.skills || []
}

async function onUploadZip(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  uploading.value = true
  try {
    const result = await uploadSkillZip(file)
    await loadSkills()
    await loadSnapshot()
    notify(`已导入 ${result.name}（${result.files} 个文件）`)
  } catch (err) {
    notify('上传失败：' + err.message, 'err')
  } finally {
    uploading.value = false
  }
}

function selectSkill(name) {
  if (selectedSkill.value === name) return
  selectedSkill.value = name
  openedFile.value = ''
  fileContent.value = ''
  loadSkillFiles()
}

async function loadSkillFiles() {
  skillFiles.value = []
  try {
    const data = await getSkillFiles(selectedSkill.value)
    skillFiles.value = data?.files || []
  } catch (err) {
    notify('读取文件列表失败：' + err.message, 'err')
  }
}

async function openFile(path) {
  try {
    const data = await readSkillFile(selectedSkill.value, path)
    openedFile.value = path
    fileContent.value = data.content
  } catch (err) {
    notify('读取失败：' + err.message, 'err')
  }
}

async function saveFile() {
  savingFile.value = true
  try {
    await writeSkillFile(selectedSkill.value, openedFile.value, fileContent.value)
    notify('文件已保存')
  } catch (err) {
    notify('保存失败：' + err.message, 'err')
  } finally {
    savingFile.value = false
  }
}

async function onToggleSkill(s) {
  const next = !s.enabled
  try {
    await toggleSkill(s.name, next)
    s.enabled = next
    await loadSnapshot()
  } catch (err) {
    notify('切换失败：' + err.message, 'err')
  }
}

async function onDeleteSkill(name) {
  if (!window.confirm(`删除 Skill「${name}」？该操作不可恢复。`)) return
  try {
    await deleteSkill(name)
    if (selectedSkill.value === name) {
      selectedSkill.value = ''
      skillFiles.value = []
      openedFile.value = ''
    }
    await loadSkills()
    await loadSnapshot()
    notify(`已删除 ${name}`)
  } catch (err) {
    notify('删除失败：' + err.message, 'err')
  }
}

/* ---------- MCP ---------- */
const mcpServers = ref([])
const mcpSaving = ref(false)
const mcpForm = reactive({
  name: '',
  transport: 'stdio',
  command: '',
  argsText: '[]',
  envText: '{}',
  url: '',
  headersText: '{}',
  enabled: true
})

async function loadMcp() {
  try {
    const [running, configs] = await Promise.all([getMcpServers(), getMcpServerConfigs()])
    const statusByName = new Map((running?.servers || []).map((s) => [s.name, s]))
    mcpServers.value = (configs?.servers || []).map((cfg) => {
      const status = statusByName.get(cfg.name)
      return {
        ...cfg,
        available: !!status?.available,
        tool_count: status?.tool_count ?? 0,
        transport: cfg.transport || 'stdio'
      }
    })
  } catch (err) {
    notify('读取 MCP 配置失败：' + err.message, 'err')
  }
}

function editMcp(s) {
  mcpForm.name = s.name
  mcpForm.transport = s.transport || 'stdio'
  mcpForm.command = s.command || ''
  mcpForm.argsText = JSON.stringify(s.args || [], null, 0)
  mcpForm.envText = JSON.stringify(s.env || {}, null, 0)
  mcpForm.url = s.url || ''
  mcpForm.headersText = JSON.stringify(s.headers || {}, null, 0)
  mcpForm.enabled = s.enabled !== false
}

function resetMcpForm() {
  mcpForm.name = ''
  mcpForm.transport = 'stdio'
  mcpForm.command = ''
  mcpForm.argsText = '[]'
  mcpForm.envText = '{}'
  mcpForm.url = ''
  mcpForm.headersText = '{}'
  mcpForm.enabled = true
}

function buildMcpCfg() {
  const cfg = {
    name: mcpForm.name,
    transport: mcpForm.transport,
    enabled: mcpForm.enabled
  }
  if (mcpForm.transport === 'stdio') {
    cfg.command = mcpForm.command
    try {
      cfg.args = JSON.parse(mcpForm.argsText || '[]')
    } catch (e) {
      throw new Error('args JSON 解析失败：' + e.message)
    }
    try {
      cfg.env = JSON.parse(mcpForm.envText || '{}')
    } catch (e) {
      throw new Error('env JSON 解析失败：' + e.message)
    }
  } else {
    cfg.url = mcpForm.url
    try {
      cfg.headers = JSON.parse(mcpForm.headersText || '{}')
    } catch (e) {
      throw new Error('headers JSON 解析失败：' + e.message)
    }
  }
  return cfg
}

async function saveMcp() {
  mcpSaving.value = true
  try {
    const cfg = buildMcpCfg()
    if (mcpForm.name) {
      await updateMcpServer(mcpForm.name, cfg)
    } else {
      await createMcpServer(cfg)
    }
    resetMcpForm()
    await loadMcp()
    await loadSnapshot()
    notify('MCP server 已保存')
  } catch (err) {
    notify('保存失败：' + err.message, 'err')
  } finally {
    mcpSaving.value = false
  }
}

async function onDeleteMcp(name) {
  if (!window.confirm(`删除 MCP server「${name}」？`)) return
  try {
    await deleteMcpServer(name)
    await loadMcp()
    await loadSnapshot()
    notify(`已删除 ${name}`)
  } catch (err) {
    notify('删除失败：' + err.message, 'err')
  }
}

async function onReconnectMcp(name) {
  try {
    await reconnectMcpServer(name)
    await loadMcp()
    await loadSnapshot()
    notify(`已重连 ${name}`)
  } catch (err) {
    notify('重连失败：' + err.message, 'err')
  }
}

/* ---------- 自定义工具 ---------- */
const customTools = ref([])
const customSaving = ref(false)
const testing = ref(false)
const customForm = reactive({
  isNew: true,
  name: '',
  template: 'http',
  defText: JSON.stringify(HTTP_TEMPLATE, null, 2)
})
const testTarget = ref('')
const testArgsText = ref('{}')
const testResult = ref(null)
const testIsError = ref(false)

async function loadCustom() {
  const data = await getCustomTools()
  customTools.value = data?.tools || []
}

function applyCustomTemplate() {
  const tpl = customForm.template === 'http' ? HTTP_TEMPLATE : PYTHON_TEMPLATE
  customForm.defText = JSON.stringify(tpl, null, 2)
}

async function saveCustom() {
  customSaving.value = true
  try {
    const tool = JSON.parse(customForm.defText)
    if (customForm.isNew) {
      await createCustomTool(tool)
    } else {
      await updateCustomTool(customForm.name, tool)
    }
    await loadCustom()
    await loadSnapshot()
    notify(`工具 ${tool.name} 已保存`)
  } catch (err) {
    notify('保存失败：' + err.message, 'err')
  } finally {
    customSaving.value = false
  }
}

async function editCustom(name) {
  try {
    const data = await getCustomToolRaw(name)
    customForm.isNew = false
    customForm.name = name
    customForm.defText = JSON.stringify(data.tool, null, 2)
    testTarget.value = ''
    testResult.value = null
  } catch (err) {
    notify('读取工具失败：' + err.message, 'err')
  }
}

async function onToggleCustom(t) {
  const next = !t.enabled
  try {
    await toggleCustomTool(t.name, next)
    t.enabled = next
    await loadSnapshot()
  } catch (err) {
    notify('切换失败：' + err.message, 'err')
  }
}

async function onDeleteCustom(name) {
  if (!window.confirm(`删除自定义工具「${name}」？`)) return
  try {
    await deleteCustomTool(name)
    if (customForm.name === name) {
      customForm.isNew = true
      customForm.name = ''
      applyCustomTemplate()
    }
    if (testTarget.value === name) {
      testTarget.value = ''
      testResult.value = null
    }
    await loadCustom()
    await loadSnapshot()
    notify(`已删除 ${name}`)
  } catch (err) {
    notify('删除失败：' + err.message, 'err')
  }
}

async function testCustom(name) {
  testTarget.value = name
  testResult.value = null
  testArgsText.value = '{}'
}

async function runTest() {
  testing.value = true
  try {
    const args = JSON.parse(testArgsText || '{}')
    const data = await testCustomTool(testTarget.value, args)
    const result = data?.result ?? {}
    testIsError.value = 'error' in result
    testResult.value = JSON.stringify(result, null, 2)
  } catch (err) {
    testIsError.value = true
    testResult.value = err.message
  } finally {
    testing.value = false
  }
}

// getCustomTool 返回 { tool }，重命名引用避免与本地变量混淆
const getCustomToolRaw = getCustomTool

function switchTab(key) {
  tab.value = key
  notice.value = ''
}

onMounted(() => {
  loadSnapshot()
  loadSkills()
  loadMcp()
  loadCustom()
})
</script>

<style scoped>
.tc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.tabs {
  display: flex;
  gap: 6px;
}

.tab {
  padding: 5px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
}

.tab.active {
  background: var(--bg-panel);
  border-color: var(--accent);
  color: var(--accent);
}

.pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tool-table {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tool-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
}

.tool-name {
  font-weight: 600;
  min-width: 130px;
}

.tool-desc {
  flex: 1 1 auto;
  color: var(--text-weak);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-type {
  color: var(--text-weak);
  font-size: 11px;
}

.badge {
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
  font-size: 11px;
}

.badge.ok {
  color: var(--ok);
  border-color: var(--ok);
}

.badge.err {
  color: var(--err);
  border-color: var(--err);
}

.badge.na {
  color: var(--text-weak);
}

.badge.info {
  color: var(--accent);
  border-color: var(--accent);
}

.badge.warn {
  color: var(--warn, #b8860b);
  border-color: var(--warn, #b8860b);
}

.row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.upload-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border: 1px dashed var(--border);
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  color: var(--text);
}

.upload-btn input[type='file'] {
  display: none;
}

.skill-list,
.mcp-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.item-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
}

.item-row.selected {
  border-color: var(--accent);
}

.item-name {
  font-weight: 600;
  font-size: 13px;
  min-width: 120px;
}

.item-desc {
  flex: 1 1 auto;
  color: var(--text-weak);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switch {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-weak);
  cursor: pointer;
}

.file-panel {
  border-top: 1px solid var(--border);
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.panel-title {
  margin: 0;
  font-size: 13px;
  color: var(--text-weak);
}

.file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.file-chip {
  padding: 3px 10px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: transparent;
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
}

.file-chip.active {
  border-color: var(--accent);
  color: var(--accent);
}

.editor {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.editor-path {
  font-size: 13px;
  font-weight: 600;
}

.editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.small-select {
  padding: 4px 8px;
  font-size: 12px;
}

textarea {
  width: 100%;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  box-sizing: border-box;
  resize: vertical;
}

input[type='text'],
select {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  box-sizing: border-box;
}

button.small {
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}

button.danger {
  color: var(--err);
  border-color: var(--err);
}

button.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

button {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: var(--text);
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.tc-notice {
  margin: 0;
  font-size: 12px;
}

.tc-notice.ok {
  color: var(--ok);
}

.tc-notice.err {
  color: var(--err);
}

.run-result {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}

.run-label {
  font-size: 11px;
  color: var(--text-weak);
  margin-bottom: 6px;
}

.run-pre {
  margin: 0;
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
</style>
