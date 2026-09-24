/**
 * UsersView 测试（用户管理，仅站长可见）。
 *
 * 这一页自己不碰 `@/api`：所有读写都经过 auth store（store 里维护 users /
 * usersLoading / usersError，并在写操作成功后**本地更新列表**而不是重新拉一次）。
 * 所以测试 spy 的是 store 底下的 `authApi.*`，走真实 store —— 这样「列表有没有跟着变」
 * 才是真实行为的证据，而不是被 mock 掉的假设。守的契约：
 *
 * 1. **创建成功不再重新拉列表**：store 把返回的用户追加进 users，多打一次接口纯属浪费；
 * 2. **字段级错误贴到字段旁**：422 的 fields 要显示在对应输入框下面，且不再弹笼统 toast；
 * 3. **角色 / 状态变更以后端返回值为准**：本地先改再等接口回包会出现「显示成了站长、
 *    其实没改成功」，所以失败时行上必须仍是原值（没有乐观更新）；
 * 4. **不能改自己的角色**：自己那一行的按钮禁用；
 * 5. **失败态与空列表分得开**：usersError 要显示错误 + 重试入口，而不是「0 个用户」；
 * 6. **删除用户**：确认前不发请求、取消什么都不做、失败保留确认框且不让行消失。
 *
 * 回归守卫：删除**成功**后确认框必须关掉。这一条曾经是坏的 —— `useConfirmDelete`
 * 用 `=== undefined` 判定失败，而 store 的 `removeUser` 是 async void，无论接口返回
 * 什么都 resolve 成 undefined，于是「成功」被判成「失败」：行删掉了、成功提示也弹了，
 * 确认框却一直留着。本轮把它固化成回归用例（文末那条），防止哨兵语义再被改回去。
 *
 * 说明：不 mock 业务模块（store 也是真的），只替换网络出口。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, authApi } from '@/api'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import type { User } from '@/types'

import UsersView from './UsersView.vue'

/* ------------------------------------------------------------ 测试数据 */

const ADMIN: User = {
  id: 1,
  username: 'admin',
  nickname: '站长本人',
  avatar_url: null,
  email: 'admin@example.com',
  bio: null,
  role: 'admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

const AUTHOR: User = {
  id: 2,
  username: 'author',
  nickname: null,
  avatar_url: null,
  email: 'author@example.com',
  bio: null,
  role: 'author',
  is_active: true,
  created_at: '2026-01-02T00:00:00Z',
}

const DISABLED: User = {
  id: 3,
  username: 'old-user',
  nickname: '老账号',
  avatar_url: null,
  email: 'old@example.com',
  bio: null,
  role: 'author',
  is_active: false,
  created_at: '2026-01-03T00:00:00Z',
}

const USERS: User[] = [ADMIN, AUTHOR, DISABLED]

/** 手动控制 resolve 时机的 promise：用来观察「请求还没回来」这段中间态。 */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/* ------------------------------------------------------------ 测试脚手架 */

const Blank = { template: '<div />' }
let pinia: Pinia

/* 这一页的输入框没有 aria-label，只能按 placeholder 定位（模板里没给可访问名称） */
const USERNAME = 'input[placeholder="至少 3 个字符"]'
const EMAIL = 'input[placeholder="name@example.com"]'
const PASSWORD = 'input[placeholder="至少 8 位"]'
const NICKNAME = 'input[placeholder="展示用名称"]'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/admin/users', component: Blank }],
  })
}

async function mountUsers() {
  const router = makeRouter()
  await router.push('/admin/users')
  await router.isReady()
  const auth = useAuthStore()
  // 当前登录者就是列表里的第一个用户：这样「（我）」标记与自己的按钮禁用态都能验证
  auth.user = ADMIN
  const wrapper = mount(UsersView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router, auth }
}

/** 桌面表格的行；移动端卡片是同一份数据的另一份渲染，断言一律只看表格。 */
function rows(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('tbody tr')
}

function rowByText(wrapper: VueWrapper, text: string): DOMWrapper<Element> {
  const found = rows(wrapper).find((row) => row.text().includes(text))
  if (!found) throw new Error(`找不到包含「${text}」的行`)
  return found
}

function rowButton(row: DOMWrapper<Element>, label: string): DOMWrapper<Element> {
  const found = row.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`行内找不到文案为「${label}」的按钮`)
  return found
}

/** 按文案取按钮：模板里同一屏有十几个 button，按 class 取会随样式改动而失效。 */
function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!found) throw new Error(`找不到文案为「${label}」的按钮`)
  return found
}

function inputValue(wrapper: VueWrapper, selector: string): string {
  return (wrapper.get(selector).element as HTMLInputElement).value
}

/** 字段级错误渲染在输入框下面的红色 span 里。 */
function fieldErrorTexts(wrapper: VueWrapper): string[] {
  return wrapper.findAll('span.text-red-600').map((item) => item.text())
}

async function submitCreate(wrapper: VueWrapper): Promise<void> {
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

/* 确认框 Teleport 到 body，只能从 document 里取（与 ConfirmDialog.spec.ts 一致） */
function dialog(): HTMLElement | null {
  return document.querySelector('[role="dialog"]')
}

function dialogButton(label: string): HTMLButtonElement | undefined {
  const panel = dialog()?.querySelector('.card')
  return [...(panel?.querySelectorAll('button') ?? [])].find(
    (item) => item.textContent?.trim() === label,
  ) as HTMLButtonElement | undefined
}

function lastToastMessage(): string | undefined {
  return useToast().items.value.at(-1)?.message
}

function lastToastKind(): string | undefined {
  return useToast().items.value.at(-1)?.kind
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().items.value = []
  vi.spyOn(authApi, 'listUsers').mockResolvedValue(USERS)
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

/* ------------------------------------------------------------ 用例 */

describe('UsersView · 加载 / 失败 / 空列表', () => {
  it('挂载时拉取用户列表，并逐列渲染昵称、用户名、邮箱、角色与状态', async () => {
    const list = vi.spyOn(authApi, 'listUsers').mockResolvedValue(USERS)

    const { wrapper } = await mountUsers()

    expect(list).toHaveBeenCalledTimes(1)
    expect(rows(wrapper)).toHaveLength(3)

    const admin = rowByText(wrapper, 'admin@example.com')
    // 昵称优先，用户名另起一行小字（重名时靠它区分）
    expect(admin.text()).toContain('站长本人')
    expect(admin.text()).toContain('admin')
    expect(admin.text()).toContain('站长')
    expect(admin.text()).toContain('正常')

    expect(rowByText(wrapper, 'old@example.com').text()).toContain('已停用')
    expect(rowByText(wrapper, 'old@example.com').text()).toContain('老账号')
    expect(wrapper.text()).toContain('全部用户 3')
  })

  it('加载中显示骨架屏，而不是一张空表加「0 个用户」', async () => {
    const pending = deferred<User[]>()
    vi.spyOn(authApi, 'listUsers').mockReturnValue(pending.promise)

    const { wrapper } = await mountUsers()

    expect(wrapper.findAll('.skeleton')).toHaveLength(3)
    expect(rows(wrapper)).toHaveLength(0)
    expect(wrapper.text()).toContain('全部用户 0')

    pending.resolve(USERS)
    await flushPromises()
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(rows(wrapper)).toHaveLength(3)
  })

  it('加载失败显示错误与重试入口（不能报成「一个用户都没有」）', async () => {
    const list = vi
      .spyOn(authApi, 'listUsers')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce(USERS)

    const { wrapper } = await mountUsers()

    expect(wrapper.get('[role="alert"]').text()).toContain('服务器开小差了，请稍后重试')
    expect(rows(wrapper)).toHaveLength(0)

    await button(wrapper, '重试').trigger('click')
    await flushPromises()

    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(rows(wrapper)).toHaveLength(3)
  })

  it('空列表时只显示表头与计数 0（站点还没分配过账号）', async () => {
    vi.spyOn(authApi, 'listUsers').mockResolvedValue([])

    const { wrapper } = await mountUsers()

    expect(wrapper.text()).toContain('全部用户 0')
    expect(rows(wrapper)).toHaveLength(0)
    expect(wrapper.findAll('thead th').map((item) => item.text())).toContain('用户')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('自己那一行有「（我）」标记，且角色按钮禁用（不能给自己降权）', async () => {
    const { wrapper } = await mountUsers()

    const me = rowByText(wrapper, 'admin@example.com')
    expect(me.text()).toContain('（我）')
    expect(rowButton(me, '降为作者').attributes('disabled')).toBeDefined()
    expect(rowButton(me, '降为作者').attributes('title')).toBe('不能修改自己的角色')
    // 别人的行不受影响
    expect(rowButton(rowByText(wrapper, 'author@example.com'), '设为站长').attributes('disabled')).toBeUndefined()
  })
})

describe('UsersView · 新建用户', () => {
  it('提交前 trim，空昵称转 null，成功后就地追加到列表（不再重新拉一次）', async () => {
    const created: User = {
      id: 9,
      username: 'newbie',
      nickname: null,
      avatar_url: null,
      email: 'newbie@example.com',
      bio: null,
      role: 'author',
      is_active: true,
      created_at: '2026-01-04T00:00:00Z',
    }
    const createUser = vi.spyOn(authApi, 'createUser').mockResolvedValue(created)
    const list = vi.spyOn(authApi, 'listUsers').mockResolvedValue(USERS)

    const { wrapper } = await mountUsers()
    await wrapper.get(USERNAME).setValue('  newbie  ')
    await wrapper.get(EMAIL).setValue('  newbie@example.com  ')
    await wrapper.get(PASSWORD).setValue('secret123')
    await wrapper.get(NICKNAME).setValue('   ')
    await submitCreate(wrapper)

    expect(createUser).toHaveBeenCalledWith({
      username: 'newbie',
      email: 'newbie@example.com',
      password: 'secret123',
      // 空昵称要传 null，传空串会让列表里出现「没有名字」的用户
      nickname: null,
      role: 'author',
    })
    expect(lastToastMessage()).toBe('用户已创建')
    // store 已经把返回的用户追加进列表，再拉一次纯属浪费
    expect(list).toHaveBeenCalledTimes(1)
    expect(rowByText(wrapper, 'newbie@example.com').text()).toContain('作者')
    // 表单清空，方便连续建号
    expect(inputValue(wrapper, USERNAME)).toBe('')
    expect(inputValue(wrapper, PASSWORD)).toBe('')
  })

  it('选了站长就按站长创建，列表里的角色徽标跟着变', async () => {
    const createUser = vi
      .spyOn(authApi, 'createUser')
      .mockResolvedValue({ ...AUTHOR, id: 10, username: 'boss', role: 'admin' })

    const { wrapper } = await mountUsers()
    await wrapper.get(USERNAME).setValue('boss')
    await wrapper.get(EMAIL).setValue('boss@example.com')
    await wrapper.get(PASSWORD).setValue('secret123')
    await wrapper.get('select').setValue('admin')
    await submitCreate(wrapper)

    expect(createUser).toHaveBeenCalledWith(expect.objectContaining({ role: 'admin' }))
    expect(rowByText(wrapper, 'author@example.com').text()).toContain('作者')
  })

  it('422 字段级错误贴在对应输入框下面，且不再弹一句笼统提示', async () => {
    vi.spyOn(authApi, 'createUser').mockRejectedValue(
      new ApiError('提交的内容有误', 422, 'validation_error', {
        username: '用户名已存在',
        password: '密码至少 8 位',
      }),
    )

    const { wrapper } = await mountUsers()
    await wrapper.get(USERNAME).setValue('author')
    await wrapper.get(EMAIL).setValue('author@example.com')
    await wrapper.get(PASSWORD).setValue('short')
    await submitCreate(wrapper)

    expect(fieldErrorTexts(wrapper)).toEqual(['用户名已存在', '密码至少 8 位'])
    // 有字段级错误时 useAction 不再 toast：两处都提示反而让人不知道看哪个
    expect(useToast().items.value).toHaveLength(0)
  })

  it('非字段错误才弹 toast，且输入内容不丢、按钮解禁', async () => {
    vi.spyOn(authApi, 'createUser').mockRejectedValue(
      new ApiError('邮箱已被占用', 409, 'conflict'),
    )

    const { wrapper } = await mountUsers()
    await wrapper.get(USERNAME).setValue('newbie')
    await wrapper.get(EMAIL).setValue('used@example.com')
    await wrapper.get(PASSWORD).setValue('secret123')
    await submitCreate(wrapper)

    expect(lastToastMessage()).toBe('邮箱已被占用')
    expect(lastToastKind()).toBe('error')
    expect(inputValue(wrapper, USERNAME)).toBe('newbie')
    expect(button(wrapper, '创建用户').attributes('disabled')).toBeUndefined()
  })

  it('重新提交时会清掉上一次的字段级错误（否则红字会一直挂着）', async () => {
    vi.spyOn(authApi, 'createUser')
      .mockRejectedValueOnce(
        new ApiError('提交的内容有误', 422, 'validation_error', { username: '用户名已存在' }),
      )
      .mockResolvedValueOnce({ ...AUTHOR, id: 11, username: 'newbie' })

    const { wrapper } = await mountUsers()
    await wrapper.get(USERNAME).setValue('newbie')
    await wrapper.get(EMAIL).setValue('newbie@example.com')
    await wrapper.get(PASSWORD).setValue('secret123')
    await submitCreate(wrapper)
    expect(fieldErrorTexts(wrapper)).toEqual(['用户名已存在'])

    await wrapper.get(USERNAME).setValue('newbie2')
    await submitCreate(wrapper)

    expect(fieldErrorTexts(wrapper)).toEqual([])
    expect(lastToastMessage()).toBe('用户已创建')
  })

  it('创建进行中按钮禁用并显示「创建中…」（防重复提交建出两个账号）', async () => {
    const pending = deferred<User>()
    vi.spyOn(authApi, 'createUser').mockReturnValue(pending.promise)

    const { wrapper } = await mountUsers()
    await wrapper.get(USERNAME).setValue('newbie')
    await wrapper.get(EMAIL).setValue('newbie@example.com')
    await wrapper.get(PASSWORD).setValue('secret123')
    await submitCreate(wrapper)

    expect(button(wrapper, '创建中…').attributes('disabled')).toBeDefined()

    pending.resolve({ ...AUTHOR, id: 12, username: 'newbie' })
    await flushPromises()
    expect(button(wrapper, '创建用户').attributes('disabled')).toBeUndefined()
  })
})

describe('UsersView · 角色与状态变更', () => {
  it('点「设为站长」提交 role=admin，行上角色与按钮文案一起翻过来', async () => {
    const updateUser = vi
      .spyOn(authApi, 'updateUser')
      .mockResolvedValue({ ...AUTHOR, role: 'admin' })

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '设为站长').trigger('click')
    await flushPromises()

    expect(updateUser).toHaveBeenCalledWith(2, { role: 'admin' })
    expect(lastToastMessage()).toBe('角色已更新')

    const row = rowByText(wrapper, 'author@example.com')
    // 徽标与按钮都以 store 里的新值为准
    expect(row.text()).toContain('站长')
    expect(rowButton(row, '降为作者').exists()).toBe(true)
  })

  it('点「降为作者」提交 role=author', async () => {
    const updateUser = vi
      .spyOn(authApi, 'updateUser')
      // 回包必须保持同一个人（同 id / 同 email），否则 store 用返回值替换那一行之后
      // 这个用户就从列表里"消失"了，后面的行定位会找不到
      .mockResolvedValueOnce({ ...DISABLED, role: 'admin' })
      .mockResolvedValueOnce({ ...DISABLED, role: 'author' })

    const { wrapper } = await mountUsers()
    // 自己的按钮是禁用的，用第三个人来点（他本来就是作者，这里换个方向验证参数）
    await rowButton(rowByText(wrapper, 'old@example.com'), '设为站长').trigger('click')
    await flushPromises()
    expect(updateUser).toHaveBeenCalledWith(3, { role: 'admin' })
    expect(rowByText(wrapper, 'old@example.com').text()).toContain('站长')

    await rowButton(rowByText(wrapper, 'old@example.com'), '降为作者').trigger('click')
    await flushPromises()
    expect(updateUser).toHaveBeenLastCalledWith(3, { role: 'author' })
    // 按钮与徽标按新角色翻回来
    expect(rowByText(wrapper, 'old@example.com').text()).toContain('作者')
    expect(rowButton(rowByText(wrapper, 'old@example.com'), '设为站长').exists()).toBe(true)
  })

  it('角色变更失败：提示后端文案，行上仍是原角色（没有乐观更新的假象）', async () => {
    vi.spyOn(authApi, 'updateUser').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '设为站长').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('服务器开小差了，请稍后重试')
    expect(lastToastKind()).toBe('error')
    const row = rowByText(wrapper, 'author@example.com')
    expect(row.text()).toContain('作者')
    expect(rowButton(row, '设为站长').exists()).toBe(true)
  })

  it('点「停用」提交 is_active=false，成功后状态与按钮文案都变', async () => {
    const updateUser = vi
      .spyOn(authApi, 'updateUser')
      .mockResolvedValue({ ...AUTHOR, is_active: false })

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '停用').trigger('click')
    await flushPromises()

    expect(updateUser).toHaveBeenCalledWith(2, { is_active: false })
    expect(lastToastMessage()).toBe('已停用')

    const row = rowByText(wrapper, 'author@example.com')
    expect(row.text()).toContain('已停用')
    expect(rowButton(row, '启用').exists()).toBe(true)
  })

  it('点「启用」提交 is_active=true，提示「已启用」', async () => {
    const updateUser = vi
      .spyOn(authApi, 'updateUser')
      .mockResolvedValue({ ...DISABLED, is_active: true })

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'old@example.com'), '启用').trigger('click')
    await flushPromises()

    expect(updateUser).toHaveBeenCalledWith(3, { is_active: true })
    expect(lastToastMessage()).toBe('已启用')
    expect(rowByText(wrapper, 'old@example.com').text()).toContain('正常')
  })

  it('停用失败：后端原文照传（「不能取消最后一个可用管理员」这类保护），状态不变', async () => {
    vi.spyOn(authApi, 'updateUser').mockRejectedValue(
      new ApiError('不能停用最后一个可用的管理员', 400, 'bad_request'),
    )

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '停用').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('不能停用最后一个可用的管理员')
    const row = rowByText(wrapper, 'author@example.com')
    expect(row.text()).toContain('正常')
    expect(rowButton(row, '停用').exists()).toBe(true)
  })

  it('变更失败后按钮解禁可重试（action 计数不会漏减）', async () => {
    const updateUser = vi
      .spyOn(authApi, 'updateUser')
      .mockRejectedValueOnce(new ApiError('boom', 500, 'internal_error'))
      .mockResolvedValueOnce({ ...AUTHOR, role: 'admin' })

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '设为站长').trigger('click')
    await flushPromises()

    await rowButton(rowByText(wrapper, 'author@example.com'), '设为站长').trigger('click')
    await flushPromises()

    expect(updateUser).toHaveBeenCalledTimes(2)
    expect(lastToastMessage()).toBe('角色已更新')
  })
})

describe('UsersView · 删除用户', () => {
  it('点「删除」只打开确认框，一个请求都不发', async () => {
    const removeUser = vi.spyOn(authApi, 'removeUser')

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '删除').trigger('click')
    await flushPromises()

    expect(removeUser).not.toHaveBeenCalled()
    // 文案要说清连带代价：文章与附件一起没
    expect(dialog()?.textContent).toContain('该用户名下的所有文章与附件都会被一并删除')
  })

  it('取消删除：不发请求、行还在、确认框关掉', async () => {
    const removeUser = vi.spyOn(authApi, 'removeUser')

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '删除').trigger('click')
    await flushPromises()
    dialogButton('取消')?.click()
    await flushPromises()

    expect(removeUser).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
    expect(rows(wrapper)).toHaveLength(3)
  })

  it('删除失败：报错、行不消失、确认框留着可重试', async () => {
    const removeUser = vi
      .spyOn(authApi, 'removeUser')
      .mockRejectedValue(new ApiError('该用户还有未转移的文章', 409, 'conflict'))

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除用户')?.click()
    await flushPromises()

    expect(removeUser).toHaveBeenCalledWith(2)
    expect(lastToastMessage()).toBe('该用户还有未转移的文章')
    expect(lastToastKind()).toBe('error')
    // 失败让行消失 = 用户以为删成功了
    expect(rowByText(wrapper, 'author@example.com')).toBeTruthy()
    expect(dialog()).not.toBeNull()
  })

  it('删除进行中：确认框按钮禁用并显示「处理中…」', async () => {
    const pending = deferred<void>()
    vi.spyOn(authApi, 'removeUser').mockReturnValue(pending.promise)

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除用户')?.click()
    await flushPromises()

    expect(dialogButton('处理中…')?.disabled).toBe(true)
    expect(dialogButton('取消')?.disabled).toBe(true)

    pending.resolve()
    await flushPromises()
  })

  it('回归：删除用户成功后确认框必须关闭（store 的 async void 只 resolve 出 undefined）', async () => {
    // authApi.removeUser 的契约是 Promise<void>（后端 204 空响应），store 又把它包成
    // async void：undefined 是最真实、也最刁钻的返回值。useConfirmDelete 曾经拿它当
    // 失败哨兵，于是「成功」被判成「失败」—— 行删了、绿色提示弹了，框却一直开着，
    // 用户以为没删掉、再点一次就打到一个已经不存在的 id 上。现在它守的是修复后的契约。
    const removeUser = vi.spyOn(authApi, 'removeUser').mockResolvedValue(undefined)

    const { wrapper } = await mountUsers()
    await rowButton(rowByText(wrapper, 'author@example.com'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除用户')?.click()
    await flushPromises()

    expect(removeUser).toHaveBeenCalledWith(2)
    // 行被删掉、成功提示弹了 —— 界面与提示必须一致
    expect(rows(wrapper)).toHaveLength(2)
    expect(wrapper.text()).not.toContain('author@example.com')
    expect(lastToastMessage()).toBe('用户已删除')
    expect(dialog()).toBeNull()
  })
})
