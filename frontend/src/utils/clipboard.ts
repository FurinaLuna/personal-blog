/**
 * 剪贴板写入（唯一实现）。
 *
 * ## 为什么要抽出来
 *
 * 这段「优先 navigator.clipboard、失败降级到隐藏 textarea」的逻辑原先内联在
 * `MarkdownRenderer.vue` 里。电梯栏的「复制微信号 / QQ 号」是**第二处**消费点，
 * 第三处（将来任何「复制分享链接」）已经在路上——复制这种东西各写一遍，
 * 迟早出现「某个入口在局域网 HTTP 下静默失败」而没人发现。
 *
 * ## 为什么必须降级
 *
 * `navigator.clipboard` 只在**安全上下文**（HTTPS / localhost）可用；
 * 用局域网 IP 打开开发服务器时它是 `undefined`。此时唯一还能用的路径是
 * `document.execCommand('copy')`：建一个不可见的 textarea、选中、执行。
 * 它是过时 API，但仍是这类环境里唯一能用的方案。
 */

/**
 * 把文本写进剪贴板。
 *
 * 返回 `true` 表示确实写进去了；`false` 表示两条路径都失败 —— **调用方负责
 * 提示用户手动复制**，工具函数不碰 toast（否则后台编辑器复用时会被迫引入 UI 依赖）。
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限被拒或页面失焦，继续走降级路径
    }
  }

  const helper = document.createElement('textarea')
  helper.value = text
  // readonly 防止移动端唤起键盘；position:fixed 避免插入后把页面顶动
  helper.setAttribute('readonly', '')
  helper.style.position = 'fixed'
  helper.style.opacity = '0'
  document.body.append(helper)
  helper.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    helper.remove()
  }
}
