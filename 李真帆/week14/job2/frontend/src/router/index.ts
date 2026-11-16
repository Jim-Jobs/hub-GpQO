import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/kb' },
    { path: '/kb', component: () => import('@/pages/KnowledgeBase/index.vue') },
    { path: '/chat', component: () => import('@/pages/Chat/index.vue') },
  ],
})

export default router
