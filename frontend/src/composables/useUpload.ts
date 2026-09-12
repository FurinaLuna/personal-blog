/**
 * 单文件上传的通用封装。
 *
 * 封面图、正文图片、媒体库逐个上传，此前是三份各自手写的
 * 「大小校验 + attachmentApi.upload + toast + 计数」。
 * 收敛后错误口径只有一处；成功提示由调用方注入（各场景文案不同）。
 *
 * 刻意不做的事：不管理 `<input type="file">` 元素——各场景的 accept /
 * multiple / 重置时机不同，DOM 细节留给视图；这里只管「文件 → 上传 → 提示」。
 * 批量上传（部分失败语义）由调用方循环调用并在外层汇总，见 MediaView。
 */
import { computed, ref, type ComputedRef } from 'vue'

import { attachmentApi } from '@/api'
import { toErrorMessage } from '@/composables/useAsyncData'
import { useToast } from '@/composables/useToast'
import type { Attachment } from '@/types'
import { formatBytes } from '@/utils/format'

export interface UploadCallOptions {
  /**
   * 成功提示文案；不传用默认「上传成功」；传 false 完全不提示
   * （批量场景由调用方汇总「成功 N 个」）。
   */
  successMessage?: string | false
  /** 失败兜底文案 */
  errorMessage?: string
  /** 失败提示前缀（批量场景带上文件名，定位「哪个文件坏了」） */
  errorPrefix?: string
}

export interface Upload {
  /** 是否有上传在进行（并发计数，喂给按钮禁用态） */
  uploading: ComputedRef<boolean>
  /** 上传单个文件；失败返回 null（已提示），成功返回附件对象 */
  upload: (file: File, options?: UploadCallOptions) => Promise<Attachment | null>
}

export function useUpload(options: { maxSizeMB?: number } = {}): Upload {
  const toast = useToast()
  const pending = ref(0)
  const uploading = computed(() => pending.value > 0)
  const maxSizeBytes = options.maxSizeMB ? options.maxSizeMB * 1024 * 1024 : null

  async function upload(
    file: File,
    callOptions: UploadCallOptions = {},
  ): Promise<Attachment | null> {
    // 客户端预检是可选的：封面图这类「明确该多大」的场景提前拦，
    // 通用场景不做（服务端 MAX_UPLOAD_SIZE 才是唯一权威上限）
    if (maxSizeBytes !== null && file.size > maxSizeBytes) {
      toast.error(
        `图片过大（${formatBytes(file.size)}），请压缩到 ${options.maxSizeMB}MB 以内`,
      )
      return null
    }

    pending.value += 1
    try {
      const result = await attachmentApi.upload(file)
      if (callOptions.successMessage !== false) {
        toast.success(callOptions.successMessage ?? '上传成功')
      }
      return result
    } catch (error) {
      const prefix = callOptions.errorPrefix ? `${callOptions.errorPrefix}：` : ''
      toast.error(prefix + toErrorMessage(error, callOptions.errorMessage ?? '上传失败'))
      return null
    } finally {
      pending.value -= 1
    }
  }

  return { uploading, upload }
}
