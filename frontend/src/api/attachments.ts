/** 附件（媒体库）接口。 */
import { api, http } from './http'

import type { Attachment, BackfillResult, Page } from '@/types'

/**
 * 上传的超时时间。
 *
 * 不能用全局的 20s：那是给 JSON 接口定的。一张 5MB 的封面图在 2Mbps 的上行
 * 需要约 20 秒，慢一点的网络必然超时——而超时后浏览器已经发出去的数据白费，
 * 用户只能重来。服务端有 `MAX_UPLOAD_SIZE` 兜底，这里给足时间更合理。
 */
const UPLOAD_TIMEOUT_MS = 120_000

export const attachmentApi = {
  /**
   * 上传文件。
   *
   * 用 multipart 直接发 FormData，不手动设置 Content-Type ——
   * 手写会丢掉浏览器自动生成的 `boundary`，后端会解析失败。
   */
  async upload(file: File, onProgress?: (percent: number) => void): Promise<Attachment> {
    const form = new FormData()
    form.append('file', file)
    const { data } = await http.post<Attachment>('/attachments/upload', form, {
      timeout: UPLOAD_TIMEOUT_MS,
      onUploadProgress: (event) => {
        if (!onProgress || !event.total) return
        onProgress(Math.round((event.loaded / event.total) * 100))
      },
    })
    return data
  },

  list(page = 1, pageSize = 24, kind?: 'image' | 'file') {
    return api.get<Page<Attachment>>('/attachments', {
      page,
      page_size: pageSize,
      kind,
    })
  },

  remove(id: number) {
    return api.delete(`/attachments/${id}`)
  },

  /**
   * 为存量图片补生成多尺寸变体（仅站长）。
   *
   * 幂等运维接口：只处理还没有变体的图片记录，可反复调用直到
   * 返回的 processed 为 0。后端单轮默认处理 100 张。
   */
  backfillVariants() {
    return api.post<BackfillResult>('/attachments/backfill-variants')
  },
}
