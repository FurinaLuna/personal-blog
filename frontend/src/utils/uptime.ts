/**
 * 站点运行时长（「自从搭建已经历 N 天 HH 小时 MM 分 SS 秒」）。
 *
 * ## 它对应旧站页脚的哪一行
 *
 * 旧站页脚加载 `/js/timeDate.js`，写两个 span：`#timeDate` 与 `#times`。
 * 读那个脚本才发现它**不是时钟**（我一开始也以为是当前时间）：
 * 起点写死在脚本里 —— `new Date("11/14/2025 13:14:52")`，
 * 文案是「FurinaLuna-Laboratory 自从搭建已经历 353 天 02 小时 15 分 04 秒」。
 * 所以这里按**真实语义**迁移：一个建站计时，不是 `HH:MM:SS` 的当前时间。
 */

/** 建站时刻：旧站脚本里的起点（`11/14/2025 13:14:52`，Asia/Shanghai = UTC+8）。 */
export const SITE_EPOCH_MS = Date.parse('2025-11-14T13:14:52+08:00')

const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

export interface Uptime {
  /** 完整天数（小时数不折进这里之外，天数已是「总天数」）。 */
  days: number
  hours: number
  minutes: number
  seconds: number
}

/** 两位补零（旧站也是这个格式：`02 小时 15 分 04 秒`）。 */
function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/**
 * 把「已运行毫秒数」拆成天/时/分/秒。
 *
 * 负数（时钟回拨，或起点被改到未来）一律当 0：显示「-1 天」比显示 0 更糟。
 * 减法顺序固定为 天 → 时 → 分 → 秒，最后 `Math.floor` 而不是 `Math.round` ——
 * 旧站那版用 `Math.round` 算秒，会跳到 60（`Math.round(59.6) === 60`），
 * 于是偶尔显示「60 秒」。这是旧站脚本的真实缺陷，这里不继承。
 */
export function splitUptime(elapsedMs: number): Uptime {
  const total = Math.max(0, elapsedMs)
  const days = Math.floor(total / DAY)
  const hours = Math.floor((total % DAY) / HOUR)
  const minutes = Math.floor((total % HOUR) / MINUTE)
  const seconds = Math.floor((total % MINUTE) / SECOND)
  return { days, hours, minutes, seconds }
}

/** 按旧站文案渲染，例：`353 天 02 小时 15 分 04 秒`。 */
export function formatUptime(uptime: Uptime): string {
  return `${uptime.days} 天 ${pad(uptime.hours)} 小时 ${pad(uptime.minutes)} 分 ${pad(uptime.seconds)} 秒`
}
