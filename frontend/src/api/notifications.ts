/** 评论通知接口（目前只有退订一个端点）。 */
import { api } from './http'

import type { Message } from '@/types'

export const notificationApi = {
  /**
   * 凭邮件里的签名 token 退订评论回复通知。
   *
   * 幂等：重复点链接、邮件客户端预取都不会报错。
   */
  unsubscribe(token: string) {
    return api.post<Message>('/notifications/unsubscribe', { token })
  },
}
