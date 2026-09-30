/**
 * ElevatorBar 测试（含 ContactQrcode 弹层：两者是一个交互单元）。
 *
 * ## 为什么必须钉住这几条
 *
 * 电梯栏挂在**每一个公开页**上，它坏掉的方式都很安静：按钮不出现、弹层点不开、
 * 点了「返回顶部」没反应、空配置却留一颗点了没反应的按钮。所以这里钉住：
 *
 * 1. **显示条件**：联系站长看配置非空、返回顶部看滚动阈值（300px，决策 D-电梯-2）；
 * 2. **弹层内容**：label 缺省、二维码 `<img>` 的 src/alt、复制按钮；
 * 3. **关闭路径**：点外部 / Esc → 收起并**把焦点还给触发按钮**（键盘用户不该迷路）；
 * 4. **脏数据兜底**：两项皆空的条目被过滤、未知 kind 不渲染成空格子。
 *
 * ## 环境说明
 *
 * - 与仓库其它 spec 一致：**不 mock 业务模块**，只替换网络出口（`siteApi.profile`）
 *   与全局 API（scrollY / rAF / scrollTo / clipboard / matchMedia），走真实 store 与真实组件。
 * - `mount(..., { attachTo: document.body })` 是必须的：弹层关闭后要把焦点还给按钮，
 *   而 jsdom 里**游离节点接不住焦点**，不挂到文档上这条断言会假绿（`activeElement`
 *   永远是 body）。
 * - `useScrollY` 是模块级单例，用例之间必须用它导出的 `__resetScrollYForTest()` 归零，
 *   否则上一条用例的滚动位置会漏进下一条，出现「单跑红、连跑绿」。
 */
import { flushPromises, mount, DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { siteApi } from '@/api'
import { __resetScrollYForTest } from '@/composables/useScrollY'
import { useToast } from '@/composables/useToast'
import { useSiteStore } from '@/stores/site'
import type { ContactQrcode, ContactQrcodeKind, SiteProfile } from '@/types'

import ElevatorBar from './ElevatorBar.vue'

/* ------------------------------------------------------------ 测试数据 */

function makeProfile(overrides: Partial<SiteProfile> = {}): SiteProfile {
  return {
    owner_name: '写代码的猫',
    headline: null,
    avatar_url: null,
    bio_md: null,
    about_md: null,
    email: null,
    location: null,
    icp: null,
    social_links: null,
    skills: null,
    comment_need_approval: true,
    allow_guest_comment: true,
    show_login_entry: true,
    contact_qrcodes: null,
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

/** 有图有号，label 故意留空 —— 缺省文案必须按 kind 补上「微信」。 */
const WECHAT: ContactQrcode = {
  kind: 'wechat',
  label: '',
  image_url: '/media/qrcodes/wechat.png',
  value: 'code-cat',
}

/** 只有号没有图：这条走「纯文本 + 复制」的渲染分支。 */
const QQ: ContactQrcode = {
  kind: 'qq',
  label: 'QQ 答疑群',
  image_url: null,
  value: '123456789',
}

/* ------------------------------------------------------------ 挂载与全局桩 */

let pinia: Pinia
let active: VueWrapper | null = null
const scrollToMock = vi.fn()

/** 滚到指定位置：先钉住 `window.scrollY`，再派发一次 scroll。 */
async function scrollToY(value: number): Promise<void> {
  vi.stubGlobal('scrollY', value)
  window.dispatchEvent(new Event('scroll'))
  // rAF 已被换成同步执行，dispatchEvent 返回时 useScrollY 的 y 已经更新，
  // 这里只等 DOM 跟上
  await nextTick()
}

async function mountBar(overrides: Partial<SiteProfile> = {}): Promise<VueWrapper> {
  vi.spyOn(siteApi, 'profile').mockResolvedValue(makeProfile(overrides))
  // 走真实加载链路：档案由 DefaultLayout 灌进 store，这里手动跑同一条路
  await useSiteStore().load()

  active = mount(ElevatorBar, { attachTo: document.body })
  await flushPromises()
  return active
}

/** 触发按钮与弹层共同的父节点：hover / 焦点都挂在它身上（一个交互单元）。 */
function hoverRoot(wrapper: VueWrapper): DOMWrapper<Element> {
  const parent = wrapper.get('button[aria-label="联系站长"]').element.parentElement
  if (!parent) throw new Error('触发按钮没有父节点，选择器或结构变了')
  return new DOMWrapper(parent)
}

async function openPanel(wrapper: VueWrapper): Promise<void> {
  await wrapper.get('button[aria-label="联系站长"]').trigger('click')
}

const panel = (wrapper: VueWrapper) => wrapper.find('[role="dialog"]')
const contactButton = (wrapper: VueWrapper) => wrapper.find('button[aria-label="联系站长"]')
const topButton = (wrapper: VueWrapper) => wrapper.find('button[aria-label="返回顶部"]')
/**
 * 触发按钮的原生节点。
 *
 * 焦点断言要拿它跟 `document.activeElement`（原生节点）比较，而 `wrapper.get(...)`
 * 的 `.element` 类型是 `VueNode<Element>`（可能是组件实例），收窄一次比在每个用例里
 * 各 cast 一次干净。
 */
const triggerEl = (wrapper: VueWrapper) =>
  wrapper.get('button[aria-label="联系站长"]').element as HTMLElement
const copyButtons = (wrapper: VueWrapper) =>
  wrapper.findAll('button').filter((button) => button.text() === '复制')
const lastToast = () => useToast().items.value.at(-1)

/** 让剪贴板可用/不可用：`navigator.clipboard` 只在安全上下文里存在。 */
function stubClipboard(writeText: (text: string) => Promise<void>): void {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
}

/**
 * `window.matchMedia` 的原始实现（`src/test/setup.ts` 装的桩），用来在用例后还原。
 *
 * 这里**不能**用 `vi.stubGlobal('matchMedia', ...)`：setup.ts 是在 window 上
 * `Object.defineProperty(value, writable: true)`（configurable 默认 false），
 * 再 defineProperty 一次会直接抛「Cannot redefine property」。它是 writable 的，
 * 直接赋值、用例后还原即可 —— 而在 vitest 里 `window === globalThis`，赋值同样能被
 * 组件里的 `window.matchMedia` 读到。
 */
const realMatchMedia = window.matchMedia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  // 模块级单例归零：不然上一条用例的滚动位置会漏进来
  __resetScrollYForTest()

  // rAF 同步执行：滚动位置在 dispatchEvent 返回时就算好，测试不必等真实帧
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback): number => {
    callback(0)
    return 0
  })
  vi.stubGlobal('scrollY', 0)
  vi.stubGlobal('scrollTo', scrollToMock)
  scrollToMock.mockClear()
  useToast().items.value = []
})

afterEach(() => {
  active?.unmount()
  active = null
  window.matchMedia = realMatchMedia
  Reflect.deleteProperty(navigator, 'clipboard')
  Reflect.deleteProperty(document, 'execCommand')
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/* ------------------------------------------------------------ 用例 */

describe('ElevatorBar · 显示条件', () => {
  it('未配置二维码：不渲染「联系站长」，但滚过 300px 后「返回顶部」照常渲染可用', async () => {
    const wrapper = await mountBar({ contact_qrcodes: null })

    expect(contactButton(wrapper).exists()).toBe(false)
    expect(topButton(wrapper).exists()).toBe(false)

    await scrollToY(400)

    // 计划 Phase 2 的明文验收：没有二维码配置**不能**把返回顶部一起带走
    expect(contactButton(wrapper).exists()).toBe(false)
    expect(topButton(wrapper).exists()).toBe(true)
    await topButton(wrapper).trigger('click')
    expect(scrollToMock).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' })
  })

  it('返回顶部：scrollY ≤ 300 不渲染，> 300 渲染（决策 D-电梯-2 的边界）', async () => {
    const wrapper = await mountBar()
    expect(topButton(wrapper).exists()).toBe(false)

    // 阈值本身是「大于」而不是「大于等于」：正好 300 时仍不该出现
    await scrollToY(300)
    expect(topButton(wrapper).exists()).toBe(false)

    await scrollToY(301)
    const top = topButton(wrapper)
    expect(top.exists()).toBe(true)
    expect(top.attributes('type')).toBe('button')
    expect(top.attributes('aria-label')).toBe('返回顶部')
  })

  it('配置齐全：渲染「联系站长」，点击展开弹层，aria-expanded 跟着变', async () => {
    const wrapper = await mountBar({ contact_qrcodes: [WECHAT, QQ] })

    const trigger = contactButton(wrapper)
    expect(trigger.attributes('aria-haspopup')).toBe('true')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(panel(wrapper).exists()).toBe(false)

    await openPanel(wrapper)

    expect(trigger.attributes('aria-expanded')).toBe('true')
    const dialog = panel(wrapper)
    // label 缺省按 kind 补（微信那条是空 label），后台填过名字的原样显示
    expect(dialog.text()).toContain('微信')
    expect(dialog.text()).toContain('QQ 答疑群')
    expect(dialog.text()).toContain('123456789')
  })

  it('弹层里二维码是 <img src> 且带「{label}二维码」的 alt', async () => {
    const wrapper = await mountBar({ contact_qrcodes: [WECHAT, QQ] })
    await openPanel(wrapper)

    const image = panel(wrapper).get('img')
    expect(image.attributes('src')).toBe('/media/qrcodes/wechat.png')
    // alt 是给读屏用户的等价信息：只说「二维码」等于什么都没说
    expect(image.attributes('alt')).toBe('微信二维码')
    expect(copyButtons(wrapper)).toHaveLength(2)
  })

  it('两项皆空的条目被过滤掉，只剩有内容的那张卡', async () => {
    const wrapper = await mountBar({
      contact_qrcodes: [
        // 后台刚点「添加二维码」，用户还没填 —— 渲染出来就是一张空卡片
        { kind: 'wechat', label: '', image_url: null, value: null },
        QQ,
      ],
    })
    await openPanel(wrapper)

    const dialog = panel(wrapper)
    expect(dialog.findAll('li')).toHaveLength(1)
    expect(dialog.text()).toContain('QQ 答疑群')
  })

  it('所有条目都空（含只有空白的字符串）→ 等价于没配置，按钮不渲染', async () => {
    const wrapper = await mountBar({
      contact_qrcodes: [{ kind: 'qq', label: '  ', image_url: '  ', value: '  ' }],
    })

    expect(contactButton(wrapper).exists()).toBe(false)
  })

  it('未知 kind（库里的历史脏数据）走通用图标与中性文案，不渲染成空格子', async () => {
    const wrapper = await mountBar({
      contact_qrcodes: [
        { kind: 'telegram' as ContactQrcodeKind, label: '', image_url: null, value: 'vip-only' },
      ],
    })
    await openPanel(wrapper)

    const dialog = panel(wrapper)
    // 不冒充「微信」：宁可是中性文案，也不能给用户一个错误的联系方式
    expect(dialog.text()).toContain('联系方式')
    expect(dialog.text()).not.toContain('微信')
    expect(dialog.find('svg').exists()).toBe(true)
    expect(dialog.text()).toContain('vip-only')
  })
})

describe('ElevatorBar · 展开与关闭', () => {
  it('桌面：悬停展开、移开收起；点一下钉住后移开仍保持', async () => {
    const wrapper = await mountBar({ contact_qrcodes: [WECHAT] })
    const trigger = contactButton(wrapper)

    await hoverRoot(wrapper).trigger('mouseenter')
    expect(trigger.attributes('aria-expanded')).toBe('true')

    await hoverRoot(wrapper).trigger('mouseleave')
    expect(panel(wrapper).exists()).toBe(false)

    // 悬停偷看之后点一下 = 钉住：鼠标移开也留着
    await hoverRoot(wrapper).trigger('mouseenter')
    await openPanel(wrapper)
    await hoverRoot(wrapper).trigger('mouseleave')

    expect(panel(wrapper).exists()).toBe(true)
    expect(trigger.attributes('aria-expanded')).toBe('true')
  })

  it('触屏（hover: none）：悬停不展开，只能点击开合', async () => {
    // 触屏浏览器 tap 之后会合成 mouseenter，不禁用 hover 分支就会出现
    // 「点一下先开、再被 click 关掉」的幽灵交互
    const fakeMatchMedia = (query: string) => ({
      matches: query === '(hover: none)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })
    window.matchMedia = fakeMatchMedia as unknown as typeof window.matchMedia

    const wrapper = await mountBar({ contact_qrcodes: [WECHAT] })

    await hoverRoot(wrapper).trigger('mouseenter')
    expect(panel(wrapper).exists()).toBe(false)

    await openPanel(wrapper)
    expect(panel(wrapper).exists()).toBe(true)

    await openPanel(wrapper)
    expect(panel(wrapper).exists()).toBe(false)
  })

  it('键盘 Tab 进按钮即展开（焦点路径），Esc 关闭并把焦点还给按钮', async () => {
    const wrapper = await mountBar({ contact_qrcodes: [WECHAT] })
    const trigger = contactButton(wrapper)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }))
    triggerEl(wrapper).focus()
    await nextTick()
    expect(panel(wrapper).exists()).toBe(true)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()

    expect(panel(wrapper).exists()).toBe(false)
    expect(trigger.attributes('aria-expanded')).toBe('false')
    // 关掉之后焦点必须回到触发按钮：否则键盘用户要重新 Tab 一遍整页
    expect(document.activeElement).toBe(triggerEl(wrapper))
  })

  it('点弹层外部关闭，并把焦点还给按钮', async () => {
    const wrapper = await mountBar({ contact_qrcodes: [WECHAT] })
    const trigger = contactButton(wrapper)
    await openPanel(wrapper)
    expect(panel(wrapper).exists()).toBe(true)

    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await nextTick()

    expect(panel(wrapper).exists()).toBe(false)
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(triggerEl(wrapper))
  })
})

describe('ElevatorBar · 复制联系方式', () => {
  it('点「复制」写进剪贴板并提示成功；两条路径都失败时提示手动复制', async () => {
    const wrapper = await mountBar({ contact_qrcodes: [QQ] })
    await openPanel(wrapper)

    const writeText = vi.fn().mockResolvedValue(undefined)
    stubClipboard(writeText)
    await copyButtons(wrapper)[0].trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('123456789')
    expect(lastToast()?.message).toBe('已复制QQ 答疑群')
    expect(lastToast()?.kind).toBe('success')

    // 降级路径也失败（局域网 HTTP 下没有 navigator.clipboard，execCommand 也可能被禁）
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: vi.fn(() => false),
    })
    await copyButtons(wrapper)[0].trigger('click')
    await flushPromises()

    expect(lastToast()?.kind).toBe('error')
    expect(lastToast()?.message).toBe('浏览器不允许自动复制，请手动选中复制')
  })
})
