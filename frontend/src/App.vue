<script setup lang="ts">
/**
 * 应用根组件：只负责「按路由 meta 选布局」+ 给页面切换加一点过渡。
 *
 * 布局放到这里而不是每个页面里，是为了让页面组件保持纯粹的内容职责——
 * 页面不该知道自己的顶部导航长什么样。
 */
import { computed, type Component } from 'vue'
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
</script>

<template>
  <component :is="layout">
    <router-view v-slot="{ Component: Page }">
      <transition name="fade" mode="out-in">
        <component :is="Page" />
      </transition>
    </router-view>
  </component>
</template>
