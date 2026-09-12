/** 登录态 Store。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, authApi, tokenStore } from '@/api'
import type { User, UserCreatePayload, UserUpdatePayload } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  /** 是否已经尝试过恢复登录态（用于避免路由守卫在恢复完成前误判为未登录） */
  const restored = ref(false)
  const restoring = ref(false)

  const isAuthenticated = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const isAuthor = computed(() => user.value?.role === 'author' || user.value?.role === 'admin')
  const displayName = computed(
    () => user.value?.nickname || user.value?.username || '访客',
  )

  /**
   * 进行中的恢复任务。
   *
   * **必须让并发调用方共享并等待同一个 Promise**，不能用「发现有人在做就直接 return」。
   * 曾经踩过的坑：`restore()` 里写的是 `if (restoring.value) return`，于是——
   *
   * 1. 应用挂载后某个布局组件的 onMounted 先调起 restore()，/auth/me 起飞；
   * 2. 路由守卫随后调用 restore()，发现"正在恢复中"直接返回、**不等待**；
   * 3. 守卫立刻读到 `user === null`，判定未登录，把用户弹回登录页 ——
   *    而此时 /auth/me 其实已经返回 200 了（网络面板能看到，但用户已经被踢走）。
   *
   * 这个 bug 只在「布局 chunk 加载慢于挂载」时才复现，属于典型的时序竞态，
   * 手动测试很难碰上，必须靠结构上去掉竞态窗口。
   */
  let restorePromise: Promise<void> | null = null

  /**
   * 恢复登录态。
   *
   * 只在有 token 时才请求 /auth/me —— 否则未登录用户每次进站都会白白吃一个 401。
   * 请求失败（token 过期）时静默清空，不弹任何错误：用户只是"没登录"而已。
   */
  function restore(force = false): Promise<void> {
    if (restorePromise && !force) {
      return restorePromise
    }

    const task = async (): Promise<void> => {
      if (!tokenStore.access) {
        user.value = null
        restored.value = true
        return
      }

      restoring.value = true
      try {
        user.value = await authApi.me()
      } catch (error) {
        // 401 说明凭证已失效，其余错误（网络抖动）不清理，下次再试
        if (error instanceof ApiError && error.isUnauthorized) {
          tokenStore.clear()
        }
        user.value = null
      } finally {
        restoring.value = false
        restored.value = true
      }
    }

    const current = task()
    restorePromise = current
    return current.finally(() => {
      // 只有当自己仍是"最新一次"任务时才清空标记，
      // 避免把后发起的 force 恢复任务标识误删
      if (restorePromise === current) restorePromise = null
    })
  }

  /**
   * 登录。
   *
   * 成功时**返回 user 对象**（而不是 void）：LoginView 用
   * ``action.run`` 的返回值区分成败（``undefined`` 视为失败），
   * 若这里不返回有意义的值，登录成功也会被当成失败而跳过跳转。
   * 这是实测踩过的坑：POST /auth/login 与 /auth/me 均 200、toast 已弹，
   * 但用户卡在登录页 —— 因为返回值被 ``ok === undefined`` 拦截了。
   */
  async function login(username: string, password: string): Promise<User | null> {
    await authApi.login({ username, password })
    user.value = await authApi.me()
    restored.value = true
    return user.value
  }

  async function logout(): Promise<void> {
    await authApi.logout()
    user.value = null
    restored.value = true
  }

  async function updateProfile(payload: {
    nickname?: string | null
    email?: string
    avatar_url?: string | null
    bio?: string | null
  }): Promise<void> {
    user.value = await authApi.updateMe(payload)
  }

  async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
    await authApi.changePassword(oldPassword, newPassword)
  }

  /* -------------------------------------------------- 站长专属：用户管理 */

  const users = ref<User[]>([])
  const usersLoading = ref(false)

  async function fetchUsers(): Promise<void> {
    usersLoading.value = true
    try {
      users.value = await authApi.listUsers()
    } finally {
      usersLoading.value = false
    }
  }

  async function createUser(payload: UserCreatePayload): Promise<User> {
    const created = await authApi.createUser(payload)
    users.value = [...users.value, created]
    return created
  }

  async function updateUser(id: number, payload: UserUpdatePayload): Promise<User> {
    const updated = await authApi.updateUser(id, payload)
    users.value = users.value.map((item) => (item.id === id ? updated : item))
    // 改的是自己时同步当前登录态，否则顶栏还会显示旧昵称
    if (user.value?.id === id) user.value = updated
    return updated
  }

  async function removeUser(id: number): Promise<void> {
    await authApi.removeUser(id)
    users.value = users.value.filter((item) => item.id !== id)
  }

  return {
    user,
    restored,
    restoring,
    isAuthenticated,
    isAdmin,
    isAuthor,
    displayName,
    users,
    usersLoading,
    restore,
    login,
    logout,
    updateProfile,
    changePassword,
    fetchUsers,
    createUser,
    updateUser,
    removeUser,
  }
})
