import { createRouter, createWebHistory } from 'vue-router'
import { APP_ROUTES } from './routes.js'
import { useSessionStore } from '@/stores/session.js'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    ...APP_ROUTES,
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})

router.beforeEach(async (to) => {
  const session = useSessionStore()
  if (!session.demoUsersLoaded) {
    await session.initializeSession()
  }

  const allowedRoles = to.meta?.roles
  if (allowedRoles && Array.isArray(allowedRoles)) {
    const role = session.me?.building_role || 'USER'
    if (!allowedRoles.includes(role)) {
      return { name: 'dashboard' }
    }
  }
  return true
})

export default router
