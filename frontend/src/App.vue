<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">AI 智能助手</div>
      <nav class="nav">
        <router-link to="/" class="nav-link">Chat</router-link>
        <router-link to="/settings" class="nav-link">Settings</router-link>
      </nav>
    </header>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
// 顶层布局：顶部简洁导航 + 路由视图
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

.content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>