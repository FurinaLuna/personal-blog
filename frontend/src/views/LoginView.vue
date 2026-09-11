<script setup lang="ts">
/** 登录页。站点不开放自助注册，账号由站长在后台分配。 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api'
import { toErrorMessage } from '@/composables/useAsyncData'
import { useHead } from '@/composables/useHead'
import { useToast } from '@/composables/useToast'

useHead({ title: '登录' })
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

const form = ref({ username: '', password: '' })
const submitting = ref(false)
const errorMessage = ref('')

const redirect = computed(() => {
  const value = route.query.redirect
  return typeof value === 'string' && value.startsWith('/') ? value : '/admin'
})

async function submit(): Promise<void> {
  if (!form.value.username.trim() || !form.value.password) {
    errorMessage.value = '请填写用户名和密码'
    return
  }

  submitting.value = true
  errorMessage.value = ''
  try {
    await auth.login(form.value.username.trim(), form.value.password)
    toast.success(`欢迎回来，${auth.displayName}`)
    await router.replace(redirect.value)
  } catch (error) {
    // 字段级错误优先展示在对应输入框下方，比只弹一个 toast 更容易定位问题
    errorMessage.value =
      error instanceof ApiError ? error.message : toErrorMessage(error, '登录失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  // 已经登录的人不该再看到登录页
  void auth.restore().then(() => {
    if (auth.isAuthenticated) void router.replace(redirect.value)
  })
})
</script>

<template>
  <div class="card p-6 shadow-sm sm:p-8">
    <h1 class="text-lg font-semibold text-ink">登录后台</h1>
    <p class="mt-1.5 text-sm text-ink-soft">本站不开放注册，账号由站长分配。</p>

    <form class="mt-6 space-y-4" @submit.prevent="submit">
      <div>
        <label for="username" class="mb-1.5 block text-sm text-ink-soft">用户名或邮箱</label>
        <input
          id="username"
          v-model="form.username"
          class="input"
          autocomplete="username"
          placeholder="admin"
          required
        />
      </div>

      <div>
        <label for="password" class="mb-1.5 block text-sm text-ink-soft">密码</label>
        <input
          id="password"
          v-model="form.password"
          class="input"
          type="password"
          autocomplete="current-password"
          placeholder="••••••••"
          required
        />
      </div>

      <p v-if="errorMessage" class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/50 dark:text-red-300">
        {{ errorMessage }}
      </p>

      <button type="submit" class="btn-primary w-full" :disabled="submitting">
        {{ submitting ? '登录中…' : '登录' }}
      </button>
    </form>

    <RouterLink to="/" class="mt-5 block text-center text-xs text-ink-faint hover:text-brand-600">
      ← 返回前台
    </RouterLink>
  </div>
</template>
