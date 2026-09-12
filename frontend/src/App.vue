<script setup lang="ts">
/**
 * 应用根组件：只负责「按路由 meta 选布局」+ 给页面切换加一点过渡。
 *
 * 布局放到这里而不是每个页面里，是为了让页面组件保持纯粹的内容职责——
 * 页面不该知道自己的顶部导航长什么样。
 *
 * 另外挂了一层全局错误边界：任何一个页面组件在渲染/生命周期里抛错，
 * 在没有边界的情况下整个 #app 会被 Vue 卸载，用户看到的是纯白页面，
 * 连「刷新一下」的提示都没有。这里兜住它，给出可操作的恢复入口。
 */
import { computed, onErrorCaptured, ref, type Component } from 'vue'
import { useRoute } from 'vue-router'

import AdminLayout from '@/layouts/AdminLayout.vue'
import BlankLayout from '@/layouts/BlankLayout.vue'
import DefaultLayout from '@/layouts/DefaultLayout.vue'

const route = useRoute()

const LAYOUTS: Record<string, Component> = {
  default: DefaultLayout,
  admin: AdminLayout,
  blank: BlankLayout,
}

const layout = computed<Component>(() => LAYOUTS[route.meta.layout ?? 'default'] ?? DefaultLayout)

const crashed = ref(false)

onErrorCaptured((error) => {
  crashed.value = true
  // 保留 console.error：兜底 UI 是给用户的，排障信息仍然要留在控制台
  console.error('[App] 页面渲染出错：', error)
  // 返回 false 阻止错误继续向上冒泡（否则 Vue 仍会在控制台再抛一次未捕获异常）
  return false
})
</script>

<template>
  <!-- 兜底页：不复用 DefaultLayout，因为崩溃有可能就是布局里的问题 -->
  <div v-if="crashed" class="mx-auto max-w-content px-4 py-24 text-center">
    <p class="font-mono text-5xl font-semibold tracking-tight text-ink-faint">:(</p>
    <h1 class="mt-4 text-xl font-semibold text-ink">这个页面出错了</h1>
    <p class="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-ink-soft">
      页面渲染时发生了意外。可以先返回首页，或刷新重试；问题持续存在时请联系站长。
    </p>
    <div class="mt-6 flex items-center justify-center gap-3">
      <a href="/" class="btn--primary">返回首页</a>
      <button type="button" class="btn--ghost" @click="crashed = false">
        重试
      </button>
    </div>
  </div>

  <component :is="layout" v-else>
    <router-view v-slot="{ Component: Page }">
      <transition name="fade" mode="out-in">
        <component :is="Page" />
      </transition>
    </router-view>
  </component>
</template>
