<template>
  <div class="md" @click="onClick">
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div ref="body" class="md-body" v-html="html"></div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/common'
import 'highlight.js/styles/github-dark.min.css'

const props = defineProps({
  text: { type: String, default: '' },
  streaming: { type: Boolean, default: false }
})

const body = ref(null)

marked.use({
  gfm: true,
  breaks: true,
  renderer: {
    code(token) {
      const text = token?.text ?? token
      const lang = (token?.lang || '').split(/\s+/)[0]
      const validLang = lang && hljs.getLanguage(lang) ? lang : 'plaintext'
      let highlighted
      try {
        highlighted = hljs.highlight(text, { language: validLang, ignoreIllegals: true }).value
      } catch {
        highlighted = escapeHtml(text)
      }
      return (
        `<div class="code-block">` +
        `<div class="code-header">` +
        `<span class="code-lang">${escapeHtml(lang || 'text')}</span>` +
        `<button type="button" class="code-copy">复制</button>` +
        `</div>` +
        `<pre><code class="hljs language-${escapeHtml(validLang)}">${highlighted}</code></pre>` +
        `</div>`
      )
    }
  }
})

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

const html = computed(() => {
  const raw = props.text || ''
  if (!raw.trim()) return ''
  const parsed = marked.parse(raw, { async: false })
  return DOMPurify.sanitize(parsed, {
    ADD_ATTR: ['target', 'rel']
  })
})

async function onClick(e) {
  const btn = e.target.closest('.code-copy')
  if (!btn) return
  const block = btn.closest('.code-block')
  const code = block?.querySelector('code')?.textContent ?? ''
  try {
    await navigator.clipboard.writeText(code)
    btn.textContent = '已复制'
    setTimeout(() => {
      btn.textContent = '复制'
    }, 1500)
  } catch {
    btn.textContent = '复制失败'
    setTimeout(() => {
      btn.textContent = '复制'
    }, 1500)
  }
}
</script>

<style scoped>
.md {
  max-width: 100%;
  overflow-wrap: break-word;
}

.md-body :deep(p) {
  margin: 0.4em 0;
}

.md-body :deep(p:first-child) {
  margin-top: 0;
}

.md-body :deep(p:last-child) {
  margin-bottom: 0;
}

.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  margin: 0.9em 0 0.4em;
  line-height: 1.35;
}

.md-body :deep(h1:first-child),
.md-body :deep(h2:first-child),
.md-body :deep(h3:first-child) {
  margin-top: 0;
}

.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 0.4em 0;
  padding-left: 1.5em;
}

.md-body :deep(li) {
  margin: 0.15em 0;
}

.md-body :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.md-body :deep(blockquote) {
  margin: 0.5em 0;
  padding: 2px 12px;
  border-left: 3px solid var(--accent);
  color: var(--text-weak);
  background: var(--bg-soft);
  border-radius: 0 6px 6px 0;
}

.md-body :deep(table) {
  border-collapse: collapse;
  margin: 0.5em 0;
  width: 100%;
  font-size: 13px;
}

.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}

.md-body :deep(th) {
  background: var(--bg-soft);
  font-weight: 600;
}

.md-body :deep(code) {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 0.88em;
}

.md-body :deep(:not(pre) > code) {
  background: var(--bg-code);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
}

.md-body :deep(img) {
  max-width: 100%;
  border-radius: 8px;
}

.md-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 1em 0;
}

/* 代码块 */
.code-block {
  margin: 0.6em 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-code);
}

.code-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  background: var(--bg-soft);
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}

.code-lang {
  color: var(--text-weak);
  font-family: "SFMono-Regular", Consolas, Menlo, monospace;
}

.code-copy {
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text-weak);
}

.code-copy:hover {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-weak);
}

.code-block :deep(pre) {
  margin: 0;
  padding: 12px;
  overflow-x: auto;
  background: var(--bg-code);
}

.code-block :deep(code) {
  background: transparent;
  font-size: 12.5px;
  line-height: 1.55;
}

.code-block :deep(code.hljs) {
  background: transparent;
  padding: 0;
}
</style>
