# 前端优化 · 快速见效清单（1-2 天）

> 三档路线图见会话中的可视化卡片。本文档只展开第一档，每项是「改哪个文件、改什么、为什么」。

## 1. 代码块加「复制 + 语言标签」

**文件**：`frontend/src/utils/markdown.ts`（渲染流水线末尾）+ `frontend/src/style.css`

现在代码块只有高亮，没有复制按钮 —— 技术博客里这是使用频率最高的缺失功能。

在 `renderMarkdown()` 里、hljs 高亮之后，给每个 `pre` 包一层容器并插入按钮：

```ts
container.querySelectorAll('pre').forEach((block) => {
  const code = block.querySelector('code')
  const lang = (Array.from(code?.classList ?? [])
    .find((c) => c.startsWith('language-')) || '').replace('language-', '')
  const wrapper = document.createElement('div')
  wrapper.className = 'code-block'
  block.replaceWith(wrapper)
  wrapper.append(block)
  if (lang) {
    const tag = document.createElement('span')
    tag.className = 'code-lang'
    tag.textContent = lang
    wrapper.append(tag)
  }
  // 按钮的真实点击逻辑在组件层（需要 toast），这里只放标记
  wrapper.dataset.copyable = 'true'
})
```

按钮本体放在 `MarkdownRenderer.vue` 里用事件委托绑定（而不是在这里塞 DOM 事件），这样能复用 `useToast`，也避免把 UI 行为写进工具函数。

## 2. 阅读进度条

**文件**：`frontend/src/components/TableOfContents.vue` 同级新建 `ReadingProgress.vue`，挂在 `ArticleDetailView.vue`

顶部一条 2px 的进度线。用 `scroll` + `requestAnimationFrame` 节流，或者更省事：纯 CSS `animation-timeline: scroll()`（Chrome 115+ 支持，不支持的浏览器自动降级成不显示，无副作用）。

## 3. 移动端浮动目录

**文件**：`frontend/src/views/ArticleDetailView.vue`

现在目录是 `absolute -right-64 ... hidden xl:block`，**移动端完全没有目录**。长文章在手机上没法跳转章节。

做法：xl 以下渲染一个右下角悬浮按钮，点开抽屉展示同一个 `TableOfContents` 组件（复用现有 `items`，零额外数据结构）。

## 4. 主题三态（亮 / 暗 / 跟随系统）

**文件**：`stores/theme.ts`、`components/ThemeToggle.vue`

现在是二态切换。缺「跟随系统」这一档 —— 而 `index.html` 的内联脚本其实已经实现了跟随逻辑（无存储时读 `prefers-color-scheme`），只是 UI 上表达不出来。

改动：`ThemeMode` 增加 `'system'`；`mode === 'system'` 时不写 localStorage（或写 `'system'`），并监听 `matchMedia` 变化实时响应。

## 5. 顺手可做的小优化

| 项 | 位置 | 说明 |
|---|---|---|
| 中文行高 | `tailwind.config.js` 的 `typography` | 现在 `1.85` 对中文偏松，正文建议 `1.75`，标题 `1.3` |
| 中英文间距 | 正文容器 | 加 `text-autospace` 或用 pangu.js 在渲染时补空格，混排观感提升明显 |
| 首屏最大 chunk | `vite.config.ts` | markdown 包 233KB（gzip 77KB），因为 `highlight.js/lib/common` 打了约 40 种语言；改成按需注册 8-10 种可降到 ~60KB |
| 图片加载占位 | `ArticleCard.vue` | 封面图加 `aspect-ratio` 占位，避免加载完成时布局跳动 |

---

需要我直接实现其中哪几项？（建议先做 1 + 3，这两项用户感知最强）
