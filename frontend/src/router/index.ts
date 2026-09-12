/**
 * 路由表。
 *
 * 组织原则：**前台用短路径、后台统一挂在 `/admin` 下**。
 * 好处是路由守卫只需要判断前缀，而不是维护一张「哪些页面算后台」的清单——
 * 清单一定会漏，前缀不会。
 *
 * meta 约定：
 * - `title` 页面标题（写入 document.title）
 * - `requiresAuth` 需要登录
 * - `requiresAdmin` 需要站长角色（隐含 requiresAuth）
 * - `layout` 使用哪个布局组件（缺省用 DefaultLayout）
 */
import type { RouteRecordRaw } from 'vue-router'
import { createRouter, createWebHistory } from 'vue-router'

import { tokenStore } from '@/api'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    requiresAuth?: boolean
    requiresAdmin?: boolean
    layout?: 'default' | 'admin' | 'blank'
  }
}

const routes: RouteRecordRaw[] = [
  // ---------------------------------------------------------------- 前台
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
    meta: { title: '首页' },
  },
  {
    path: '/article/:slug',
    name: 'article-detail',
    component: () => import('@/views/ArticleDetailView.vue'),
    meta: { title: '文章' },
  },
  {
    path: '/categories',
    name: 'categories',
    component: () => import('@/views/CategoriesView.vue'),
    meta: { title: '分类' },
  },
  {
    path: '/tags',
    name: 'tags',
    component: () => import('@/views/TagsView.vue'),
    meta: { title: '标签' },
  },
  {
    path: '/archive',
    name: 'archive',
    component: () => import('@/views/ArchiveView.vue'),
    meta: { title: '归档' },
  },
  {
    path: '/series',
    name: 'series',
    component: () => import('@/views/SeriesView.vue'),
    meta: { title: '系列' },
  },
  {
    path: '/series/:slug',
    name: 'series-detail',
    component: () => import('@/views/SeriesDetailView.vue'),
    meta: { title: '系列详情' },
  },
  {
    path: '/about',
    name: 'about',
    component: () => import('@/views/AboutView.vue'),
    meta: { title: '关于' },
  },
  // 占位页：导航里先留好入口，内容后续再补。共用一个组件、靠 meta 传文案，
  // 比给每个空页面建一个文件更省事，也不会让路由表虚胖。
  {
    path: '/links',
    name: 'links',
    component: () => import('@/views/PlaceholderView.vue'),
    meta: { title: '友情链接' },
    props: { heading: '友情链接', hint: '这里会放一些我经常逛的站点。' },
  },
  {
    path: '/guestbook',
    name: 'guestbook',
    component: () => import('@/views/PlaceholderView.vue'),
    meta: { title: '留言板' },
    props: { heading: '留言板', hint: '想说什么都可以，不限于文章内容。' },
  },

  // ---------------------------------------------------------------- 认证
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', layout: 'blank' },
  },

  // ---------------------------------------------------------------- 后台
  {
    path: '/admin',
    // 这里刻意 **不写 component**。
    //
    // 布局由 App.vue 依据 meta.layout 统一渲染。如果这里再挂一个
    // AdminLayout 作为路由组件，顶层 router-view 会把它渲染一遍，
    // 而 App.vue 又按 meta.layout 再包一层 —— 结果是嵌套两层 AdminLayout：
    // 侧边栏重复出现，且内层实例拿不到 slot 内容，子页面（文章列表等）根本不渲染。
    //
    // 「布局用 meta 表达」和「布局用路由嵌套表达」只能选一个，两者混用必然重复。
    meta: { requiresAuth: true, layout: 'admin' },
    children: [
      {
        path: '',
        name: 'admin-dashboard',
        component: () => import('@/views/admin/DashboardView.vue'),
        meta: { title: '仪表盘', requiresAuth: true, layout: 'admin' },
      },
      {
        path: 'articles',
        name: 'admin-articles',
        component: () => import('@/views/admin/ArticleListView.vue'),
        meta: { title: '文章管理', requiresAuth: true, layout: 'admin' },
      },
      {
        path: 'articles/new',
        name: 'admin-article-create',
        component: () => import('@/views/admin/ArticleEditView.vue'),
        meta: { title: '写文章', requiresAuth: true, layout: 'admin' },
      },
      {
        path: 'articles/:id/edit',
        name: 'admin-article-edit',
        component: () => import('@/views/admin/ArticleEditView.vue'),
        meta: { title: '编辑文章', requiresAuth: true, layout: 'admin' },
        props: true,
      },
      {
        path: 'comments',
        name: 'admin-comments',
        component: () => import('@/views/admin/CommentListView.vue'),
        meta: { title: '评论管理', requiresAuth: true, layout: 'admin' },
      },
      {
        path: 'taxonomy',
        name: 'admin-taxonomy',
        component: () => import('@/views/admin/TaxonomyView.vue'),
        meta: { title: '分类与标签', requiresAuth: true, layout: 'admin' },
      },
      {
        path: 'series',
        name: 'admin-series',
        component: () => import('@/views/admin/SeriesView.vue'),
        meta: { title: '系列管理', requiresAuth: true, layout: 'admin' },
      },
      {
        path: 'media',
        name: 'admin-media',
        component: () => import('@/views/admin/MediaView.vue'),
        meta: { title: '媒体库', requiresAuth: true, layout: 'admin' },
      },
      {
        path: 'users',
        name: 'admin-users',
        component: () => import('@/views/admin/UsersView.vue'),
        meta: { title: '用户管理', requiresAuth: true, requiresAdmin: true, layout: 'admin' },
      },
      {
        path: 'settings',
        name: 'admin-settings',
        component: () => import('@/views/admin/SettingsView.vue'),
        meta: { title: '站点设置', requiresAuth: true, requiresAdmin: true, layout: 'admin' },
      },
    ],
  },

  // ---------------------------------------------------------------- 兜底
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    // 浏览器前进/后退时恢复原滚动位置，点链接进新页面则回到顶部
    return savedPosition ?? { top: 0 }
  },
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // 首次进入需要登录的区域时才去恢复登录态：公开页面不做多余请求
  const needsIdentity = Boolean(to.meta.requiresAuth) || tokenStore.access !== null
  if (needsIdentity && !auth.restored) {
    await auth.restore()
  }

  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (to.meta.requiresAdmin && !auth.isAdmin) {
    // 已登录但权限不够：留在原页并提示，而不是跳登录页（会让人误以为没登录）
    return { name: 'home' }
  }

  return true
})

router.afterEach((to) => {
  const base = '个人博客'
  document.title = to.meta.title ? `${to.meta.title} · ${base}` : base
})

export default router
