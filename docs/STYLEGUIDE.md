# 样式规范（Style Guide）

> 适用：`frontend/src/styles/` 与本项目所有 Vue 组件。
> 目标：**样式只有一个出处**，改一处全站生效；命名能自解释，不靠记忆。

## 一、文件组织

```
frontend/src/styles/
├── index.css        唯一入口：@import 各层 + @tailwind 三指令
├── tokens.css       设计令牌：颜色 / 字体 / 动效时长（全站唯一定义处）
├── base.css         元素级默认 + 可访问性底线 + 减少动效
├── components.css   可复用组件类（BEM 命名）
├── prose.css        文章正文里 typography 插件表达不了的部分
└── vendor.css       第三方样式覆盖（highlight.js 暗色主题）
```

**分层的判断标准**（按顺序问自己）：

| 这段样式是… | 放哪 |
|---|---|
| 颜色 / 字体 / 时长的**定义** | `tokens.css` |
| 选择器是 `html` / `body` / `::selection` 这类元素 | `base.css` |
| 可复用的类（按钮、卡片、表单…） | `components.css` |
| 只作用于 `.prose`（Markdown 正文） | `prose.css` |
| 覆盖第三方库（hljs / 未来的其他库） | `vendor.css` |
| 只在一个组件里用一次 | **就地写 Tailwind 工具类**，不要新建文件 |

> 组件级 `<style scoped>` 目前全项目为 0 处 —— 保持这个状态。
> 样式一旦分散到 30 个组件里，「按钮圆角统一」这种事就只能靠全局搜索。

### 入口顺序不能乱

```css
@import './tokens.css';    /* 1. 令牌最先，后续所有 @apply 都依赖它 */
@import './base.css';
@import './components.css';
@import './prose.css';
@import './vendor.css';    /* 2. 第三方覆盖最后 */

@tailwind base;
@tailwind components;
@tailwind utilities;
```

`@import` 按 CSS 规范必须先于其他语句；Tailwind 会把各文件里 `@layer xxx`
的内容归入对应层，与书写位置无关；**未包裹在 `@layer` 里的规则天然高于任何分层
规则**，因此像 `.code-block`、`.prose pre` 这类覆盖能稳定压过第三方与工具类。

## 二、命名约定（BEM）

```
.block              块：可独立复用的组件
.block__element     元素：块的组成部分，不能脱离块
.block--modifier    修饰符：变体
[data-state="done"] 状态：用 data-* 属性，不用 .is-done 类
```

现状与例子：

| 类名 | 说明 |
|---|---|
| `.card` / `.card--hover` | 卡片与其悬停变体 |
| `.btn` / `.btn--primary` / `.btn--ghost` / `.btn--danger` / `.btn--icon` | 按钮家族 |
| `.input` | 表单输入 |
| `.chip` / `.skeleton` | 标签胶囊 / 骨架屏 |
| `.code-block` / `.code-block__lang` / `.code-block__copy` | 代码块及其两个角标 |
| `.code-block__copy[data-state='done']` | 复制成功态 |

**为什么不用 `.btn-primary` 这种写法**：单连字符既表示"块的一部分"又表示"变体"，
读的人无法区分 `.btn-icon` 是"图标型按钮"还是"按钮里的图标"。`--` 与 `__` 是
一眼可辨的边界。（重构前正是混用状态，已统一。）

**为什么状态用 `data-*`**：属性选择器与类名解耦，`data-state` 可以承载多个取值，
且不会被误当成布局类。

## 三、选择器纪律

1. **能一层就不要两层**。BEM 类名已携带归属关系，后代选择器只会抬高特异性，
   让后续覆盖变困难。
2. **例外只有一种**：需要表达"父级 hover 影响子元素"时，
   `.code-block:hover .code-block__copy` 是必要的（子元素自己无法感知父级状态）。
   这类写法必须在旁边注释说明**为什么必须两层**。
3. **不用 `!important`**，唯一例外是覆盖第三方库的既有声明（`vendor.css` 里的
   `.hljs { background: transparent !important }`）。用一次要写一句理由。
4. **不写元素选择器**（`div > span`）：结构一改就失效。

## 四、令牌（tokens）

- 颜色变量值是 **`R G B` 三个数字**，不带 `rgb()` 包裹 —— Tailwind 的
  `<alpha-value>` 要靠它拼透明度（`bg-ink/50`）。
- 新增语义色（如 success / warning）**必须同时补亮暗两套**，否则暗色模式会漏。
- 组件里**禁止写死色值**，唯一例外是 `vendor.css`（那是上游主题的原始配色，
  套进自己的令牌反而失真）。

> 血的教训：`.code-lang` 曾引用 `var(--font-mono)`，但这个变量在全仓**从未定义**
> —— `font-family` 声明因此在计算时失效、静默回退到继承值，语言标签一直用着正文
> 字体。这类"变量缺失"不会报错，只会悄悄错。**新增 var() 引用时，
> 一定顺手确认它真的有定义。**

## 五、加样式的正确姿势

```vue
<!-- ✅ 优先：一次性样式直接用工具类，不新增 CSS -->
<div class="rounded-lg border border-border bg-surface px-3 py-2 text-sm">

<!-- ✅ 复用：用 components.css 里已有的类 -->
<button class="btn btn--primary">保存</button>

<!-- ❌ 不要：为一个按钮单独开 CSS 文件或 scoped 样式 -->
```

只有**同一套样式被 3 处以上复用**时，才把它提到 `components.css`。

## 六、重构后如何验证"视觉没变"

样式改动最容易出的事是「某个角落悄悄变了」，而截图对比在本项目不可靠
（详情页每次访问阅读量 +1、相对时间文案会变，字节比对必然失败）。
因此引入两个脚本：

```bash
# 1) 采集关键元素的计算样式（重命名无关的选择器）
node tools/style-baseline.mjs http://127.0.0.1:5173 ./before.json
# …做你的样式改动…
node tools/style-baseline.mjs http://127.0.0.1:5173 ./after.json

# 2) 比对；无差异返回 0，有差异逐条打印
node tools/style-diff.mjs ./before.json ./after.json
```

判定原则：**差异必须能被解释**。要么是本次有意修正（例如字体族 bug），
要么是脚本问题；不允许出现"说不上为什么变了"的差异。

> 覆盖范围要包含**文章详情页**——代码块角标只在那里出现。
> 曾经因为路由列表里漏了详情页，得到"7 个路由零差异"的假安全感。
