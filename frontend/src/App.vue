<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-logo">✦</span>
        <span>AI 智能助手</span>
      </div>
      <nav class="nav">
        <router-link to="/" class="nav-link">对话</router-link>
        <router-link to="/settings" class="nav-link">设置</router-link>
      </nav>
      <div class="topbar-right">
        <button
          class="icon-btn theme-btn"
          :title="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
          @click="toggle"
        >
          {{ theme === 'dark' ? '☀' : '☾' }}
        </button>
        <span v-if="token" class="auth-chip">已登录</span>
        <button v-if="token" class="logout-btn" @click="logout">退出</button>
      </div>
    </header>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { getToken, setToken, setUnauthorizedHandler } from './api/http'
import { useTheme } from './composables/useTheme'

const token = ref(getToken())
const router = useRouter()
const { theme, toggle } = useTheme()

setUnauthorizedHandler(() => {
  setToken('')
  token.value = ''
  router.push('/login')
})

function logout() {
  setToken('')
  token.value = ''
  router.push('/login')
}
</script>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 54px;
  flex: 0 0 auto;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
}

.brand-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--gradient);
  color: #fff;
  font-size: 14px;
  box-shadow: 0 2px 6px rgba(74, 125, 255, 0.35);
}

.nav {
  display: flex;
  gap: 4px;
}

.nav-link {
  padding: 6px 14px;
  border-radius: 8px;
  color: var(--text-weak);
  font-size: 13px;
  transition: color 0.15s ease, background 0.15s ease;
}

.nav-link:hover {
  color: var(--text);
  background: var(--bg-hover);
}

.nav-link.router-link-active {
  color: var(--accent);
  font-weight: 500;
  background: var(--accent-weak);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.icon-btn {
  width: 32px;
  height: 32px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-panel);
  color: var(--text);
}

.icon-btn:hover {
  background: var(--bg-hover);
}

.auth-chip {
  font-size: 12px;
  color: var(--ok);
}

.logout-btn {
  font-size: 12px;
  padding: 4px 10px;
}

.content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>
