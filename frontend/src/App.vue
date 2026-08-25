<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">AI 智能助手</div>
      <nav class="nav">
        <router-link to="/" class="nav-link">对话</router-link>
        <router-link to="/settings" class="nav-link">设置</router-link>
      </nav>
      <div class="topbar-right">
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

const token = ref(getToken())
const router = useRouter()

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
  height: 52px;
  flex: 0 0 auto;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}

.brand {
  font-size: 15px;
  font-weight: 600;
}

.nav {
  display: flex;
  gap: 4px;
}

.nav-link {
  padding: 6px 14px;
  border-radius: 6px;
  color: var(--text-weak);
  font-size: 13px;
}

.nav-link:hover {
  color: var(--text);
  background: var(--bg-hover);
}

.nav-link.router-link-active {
  color: var(--accent);
  font-weight: 500;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
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
