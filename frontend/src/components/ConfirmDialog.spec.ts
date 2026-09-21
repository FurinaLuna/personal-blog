/**
 * ConfirmDialog 测试。
 *
 * 这个组件声明了 `aria-modal="true"`，那就必须真的兑现，否则屏幕阅读器会以为
 * 用户还在原页面、键盘 Tab 也会跑到遮罩背后的控件上去——这是之前踩过并修掉的坑。
 * 这里把那四条无障碍契约钉成断言，顺便守住两条交互语义：
 *
 * 1. 打开时焦点移进对话框；危险操作默认落在「取消」上（Enter 的肌肉记忆不该删数据）；
 * 2. Tab / Shift+Tab 在对话框内循环，不会漏到页面其余部分；
 * 3. ESC 取消，且关闭后焦点还给打开它的那个元素；
 * 4. 标题/正文通过 aria-labelledby / aria-describedby 关联；
 * 5. 打开时锁滚动，关闭（含卸载）时恢复；
 * 6. loading 时两个按钮都禁用并显示「处理中…」，避免重复提交。
 *
 * 对话框是 Teleport 到 body 的，所以断言一律查 document，而不是 wrapper 内部。
 */
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'

import ConfirmDialog from '@/components/ConfirmDialog.vue'

function dialog(): HTMLElement | null {
  return document.querySelector('[role="dialog"]')
}

function buttonByText(text: string): HTMLButtonElement | undefined {
  const panel = dialog()?.querySelector('.card')
  return [...(panel?.querySelectorAll('button') ?? [])].find(
    (b) => b.textContent?.trim() === text,
  ) as HTMLButtonElement | undefined
}

/** 造一个「触发按钮」当焦点来源，用于验证关闭后焦点归还 */
function makeTrigger(): HTMLButtonElement {
  const trigger = document.createElement('button')
  trigger.textContent = '触发'
  document.body.appendChild(trigger)
  trigger.focus()
  return trigger
}

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

async function openDialog(props: Record<string, unknown> = {}) {
  const wrapper = mount(ConfirmDialog, {
    props: { open: false, ...props },
    attachTo: document.body,
  })
  await wrapper.setProps({ open: true })
  await nextTick()
  // 焦点是在 nextTick 里移的，多等一拍
  await nextTick()
  return wrapper
}

describe('ConfirmDialog', () => {
  it('关闭时不渲染对话框，打开后带 aria-modal 且标题关联正确', async () => {
    const wrapper = mount(ConfirmDialog, {
      props: { open: false, title: '删除文章', message: '删除后不可恢复' },
      attachTo: document.body,
    })
    expect(dialog()).toBeNull()

    await wrapper.setProps({ open: true })
    await nextTick()

    const el = dialog()
    expect(el).not.toBeNull()
    expect(el?.getAttribute('aria-modal')).toBe('true')
    const titleId = el?.getAttribute('aria-labelledby')
    expect(el?.querySelector(`#${titleId}`)?.textContent).toContain('删除文章')
    // 有 message 时才挂 describedby，没有就不该挂一个空引用
    expect(el?.getAttribute('aria-describedby')).toBeTruthy()
  })

  it('危险操作打开时焦点落在「取消」上，避免顺手 Enter 删掉数据', async () => {
    await openDialog({ danger: true, confirmLabel: '删除', cancelLabel: '取消' })
    expect(document.activeElement?.textContent?.trim()).toBe('取消')
  })

  it('普通操作打开时焦点落在「确认」上', async () => {
    await openDialog({ confirmLabel: '确认', cancelLabel: '取消' })
    expect(document.activeElement?.textContent?.trim()).toBe('确认')
  })

  it('Tab 在对话框内循环：末个元素再 Tab 回到第一个', async () => {
    await openDialog({ confirmLabel: '确认', cancelLabel: '取消' })
    const cancel = buttonByText('取消')!
    const confirm = buttonByText('确认')!

    confirm.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    await nextTick()
    expect(document.activeElement).toBe(cancel)

    // 反向：第一个元素 Shift+Tab 回到最后一个
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }),
    )
    await nextTick()
    expect(document.activeElement).toBe(confirm)
  })

  it('ESC 触发取消；关闭状态下按 ESC 不会误发事件', async () => {
    const wrapper = await openDialog()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('cancel')).toHaveLength(1)

    await wrapper.setProps({ open: false })
    await nextTick()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('关闭后把焦点还给打开它的元素', async () => {
    const trigger = makeTrigger()
    const wrapper = await openDialog()
    expect(document.activeElement).not.toBe(trigger)

    await wrapper.setProps({ open: false })
    await nextTick()
    await nextTick()
    expect(document.activeElement).toBe(trigger)
  })

  it('打开时锁滚动，关闭后恢复', async () => {
    const wrapper = await openDialog()
    expect(document.body.style.overflow).toBe('hidden')

    await wrapper.setProps({ open: false })
    await nextTick()
    expect(document.body.style.overflow).toBe('')

    // 卸载时也必须解锁：否则组件被路由卸掉后页面永远滚不动
    await wrapper.setProps({ open: true })
    await nextTick()
    wrapper.unmount()
    expect(document.body.style.overflow).toBe('')
  })

  it('点遮罩本身取消，点面板内部不取消', async () => {
    const wrapper = await openDialog()
    const overlay = dialog()!
    const panel = overlay.querySelector('.card') as HTMLElement

    panel.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('cancel')).toBeUndefined()

    overlay.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('loading 时两个按钮都禁用并显示「处理中…」，防止重复提交', async () => {
    await openDialog({ loading: true, confirmLabel: '删除' })
    const buttons = [...(dialog()?.querySelectorAll('button') ?? [])] as HTMLButtonElement[]
    expect(buttons).toHaveLength(2)
    expect(buttons.every((b) => b.disabled)).toBe(true)
    // 内容在 Teleport 里，wrapper.text() 是空的，必须从对话框节点上取
    expect(dialog()?.textContent).toContain('处理中…')
  })
})
