# 文章详情页前端升级计划（目录改造 + 电梯栏）

> 交付对象：可直接交给开发（含 AI 开发者）执行的前端升级计划。
> 撰写日期：2026-09-27。勘察基准：远端 `main` = `4277551`，CI #59 8/8 全绿。
> 所有文件路径、组件名、类名均对照当前仓库实写，实施时以本文件为唯一规格来源；
> 若实现时发现与本文件冲突，**先停下来对质本文件，不要自行改设计**。

---

## 0. 范围与硬约束

**范围内**：文章详情页目录（TOC）改造、全局「电梯栏」悬浮组件（含后台可配置的微信/QQ 二维码）、
若干前端优化点。

**硬约束（逐条验收）**：

1. **不改动文章数据结构**：`articles` 表、`ArticleDetail` / `TocItem` 结构、
   `renderMarkdown()` 的输入输出契约（`{ html, toc: TocItem[] }`）全部保持不变。
   目录树在消费侧由平铺数组**派生**，不反向改渲染管线。
2. **组件化**：新能力必须是独立 `.vue` / composable，详情页模板只增引用、不堆逻辑。
3. **配置项集中管理**：站点级配置一律走 `siteStore.profile`（`frontend/src/stores/site.ts`），
   组件内**不允许**出现硬编码配置字面量。
4. **沿用现有样式变量与设计语言**：颜色/字体/动效/圆角只用
   `frontend/src/styles/tokens.css` 令牌（Tailwind 类如 `text-ink-soft`、`bg-surface`、
   `border-border`、`bg-brand-600`），不写死色值；组件样式遵循 BEM（`docs/STYLEGUIDE.md`），
   状态用 `data-*` 属性；动效用 `var(--duration-*)`。
5. **移动端有降级/隐藏方案**：每一项新交互都必须写明 < xl（Tailwind 默认 1280px）断点下的形态。
6. **可访问性底线**：键盘可达、`:focus-visible` 样式自动生效（`base.css` 已有全局规则）、
   读屏语义正确（`prefers-reduced-motion` 已有全局规则，新动效自动被关掉，不用自己处理）。

---

## 一、现状问题

### 1.1 目录（TOC）现状盘点

现状代码：`frontend/src/components/TableOfContents.vue`（桌面侧栏）、
`frontend/src/components/MobileToc.vue`（移动端抽屉）、`frontend/src/utils/markdown.ts`
（渲染时收集 `TocItem[]`，平铺数组，`depth` 为 1–4）、挂载点
`frontend/src/views/ArticleDetailView.vue` 第 342–350 行。

已具备的能力（**不要回退**）：IntersectionObserver 滚动高亮、锚点 `replaceState` 写回、
文章切换时 `nextTick` 重建观察器、移动端抽屉（焦点管理 + 滚动锁 + Esc）、
`xl` 断点以下隐藏侧栏。

逐项诊断：

| # | 问题 | 现状证据 | 影响 |
|---|------|----------|------|
| T1 | **层级只靠缩进表达** | `indentClass()` 仅 `pl-0/pl-4/pl-8`；h2/h3/h4 字号字重完全相同（统一 `text-[13px]`） | 长文目录「一眼望不到结构」，h4 与 h2 视觉权重一样 |
| T2 | **无折叠/收起** | 全部平铺渲染，侧栏仅靠 `max-h-[calc(100vh-8rem)] overflow-auto` 硬滚 | 50+ 标题的长文目录要滚动很久才能找到章节；移动端抽屉 `max-h-[60vh]` 内同样难用 |
| T3 | **高亮项不会自动滚入侧栏可视区** | `activeId` 变化只改样式，侧栏容器不滚动 | 滚动正文时，目录高亮会「滚出」侧栏可视区，用户看不到当前读到哪 |
| T4 | **无 `aria-current`** | active 项只有颜色/字重差异 | 读屏用户无法感知当前章节 |
| T5 | **平铺数组无父子结构** | `TocItem { id, text, depth }` 无 `children` | 折叠交互必须在消费侧按 depth 重建树（本计划第二节解决；渲染产出不改） |
| T6 | **移动端浮动按钮无「返回顶部」** | `MobileToc.vue` 只有一个目录按钮 | 长文回顶只能靠手搓滚动条 |
| T7 | **移动端抽屉与未来的电梯栏会抢占同一屏幕区域** | 抽屉按钮 `fixed bottom-6 right-5 z-40` | 新增任何右下悬浮件都必须统一排布，否则重叠 |
| T8 | **目录无「当前进度」反馈** | 仅高亮当前项，无「第 N/M 节」量化 | 次要问题，顺手在电梯栏补 |

> 说明：滚动高亮「滚过最后一个标题后停在末章」是**有意行为**（文末评论区没有标题，
> 停留在末章才符合直觉），不是 bug，改造后保持。

### 1.2 全局导航现状

已有 `frontend/src/components/ReadingProgress.vue`（顶部 2px 进度条，rAF 节流）。
**没有**返回顶部、没有联系站长入口、没有全局锚点导航 —— 即「电梯栏」要补的全部。

### 1.3 配置链路现状

`siteStore.profile`（`frontend/src/stores/site.ts`）已缓存 `GET /api/v1/site/profile`
全站共享；`SiteProfile`（`frontend/src/types/site.ts`）已有
`social_links: { label, url, icon }[]` 先例，后台 `SettingsView.vue` 已支持增删。
**二维码配置没有字段**，需按第三节方案落地。

---

## 二、目录改造方案

### 2.1 新增 composable：`frontend/src/composables/useTocTree.ts`

把「平铺 → 树 → 折叠/展开 → active 分支」的纯逻辑从组件里抽出来，组件只管渲染。

```ts
import type { TocItem } from '@/utils/markdown'

export interface TocNode {
  item: TocItem
  children: TocNode[]
}

/** 平铺 TocItem[] → 树。规则：depth 大的项挂到前面最近的更小 depth 节点下；
 *  首个节点若 depth > 2（文章直接从 h3 起），按顶层处理，防止整棵树被吞掉。 */
export function buildTocTree(items: TocItem[]): TocNode[]

export interface UseTocTreeOptions {
  items: () => TocItem[]
  /** 初始折叠策略：depth >= 3 的节点默认收起（见 2.4 决策点 D-目录-1） */
  defaultCollapsed?: (node: TocNode) => boolean
}

export interface UseTocTreeReturn {
  /** 渲染用扁平视图：按「收起状态」过滤后的可见节点，带层级缩进信息 */
  visibleNodes: ComputedRef<Array<{ node: TocNode; depth: number }>>
  isCollapsed: (id: string) => boolean
  toggle: (id: string) => void
  /** 滚动高亮变化时调用：展开 active 项的全部祖先链 */
  revealActive: (id: string) => void
}
```

实现要点（写给开发者）：

- `buildTocTree` 用栈实现，O(n)；**必须**处理「depth 跳级」（h2 直接接 h4）。
- 折叠状态用 `ref<Set<string>>`；`toggle` 返回新 Set（保持响应式）。
- `revealActive` 只做「展开」，不做「收起」——用户手动收起的分支，滚动经过时不替他重新打开
  （例外：首次高亮到达时若 active 自身被收起则必须展开，否则高亮不可见）。
- 纯函数部分（`buildTocTree`）单独导出，单测直接覆盖，不挂载组件。

### 2.2 改造 `TableOfContents.vue`

保留现有 IntersectionObserver / `jumpTo` / 观察器重建逻辑（1.1 已列，不回退），在此基础上：

**结构**：

```vue
<nav v-if="visible.length > 1" aria-label="文章目录">
  <div class="mb-3 flex items-center justify-between">
    <p class="text-xs font-medium text-ink-faint">目录</p>
    <!-- 「全部展开/收起」开关，见 2.4 决策点 D-目录-2 -->
    <button type="button" class="text-xs text-ink-faint hover:text-ink" @click="toggleAll">
      {{ allCollapsed ? '全部展开' : '全部收起' }}
    </button>
  </div>
  <ul class="border-l border-border">
    <li v-for="entry in tree.visibleNodes" :key="entry.node.item.id">
      <div class="flex items-stretch">
        <a
          :href="`#${encodeURIComponent(entry.node.item.id)}`"
          :aria-current="activeId === entry.node.item.id ? 'location' : undefined"
          class="-ml-px block w-full border-l-2 py-1 pr-2 text-left leading-snug transition-colors"
          :class="[linkClass(entry.node), indentClass(entry.node.item.depth), activeClass(entry.node)]"
          @click="jumpTo($event, entry.node.item.id)"
        >
          {{ entry.node.item.text }}
        </a>
        <button
          v-if="entry.node.children.length"
          type="button"
          :aria-expanded="!tree.isCollapsed(entry.node.item.id)"
          :aria-label="(tree.isCollapsed(entry.node.item.id) ? '展开' : '收起') + entry.node.item.text"
          class="shrink-0 px-1 text-ink-faint hover:text-ink"
          @click="tree.toggle(entry.node.item.id)"
        >
          <svg class="h-3 w-3 transition-transform" :class="{ 'rotate-90': !tree.isCollapsed(...) }">…chevron-right…</svg>
        </button>
      </div>
    </li>
  </ul>
</nav>
```

**视觉规范（必须严格遵守）**：

| 层级 | 字号/字重 | 颜色（非 active） | 缩进 |
|------|-----------|--------------------|------|
| h2 | `text-[13px] font-medium` | `text-ink-soft` | `pl-0` |
| h3 | `text-[13px]` | `text-ink-soft` | `pl-4` |
| h4 | `text-xs` | `text-ink-faint` | `pl-8` |

- active 项：`border-brand-500 font-medium text-brand-600`（沿用现状），**新增**
  `aria-current="location"`（现状问题 T4）。
- 折叠箭头：16px 可点区，chevron 右向、展开时顺时针转 90°，过渡用
  `transition-transform var(--duration-fast)`。
- 桌面侧栏容器保持 `sticky top-24 max-h-[calc(100vh-8rem)] overflow-auto`
  （`ArticleDetailView.vue` 第 343 行，不动）。

**行为增补**：

1. **T3 高亮自动滚入可视区**：`watch(activeId)` → 取 active 链接元素
   `element.scrollIntoView({ block: 'nearest' })`。`block: 'nearest'` 是关键：
   用 `'start'` 会把整个页面带着跳。仅当侧栏自身可滚动（`scrollHeight > clientHeight`）时执行。
2. **文章切换**：沿用 `watch(props.items)` 重建观察器；**同时**重置折叠状态为默认策略
   （上一篇的折叠记忆不带到下一篇；跨篇记忆是 2.4 决策点 D-目录-3，默认不做）。
3. `visible.length > 1` 的显示门槛保持不变（单标题文章不出目录）。

### 2.3 `MobileToc.vue` 微调

- 抽屉内容区直接复用改造后的 `TableOfContents`（现有结构已复用，保持），
  自动获得折叠能力。
- 浮动按钮的排布**移交给电梯栏统一计算**（见 3.4），本组件只保留 `bottom-offset` prop：

```ts
defineProps<{ items: TocItem[]; bottomOffset?: number }>() // 单位 px，默认 0
```

模板按钮改为 `:style="{ bottom: `${bottomOffset + 24}px` }"`（基准 bottom-6 = 24px）。

### 2.4 目录改造决策点（默认取值已按推荐写明）

| 编号 | 决策点 | 默认（推荐） | 备选 |
|------|--------|--------------|------|
| D-目录-1 | 初始折叠策略 | depth ≥ 3 的节点默认收起 | 全部展开 / 仅 h4 收起 |
| D-目录-2 | 「全部展开/收起」开关 | 提供，状态不记忆 | 不提供 / localStorage 记忆 |
| D-目录-3 | 折叠状态跨文章记忆 | 不记忆（切文章重置） | localStorage 按 slug 记忆 |
| D-目录-4 | 桌面侧栏宽度 | 维持 `w-56` 不动 | 2xl 以上 `w-64` |

---

## 三、电梯栏方案（ElevatorBar）

### 3.1 组件构成与文件清单

| 文件 | 职责 |
|------|------|
| `frontend/src/components/ElevatorBar.vue` | 壳：定位、断点显隐、滚动监听、按钮列渲染 |
| `frontend/src/components/ContactQrcode.vue` | 微信/QQ 二维码弹层（hover/focus 展开 + 触屏点击展开） |
| `frontend/src/composables/useScrollY.ts` | 共享滚动位置（rAF 节流），ReadingProgress 后续可复用 |

挂载点：`frontend/src/layouts/DefaultLayout.vue`（**全局公开页**，AdminLayout 不加）。
这是默认决策 **D-电梯-1**，备选仅文章详情页。

### 3.2 按钮构成（自上而下）

| 按钮 | 桌面（≥xl） | 移动端（<xl） | 显示条件 |
|------|-------------|---------------|----------|
| 目录 | 不显示（侧栏已常驻） | 不渲染（避免与 MobileToc 重复） | — |
| 联系站长（二维码） | 显示 | 显示 | `contact_qrcodes` 配置非空 |
| 返回顶部 | 显示 | 显示 | `scrollY > 300` |
| 分享（复制链接） | 显示 | 不显示（浏览器自带） | 见 3.6 决策点 D-电梯-4，默认**不做** |

> 「目录」不进电梯栏：移动端已有 MobileToc 按钮，重复入口只会互相打架。
> 电梯栏与 MobileToc 的排布关系见 3.4。

### 3.3 视觉规范

- 容器：`fixed right-5 z-40 flex flex-col gap-2`（按钮 40×40，`rounded-full`、
  `bg-surface border border-border shadow-lg text-ink-soft hover:text-brand-600`），
  图标 20px，`btn--icon` 同款 44px 命中区（`components.css` 已有伪元素方案，直接复用类）。
- 垂直位置：桌面 `bottom-28`；移动端由 3.4 的避让计算决定。
- 出现/消失：返回顶部按钮用 `<Transition>` 淡入淡出（duration-fast），**不要**位移动画
  （位移会诱导误点）。
- 暗色模式：全部使用令牌类，自动适配，无需 dark: 变体（tokens.css 双套变量）。

### 3.4 移动端避让排布（解决现状问题 T7）

移动端右下角的悬浮件有且只有两个：MobileToc 目录按钮、电梯栏（联系 + 回顶）。
统一规则：

- MobileToc 按钮：保持 `bottom-6 right-5`（24px / 20px）不动，尺寸 48px 不动。
- 电梯栏容器移动端：`right-5`，**bottom 动态避让** = 24（底边距）+ 48（目录按钮）+ 12（间距）
  = `bottom-24`（96px），即电梯栏整体浮在目录按钮正上方。
- 目录按钮让位场景：当电梯栏的「联系站长」弹层打开时，目录按钮**不需要**让位
  （弹层向左展开，见 3.5）；但弹层打开期间 `z-index` 必须高于目录按钮（弹层 z-50 > 目录按钮 z-40）。
- 实现：电梯栏读不到 MobileToc 的状态，**不跨组件通信**；位置用纯 CSS 常量约定
  （上述 96px 写进 ElevatorBar 的移动端样式并注释来源）。MobileToc 的
  `bottomOffset` prop 预留给「将来若有第三个悬浮件」的扩展，本期保持默认 0。

### 3.5 二维码交互（ContactQrcode.vue）

配置数据：`siteStore.profile.contact_qrcodes`（结构见 3.7）。配置为空 → 整个按钮不渲染。

- **触发**：一个「联系站长」图标按钮（chat 图标），`aria-haspopup="true"`、
  `:aria-expanded="open"`。
- **展开形态**：弹层卡片（`card` 类 + `p-4`），出现在按钮**左侧**
  （`absolute right-full mr-3`，避免在右边缘被裁切），宽度 `w-60`。
- **桌面触发方式**：`group-hover:` + `:focus-within` 展开；点击图标可「钉住」
  （click  toggles pinned 状态，移出 hover 后仍保持）。
- **触屏触发方式**：`@media (hover: none)` 下仅点击展开/收起（hover 不存在），
  展开期间点弹层外部或 Esc 关闭，关闭后焦点回触发按钮（照抄 MobileToc 的
  `close()` 焦点归还模式）。
- **弹层内容**：每项配置渲染一张联系卡：
  - 图标（微信/QQ 按 `kind` 映射的内联 SVG，**不接外部图标库**，保持零依赖）、
    `label`（如「微信」）、二维码图（`image_url` 非空时 `<img :src>` + `alt="${label}二维码"`）、
  - `value`（微信号/QQ号）文本 + 「复制」小按钮（复用 `MarkdownRenderer.vue` 的
    clipboard 降级方案，抽成 `frontend/src/utils/clipboard.ts` 共享，**复制逻辑不要第三次复制粘贴**）。
  - `image_url` 为空但 `value` 非空 → 只显示文本 + 复制；两者皆空 → 该项不渲染。
- 弹层最大高度 `max-h-80 overflow-auto`，多项配置可滚。

### 3.6 电梯栏决策点

| 编号 | 决策点 | 默认（推荐） | 备选 |
|------|--------|--------------|------|
| D-电梯-1 | 挂载范围 | 全局公开页（DefaultLayout） | 仅文章详情页 |
| D-电梯-2 | 返回顶部阈值 | 300px | 500px / 出现「返回顶部」文案 |
| D-电梯-3 | 移动端回顶形态 | 并入电梯栏竖排（3.4 避让排布） | 独立第四颗 FAB |
| D-电梯-4 | 分享/复制链接入口 | 本期不做 | 做（电梯栏加一个链接图标） |
| D-电梯-5 | 二维码弹层最大项数 | 2（微信 + QQ） | 后台表单不限制，前端自然堆叠 |

### 3.7 配置项与数据结构

**推荐方案 B（后端加一个 JSON 列，与 `social_links` 完全同构，成本一条迁移）：**

前端 `frontend/src/types/site.ts` 追加：

```ts
export type ContactQrcodeKind = 'wechat' | 'qq'

export interface ContactQrcode {
  /** 决定图标与默认文案 */
  kind: ContactQrcodeKind
  /** 展示名，缺省按 kind 显示「微信」/「QQ」 */
  label: string
  /** 二维码图片地址；与 value 至少填一个 */
  image_url: string | null
  /** 微信号/QQ号，展示并提供一键复制 */
  value: string | null
}

export interface SiteProfile {
  // …现有字段全部保留…
  contact_qrcodes: ContactQrcode[] | null
}
```

后端（照抄 `social_links` 的双模型先例，`backend/src/app/schemas/site.py`）：

```python
class ContactQrcode(BaseModel):
    """读模型：不加校验器（同 SocialLink 的理由，旧数据必须能读出来）。"""
    kind: Literal["wechat", "qq"]
    label: str = Field(default="", max_length=20)
    image_url: str | None = Field(default=None, max_length=500)
    value: str | None = Field(default=None, max_length=50)

class ContactQrcodeInput(ContactQrcode):
    """写模型：image_url 复用 _normalize_site_url 规则（拦 javascript:/data://、//host）。"""
    @field_validator("image_url")
    @classmethod
    def _clean_image_url(cls, value: str | None) -> str | None:
        return _normalize_site_url(value, field="二维码图片地址")
```

- 模型：`backend/src/app/models/site.py` 的 `SiteProfile` 加
  `contact_qrcodes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)`，
  alembic 手写一条 `add_column(server_default=None)` 迁移（单行表，无回填成本）。
- `SiteProfileRead` / `SiteProfileUpdate` 各加一行字段；`site_service.py` 的
  可更新字段白名单加 `"contact_qrcodes"`。
- 后台：`frontend/src/views/admin/SettingsView.vue` 新增「联系二维码」区块
  （kind 下拉 + label 输入 + image_url 输入 + value 输入，上限 2 条，
  交互照抄现有 social_links 的增删行）。
- seed：演示档案补一条示例（`backend/src/app/services/seed.py` 的
  `default_site_profile`），值全留空即可——二维码不该编造假数据。

**备选方案 A（纯前端，零后端改动）**：扩展 `social_links` 的 `icon` 白名单识别
`wechat` / `qq`，约定 `url` 存二维码图片地址。代价：`value`（微信号复制）无字段可放、
社交链接与联系方式语义混杂、后台表单需要前端特判。**仅当本期明确不动后端时采用**。

---

## 四、其他优化建议（按优先级）

成本单位为人日（含测试）。P0 随本期目录改造顺手做；P1 建议本期做；P2+ 独立排期。

| 编号 | 优先级 | 事项 | 成本 | 说明 |
|------|--------|------|------|------|
| O1 | P0 | 目录 `aria-current` + 高亮自动滚入可视区 | 已含在 §2 | 即现状问题 T3/T4 |
| O2 | P1 | 评论区分批挂载 | 0.5 | `CommentSection` 用 IntersectionObserver 在进入视口前不挂载，减少首屏请求竞争；`initial-count` 已由详情响应携带，无额外请求 |
| O3 | P1 | 正文图片防 CLS | 0.5 | `utils/markdown.ts` 第 4 步后给 `img` 补 `width/height`（后端附件响应已知尺寸时）；响应式 `srcset` 涉及渲染管线识别站内附件，成本升至 1.5，单列待确认 **D-图片** |
| O4 | P2 | 暗色模式走查 | 0.25 | 重点：二维码弹层、目录 h4 的 `ink-faint` 对比度、电梯栏阴影 |
| O5 | P2 | 滚动监听合并 | 0.5 | `ReadingProgress` 与新 `useScrollY` 合并为单一 rAF 管道，消除双监听器 |
| O6 | P3 | 系列进度「第 N / M 篇」 | 0.5 | 系列导航条（`ArticleDetailView.vue` 第 302–333 行）加进度文案，需后端在系列详情里带序号或前端按 `series_order` 计算（**D-系列**：涉及后端则升 P2） |
| O7 | P3 | 相关文章骨架屏 | 0.25 | 相关文章加载期间该区域空白，补 2 个骨架卡片 |

---

## 五、实施步骤与验收标准

### Phase 1：目录改造（纯前端，可独立发布）

1. 新建 `composables/useTocTree.ts`（`buildTocTree` 纯函数导出）。
2. 新建 `composables/useTocTree.spec.ts`：树构建（含 depth 跳级、首个节点 depth>2）、
   默认折叠、toggle、revealActive 只展开不收起。
3. 改造 `TableOfContents.vue`（§2.2），新建/更新同名 spec：
   - 折叠按钮的 `aria-expanded` 与 DOM 节点显隐；
   - active 变化时容器 `scrollIntoView({ block: 'nearest' })` 被调用；
   - `aria-current="location"` 落在当前项；
   - 回归锚点：`jumpTo` 的 `replaceState` 行为不变（现有用例守着）。
4. `MobileToc.vue` 加 `bottomOffset` prop（默认 0，本期不改调用方）。
5. 自验：`npx vitest run`、`npx vue-tsc --noEmit`、`npx eslint .` 零输出。

**Phase 1 验收**：
- 单测新增 ≥ 12 例且全绿；既有 TOC/MobileToc 用例零修改通过（修改了就要在 PR 里说明理由）。
- 真实浏览器走查（开发服务器 5173）：长文目录默认收起 depth≥3；点击章节跳转 + 高亮；
  快速滚动到底部高亮停在末章；切文章（下一篇 → 上一篇）高亮与折叠重置、观察器正常工作；
  移动端抽屉目录可折叠；键盘 Tab 能进出折叠按钮、Enter 可切换。

### Phase 2：电梯栏（前端 + 后端配置）

1. 后端：`contact_qrcodes` 字段（模型/迁移/schema/服务白名单/seed），后端 pytest
   新增写模型校验用例（协议、kind 白名单、长度上限），对齐 `social_links` 测试风格。
2. 前端：`utils/clipboard.ts`（从 MarkdownRenderer 抽出复制逻辑，MarkdownRenderer 改引用）、
   `useScrollY.ts`、`ContactQrcode.vue`、`ElevatorBar.vue`、`DefaultLayout.vue` 挂载、
   `types/site.ts`、`SettingsView.vue` 配置区块。
3. 自验：前端三件套（vitest/vue-tsc/eslint）；后端 pytest；`tools/e2e_live/e2e_run.py`
   两种方言各跑一遍（动了 site profile 契约）。
4. 同步 `tools/full-check.mjs` / `smoke-check.mjs`：新增电梯栏存在性断言
   （桌面返回顶部按钮滚动后出现、二维码配置为空时不渲染联系按钮），并在
   `docs/TEST-REPORT.md` 登记。

**Phase 2 验收**：
- 配置为空 → 联系按钮不渲染、返回顶部仍可用（DOM 断言）。
- 配置齐全 → 桌面 hover 展开弹层含二维码图与复制按钮；触屏点击展开、Esc/点外关闭、焦点归还。
- 移动端电梯栏与 MobileToc 按钮不重叠（截图比对：两按钮间距 ≥ 12px）。
- `backend/.env.example` 无需变动（无环境变量）；`README.md`「当前基线」表与
  `CHANGELOG.md` 按新增用例数更新。
- CI 全绿（含 e2e_live 双方言、真实浏览器三项）。

### Phase 3：其他优化（按需立项）

按 §四 优先级独立拆分，每个 O 编号一个小 PR，验收参照 Phase 1/2 的
「单测 + 三件套零输出 + 走查清单」模式。

### 全阶段通用红线

- 不得修改 `renderMarkdown()` 的签名与 `TocItem` 结构（O3 若做 srcset 也只加属性、不改签名）。
- 不得新增运行时依赖（图标一律内联 SVG，动画一律 CSS）。
- `docs/devlog/YYYY-MM-DD.md` 记录当批改动与验证数字（仓库硬约定）。

---

## 六、待确认项清单（开工前请逐项拍板）

| # | 待确认项 | 默认值（开工即按此，有异议先停下） |
|---|----------|-------------------------------------|
| Q1 | 二维码配置方案：A（纯前端复用 social_links）/ B（后端新增 contact_qrcodes 列） | **B** |
| Q2 | 电梯栏挂载范围：全局公开页 / 仅文章详情页 | 全局公开页 |
| Q3 | 二维码展示形态：hover 展开（桌面）+ 点击弹层（触屏）双轨 / 统一点击弹层 | 双轨（§3.5） |
| Q4 | 「分享/复制链接」是否进电梯栏 | 不进（D-电梯-4 默认不做） |
| Q5 | 目录默认折叠策略（D-目录-1） | depth ≥ 3 收起 |
| Q6 | 折叠状态是否需要 localStorage 记忆（D-目录-2/3） | 不记忆 |
| Q7 | 返回顶部出现阈值（D-电梯-2） | 300px |
| Q8 | 移动端电梯栏形态（D-电梯-3） | 并入电梯栏竖排、目录按钮正上方 96px |
| Q9 | 正文图片是否做响应式 srcset（O3 加强版，成本 1.5 人日） | 仅补 width/height 防 CLS |
| Q10 | 系列进度「第 N/M 篇」是否本期做（O6） | 不做，P3 独立排期 |
| Q11 | 后台联系二维码条目上限（D-电梯-5） | 2 条 |
| Q12 | 电梯栏在管理后台（AdminLayout）是否出现 | 不出现 |
