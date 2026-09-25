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

import { setAuthRequiredProbe } from '@/api/http'
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
    // 独立搜索页：首页的 ?keyword= 是「在列表里筛」（按时间排），
    // 这里是「按相关度找」（走 FTS5），两者语义不同，所以是两个入口。
    path: '/search',
    name: 'search',
    component: () => import('@/views/SearchView.vue'),
    meta: { title: '搜索' },
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
    component: () => import('@/views/LinksView.vue'),
    meta: { title: '友情链接' },
  },
  {
    path: '/guestbook',
    name: 'guestbook',
    component: () => import('@/views/GuestbookView.vue'),
    meta: { title: '留言板' },
  },

  // 退订页：从邮件里的链接进入（/unsubscribe?token=xxx），导航里没有入口。
  // 挂在公开区而不是后台：收件人多数不是本站用户，不该被登录守卫拦下。
  {
    path: '/unsubscribe',
    name: 'unsubscribe',
    component: () => import('@/views/UnsubscribeView.vue'),
    meta: { title: '退订通知' },
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
        path: 'links',
        name: 'admin-links',
        component: () => import('@/views/admin/LinksView.vue'),
        meta: { title: '友链管理', requiresAuth: true, requiresAdmin: true, layout: 'admin' },
      },
      {
        path: 'guestbook',
        name: 'admin-guestbook',
        component: () => import('@/views/admin/GuestbookView.vue'),
        meta: { title: '留言板管理', requiresAuth: true, requiresAdmin: true, layout: 'admin' },
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
      {
        // 后台内部的兜底：没有它，`/admin/typo` 会落到下面那个顶层兜底路由，
        // 而那条路由没有 layout，于是**后台的 404 会渲染成前台布局** ——
        // 侧边栏消失、用户以为自己被登出了。
        path: ':pathMatch(.*)*',
        name: 'admin-not-found',
        component: () => import('@/views/NotFoundView.vue'),
        meta: { title: '页面不存在', requiresAuth: true, layout: 'admin' },
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

/**
 * 守卫里等待「恢复登录态」的上限。
 *
 * 为什么需要上限：`/auth/me` 走的是 20s 超时 + 一次 400ms 退避重试的链路
 * （见 stores/auth.ts 的 attemptRestore），网络半死时最坏能把一次导航按住
 * 几十秒 —— 用户看到的是"点了链接没反应"。
 *
 * 6 秒的取法：正常网络下 `/auth/me` 是几十毫秒；超过 6 秒基本可以判定
 * "问不出来"，此时**放行**比继续等更有用（页面自己的加载态/错误态会表达问题，
 * 真的没登录的话后续请求 401 会把用户带回登录页）。
 */
const RESTORE_TIMEOUT_MS = 6000

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  const requiresAuth = Boolean(to.meta.requiresAuth)

  if (requiresAuth) {
    // 需要决定"放不放行"时才等，而且带超时。
    //
    // 这里**不能**再加 `&& tokenStore.access`：access token 只存内存，
    // 刷新页面后它必然是空的，加了就会导致有登录态的用户刷新后台页面时
    // 守卫不问服务端、直接按未登录弹去登录页。`restore()` 内部会先拿
    // Cookie 静默续期再问 /auth/me，空内存正是它要处理的场景。
    if (!auth.restored) {
      await Promise.race([
        auth.restore(),
        new Promise((resolve) => setTimeout(resolve, RESTORE_TIMEOUT_MS)),
      ])
    }

    if (!auth.isAuthenticated) {
      // 关键区分：`restored === false` 表示"没问到"（网络问题 / 超时），
      // 不代表"没登录"。把这种情况也踢去登录页，会让网络抖动的用户
      // 以为自己被登出了 —— 放行，让页面自己表达失败。
      if (!auth.restored) return true
      return { name: 'login', query: { redirect: to.fullPath } }
    }

    if (to.meta.requiresAdmin && !auth.isAdmin) {
      // 已登录但权限不够：留在原页并提示，而不是跳登录页（会让人误以为没登录）
      return { name: 'home' }
    }

    return true
  }

  // 公开页面**绝不等待**身份恢复：它只影响顶栏显示什么，不影响内容渲染。
  // 旧实现是 `await auth.restore()`（只要本地有 token 就等），
  // 于是"带着 token 打开首页"会被一个后台请求堵住首屏。
  //
  // 现在多一道判断：**只有"可能有会话"时才去恢复**。
  // access token 只存内存后，前端在页面加载时无从判断有没有会话，于是匿名访客
  // 每次进站也会先打一次 POST /auth/refresh 白吃一个 401 —— 这就是提示 Cookie 要消掉的
  // 那一次请求。判断本身收在 `restoreIfLikely()` 里（store 是唯一的真相来源），
  // 这里不再自己写一遍条件：**同一个条件写在两处，就是下次漂移的起点**。
  //
  // ⚠️ 受保护路由上面那条分支仍然用 `restore()`（无条件尝试）。原因是提示位只是
  // 省请求的优化、可能与真实会话状态不一致；拿它当授权判据会变成"偶发被登出"。
  if (!auth.restored) {
    void auth.restoreIfLikely()
  }

  return true
})

// 告诉 http 层"当前页面是否需要登录"：凭证失效时只有需要登录的页面才整页跳转，
// 公开页面被 401 不该把访客弹走（详见 api/http.ts 的 forceLogout）。
setAuthRequiredProbe(() => Boolean(router.currentRoute.value.meta.requiresAuth))

router.afterEach((to) => {
  const base = '个人博客'
  document.title = to.meta.title ? `${to.meta.title} · ${base}` : base
})

export default router
