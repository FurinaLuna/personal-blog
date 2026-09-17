/** 登录态 Store。 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, authApi, tokenStore } from '@/api'
import type { User, UserCreatePayload, UserUpdatePayload } from '@/types'

export const useAuthStore = defineStore('auth', () => {  const user = ref<User | null>(null)
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
   * 带重试的 /auth/me。
   *
   * 只对**瞬时**错误重试：401 / 403 是确定答案，重试没有意义，立刻抛出。
   * 两次之间隔 400ms —— 足够跨过一次连接抖动或后端重启，
   * 又短到用户感觉不到卡顿。
   */
  async function attemptRestore(attempts: number): Promise<User> {
    let lastError: unknown
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        return await authApi.me()
      } catch (error) {
        lastError = error
        // 确定性失败：凭证问题重试多少次都一样
        if (error instanceof ApiError && (error.isUnauthorized || error.isForbidden)) throw error
        if (attempt < attempts - 1) {
          await new Promise((resolve) => setTimeout(resolve, 400))
        }
      }
    }
    throw lastError
  }

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
        // 网络抖动时重试一次再下结论。这个请求是「进入后台的第一道门」，
        // 一次瞬时 502 就把有登录态的作者弹回登录页，体验上非常像"被登出了"。
        user.value = await attemptRestore(2)
      } catch (error) {
        if (error instanceof ApiError && error.isUnauthorized) {
          // 401 是**确定的**答案：凭证已失效
          tokenStore.clear()
          user.value = null
          restored.value = true
        } else {
          // 其余错误（断网 / 502 / 超时）是**不确定**的答案。
          // 以前这里也置 restored = true，等于把「网络不好」永久缓存成
          // 「你没登录」—— 之后守卫再也不会重试，用户只能刷新整页才能恢复。
          // 保持 restored = false，下一次路由跳转就会自动再试一次。
          user.value = null
          restored.value = false
        }
      } finally {
        restoring.value = false
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
  /** 用户列表加载失败的提示。为 null 表示没出错。 */
  const usersError = ref<string | null>(null)

  async function fetchUsers(): Promise<void> {
    usersLoading.value = true
    usersError.value = null
    try {
      users.value = await authApi.listUsers()
    } catch (error) {
      // 必须显式接管：否则请求失败时表格渲染成空列表、计数显示 0，
      // 站长无法区分「还没有别的用户」和「请求挂了」，而且 rejection 无人处理。
      usersError.value = error instanceof ApiError ? error.message : '用户列表加载失败'
      users.value = []
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
    usersError,
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
