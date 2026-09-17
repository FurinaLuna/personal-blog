/**
 * 展示层格式化工具测试。
 *
 * 重点在「空值防护」：计数字段可能缺失（旧缓存 / 部分接口不返回），
 * null/undefined 必须兜底成 0，而不是在界面上渲染出 "null" / "NaN"。
 */
import { describe, expect, it } from 'vitest'

import {
  buildSrcset,
  formatBytes,
  formatCount,
  formatReadingTime,
  formatYearMonth,
  safeExternalUrl,
} from '@/utils/format'

describe('formatCount', () => {
  it('按量级缩写：千位 k、万位 w', () => {
    expect(formatCount(0)).toBe('0')
    expect(formatCount(999)).toBe('999')
    expect(formatCount(1234)).toBe('1.2k')
    expect(formatCount(9500)).toBe('9.5k')
    expect(formatCount(12345)).toBe('1.2w')
  })

  it('null/undefined/NaN 一律按 0 展示', () => {
    expect(formatCount(null)).toBe('0')
    expect(formatCount(undefined)).toBe('0')
    expect(formatCount(Number.NaN)).toBe('0')
  })
})

describe('formatBytes', () => {
  it('按量级换算单位', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.00 MB')
  })

  it('空值按 0 B 兜底', () => {
    expect(formatBytes(null)).toBe('0 B')
    expect(formatBytes(undefined)).toBe('0 B')
  })
})

describe('formatReadingTime', () => {
  it('空值回落到「少于 1 分钟」，正常值照常展示', () => {
    expect(formatReadingTime(null)).toBe('少于 1 分钟')
    expect(formatReadingTime(undefined)).toBe('少于 1 分钟')
    expect(formatReadingTime(5)).toBe('约 5 分钟')
  })
})

describe('formatYearMonth', () => {
  it('正常格式化为「YYYY 年 M 月」', () => {
    expect(formatYearMonth('2026-09')).toBe('2026 年 9 月')
  })

  it('空值不抛异常，回落为空串', () => {
    expect(formatYearMonth(null)).toBe('')
    expect(formatYearMonth(undefined)).toBe('')
  })
})

describe('buildSrcset', () => {
  it('变体列表拼成 srcset 属性值，宽度带 w 描述符', () => {
    expect(
      buildSrcset([
        { width: 480, url: '/media/a-480.avif' },
        { width: 800, url: '/media/a-800.avif' },
      ]),
    ).toBe('/media/a-480.avif 480w, /media/a-800.avif 800w')
  })

  it('空列表返回空串，调用方用 || undefined 让属性不渲染', () => {
    expect(buildSrcset([])).toBe('')
  })
})

describe('safeExternalUrl', () => {
  it('放行 http / https', () => {
    expect(safeExternalUrl('https://example.com/a')).toBe('https://example.com/a')
    expect(safeExternalUrl('http://example.com')).toBe('http://example.com/')
  })

  it('挡掉伪协议——Vue 不清洗动态 href，这些会被原样写进 DOM', () => {
    expect(safeExternalUrl('javascript:alert(document.cookie)')).toBeNull()
    expect(safeExternalUrl('JavaScript:alert(1)')).toBeNull()
    expect(safeExternalUrl('  javascript:alert(1)')).toBeNull()
    expect(safeExternalUrl('data:text/html,<script>alert(1)</script>')).toBeNull()
    expect(safeExternalUrl('vbscript:msgbox(1)')).toBeNull()
  })

  it('空值与垃圾输入返回 null，交给调用方渲染成纯文本', () => {
    expect(safeExternalUrl(null)).toBeNull()
    expect(safeExternalUrl(undefined)).toBeNull()
    expect(safeExternalUrl('')).toBeNull()
    expect(safeExternalUrl('   ')).toBeNull()
    expect(safeExternalUrl('不是一个网址')).toBeNull()
  })
})
