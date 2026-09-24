/**
 * SettingsView 测试（站点设置，仅站长）。
 *
 * 这一页自己不碰 `@/api`：读写都经过 site store（store 里维护 profile / loaded，
 * `update()` 成功后把服务端返回值写回 profile）。所以测试 spy 的是 store 底下的
 * `siteApi.profile` / `siteApi.updateProfile`，走真实 store —— 这样「表单有没有
 * 真的以服务端返回值为准」才是真实行为的证据，而不是被 mock 掉的假设。守的契约：
 *
 * 1. **回填口径**：技能数组用「, 」拼成一行文本，社交链接是**副本**（改表单不能
 *    污染 store 里的对象），null 一律渲染成空输入框而不是 "null"；
 * 2. **写操作的 payload**：文本字段 trim、空串转 null（后端靠 null 清空字段）、
 *    站点名称空则回落「个人博客」、技能按中英文逗号切分并丢掉空项、
 *    名称为空或地址为空的半成品链接会被丢弃（否则前台页脚出现空链接）；
 * 3. **失败不等于成功**：保存失败要提示、按钮解禁、**表单草稿留在原地**，
 *    并且 store 里的 profile 必须还是旧值（前台不能已经显示新配置）；
 * 4. **防重复提交**：保存中顶部与底部两个按钮共用同一 action 计数，一起禁用；
 * 5. **本地边界**：社交链接最多 8 条，第 9 次只提示、不加行；
 * 6. **Markdown 正文原样提交**：只有空串转 null，不做 trim（留白由作者掌握）。
 *
 * 另外钉住两条**现状缺陷**（本轮只加测试、不改产品代码，见文末「加载失败」一节）：
 * 档案加载失败时表单是空白的且没有任何失败提示，而这份空白表单可以直接保存 ——
 * 会把站点名称、简介、关于、评论策略整片覆盖成默认值。用例把危险行为固定下来，
 * 避免以后误以为这里已经有保护。
 *
 * 说明：不 mock 业务模块（store 也是真的），只替换网络出口。
 */
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { ApiError, siteApi } from '@/api'
import { useToast } from '@/composables/useToast'
import { useSiteStore } from '@/stores/site'
import type { SiteProfile } from '@/types'

import SettingsView from './SettingsView.vue'

/* ------------------------------------------------------------ 测试数据 */

const PROFILE: SiteProfile = {
  owner_name: '写代码的猫',
  headline: '一个喜欢把事情做到底的开发者。',
  avatar_url: '/media/avatars/cat.png',
  bio_md: '第一段简介',
  about_md: '## 关于我',
  email: 'cat@example.com',
  location: '杭州',
  icp: '浙ICP备00000000号',
  social_links: [
    { label: 'GitHub', url: 'https://github.com/cat' },
    { label: '博客', url: 'https://cat.example.com' },
  ],
  skills: ['Python', 'FastAPI', 'Vue'],
  comment_need_approval: true,
  allow_guest_comment: false,
  updated_at: '2026-01-01T00:00:00Z',
}

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
    routes: [{ path: '/admin/settings', component: Blank }],
  })
}

/** preloaded：模拟「站点档案在应用外壳里已经加载过」，此时页面不该再请求一次。 */
async function mountSettings(options: { preloaded?: boolean } = {}) {
  const router = makeRouter()
  await router.push('/admin/settings')
  await router.isReady()
  const site = useSiteStore()
  if (options.preloaded) {
    site.profile = { ...PROFILE }
    site.loaded = true
  }
  const wrapper = mount(SettingsView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { wrapper, router, site }
}

/**
 * 字段定位：这一页的输入框只有 `<label>` 里的文字作为可访问名称
 * （模板没给 aria-label），所以按标签文字取控件。
 */
function fieldControl(wrapper: VueWrapper, label: string): DOMWrapper<Element> {
  const found = wrapper
    .findAll('label')
    .find((item) => item.find('span').exists() && item.find('span').text() === label)
  if (!found) throw new Error(`找不到标签为「${label}」的字段`)
  const control = found.find('input, textarea')
  if (!control.exists()) throw new Error(`标签「${label}」下没有输入控件`)
  return control
}

function fieldValue(wrapper: VueWrapper, label: string): string {
  return (fieldControl(wrapper, label).element as HTMLInputElement).value
}

async function setField(wrapper: VueWrapper, label: string, value: string): Promise<void> {
  await fieldControl(wrapper, label).setValue(value)
}

/* 注：wrapper.get() 的返回类型是 Omit<DOMWrapper, 'exists'>，这里不写返回类型交给推断 */
function skillsInput(wrapper: VueWrapper) {
  return wrapper.get('input[placeholder="Python, FastAPI, Vue, PostgreSQL"]')
}

function socialSection(wrapper: VueWrapper): DOMWrapper<Element> {
  const found = wrapper
    .findAll('section')
    .find((item) => item.text().includes('社交链接'))
  if (!found) throw new Error('找不到「社交链接」区块')
  return found
}

function socialNames(wrapper: VueWrapper): string[] {
  return socialSection(wrapper)
    .findAll('input[aria-label="链接名称"]')
    .map((item) => (item.element as HTMLInputElement).value)
}

function socialUrls(wrapper: VueWrapper): string[] {
  return socialSection(wrapper)
    .findAll('input[aria-label="链接地址"]')
    .map((item) => (item.element as HTMLInputElement).value)
}

function socialAddButton(wrapper: VueWrapper): DOMWrapper<Element> {
  const found = socialSection(wrapper)
    .findAll('button')
    .find((item) => item.text().trim() === '添加')
  if (!found) throw new Error('找不到「添加」按钮')
  return found
}

/** 顶部与底部各有一个保存按钮，共用同一个 action 状态。 */
function saveButtons(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('button').filter((item) => item.text().includes('保存'))
}

async function clickButton(wrapper: VueWrapper, label: string): Promise<void> {
  const target = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!target) throw new Error(`找不到文案为「${label}」的按钮`)
  await target.trigger('click')
  await flushPromises()
}

async function save(wrapper: VueWrapper): Promise<void> {
  await (saveButtons(wrapper)[0] as DOMWrapper<Element>).trigger('click')
  await flushPromises()
}

/** 两个评论策略开关：模板里顺序是「需要审核」「允许游客」。 */
function checkboxes(wrapper: VueWrapper): DOMWrapper<Element>[] {
  return wrapper.findAll('input[type="checkbox"]')
}

function isChecked(wrapper: VueWrapper, index: number): boolean {
  return (checkboxes(wrapper)[index]?.element as HTMLInputElement).checked
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
  vi.spyOn(siteApi, 'profile').mockResolvedValue({ ...PROFILE })
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

/* ------------------------------------------------------------ 用例 */

describe('SettingsView · 加载与回填', () => {
  it('挂载时拉取站点档案，并把各项（含技能、社交链接、开关）回填进表单', async () => {
    const profile = vi.spyOn(siteApi, 'profile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()

    expect(profile).toHaveBeenCalledTimes(1)
    expect(fieldValue(wrapper, '站点名称')).toBe('写代码的猫')
    expect(fieldValue(wrapper, '所在地')).toBe('杭州')
    expect(fieldValue(wrapper, '一句话签名')).toBe('一个喜欢把事情做到底的开发者。')
    expect(fieldValue(wrapper, '联系邮箱')).toBe('cat@example.com')
    expect(fieldValue(wrapper, '备案号 / 页脚附加信息')).toBe('浙ICP备00000000号')
    expect(fieldValue(wrapper, '头像地址')).toBe('/media/avatars/cat.png')
    expect(fieldValue(wrapper, '个人简介')).toBe('第一段简介')
    expect(fieldValue(wrapper, '关于本站')).toBe('## 关于我')

    // 技能是数组，编辑态用「, 」拼成一行（改回数组时按逗号切）
    expect((skillsInput(wrapper).element as HTMLInputElement).value).toBe('Python, FastAPI, Vue')

    expect(socialNames(wrapper)).toEqual(['GitHub', '博客'])
    expect(socialUrls(wrapper)).toEqual(['https://github.com/cat', 'https://cat.example.com'])

    // 评论策略开关按档案回填：开着审核、但这里关掉了游客评论
    expect(isChecked(wrapper, 0)).toBe(true)
    expect(isChecked(wrapper, 1)).toBe(false)

    // 顶部与底部各一个保存入口
    expect(saveButtons(wrapper)).toHaveLength(2)
  })

  it('store 已有缓存时立刻回填，且不再发一次档案请求（应用外壳已经拉过）', async () => {
    const profile = vi.spyOn(siteApi, 'profile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings({ preloaded: true })

    expect(profile).not.toHaveBeenCalled()
    expect(fieldValue(wrapper, '站点名称')).toBe('写代码的猫')
    expect((skillsInput(wrapper).element as HTMLInputElement).value).toBe('Python, FastAPI, Vue')
  })

  it('技能为 null 时渲染成空输入框（不能出现字面量 null）', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue({ ...PROFILE, skills: null })

    const { wrapper } = await mountSettings()

    expect((skillsInput(wrapper).element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).not.toContain('null')
  })

  it('社交链接为 null 时显示「还没有添加链接」，不报错', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue({ ...PROFILE, social_links: null })

    const { wrapper } = await mountSettings()

    expect(socialSection(wrapper).text()).toContain('还没有添加链接。')
    expect(socialNames(wrapper)).toHaveLength(0)
  })

  it('回填的社交链接是副本：改表单不会改到 store 里的档案对象', async () => {
    const { wrapper, site } = await mountSettings()

    await socialSection(wrapper).findAll('input[aria-label="链接名称"]')[0]?.setValue('改名了')

    // 直接改 store 里的对象会让「未保存的草稿」污染前台页脚
    expect(site.profile.social_links?.[0]?.label).toBe('GitHub')
  })
})

describe('SettingsView · 保存的 payload 口径', () => {
  it('文本字段 trim、空串转 null，技能按中英文逗号切分并丢掉空项', async () => {
    const update = vi
      .spyOn(siteApi, 'updateProfile')
      .mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()
    await setField(wrapper, '站点名称', '  写代码的猫  ')
    await setField(wrapper, '所在地', '   ')
    await setField(wrapper, '一句话签名', '  新的签名  ')
    await setField(wrapper, '联系邮箱', '  ')
    await setField(wrapper, '备案号 / 页脚附加信息', ' 浙ICP备11111111号 ')
    await setField(wrapper, '头像地址', ' ')
    await skillsInput(wrapper).setValue('Python, , FastAPI，Vue , ')
    await save(wrapper)

    expect(update).toHaveBeenCalledWith({
      owner_name: '写代码的猫',
      // 空串必须转 null：后端靠 null 清空字段，传空串会把页脚写成空白
      headline: '新的签名',
      avatar_url: null,
      location: null,
      email: null,
      icp: '浙ICP备11111111号',
      skills: ['Python', 'FastAPI', 'Vue'],
      // 已填好的两条链接原样提交（半成品被丢弃的场景见下一节）
      social_links: [
        { label: 'GitHub', url: 'https://github.com/cat' },
        { label: '博客', url: 'https://cat.example.com' },
      ],
      comment_need_approval: true,
      allow_guest_comment: false,
      bio_md: '第一段简介',
      about_md: '## 关于我',
    })
  })

  it('Markdown 正文原样提交（只把空串转 null，不做 trim）', async () => {
    const update = vi.spyOn(siteApi, 'updateProfile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()
    // Markdown 里的首尾留白是作者的排版意图，不能悄悄吃掉
    await setField(wrapper, '个人简介', '  第一段  ')
    await setField(wrapper, '关于本站', '')
    await save(wrapper)

    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({ bio_md: '  第一段  ', about_md: null }),
    )
  })

  it('站点名称为空（或只有空格）时按默认名「个人博客」提交，前台标题不能是空白', async () => {
    const update = vi.spyOn(siteApi, 'updateProfile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()
    await setField(wrapper, '站点名称', '   ')
    await save(wrapper)

    expect(update).toHaveBeenCalledWith(expect.objectContaining({ owner_name: '个人博客' }))
  })

  it('技能只填逗号与空格时提交空数组，而不是含空串的数组（脏数据会让关于页出现空标签）', async () => {
    const update = vi.spyOn(siteApi, 'updateProfile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()
    await skillsInput(wrapper).setValue(' , ， ')
    await save(wrapper)

    expect(update).toHaveBeenCalledWith(expect.objectContaining({ skills: [] }))
  })

  it('两个评论开关按勾选状态提交（关掉审核 = 访客评论直接显示）', async () => {
    const update = vi.spyOn(siteApi, 'updateProfile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()
    await checkboxes(wrapper)[0]?.setValue(false)
    await checkboxes(wrapper)[1]?.setValue(true)
    await save(wrapper)

    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({ comment_need_approval: false, allow_guest_comment: true }),
    )
  })

  it('名称为空或地址为空的半成品链接在保存时被丢弃，地址两边空白会被 trim', async () => {
    const update = vi.spyOn(siteApi, 'updateProfile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()
    // 第 3 行是刚点出来还没填的空行；第 4 行只有名称
    await socialAddButton(wrapper).trigger('click')
    await socialAddButton(wrapper).trigger('click')
    const names = socialSection(wrapper).findAll('input[aria-label="链接名称"]')
    const urls = socialSection(wrapper).findAll('input[aria-label="链接地址"]')
    await names[0]?.setValue(' GitHub ')
    await urls[0]?.setValue('  https://github.com/cat  ')
    await names[1]?.setValue('   ')
    await urls[1]?.setValue('https://cat.example.com')
    // 第 3 行的 label 与 url 都是空的
    await names[3]?.setValue('只有名称')

    await save(wrapper)

    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({ social_links: [{ label: 'GitHub', url: 'https://github.com/cat' }] }),
    )
  })
})

describe('SettingsView · 保存成功与失败', () => {
  it('保存成功：提示「站点设置已保存」，并用服务端返回值回填表单', async () => {
    const update = vi.spyOn(siteApi, 'updateProfile').mockResolvedValue({
      ...PROFILE,
      // 后端会做归一化：空白名称回落到默认名、技能去重排序
      owner_name: '个人博客',
      skills: ['FastAPI', 'Python', 'Vue'],
    })

    const { wrapper, site } = await mountSettings()
    await setField(wrapper, '站点名称', '   ')
    await save(wrapper)

    expect(update).toHaveBeenCalledTimes(1)
    expect(lastToastMessage()).toBe('站点设置已保存')
    expect(lastToastKind()).toBe('success')
    // 以服务端返回值为准回填：否则本地草稿与服务端不一致，用户以为没保存上
    expect(fieldValue(wrapper, '站点名称')).toBe('个人博客')
    expect((skillsInput(wrapper).element as HTMLInputElement).value).toBe('FastAPI, Python, Vue')
    expect(site.profile.owner_name).toBe('个人博客')
  })

  it('保存失败：提示后端文案、按钮解禁、表单草稿留在原地（不假装已保存）', async () => {
    vi.spyOn(siteApi, 'updateProfile').mockRejectedValue(
      new ApiError('备案号格式不正确', 422, 'validation_error'),
    )

    const { wrapper, site } = await mountSettings()
    await setField(wrapper, '站点名称', '新名字')
    await save(wrapper)

    expect(lastToastMessage()).toBe('备案号格式不正确')
    expect(lastToastKind()).toBe('error')

    // 关键：store 里的档案必须还是旧值，前台不能已经显示成新配置
    expect(site.profile.owner_name).toBe('写代码的猫')
    // 用户辛苦敲的内容不能被回滚掉
    expect(fieldValue(wrapper, '站点名称')).toBe('新名字')
    // 失败后按钮必须解禁，能改完再提交
    expect((saveButtons(wrapper)[0] as DOMWrapper<Element>).attributes('disabled')).toBeUndefined()
  })

  it('失败提示走兜底：后端没给可读消息时用「保存失败」', async () => {
    vi.spyOn(siteApi, 'updateProfile').mockRejectedValue(
      new ApiError('', 500, 'internal_error'),
    )

    const { wrapper } = await mountSettings()
    await save(wrapper)

    expect(lastToastMessage()).toBe('保存失败')
    expect(lastToastKind()).toBe('error')
  })

  it('保存进行中：顶部与底部两个按钮一起禁用并显示「保存中…」（防重复提交）', async () => {
    const pending = deferred<SiteProfile>()
    vi.spyOn(siteApi, 'updateProfile').mockReturnValue(pending.promise)

    const { wrapper } = await mountSettings()
    await (saveButtons(wrapper)[0] as DOMWrapper<Element>).trigger('click')
    await flushPromises()

    const running = saveButtons(wrapper)
    expect(running).toHaveLength(2)
    expect(running[0]?.text()).toBe('保存中…')
    expect(running[1]?.text()).toBe('保存中…')
    expect(running[0]?.attributes('disabled')).toBeDefined()
    expect(running[1]?.attributes('disabled')).toBeDefined()

    pending.resolve({ ...PROFILE })
    await flushPromises()

    const settled = saveButtons(wrapper)
    expect(settled[0]?.text()).toBe('保存设置')
    expect(settled[0]?.attributes('disabled')).toBeUndefined()
  })

  it('连续保存两次会发两次请求（没有单飞锁，防重复靠禁用按钮）', async () => {
    const update = vi.spyOn(siteApi, 'updateProfile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()
    await save(wrapper)
    await save(wrapper)

    expect(update).toHaveBeenCalledTimes(2)
  })
})

describe('SettingsView · 社交链接增删', () => {
  it('点「添加」追加一行空链接，最多 8 条：第 9 次只提示、不加行', async () => {
    const { wrapper } = await mountSettings()
    const add = socialAddButton(wrapper)

    // 档案里已有 2 条，再点 6 次刚好到上限
    for (let index = 0; index < 6; index += 1) await add.trigger('click')
    expect(socialNames(wrapper)).toHaveLength(8)

    await add.trigger('click')
    expect(socialNames(wrapper)).toHaveLength(8)
    expect(lastToastMessage()).toBe('最多添加 8 个链接')
    expect(lastToastKind()).toBe('error')
  })

  it('点「移除」只删掉那一行，其余保持原顺序', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue({
      ...PROFILE,
      social_links: [
        { label: 'GitHub', url: 'https://github.com/cat' },
        { label: '博客', url: 'https://cat.example.com' },
        { label: 'RSS', url: 'https://cat.example.com/feed.xml' },
      ],
    })

    const { wrapper } = await mountSettings()
    // 社交链接区块里的按钮：0 是「添加」，之后每行一个「移除」
    await socialSection(wrapper).findAll('button')[2]?.trigger('click')

    // 删中间那一条，两侧顺序不变（用 index 当 key，错位会串行）
    expect(socialNames(wrapper)).toEqual(['GitHub', 'RSS'])
    expect(socialUrls(wrapper)).toEqual([
      'https://github.com/cat',
      'https://cat.example.com/feed.xml',
    ])
  })

  it('删光后回到空态文案，重新添加又能出现输入行', async () => {
    const { wrapper } = await mountSettings()

    await socialSection(wrapper).findAll('button')[1]?.trigger('click')
    await socialSection(wrapper).findAll('button')[1]?.trigger('click')
    expect(socialSection(wrapper).text()).toContain('还没有添加链接。')

    await socialAddButton(wrapper).trigger('click')
    expect(socialSection(wrapper).text()).not.toContain('还没有添加链接。')
    expect(socialNames(wrapper)).toEqual([''])
  })
})

describe('SettingsView · 加载失败（修复后的契约）', () => {
  it('档案取不到时给出错误条与重试入口，而不是一个没有解释的空表单', async () => {
    // site.load() 有意吞掉异常并回落默认值（站点信息属于锦上添花，前台不该白屏），
    // 但它现在**同时把失败记在 store.error 上** —— 后台据此提示 + 重试。
    // 以前这里是静默的：用户无法区分「还没加载出来」和「这个站点本来就没配过」。
    const profile = vi
      .spyOn(siteApi, 'profile')
      .mockRejectedValueOnce(new ApiError('服务器开小差了，请稍后重试', 500, 'internal_error'))
      .mockResolvedValueOnce({ ...PROFILE })

    const { wrapper, site } = await mountSettings()

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('服务器开小差了')
    // 表单此时确实是空的（store 回落默认值），但页面已经解释了为什么
    expect(fieldValue(wrapper, '站点名称')).toBe('')
    expect(site.loaded).toBe(false)

    // 重试要真的重新请求并把表单填回来
    await clickButton(wrapper, '重试')
    expect(profile).toHaveBeenCalledTimes(2)
    expect(fieldValue(wrapper, '站点名称')).toBe(PROFILE.owner_name)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('档案没加载成功时保存按钮不可用（不能用默认值覆盖真实配置）', async () => {
    // 这是原先最危险的一条：保存按钮只看 action.running，不看 site.loaded。
    // 「档案没加载出来」时点一下保存，等于把真实配置（简介 / 关于 / 邮箱 /
    // 备案号 / 评论策略）全部清成 null 与默认开关值 —— 静默数据丢失。
    vi.spyOn(siteApi, 'profile').mockRejectedValue(new ApiError('网络异常', 0, 'network_error'))
    const update = vi.spyOn(siteApi, 'updateProfile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()

    expect(saveButtons(wrapper).every((item) => item.attributes('disabled') !== undefined)).toBe(
      true,
    )

    await save(wrapper)
    expect(update).not.toHaveBeenCalled()
  })

  it('加载成功后保存按钮恢复可用', async () => {
    vi.spyOn(siteApi, 'profile').mockResolvedValue({ ...PROFILE })

    const { wrapper } = await mountSettings()

    expect(saveButtons(wrapper).every((item) => item.attributes('disabled') === undefined)).toBe(
      true,
    )
  })
})
