import { createPinia } from 'pinia'
import { createApp } from 'vue'

// 顺序很重要：先引入 highlight.js 的亮色主题，再引入自己的 style.css。
// 反过来的话，我们的暗色覆盖会被 highlight.js 的样式盖掉。
import 'highlight.js/styles/github.css'
import '@/style.css'

import App from '@/App.vue'
import router from '@/router'
import { useThemeStore } from '@/stores/theme'

const app = createApp(App)

app.use(createPinia())
app.use(router)

// Vue 的全局错误处理：生产环境不要把堆栈甩给用户，但要留在控制台便于排查
app.config.errorHandler = (error, _instance, info) => {
  console.error('[未捕获的组件错误]', info, error)
}

// 主题 store 在创建时就会把 <html> 的 class 校正到本地存储的选择，
// 这里显式实例化一次，确保「系统跟随」与「手动切换」两条路径都有同一份状态源。
useThemeStore()

/**
 * 等路由守卫跑完再挂载。
 *
 * 直接 `app.mount()` 的话，Vue 会先按「起始路由」渲染一次 —— 于是访问 /admin 时
 * 会先闪一下前台布局（DefaultLayout），而它的 onMounted 又会抢在守卫前面
 * 发起 `auth.restore()`，与守卫形成竞态。等 isReady() 之后挂载，
 * 首帧就是正确的布局，也不会有这个多余的并发。
 */
router.isReady().then(() => {
  app.mount('#app')
})
