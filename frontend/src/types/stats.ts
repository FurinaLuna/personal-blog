/** 统计趋势（访问量按天聚合）。 */

/** 一天的访问聚合，对应后端 DailyViewStats。 */
export interface DailyViewStats {
  /** ISO 日期（"2026-09-13"） */
  date: string
  /** PV：详情访问次数 */
  views: number
  /** UV：去重访客数（IP 摘要 distinct） */
  unique_visitors: number
}
