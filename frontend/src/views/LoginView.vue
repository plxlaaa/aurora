<template>
  <div class="login-view">
    <div class="login-card">
      <h1 class="title">AI 智能助手</h1>
      <p class="subtitle">登录后开始对话</p>

      <div v-if="checking" class="checking">检测鉴权配置…</div>

      <form v-else-if="authRequired" class="login-form" @submit.prevent="submit">
        <label class="field">
          <span>密码或 API Key</span>
          <input
            v-model.trim="credential"
            type="password"
            placeholder="请输入密码或 API Key"
            autofocus
          />
        </label>
        <div v-if="error" class="form-error">{{ error }}</div>
        <button class="primary login-btn" :disabled="submitting || !credential">
          {{ submitting ? '登录中…' : '登录' }}
        </button>
      </form>

      <div v-else class="auth-off">
        <p>当前未开启鉴权，点击进入对话。</p>
        <button class="primary" @click="enter">进入对话</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api'
import { setToken } from '../api/http'

const router = useRouter()
const checking = ref(true)
const authRequired = ref(false)
const credential = ref('')
const error = ref('')
const submitting = ref(false)

async function enter() {
  router.push('/')
}

async function submit() {
  submitting.value = true
  error.value = ''
  try {
    // 后端 login 接受 password 或 api_key 任一字段
    const res = await login(credential.value, credential.value)
    setToken(res.token)
    router.push('/')
  } catch (e) {
    error.value = e.message || '登录失败'
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  // 探测鉴权是否开启：无凭据调用 login，auth 关闭时返回 token，开启时返回 401
  try {
    const res = await login('', '')
    setToken(res.token)
    router.push('/')
  } catch {
    authRequired.value = true
  } finally {
    checking.value = false
  }
})
</script>

<style scoped>
.login-view {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
}

.login-view::before {
  content: '';
  position: fixed;
  inset: 0;
  background: var(--gradient);
  opacity: 0.06;
  pointer-events: none;
}

.login-card {
  width: 360px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 36px 30px;
  text-align: center;
  box-shadow: 0 8px 32px var(--shadow-lg);
}

.title {
  margin: 0;
  font-size: 20px;
}

.subtitle {
  margin: 8px 0 24px;
  color: var(--text-weak);
  font-size: 13px;
}

.checking {
  color: var(--text-weak);
  padding: 20px 0;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  text-align: left;
}

.form-error {
  color: var(--err);
  font-size: 12px;
}

.login-btn {
  width: 100%;
  margin-top: 4px;
}

.auth-off p {
  color: var(--text-weak);
  margin-bottom: 16px;
}
</style>
