/**
 * 跨平台定位 Chrome / Edge / Chromium，并给出统一的 headless 启动参数。
 *
 * ## 为什么要有这个模块
 *
 * 三个浏览器验证脚本（smoke / interaction / full-check）此前各自抄了一份
 * 「Chrome 候选路径」常量，且**只写了 Windows 路径**。后果不是"少支持两个平台"，
 * 而是 CI 里那个 e2e job 从来不可能通过：job 跑在 `ubuntu-latest`，
 * 而脚本找的是 `C:/Program Files/Google/Chrome/Application/chrome.exe`。
 * 更隐蔽的是 smoke / full-check 用的是候选列表 + `find(existsSync)`，
 * 找不到就静默走到下一步，看起来像"环境问题"，而不是"脚本写死了平台"。
 *
 * 所以这里做两件事：
 * 1. 候选路径按平台给全（Windows / macOS / Linux + 环境变量 + PATH 探测）；
 * 2. 找不到时**把找过的路径全部打印出来**，并说明怎么用环境变量覆盖——
 *    失败信息要能直接指向结论，而不是让人去猜。
 *
 * CI（GitHub Actions ubuntu-latest）自带 `/usr/bin/google-chrome`，
 * 因此 Linux 候选与 PATH 探测任一条命中即可，不需要额外安装步骤。
 */

import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { delimiter, join } from 'node:path'

/** 环境变量覆盖：按优先级从高到低。 */
const ENV_KEYS = ['CHROME_PATH', 'CHROME_BIN', 'PUPPETEER_EXECUTABLE_PATH']

const WINDOWS_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
]

const MAC_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
]

const LINUX_CANDIDATES = [
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/snap/bin/chromium',
  '/usr/bin/microsoft-edge',
  '/usr/bin/microsoft-edge-stable',
]

/** PATH 里按名字找（Linux/macOS 常见安装方式不一定落在上面的固定路径）。 */
const PATH_NAMES = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome']

function platformCandidates() {
  const candidates = []
  if (process.platform === 'win32') {
    // LOCALAPPDATA 是"只给当前用户装"的默认位置，很多人是这种装法
    if (process.env.LOCALAPPDATA) {
      candidates.push(join(process.env.LOCALAPPDATA, 'Google/Chrome/Application/chrome.exe'))
      candidates.push(join(process.env.LOCALAPPDATA, 'Microsoft/Edge/Application/msedge.exe'))
    }
    candidates.push(...WINDOWS_CANDIDATES)
  } else if (process.platform === 'darwin') {
    candidates.push(...MAC_CANDIDATES)
  } else {
    candidates.push(...LINUX_CANDIDATES)
  }
  return candidates
}

/**
 * 在 PATH 里查一个可执行文件是否存在。
 *
 * 不用 `which`/`where` 子进程：那样每个候选都要起一个进程，且在没有该命令的
 * 精简镜像里会抛异常。直接按 PATH 拼路径判断，行为更可预测。
 */
function findInPath(name) {
  const dirs = (process.env.PATH ?? '').split(delimiter).filter(Boolean)
  const suffixes = process.platform === 'win32' ? ['.exe', '.cmd', ''] : ['']
  for (const dir of dirs) {
    for (const suffix of suffixes) {
      const full = join(dir, name + suffix)
      if (existsSync(full)) return full
    }
  }
  return null
}

/** 返回所有"找过的地方"，用于失败时输出可核对的清单。 */
export function chromeSearchPaths() {
  const fromEnv = ENV_KEYS.map((key) => [key, process.env[key]]).filter(([, value]) => Boolean(value))
  return [...fromEnv.map(([key, value]) => `${key}=${value}`), ...platformCandidates(), ...PATH_NAMES.map((n) => `PATH:${n}`)]
}

/**
 * 找到可用的浏览器可执行文件。
 *
 * @returns {string} 绝对路径
 * @throws {Error} 找不到时抛出，消息里含完整候选清单与覆盖方式
 */
export function findChrome() {
  for (const key of ENV_KEYS) {
    const value = process.env[key]
    if (value && existsSync(value)) return value
  }

  for (const candidate of platformCandidates()) {
    if (existsSync(candidate)) return candidate
  }

  for (const name of PATH_NAMES) {
    const found = findInPath(name)
    if (found) return found
  }

  const tried = chromeSearchPaths()
    .map((item) => `  - ${item}`)
    .join('\n')
  throw new Error(
    `找不到 Chrome / Edge / Chromium 可执行文件（平台：${process.platform}）。\n` +
      `已尝试以下位置：\n${tried}\n` +
      `可用环境变量指定：CHROME_PATH=/path/to/chrome（或 CHROME_BIN）。\n` +
      `Debian/Ubuntu 安装示例：apt-get install -y google-chrome-stable`,
  )
}

/**
 * 三个脚本共用的 headless 启动参数。
 *
 * - `--disable-dev-shm-usage`：容器与 CI runner 的 `/dev/shm` 常常只有 64MB，
 *   不关掉会随机崩在渲染上（表现为"用例莫名其妙挂了一条"）。
 * - `--no-sandbox`：**仅在以 root 运行时**追加。GitHub runner 是非 root，
 *   不需要它；而 Docker 里以 root 跑不加会直接启动失败。按需追加，
 *   避免无条件削弱沙箱。
 */
export function chromeArgs({ port, userDataDir, windowSize, hideScrollbars = false } = {}) {
  const args = [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-dev-shm-usage',
  ]
  if (port) args.push(`--remote-debugging-port=${port}`)
  if (userDataDir) args.push(`--user-data-dir=${userDataDir}`)
  if (windowSize) args.push(`--window-size=${windowSize}`)
  if (hideScrollbars) args.push('--hide-scrollbars')
  // getuid 在 Windows 上不存在，用可选调用判定
  if (typeof process.getuid === 'function' && process.getuid() === 0) args.push('--no-sandbox')
  args.push('about:blank')
  return args
}

/** 用 execFileSync 之外的方式探测浏览器版本，失败返回空串（不抛）。 */
export function chromeVersionText(path) {
  try {
    return execFileSync(path, ['--version'], { encoding: 'utf8' }).trim()
  } catch {
    return ''
  }
}
