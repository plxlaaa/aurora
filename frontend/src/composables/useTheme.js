import { ref } from 'vue'

const THEME_KEY = 'ai_theme'

export function applyTheme(theme) {
  const resolved = theme || 'light'
  document.documentElement.setAttribute('data-theme', resolved)
}

export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'
}

export function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark'
  localStorage.setItem(THEME_KEY, next)
  applyTheme(next)
  return next
}

export function useTheme() {
  const theme = ref(currentTheme())
  const toggle = () => {
    theme.value = toggleTheme()
  }
  return { theme, toggle }
}
