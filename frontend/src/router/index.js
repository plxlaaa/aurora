import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '../api/http'

const routes = [
  {
    path: '/login',
    name: 'login',
    meta: { public: true },
    component: () => import('../views/LoginView.vue')
  },
  {
    path: '/',
    name: 'chat',
    component: () => import('../views/ChatView.vue')
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/SettingsView.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!getToken()) return { name: 'login' }
  return true
})

export default router
