/**
 * 建站时长的单元测试。
 *
 * 重点是**边界**：旧站脚本用 `Math.round` 算秒，会跳到 60（`Math.round(59.6) === 60`），
 * 所以 59.6 秒这个位置必须锁住；时钟回拨导致的负值也不能渲染成「-1 天」。
 */
import { describe, expect, it } from 'vitest'

import { formatUptime, SITE_EPOCH_MS, splitUptime } from '@/utils/uptime'

const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

describe('splitUptime', () => {
  it('零与负值都归零（时钟回拨不该显示「-1 天」）', () => {
    for (const input of [0, -1, -DAY]) {
      expect(splitUptime(input)).toEqual({ days: 0, hours: 0, minutes: 0, seconds: 0 })
    }
  })

  it('拆成天/时/分/秒，且各段不互相折进', () => {
    expect(splitUptime(DAY + 2 * HOUR + 3 * MINUTE + 4 * SECOND)).toEqual({
      days: 1,
      hours: 2,
      minutes: 3,
      seconds: 4,
    })
  })

  it('59.6 秒仍是 59 秒（旧站 Math.round 会显示 60 秒）', () => {
    expect(splitUptime(59 * SECOND + 600).seconds).toBe(59)
  })

  it('刚满一天时回到 0 时 0 分 0 秒、天数进位', () => {
    expect(splitUptime(DAY)).toEqual({ days: 1, hours: 0, minutes: 0, seconds: 0 })
  })

  it('大数（一年以上）不溢出', () => {
    const u = splitUptime(400 * DAY + 5 * HOUR)
    expect(u.days).toBe(400)
    expect(u.hours).toBe(5)
  })
})

describe('formatUptime', () => {
  it('按旧站文案格式渲染，时/分/秒补零到两位', () => {
    expect(formatUptime({ days: 353, hours: 2, minutes: 15, seconds: 4 })).toBe(
      '353 天 02 小时 15 分 04 秒',
    )
  })

  it('个位数与零都补零', () => {
    expect(formatUptime({ days: 0, hours: 0, minutes: 0, seconds: 0 })).toBe(
      '0 天 00 小时 00 分 00 秒',
    )
  })
})

describe('SITE_EPOCH_MS', () => {
  it('是旧站脚本里那个起点（11/14/2025 13:14:52 Asia/Shanghai）', () => {
    // 写成 UTC 断言，避免依赖跑测试的机器时区
    expect(new Date(SITE_EPOCH_MS).toISOString()).toBe('2025-11-14T05:14:52.000Z')
  })
})
