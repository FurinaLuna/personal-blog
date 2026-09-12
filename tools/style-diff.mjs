/**
 * 样式基线比对：找出重构前后计算样式的差异。
 *
 * 用法：node tools/style-diff.mjs <before.json> <after.json>
 * 退出码：0 = 无差异；1 = 有差异（差异会逐条打印，便于判断是否可接受）
 *
 * 判定原则：**差异必须能被解释**。要么是本次重构有意修正的问题（例如
 * 语言标签字体族从继承值改为等宽字体），要么是脚本问题；不允许出现
 * 「说不上为什么变了」的差异。
 */
import { readFileSync } from 'node:fs'

const [beforePath, afterPath] = process.argv.slice(2)
if (!beforePath || !afterPath) {
  console.error('用法: node tools/style-diff.mjs <before.json> <after.json>')
  process.exit(2)
}

const before = JSON.parse(readFileSync(beforePath, 'utf-8'))
const after = JSON.parse(readFileSync(afterPath, 'utf-8'))

const IGNORED_PROPS = new Set(['width', 'height']) // 受文本内容长度影响，非样式问题
const diffs = []

for (const route of Object.keys(before.routes)) {
  const b = before.routes[route] ?? {}
  const a = after.routes[route] ?? {}
  const selectors = new Set([...Object.keys(b), ...Object.keys(a)])

  for (const sel of selectors) {
    const bList = b[sel] ?? []
    const aList = a[sel] ?? []

    if (bList.length !== aList.length) {
      diffs.push(`${route} ${sel}: 元素数量 ${bList.length} → ${aList.length}`)
      continue
    }

    bList.forEach((bItem, index) => {
      const aItem = aList[index]
      for (const prop of Object.keys(bItem.styles)) {
        if (IGNORED_PROPS.has(prop)) continue
        const from = bItem.styles[prop]
        const to = aItem.styles[prop]
        if (from !== to) {
          diffs.push(`${route} ${sel}[${index}] ${prop}: ${from}  →  ${to}`)
        }
      }
    })
  }
}

if (diffs.length === 0) {
  console.log(`✅ 样式无差异（比对 ${Object.keys(before.routes).length} 个路由）`)
  process.exit(0)
}

console.log(`⚠️  发现 ${diffs.length} 处差异：`)
for (const line of diffs) console.log('  -', line)
process.exit(1)
