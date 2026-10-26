import DashboardView from '@/views/dashboard/DashboardView.vue'
import ReadingsView from '@/views/readings/ReadingsView.vue'
import AccountingView from '@/views/accounting/AccountingView.vue'
import ForumView from '@/views/forum/ForumView.vue'
import PostDetailView from '@/views/forum/PostDetailView.vue'
import DocumentsView from '@/views/documents/DocumentsView.vue'
import WelcomeView from '@/views/entry/WelcomeView.vue'
import ThemeLabView from '@/views/theme/ThemeLabView.vue'

export const APP_ROUTES = [
  {
    path: '/',
    name: 'dashboard',
    component: DashboardView,
    meta: {
      layout: 'AppLayout',
      roles: ['USER', 'BOARD', 'CHAIRMAN', 'ACCOUNTANT'],
      navKey: 'dashboard',
      icon: 'space_dashboard',
    },
  },
  {
    path: '/readings',
    name: 'readings',
    component: ReadingsView,
    meta: {
      layout: 'AppLayout',
      roles: ['USER', 'BOARD', 'CHAIRMAN', 'ACCOUNTANT'],
      navKey: 'readings',
      icon: 'speed',
    },
  },
  {
    path: '/forum',
    name: 'forum',
    component: ForumView,
    meta: {
      layout: 'AppLayout',
      roles: ['USER', 'BOARD', 'CHAIRMAN', 'ACCOUNTANT'],
      navKey: 'forum',
      icon: 'forum',
    },
  },
  {
    path: '/forum/:postId',
    name: 'forum-detail',
    component: PostDetailView,
    meta: {
      layout: 'AppLayout',
      roles: ['USER', 'BOARD', 'CHAIRMAN', 'ACCOUNTANT'],
    },
  },
  {
    path: '/documents',
    name: 'documents',
    component: DocumentsView,
    meta: {
      layout: 'AppLayout',
      roles: ['USER', 'BOARD', 'CHAIRMAN', 'ACCOUNTANT'],
      navKey: 'documents',
      icon: 'folder_open',
    },
  },
  {
    path: '/accounting',
    name: 'accounting',
    component: AccountingView,
    meta: {
      layout: 'AppLayout',
      roles: ['BOARD', 'CHAIRMAN', 'ACCOUNTANT'],
      navKey: 'accounting',
      icon: 'account_balance',
    },
  },
  {
    path: '/welcome',
    name: 'welcome',
    component: WelcomeView,
    meta: {
      layout: 'PublicLayout',
      roles: ['USER', 'BOARD', 'CHAIRMAN', 'ACCOUNTANT'],
    },
  },
  {
    path: '/theme-lab',
    name: 'theme-lab',
    component: ThemeLabView,
    meta: {
      layout: 'AppLayout',
      roles: ['USER', 'BOARD', 'CHAIRMAN', 'ACCOUNTANT'],
    },
  },
]
