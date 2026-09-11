/** 站点配置与统计接口。 */
import { api } from './http'

import type { SiteProfile, SiteProfilePayload, SiteStats } from '@/types'

export const siteApi = {
  profile() {
    return api.get<SiteProfile>('/site/profile')
  },

  updateProfile(payload: SiteProfilePayload) {
    return api.patch<SiteProfile>('/site/profile', payload)
  },

  stats() {
    return api.get<SiteStats>('/site/stats')
  },
}
