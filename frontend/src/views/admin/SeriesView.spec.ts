/**
 * SeriesView 测试（系列管理 / 技术连载）。
 *
 * 系列是「文章的可选分组」：删除系列**不会**删文章，只会把它们变回普通文章 ——
 * 这个代价必须写在确认文案里。守的契约：
 *
 * 1. **请求形态**：列表固定带 `with_counts=true`（要显示「N 篇」）；
 * 2. **写操作的 payload**：新建与编辑都 trim 名称、空简介转 null（后端靠 null 清空）；
 * 3. **本地校验只在新建这一侧**：名称为空要拦下并提示，不发请求；
 * 4. **重命名是原地编辑**：点「编辑」只把那一行换成输入框并回填（null 简介回填空串），
 *    取消不发请求，失败留在编辑态且草稿不丢；
 * 5. **删除先确认再发请求**：确认框里带上系列名与「文章会变回普通文章」，
 *    取消什么都不做，失败保留确认框、行不消失、列表不刷新；
 * 6. **失败与空态分得开**：接口挂了要显示后端文案，不能报成「还没有系列」；
 * 7. **权限口径**：作者点删除会拿到 403（后端 `该操作仅站长可用`），要转成人话。
 *
 * 另外钉住一条**现状缺陷**（本轮只加测试、不改产品代码）：
 * 编辑时把名称清空照样会把空名字发给后端 —— 新建那一侧有非空校验，重命名没有，
 * 与 TaxonomyView 修好后的口径不一致（那边两道都拦）。
 *
 * 说明：不 mock 业务模块，只替换网络出口（spy `@/api` 上的方法）。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, seriesApi } from '@/api'
import { useToast } from '@/composables/useToast'
import type { Series } from '@/types'

import SeriesView from './SeriesView.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeSeries(overrides: Partial<Series> = {}): Series {
  return {
    id: 1,
    name: 'SQLite 踩坑记',
    slug: 'sqlite',
    description: '边踩边记',
    article_count: 3,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const SERIES: Series[] = [
  makeSeries({ id: 1, name: 'SQLite 踩坑记', slug: 'sqlite', description: '边踩边记', article_count: 3 }),
  makeSeries({ id: 2, name: '重构手记', slug: 'refactor', description: null, article_count: 0 }),
]

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

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/admin/series', component: Blank }],
  })
}

async function mountSeries() {
  const router = makeRouter()
  await router.push('/admin/series')
  await router.isReady()
  const wrapper = mount(SeriesView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router }
}

/** 系列行（编辑态与只读态是同一个 li 的两个分支）。 */
function items(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('ul li')
}

function itemByText(wrapper: VueWrapper, text: string): DOMWrapper<Element> {
  const found = items(wrapper).find((item) => item.text().includes(text))
  if (!found) throw new Error(`找不到包含「${text}」的系列行`)
  return found
}

function itemButton(item: DOMWrapper<Element>, label: string): DOMWrapper<Element> {
  const found = item.findAll('button').find((button) => button.text().trim() === label)
  if (!found) throw new Error(`行内找不到文案为「${label}」的按钮`)
  return found
}

/** 新建区的两个输入框（aria-label 与编辑态重名，所以固定取表单里的那一个）。 */
function newNameInput(wrapper: VueWrapper): DOMWrapper<Element> {
  return wrapper.findAll('input[aria-label="系列名称"]')[0] as DOMWrapper<Element>
}

function newDescriptionInput(wrapper: VueWrapper): DOMWrapper<Element> {
  return wrapper.findAll('input[aria-label="系列简介（选填）"]')[0] as DOMWrapper<Element>
}

function createButton(wrapper: VueWrapper): DOMWrapper<Element> {
  const found = wrapper.findAll('button').find((button) => button.text().trim().startsWith('创建'))
  if (!found) throw new Error('找不到「创建」按钮')
  return found
}

/** 只读 `.element`，所以参数放宽成结构化类型：`get()` 返回的是 Omit<DOMWrapper,'exists'>。 */
function inputValue(input: { element: Element }): string {
  return (input.element as HTMLInputElement).value
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

async function confirmDialog(label: string): Promise<void> {
  dialogButton(label)?.click()
  await flushPromises()
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
  vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

/* ------------------------------------------------------------ 用例 */

describe('SeriesView · 加载 / 失败 / 空列表', () => {
  it('挂载时拉全量系列（带 with_counts），并逐行渲染名称 / 篇数 / slug / 简介', async () => {
    const list = vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)

    const { wrapper } = await mountSeries()

    // with_counts 为 false 的话「N 篇」会全是 0
    expect(list).toHaveBeenCalledWith(true)
    expect(wrapper.text()).toContain('全部系列 2')

    expect(items(wrapper)).toHaveLength(2)
    const first = itemByText(wrapper, 'SQLite 踩坑记')
    expect(first.text()).toContain('3 篇')
    expect(first.text()).toContain('/sqlite')
    expect(first.text()).toContain('边踩边记')
    expect(itemButton(first, '编辑').exists()).toBe(true)
    expect(itemButton(first, '删除').exists()).toBe(true)

    const second = itemByText(wrapper, '重构手记')
    expect(second.text()).toContain('0 篇')
    expect(second.text()).toContain('/refactor')
  })

  it('简介为 null 时不渲染空段落，也不会出现字面量 null', async () => {
    vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)

    const { wrapper } = await mountSeries()

    // 名称 + slug 两段；有简介的那一行才多一段
    expect(itemByText(wrapper, '重构手记').findAll('p')).toHaveLength(2)
    expect(itemByText(wrapper, 'SQLite 踩坑记').findAll('p')).toHaveLength(3)
    expect(itemByText(wrapper, '重构手记').text()).not.toContain('null')
  })

  it('空列表：给出「还没有系列」与说明，而不是一片空白', async () => {
    vi.spyOn(seriesApi, 'list').mockResolvedValue([])

    const { wrapper } = await mountSeries()

    expect(wrapper.text()).toContain('全部系列 0')
    expect(wrapper.text()).toContain('还没有系列')
    // 说明要讲清「挂到系列下」的收益，否则用户不知道这一页是干嘛的
    expect(wrapper.text()).toContain('详情页就会出现系列导航')
    expect(items(wrapper)).toHaveLength(0)
  })

  it('加载失败：显示后端文案，绝不能伪装成「还没有系列」', async () => {
    vi.spyOn(seriesApi, 'list').mockRejectedValue(
      new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'),
    )

    const { wrapper } = await mountSeries()

    expect(wrapper.text()).toContain('服务器开小差了，请稍后重试')
    expect(wrapper.text()).not.toContain('还没有系列')
    expect(items(wrapper)).toHaveLength(0)
  })

  it('非 ApiError 的异常会归一成兜底文案（不会渲染成一条空白）', async () => {
    vi.spyOn(seriesApi, 'list').mockRejectedValue(new TypeError('boom'))

    const { wrapper } = await mountSeries()

    expect(wrapper.text()).toContain('加载失败，请稍后重试')
    expect(wrapper.text()).not.toContain('还没有系列')
  })

  it('首屏加载中显示 3 条骨架屏，而不是先闪一下「还没有系列」', async () => {
    const pending = deferred<Series[]>()
    vi.spyOn(seriesApi, 'list').mockReturnValue(pending.promise)

    const { wrapper } = await mountSeries()

    expect(wrapper.findAll('.skeleton')).toHaveLength(3)
    expect(wrapper.text()).not.toContain('还没有系列')

    pending.resolve(SERIES)
    await flushPromises()
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(items(wrapper)).toHaveLength(2)
  })

  it('手动刷新列表时保留旧行，不闪骨架屏（ready 之后不回退到首次加载态）', async () => {
    vi.spyOn(seriesApi, 'create').mockResolvedValue(makeSeries({ id: 3, name: '新系列' }))
    const refresh = deferred<Series[]>()
    vi.spyOn(seriesApi, 'list')
      .mockResolvedValueOnce(SERIES)
      .mockReturnValueOnce(refresh.promise)

    const { wrapper } = await mountSeries()
    await newNameInput(wrapper).setValue('新系列')
    await createButton(wrapper).trigger('click')
    await flushPromises()

    // 创建后的刷新还在路上：旧内容必须留着，骨架屏不该出现（否则整页闪一下）
    expect(wrapper.findAll('.skeleton')).toHaveLength(0)
    expect(items(wrapper)).toHaveLength(2)
    expect(itemByText(wrapper, 'SQLite 踩坑记')).toBeTruthy()

    refresh.resolve(SERIES)
    await flushPromises()
    expect(items(wrapper)).toHaveLength(2)
  })
})

describe('SeriesView · 新建系列', () => {
  it('名称 trim、空简介转 null，成功后清空表单并刷新列表', async () => {
    const created = vi.spyOn(seriesApi, 'create').mockResolvedValue(
      makeSeries({ id: 3, name: '读书笔记', slug: 'reading', description: null, article_count: 0 }),
    )
    const list = vi
      .spyOn(seriesApi, 'list')
      .mockResolvedValueOnce(SERIES)
      .mockResolvedValueOnce([
        ...SERIES,
        makeSeries({ id: 3, name: '读书笔记', slug: 'reading', description: null, article_count: 0 }),
      ])

    const { wrapper } = await mountSeries()
    await newNameInput(wrapper).setValue('  读书笔记  ')
    await newDescriptionInput(wrapper).setValue('   ')
    await createButton(wrapper).trigger('click')
    await flushPromises()

    expect(created).toHaveBeenCalledWith({ name: '读书笔记', description: null })
    expect(lastToastMessage()).toBe('系列已创建')
    expect(lastToastKind()).toBe('success')
    // 表单要清空，方便连续建系列（否则用户以为没提交成功、再点一次就重名）
    expect(inputValue(newNameInput(wrapper))).toBe('')
    expect(inputValue(newDescriptionInput(wrapper))).toBe('')
    expect(list).toHaveBeenCalledTimes(2)
    expect(itemByText(wrapper, '读书笔记')).toBeTruthy()
  })

  it('填了简介就按 trim 后的值提交', async () => {
    const created = vi.spyOn(seriesApi, 'create').mockResolvedValue(makeSeries({ id: 3 }))

    const { wrapper } = await mountSeries()
    await newNameInput(wrapper).setValue('读书笔记')
    await newDescriptionInput(wrapper).setValue('  一年读五十本  ')
    await createButton(wrapper).trigger('click')
    await flushPromises()

    expect(created).toHaveBeenCalledWith({ name: '读书笔记', description: '一年读五十本' })
  })

  it('名称为空或只有空格：一个请求都不发，就地提示', async () => {
    const created = vi.spyOn(seriesApi, 'create')
    const list = vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)

    const { wrapper } = await mountSeries()
    await newNameInput(wrapper).setValue('   ')
    await createButton(wrapper).trigger('click')
    await flushPromises()

    // 交给后端只会白跑一趟并拿到 422，前端拦下来更快也更清楚
    expect(created).not.toHaveBeenCalled()
    expect(lastToastMessage()).toBe('请填写系列名称')
    expect(lastToastKind()).toBe('error')
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('创建失败：提示后端文案、表单内容不丢、按钮解禁可重试、列表不刷新', async () => {
    vi.spyOn(seriesApi, 'create').mockRejectedValue(
      new ApiError('系列名已存在', 422, 'validation_error'),
    )
    const list = vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)

    const { wrapper } = await mountSeries()
    await newNameInput(wrapper).setValue('SQLite 踩坑记')
    await createButton(wrapper).trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('系列名已存在')
    expect(lastToastKind()).toBe('error')
    // 辛苦敲的名字不能被清掉
    expect(inputValue(newNameInput(wrapper))).toBe('SQLite 踩坑记')
    expect(createButton(wrapper).attributes('disabled')).toBeUndefined()
    // 失败不刷新：列表里不能凭空多出一行（用户会以为建成功了）
    expect(list).toHaveBeenCalledTimes(1)
    expect(items(wrapper)).toHaveLength(2)
  })

  it('创建进行中按钮禁用并显示「创建中…」（防重复提交）', async () => {
    const pending = deferred<Series>()
    vi.spyOn(seriesApi, 'create').mockReturnValue(pending.promise)

    const { wrapper } = await mountSeries()
    await newNameInput(wrapper).setValue('读书笔记')
    await createButton(wrapper).trigger('click')
    await flushPromises()

    expect(createButton(wrapper).text()).toBe('创建中…')
    expect(createButton(wrapper).attributes('disabled')).toBeDefined()

    pending.resolve(makeSeries({ id: 3 }))
    await flushPromises()
    expect(createButton(wrapper).text()).toBe('创建')
    expect(createButton(wrapper).attributes('disabled')).toBeUndefined()
  })
})

describe('SeriesView · 重命名系列', () => {
  it('点「编辑」只把那一行换成输入框，并回填名称与简介（null 简介回填空串）', async () => {
    const update = vi.spyOn(seriesApi, 'update')

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, '重构手记'), '编辑').trigger('click')

    const editing = items(wrapper)[1] as DOMWrapper<Element>
    expect(items(wrapper)[0]?.find('input').exists()).toBe(false)
    expect(inputValue(editing.get('input[aria-label="系列名称"]'))).toBe('重构手记')
    expect(inputValue(editing.get('input[aria-label="系列简介"]'))).toBe('')
    expect(update).not.toHaveBeenCalled()
  })

  it('保存走 update(id, payload)，成功后退出编辑态并刷新列表', async () => {
    const updated = vi
      .spyOn(seriesApi, 'update')
      .mockResolvedValue(makeSeries({ id: 1, name: 'SQLite 笔记', description: null }))
    const list = vi
      .spyOn(seriesApi, 'list')
      .mockResolvedValueOnce(SERIES)
      .mockResolvedValueOnce([
        makeSeries({ id: 1, name: 'SQLite 笔记', description: null }),
        SERIES[1] as Series,
      ])

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '编辑').trigger('click')
    const editing = items(wrapper)[0] as DOMWrapper<Element>
    await editing.get('input[aria-label="系列名称"]').setValue('  SQLite 笔记  ')
    await editing.get('input[aria-label="系列简介"]').setValue('')
    await itemButton(editing, '保存').trigger('click')
    await flushPromises()

    expect(updated).toHaveBeenCalledWith(1, { name: 'SQLite 笔记', description: null })
    expect(lastToastMessage()).toBe('系列已更新')
    expect(list).toHaveBeenCalledTimes(2)
    // 退出编辑态：行里不再有输入框
    expect(items(wrapper)[0]?.find('input').exists()).toBe(false)
    expect(itemByText(wrapper, 'SQLite 笔记')).toBeTruthy()
  })

  it('取消编辑：不发请求，回到只读态且原内容不变', async () => {
    const update = vi.spyOn(seriesApi, 'update')

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '编辑').trigger('click')
    const editing = items(wrapper)[0] as DOMWrapper<Element>
    await editing.get('input[aria-label="系列名称"]').setValue('改了一半')
    await itemButton(editing, '取消').trigger('click')

    expect(update).not.toHaveBeenCalled()
    expect(items(wrapper)[0]?.find('input').exists()).toBe(false)
    expect(itemByText(wrapper, 'SQLite 踩坑记')).toBeTruthy()
    expect(wrapper.text()).not.toContain('改了一半')
  })

  it('保存失败：留在编辑态、草稿不丢、提示后端文案、列表不刷新', async () => {
    vi.spyOn(seriesApi, 'update').mockRejectedValue(
      new ApiError('系列名已存在', 422, 'validation_error'),
    )
    const list = vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '编辑').trigger('click')
    const editing = items(wrapper)[0] as DOMWrapper<Element>
    await editing.get('input[aria-label="系列名称"]').setValue('重构手记')
    await itemButton(editing, '保存').trigger('click')
    await flushPromises()

    expect(lastToastMessage()).toBe('系列名已存在')
    expect(lastToastKind()).toBe('error')
    // 失败还退出编辑态的话，用户改的内容就白敲了
    const stillEditing = items(wrapper)[0] as DOMWrapper<Element>
    expect(stillEditing.find('input').exists()).toBe(true)
    expect(inputValue(stillEditing.get('input[aria-label="系列名称"]'))).toBe('重构手记')
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('编辑时清空名称会被就地拦下，不发请求（与新建同一道校验）', async () => {
    // 以前只有新建拦空名称：清空再保存会把 name: '' 发给后端（换来 422），
    // 用户看到的是服务端报错而不是"这里不能为空"；万一后端放行，
    // 列表里就出现一个没有名字、点击区域还在的系列。
    const update = vi.spyOn(seriesApi, 'update').mockResolvedValue(makeSeries({ id: 1 }))

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '编辑').trigger('click')
    const editing = items(wrapper)[0] as DOMWrapper<Element>
    await editing.get('input[aria-label="系列名称"]').setValue('   ')
    await itemButton(editing, '保存').trigger('click')
    await flushPromises()

    expect(update).not.toHaveBeenCalled()
    expect(useToast().items.value.map((item) => item.message)).toContain('请填写系列名称')
    // 仍留在编辑态，用户可以补上名字直接再保存
    expect(editing.find('input[aria-label="系列名称"]').exists()).toBe(true)
  })
})

describe('SeriesView · 删除系列', () => {
  it('点「删除」只打开确认框，文案里带上系列名与「文章会变回普通文章」', async () => {
    const remove = vi.spyOn(seriesApi, 'remove')

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '删除').trigger('click')
    await flushPromises()

    expect(remove).not.toHaveBeenCalled()
    // 「文章不会被删掉」是这个操作的全部代价，必须写在确认框里
    expect(dialog()?.textContent).toContain('SQLite 踩坑记')
    expect(dialog()?.textContent).toContain('不会删除其下文章')
    expect(dialog()?.textContent).toContain('变回普通文章')
  })

  it('取消删除：不发请求、行还在、确认框关掉', async () => {
    const remove = vi.spyOn(seriesApi, 'remove')

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('取消')

    expect(remove).not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
    expect(items(wrapper)).toHaveLength(2)
  })

  it('确认删除：remove(id) + 提示 + 刷新列表（被删的行消失）', async () => {
    const remove = vi
      .spyOn(seriesApi, 'remove')
      .mockResolvedValue({ detail: '系列已删除' })
    const list = vi
      .spyOn(seriesApi, 'list')
      .mockResolvedValueOnce(SERIES)
      .mockResolvedValueOnce([SERIES[1] as Series])

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('删除系列')

    expect(remove).toHaveBeenCalledWith(1)
    // 提示要说清连带影响，否则用户不知道文章去哪了
    expect(lastToastMessage()).toBe('系列已删除，其下文章变为普通文章')
    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('SQLite 踩坑记')
    expect(dialog()).toBeNull()
  })

  it('删除失败（作者拿到 403）：给出人话「只有站长可以删除系列」，行不消失、确认框留着', async () => {
    // 后端的真实文案是 PermissionDeniedError('该操作仅站长可用')
    vi.spyOn(seriesApi, 'remove').mockRejectedValue(
      new ApiError('该操作仅站长可用', 403, 'forbidden'),
    )
    const list = vi.spyOn(seriesApi, 'list').mockResolvedValue(SERIES)

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('删除系列')

    expect(lastToastMessage()).toBe('只有站长可以删除系列')
    expect(lastToastKind()).toBe('error')
    // 失败刷新列表会让行消失，用户以为删成功了
    expect(list).toHaveBeenCalledTimes(1)
    expect(itemByText(wrapper, 'SQLite 踩坑记')).toBeTruthy()
    expect(dialog()).not.toBeNull()
  })

  it('其它失败走原文提示，不误报成权限问题', async () => {
    vi.spyOn(seriesApi, 'remove').mockRejectedValue(
      new ApiError('系列不存在', 404, 'not_found'),
    )

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '删除').trigger('click')
    await flushPromises()
    await confirmDialog('删除系列')

    expect(lastToastMessage()).toBe('系列不存在')
    expect(lastToastKind()).toBe('error')
    expect(dialog()).not.toBeNull()
  })

  it('删除进行中：确认框两个按钮都禁用并显示「处理中…」（防重复提交）', async () => {
    const pending = deferred<{ detail: string }>()
    vi.spyOn(seriesApi, 'remove').mockReturnValue(pending.promise)

    const { wrapper } = await mountSeries()
    await itemButton(itemByText(wrapper, 'SQLite 踩坑记'), '删除').trigger('click')
    await flushPromises()
    dialogButton('删除系列')?.click()
    await flushPromises()

    expect(dialogButton('处理中…')?.disabled).toBe(true)
    expect(dialogButton('取消')?.disabled).toBe(true)

    pending.resolve({ detail: '系列已删除' })
    await flushPromises()
    expect(dialog()).toBeNull()
  })
})
