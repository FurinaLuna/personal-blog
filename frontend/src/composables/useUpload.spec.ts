/**
 * useUpload 测试。
 *
 * 核心契约：成功提示由调用方注入、失败返回 null 且已 toast、
 * 客户端大小预检可选、批量场景（successMessage: false + errorPrefix）语义正确。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { attachmentApi } from '@/api'
import { useToast } from '@/composables/useToast'
import { useUpload } from '@/composables/useUpload'
import type { Attachment } from '@/types'

const toast = useToast()

const makeAttachment = (id: number): Attachment => ({
  id,
  url: `/media/uploads/${id}.png`,
  markdown: `![x](/media/uploads/${id}.png)`,
  original_name: 'x.png',
  mime_type: 'image/png',
  size: 100,
  kind: 'image',
  width: 10,
  height: 10,
  thumbnail_url: null,
  variants: [],
  created_at: '2026-01-01T00:00:00Z',
})

beforeEach(() => {
  toast.items.value = []
  vi.restoreAllMocks()
})

describe('useUpload 成功路径', () => {
  it('返回附件并弹默认成功提示', async () => {
    vi.spyOn(attachmentApi, 'upload').mockResolvedValue(makeAttachment(1))
    const { upload } = useUpload()
    const result = await upload(new File(['x'], 'a.png'))
    expect(result?.id).toBe(1)
    expect(toast.items.value.at(-1)?.message).toBe('上传成功')
  })

  it('注入的成功文案生效', async () => {
    vi.spyOn(attachmentApi, 'upload').mockResolvedValue(makeAttachment(1))
    const { upload } = useUpload()
    await upload(new File(['x'], 'a.png'), { successMessage: '封面已上传' })
    expect(toast.items.value.at(-1)?.message).toBe('封面已上传')
  })

  it('successMessage: false 不弹提示（批量场景由调用方汇总）', async () => {
    vi.spyOn(attachmentApi, 'upload').mockResolvedValue(makeAttachment(1))
    const { upload } = useUpload()
    await upload(new File(['x'], 'a.png'), { successMessage: false })
    expect(toast.items.value).toHaveLength(0)
  })
})

describe('useUpload 失败路径', () => {
  it('失败返回 null 并弹兜底文案', async () => {
    vi.spyOn(attachmentApi, 'upload').mockRejectedValue(new Error(''))
    const { upload } = useUpload()
    const result = await upload(new File(['x'], 'a.png'))
    expect(result).toBeNull()
    expect(toast.items.value.at(-1)?.message).toBe('上传失败')
  })

  it('errorPrefix 带上文件名（定位是哪个文件坏了）', async () => {
    vi.spyOn(attachmentApi, 'upload').mockRejectedValue(new Error('文件超过上限 2 MB'))
    const { upload } = useUpload()
    await upload(new File(['x'], '坏图.png'), { successMessage: false, errorPrefix: '坏图.png' })
    expect(toast.items.value.at(-1)?.message).toBe('坏图.png：文件超过上限 2 MB')
  })
})

describe('useUpload 大小预检', () => {
  it('设置 maxSizeMB 时超限文件不发请求，直接提示', async () => {
    const apiSpy = vi.spyOn(attachmentApi, 'upload')
    const { upload } = useUpload({ maxSizeMB: 1 })
    const bigFile = new File([new Uint8Array(2 * 1024 * 1024)], 'big.png')
    const result = await upload(bigFile)
    expect(result).toBeNull()
    expect(apiSpy).not.toHaveBeenCalled()
    expect(toast.items.value.at(-1)?.kind).toBe('error')
  })

  it('未设置 maxSizeMB 时不做客户端预检（服务端是唯一权威上限）', async () => {
    const apiSpy = vi
      .spyOn(attachmentApi, 'upload')
      .mockResolvedValue(makeAttachment(1))
    const { upload } = useUpload()
    const bigFile = new File([new Uint8Array(2 * 1024 * 1024)], 'big.png')
    const result = await upload(bigFile)
    expect(apiSpy).toHaveBeenCalledTimes(1)
    expect(result?.id).toBe(1)
  })
})
