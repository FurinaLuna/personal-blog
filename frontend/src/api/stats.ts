/** 统计趋势接口。 */
import { api } from './http'

import type { DailyViewStats } from '@/types'

export const statsApi = {
  /** 近 N 天 PV/UV 曲线（缺日补零，日期升序）。 */
  dailyViews(days = 30) {
    return api.get<DailyViewStats[]>('/stats/views/daily', { days })
  },
}
