<script setup lang="ts">
/** 用户管理（仅站长可见）。站点不开放注册，账号由这里分配。 */
import { onMounted, ref } from 'vue'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useAction } from '@/composables/useAction'
import { useAuthStore } from '@/stores/auth'
import type { UserRole } from '@/types'
import { formatDateTime } from '@/utils/format'

const auth = useAuthStore()
const action = useAction()

const newUser = ref({
  username: '',
  email: '',
  password: '',
  nickname: '',
  role: 'author' as UserRole,
})
const fieldErrors = ref<Record<string, string>>({})

const pendingDelete = ref<number | null>(null)

async function create(): Promise<void> {
  fieldErrors.value = {}
  const created = await action.run(
    () =>
      auth.createUser({
        username: newUser.value.username.trim(),
        email: newUser.value.email.trim(),
        password: newUser.value.password,
        nickname: newUser.value.nickname.trim() || null,
        role: newUser.value.role,
      }),
    {
      success: '用户已创建',
      errorMessage: '创建失败',
      onFieldErrors: (fields) => {
        fieldErrors.value = fields
      },
    },
  )
  if (created === undefined) return
  newUser.value = { username: '', email: '', password: '', nickname: '', role: 'author' }
}

async function toggleRole(id: number, current: UserRole): Promise<void> {
  await action.run(() => auth.updateUser(id, { role: current === 'admin' ? 'author' : 'admin' }), {
    success: '角色已更新',
    errorMessage: '更新失败',
  })
}

async function toggleActive(id: number, isActive: boolean): Promise<void> {
  // 「不能取消最后一个可用管理员」这类保护会返回 400，原文已经很清楚，直接展示
  await action.run(() => auth.updateUser(id, { is_active: !isActive }), {
    success: isActive ? '已停用' : '已启用',
    errorMessage: '更新失败',
  })
}

async function confirmDelete(): Promise<void> {
  const id = pendingDelete.value
  if (id === null) return
  const removed = await action.run(() => auth.removeUser(id), {
    success: '用户已删除',
    errorMessage: '删除失败',
  })
  if (removed !== undefined) pendingDelete.value = null
}

onMounted(() => {
  void auth.fetchUsers()
})
</script>

<template>
  <div class="space-y-6">
    <!-- 新建用户 -->
    <div class="card p-5">
      <h2 class="mb-4 text-sm font-medium text-ink">新建用户</h2>
      <form class="grid gap-4 sm:grid-cols-2" @submit.prevent="create">
        <label class="block text-sm">
          <span class="text-ink-soft">用户名</span>
          <input v-model="newUser.username" class="input mt-1.5" placeholder="至少 3 个字符" />
          <span v-if="fieldErrors.username" class="mt-1 block text-xs text-red-600">
            {{ fieldErrors.username }}
          </span>
        </label>

        <label class="block text-sm">
          <span class="text-ink-soft">邮箱</span>
          <input v-model="newUser.email" class="input mt-1.5" type="email" placeholder="name@example.com" />
          <span v-if="fieldErrors.email" class="mt-1 block text-xs text-red-600">
            {{ fieldErrors.email }}
          </span>
        </label>

        <label class="block text-sm">
          <span class="text-ink-soft">初始密码</span>
          <input v-model="newUser.password" class="input mt-1.5" type="password" placeholder="至少 8 位" />
          <span v-if="fieldErrors.password" class="mt-1 block text-xs text-red-600">
            {{ fieldErrors.password }}
          </span>
        </label>

        <label class="block text-sm">
          <span class="text-ink-soft">昵称（选填）</span>
          <input v-model="newUser.nickname" class="input mt-1.5" placeholder="展示用名称" />
        </label>

        <label class="block text-sm">
          <span class="text-ink-soft">角色</span>
          <select v-model="newUser.role" class="input mt-1.5">
            <option value="author">作者（只能管理自己的文章）</option>
            <option value="admin">站长（全部权限）</option>
          </select>
        </label>

        <div class="flex items-end">
          <button type="submit" class="btn--primary w-full sm:w-auto" :disabled="action.running.value">
            {{ action.running.value ? '创建中…' : '创建用户' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 用户列表 -->
    <div class="card overflow-hidden">
      <div class="border-b border-border px-5 py-3">
        <h2 class="text-sm font-medium text-ink">
          全部用户 <span class="text-xs font-normal text-ink-faint">{{ auth.users.length }}</span>
        </h2>
      </div>

      <div v-if="auth.usersLoading" class="space-y-2 p-5">
        <div v-for="index in 3" :key="index" class="skeleton h-10 rounded-lg"></div>
      </div>

      <div v-else class="hidden overflow-x-auto md:block">
        <table class="w-full min-w-[680px] text-sm">
          <thead class="bg-surface-muted text-xs text-ink-soft">
            <tr>
              <th class="px-5 py-2.5 text-left font-medium">用户</th>
              <th class="px-5 py-2.5 text-left font-medium">邮箱</th>
              <th class="w-28 px-5 py-2.5 text-left font-medium">角色</th>
              <th class="w-24 px-5 py-2.5 text-left font-medium">状态</th>
              <th class="w-36 px-5 py-2.5 text-left font-medium">创建时间</th>
              <th class="w-40 px-5 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border">
            <tr v-for="item in auth.users" :key="item.id" class="hover:bg-surface-muted/60">
              <td class="px-5 py-3">
                <div class="flex items-center gap-2">
                  <span class="font-medium text-ink">{{ item.nickname || item.username }}</span>
                  <span v-if="item.id === auth.user?.id" class="text-[11px] text-brand-600">（我）</span>
                </div>
                <p class="mt-0.5 font-mono text-xs text-ink-faint">{{ item.username }}</p>
              </td>
              <td class="px-5 py-3 text-ink-soft">{{ item.email }}</td>
              <td class="px-5 py-3">
                <span
                  class="rounded-md px-2 py-0.5 text-xs"
                  :class="
                    item.role === 'admin'
                      ? 'bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200'
                      : 'bg-surface-muted text-ink-soft'
                  "
                >
                  {{ item.role === 'admin' ? '站长' : '作者' }}
                </span>
              </td>
              <td class="px-5 py-3">
                <span
                  class="text-xs"
                  :class="item.is_active ? 'text-emerald-600' : 'text-ink-faint'"
                >
                  {{ item.is_active ? '正常' : '已停用' }}
                </span>
              </td>
              <td class="px-5 py-3 text-xs text-ink-faint">{{ formatDateTime(item.created_at) }}</td>
              <td class="px-5 py-3">
                <div class="flex items-center justify-end gap-3 text-xs">
                  <button
                    type="button"
                    class="text-ink-soft hover:text-ink"
                    :disabled="item.id === auth.user?.id"
                    :title="item.id === auth.user?.id ? '不能修改自己的角色' : ''"
                    @click="toggleRole(item.id, item.role)"
                  >
                    {{ item.role === 'admin' ? '降为作者' : '设为站长' }}
                  </button>
                  <button
                    type="button"
                    class="text-ink-soft hover:text-ink"
                    @click="toggleActive(item.id, item.is_active)"
                  >
                    {{ item.is_active ? '停用' : '启用' }}
                  </button>
                  <button
                    type="button"
                    class="text-red-500 hover:text-red-600"
                    @click="pendingDelete = item.id"
                  >
                    删除
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 移动端：卡片列表（表格 min-w 680 在窄屏只能横向滚动） -->
      <ul v-if="!auth.usersLoading" class="divide-y divide-border md:hidden">
        <li v-for="item in auth.users" :key="item.id" class="p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="truncate text-sm font-medium text-ink">
                {{ item.nickname || item.username }}
                <span v-if="item.id === auth.user?.id" class="text-[11px] text-brand-600">（我）</span>
              </p>
              <p class="mt-0.5 truncate font-mono text-xs text-ink-faint">{{ item.username }}</p>
            </div>
            <span
              class="shrink-0 rounded-md px-2 py-0.5 text-xs"
              :class="
                item.role === 'admin'
                  ? 'bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200'
                  : 'bg-surface-muted text-ink-soft'
              "
            >
              {{ item.role === 'admin' ? '站长' : '作者' }}
            </span>
          </div>

          <p class="mt-1.5 truncate text-xs text-ink-soft">{{ item.email }}</p>
          <p class="mt-1 flex items-center gap-3 text-xs text-ink-faint">
            <span>{{ formatDateTime(item.created_at) }}</span>
            <span :class="item.is_active ? 'text-emerald-600' : ''">
              {{ item.is_active ? '正常' : '已停用' }}
            </span>
          </p>

          <div class="mt-3 flex items-center gap-4 text-xs">
            <button
              type="button"
              class="text-ink-soft disabled:opacity-40"
              :disabled="item.id === auth.user?.id"
              @click="toggleRole(item.id, item.role)"
            >
              {{ item.role === 'admin' ? '降为作者' : '设为站长' }}
            </button>
            <button type="button" class="text-ink-soft" @click="toggleActive(item.id, item.is_active)">
              {{ item.is_active ? '停用' : '启用' }}
            </button>
            <button
              type="button"
              class="ml-auto text-red-500"
              @click="pendingDelete = item.id"
            >
              删除
            </button>
          </div>
        </li>
      </ul>
    </div>

    <ConfirmDialog
      :open="pendingDelete !== null"
      danger
      :loading="action.running.value"
      title="删除用户"
      message="该用户名下的所有文章与附件都会被一并删除，且不可恢复。"
      confirm-label="删除用户"
      @cancel="pendingDelete = null"
      @confirm="confirmDelete"
    />
  </div>
</template>
