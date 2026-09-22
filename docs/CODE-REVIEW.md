# 代码评审：臃肿、冗余、重复与内聚耦合

> ⚠️ **历史快照（2026-09-12）**：本文记录的是当时的实测结论，**不是当前状态**。
> 项目在那之后已多次迭代（测试基线、依赖版本、部署配置都变了），
> 保留原文是为了留下决策轨迹；需要当前数据请看根目录
> [`README.md`](../README.md) 的「测试与验证」与
> [`docs/ROADMAP.md`](ROADMAP.md) 第 0 节的滚动快照。

> 评审时间：2026-09-12 · 基线 `ddeadd7`
> 方法：分层越界扫描（api/service/repository 交叉引用）、重复实现逐处比对、
> 死代码探测、大文件结构剖析。所有结论都带 `文件:行号`。

## 0. 总体结论

**先说好的部分**（这三项是实测的，不是恭维）：

| 检查 | 结果 |
|---|---|
| API 层是否越界直接操作数据（`select()` / `session.execute` / 直接实例化 Repository） | **0 处违规** |
| service 层是否依赖 Web 框架（`fastapi` / `HTTPException`） | **0 处** |
| repository 层是否夹带业务规则（抛领域异常） | **0 处** |

分层纪律守得住，这在一人开发的项目里并不常见。问题**不在架构，而在细节的重复与职责渗透**：

- **1 处明确退化**（拷贝后失修，已丢失保护逻辑）
- **3 类系统性重复**（slug 生成 ×3、分页空态 ×4、状态文案/配色 ×6）
- **2 个超载模块**（`ArticleService` 跨域、`ArticleEditView` 一条 file 承载四件事）
- **1 处跨端耦合**（后端硬编码前端路由形状）
- **1 处迁移未完成**（`CommentSection` 没跟上 `useAction` 改造）
- **1 处死代码**（`markdownToText` 无引用）

---

## 1. 明确缺陷：拷贝之后失修（优先级最高）

### 1.1 三处实现「唯一 slug」，其中一处已丢失防护

| 位置 | 能力 |
|---|---|
| `services/taxonomy_service.py:174` `_unique_slug()` | 通用版：接受 `exists` 回调、支持 `exclude_id`、有 200 次上限 + `ConflictError` |
| `services/article_service.py:346` `_resolve_slug()` | 拷贝版：逻辑等价，硬编码 `self.articles.slug_exists` |
| `services/article_service.py:381` `_resolve_tag_slug()` | **退化版**：没有 `exclude_id`、**没有 200 次上限保护**（通用版里那句 `# pragma: no cover - 防御性上限` 在这里消失了） |

**成因**：`TaxonomyService._unique_slug` 是后来泛化的产物，但文章侧的两份拷贝没跟着收敛。典型的「复制粘贴后各自演化」。

**影响**：行为不一致。分类/文章重名到第 200 次会抛业务异常，标签不会——它会一直循环下去。虽然现实中很难触发，但**这类分歧会持续扩大**，下次有人改 slug 规则（比如换分隔符）必然漏改一处。

**重构建议**

```python
# app/utils/text.py 或新增 app/db/slugs.py
async def unique_slug(
    raw: str,
    *,
    exists: Callable[[str], Awaitable[bool]],   # 只传候选值，exclude 用闭包或 partial 绑定
    prefix: str,
    max_suffix: int = 200,
) -> str:
    ...
```

三处改为调用同一个函数；`exists` 用 `functools.partial(self.articles.slug_exists, exclude_id=exclude_id)` 绑定，这样 `exclude_id` 支持自动统一。
**顺手补一条测试**：三种实体（文章/分类/标签）重名时的后缀递增行为一致。

**成本**：低（纯搬运 + 1 个新测试） · **风险**：低（有 171 个测试兜底） · **收益**：消除唯一一处**已知退化**

---

## 2. 系统性重复

### 2.1 分页空态字面量重复 4 处

```ts
// HomeView.vue / ArticleListView.vue / CommentListView.vue / MediaView.vue
const emptyPage: Page<X> = { items: [], total: 0, page: 1, page_size: PAGE_SIZE, pages: 0 }
```

**成因**：`useAsyncData` 需要初始值，各视图就地手写。
**建议**：在 `composables/useAsyncData.ts` 旁加一个工厂 `emptyPage<T>(pageSize: number): Page<T>`，四处替换。
**成本**：低 · **风险**：极低 · **收益**：改分页结构（如将来加游标）只改一处

### 2.2 文章状态「文案 + 配色」散在 6 个文件

| 位置 | 内容 |
|---|---|
| `utils/format.ts` | `formatStatus()` 文案 |
| `views/admin/ArticleListView.vue:83` | `STATUS_CLASS` 配色映射 |
| `views/admin/ArticleEditView.vue` | 状态切换相关文案（5 处） |
| `components/ArticleCard.vue` | 内联三元判断徽标 |
| `views/ArticleDetailView.vue` | 内联状态展示 |
| `types/index.ts` | 状态枚举 |

**成因**：文案与配色分属"工具函数"和"视图样式"两个直觉，于是各自落地。
**建议**：抽 `composables/useStatusMeta.ts` 或在 `utils/format.ts` 里导出唯一的
`ARTICLE_STATUS: Record<ArticleStatus, { label: string; className: string }>`，
所有视图只吃这份表。**这是典型的"同一业务概念的多处表达"，改一次要搜六个文件。**
**成本**：低 · **风险**：低（跑 `make smoke` 能立刻看出徽标错色）· **收益**：一致性与可发现性

### 2.3 表单字段「label + input + 错误提示」三件套重复

- `views/admin/UsersView.vue`：`fieldErrors.` 出现 **8 次**
- `views/admin/ArticleEditView.vue`：**7 次**

**建议**：抽 `components/FormField.vue`（props: `label` / `error` / `hint`，默认插槽放 input）。
**成本**：低 · **风险**：低 · **收益**：无障碍属性（`for`/`id` 关联、`aria-invalid`）能一次性统一 —— 现在这两处都没有 `aria-invalid`，抽组件时正好补上

### 2.4 跨端重复：slug 形状写在两处

`services/feed_service.py:33-34` 硬编码 `/article/{slug}`，与前端 `router/index.ts` 的路由表**互为影子**（那里的注释也承认了："改路由时必须同步这里"）。

**建议**：升级为配置项 `SITE_ARTICLE_PATH=/article/{slug}`，或由部署时注入；至少要在 CI 里加一条断言（读前端路由表 → 与后端模板比对），把"人记得同步"变成"机器检查"。
**成本**：低 · **风险**：低 · **收益**：消灭一个会静默 404 的耦合点

---

## 3. 职责超载与内聚问题

### 3.1 `services/article_service.py`：412 行 / 22 方法，混了 4 类知识

按行号分布（`grep` 实测的四个分区）：

| 分区 | 行号 | 内容 | 应归属 |
|---|---|---|---|
| 查询 | 69–220 | 列表 / 分页 / 详情 / 相邻篇 | 文章域 ✅ |
| 写入 | 221–307 | 创建 / 更新 / 删除 / 点赞 | 文章域 ✅ |
| 归档统计 | 308–343 | `archive` / `related` / `list_by_month` | 文章域（勉强）✅ |
| **内部工具** | **344–412** | `_resolve_slug` / `_resolve_tags` / `_resolve_tag_slug` / `_resolve_category` | **分类与标签域 ❌** |

最后 68 行里，`_resolve_category`（校验分类存在）、`_resolve_tags`（upsert 标签）、`_resolve_tag_slug`（生成标签 slug）**都是分类/标签领域的规则**，在文章服务里又实现了一遍。

**成因**：文章保存要"顺带"处理标签，最省事的写法就是在文章服务里就地实现。
**建议分两步（先低风险后结构性）**：

1. 给 `TaxonomyService` 加 `ensure_tags(names: list[str]) -> list[Tag]`（内部用现成的 `get_by_names` + 创建逻辑）与 `ensure_category(category_id)`，
   `ArticleService` 改为委托调用，**删掉自己的两个拷贝**。这一步和 §1.1 是同一处代码，一并做掉
2. 之后再按读写拆 `ArticleQueryService` / `ArticleWriteService`（可选，优先级低于第 1 步）

**成本**：中 · **风险**：中（触及文章写入主链路，154+ 个测试会覆盖）· **收益**：文章服务从 412 行降到约 340 行，跨域知识归位

### 3.2 `views/admin/ArticleEditView.vue`：452 行，script 246 / template 205

script 里已有清晰的四段注释（`载入` / `标签输入` / `封面上传` / `保存`）+ 草稿保护段 —— **注释本身说明了它该被拆**：

| 抽出物 | 行数区间 | 类型 |
|---|---|---|
| `useArticleForm` | 24–49、177–245 | composable（表单状态 + 字段错误 + 载荷构造 + 保存） |
| `useDraftAutosave` 接线 | 51–84 | 已在 composable 里，视图里只剩提示状态 → 抽 `DraftBanner.vue` |
| 标签输入 | 123–153（+ template 对应块） | `TagInput.vue`（有键盘交互：Enter/逗号/Backspace，值得独立 + 单测） |
| 封面上传 | 154–176 | `CoverUploader.vue` |

**成因**：视图既当"编排层"又当"展示层"，四条独立关注点共用一份表单状态，于是越写越长。
**成本**：中 · **风险**：中（编辑器是最高频页面，必须补组件测试再动）· **收益**：单文件降到 200 行内，标签输入与上传可单独测试

### 3.3 `components/CommentSection.vue`：**迁移未完成**

它仍在手写请求状态，**没有**跟上上一轮的抽象：

```ts
const loading = ref(false)        // 应为 useAsyncData
const submitting = ref(false)     // 应为 useAction
const loadError = ref('')         // 应为 useAsyncData 的 error
async function load() { try {...} catch {...} }
```

全站 9 个视图都迁到了 `useAction`，只有这里还是旧写法。
**成因**：迁移时按"admin 视图 + 详情页 + 登录页"清点，漏了组件层。
**建议**：迁移到 `useAsyncData` + `useAction`，**顺带解决它的分页问题**（现在一次拉全量评论树，对应评估报告 §1.7）。
**成本**：中 · **风险**：中（评论是访客唯一写入路径，必须有交互验证兜底）· **收益**：全站一致 + 评论分页

### 3.4 `db/seed.py`：289 行里大半是演示文案

逻辑（`ensure_seed` / 建分类 / 建标签 / 建文章）大约 60 行，其余是 4 篇演示文章的字面量。

**建议**：演示内容挪到 `db/seed_data.py` 或 `seed/*.json`，代码只留编排。
**成本**：低 · **风险**：低 · **收益**：`seed.py` 回到可读长度（它当前覆盖率 59%，也是因为字面量不计入断言）

---

## 4. 死代码与冗余

| 项 | 位置 | 说明 |
|---|---|---|
| `markdownToText()` 无任何引用 | `utils/markdown.ts` | 主页用 `strip_markdown`（后端）与 `renderMarkdown`，这个导出是早期遗留 |
| `SocialLink` schema 在 `schemas/` 之外 0 引用 | `schemas/site.py:10` | 仅作为 `SiteProfile` 的嵌套模型使用，`__all__` 里导出属多余 |
| `emptyPage` 字面量 ×4 | 见 §2.1 | — |
| `article_service` 的两个 slug 拷贝 | 见 §1.1 | — |

> 说明：项目 `TODO/FIXME` 计数为 **0**，`any` 为 **0**，service 层无缺失返回类型注解 —— 死代码与类型卫生整体是干净的，上面这些属于零散残留。

---

## 5. 耦合面评估

| 关系 | 评价 | 说明 |
|---|---|---|
| api → services → repositories → models | **健康** | 单向依赖，实测无越界；service 不依赖框架，可被脚本复用 |
| `feed_service` → 前端路由形状 | **需处理** | 后端知道前端的 URL 结构（§2.4），改路由会静默 404 |
| `ArticleService` → 分类/标签规则 | **需处理** | 跨域知识拷贝（§3.1） |
| 前端 views → stores（auth/site/theme） | **可接受** | 全局状态的正常用法；但 `site` store 被 header/footer/about/comment 四处依赖，改它接口影响面广，需谨慎 |
| `ArticleEditView` → 4 个 API 模块 | **偏宽** | article/attachment/category/tag 全在一处，拆分后会自然收敛 |
| `useToast` 模块级单例 | **合理** | 全局提示本就该是单例；`prefetch.ts` 的 `warmed` Set 同理 |

---

## 6. 成因归纳（为什么会出现这些问题）

1. **先写后抽，抽完不回填** —— slug 泛化发生在 `TaxonomyService`，文章侧的拷贝没跟着收敛，直接导致退化。**规则：泛化时必须在同一提交里收敛所有拷贝，否则就是制造分歧。**
2. **迁移按"看得见的文件"清点** —— `useAction` 迁移覆盖了 9 个视图，漏掉组件层的 `CommentSection`。**规则：迁移要按"调用点"而非"文件类型"清点（`grep` 已写明模式，可机械核对）。**
3. **同一业务概念缺少单一来源** —— 状态文案/配色、分页空态、表单字段三件套都属此类。**规则：凡是"两个地方必须同时改"的东西，都要有唯一出处。**
4. **视图兼具编排与展示** —— 一人开发时省事，代价是 452 行的文件。**规则：script 超过 150 行或出现 3 个以上 `/* --- 分区 --- */` 时就该拆。**
5. **缺少机械化的重复检查** —— 目前无 ESLint，重复只能靠人眼。**规则：把判断交给工具（见 §7 第 6 条）。**

---

## 7. 落地顺序（按投入产出比）

> **进度（2026-09-12 迭代）**：第 1、2、3（部分）、4、5 条已完成并验证；
> 第 6、7、8、9、10 条待做。逐条状态见下表「状态」列。

| 顺序 | 动作 | 成本 | 风险 | 收益 | 状态 |
|---|---|---|---|---|---|
| 1 | §1.1 + §3.1-1：抽 `unique_slug()`，文章服务改为委托 `TaxonomyService.ensure_tags/ensure_category` | 低 | 低 | 消除唯一已知退化，文章服务瘦 60+ 行 | ✅ 已完成 |
| 2 | §4：删 `markdownToText`、收敛 `SocialLink` 导出 | 极低 | 极低 | 清理死代码 | ✅ 已完成（`markdownToText` 已删；`SocialLink` 保留在 `schemas/__init__.py`，它是公共 schema 出口，暂不动） |
| 3 | §2.1 + §2.2：`emptyPage<T>()` 工厂 + 状态元数据单一来源 | 低 | 低 | 消灭两类多处同步 | ✅ 已完成 |
| 4 | §2.4：slug 形状配置化 + CI 断言 | 低 | 低 | 消除跨端静默耦合 | ✅ 已完成（`SITE_ARTICLE_PATH` + 契约用例） |
| 5 | §3.3：`CommentSection` 迁移 + 评论分页 | 中 | 中 | 全站一致 + 修性能问题 | 🟡 迁移已完成；**评论分页待做**（改 API 契约，需单独评估） |
| 6 | §7 配套：引入 **ESLint + jscpd（或 ruff C901 复杂度上限）**，把 `max-lines`、`max-lines-per-function` 设为 CI 门禁 | 低 | 低 | 让上面这些问题不能再悄悄出现 | ⬜ 待做 |
| 7 | §2.3：抽 `FormField.vue`（顺带补 `aria-invalid`） | 低 | 低 | 表单重复 + 无障碍 | ⬜ 待做 |
| 8 | §3.4：演示数据外置 | 低 | 低 | 可读性 | ⬜ 待做 |
| 9 | §3.2：拆 `ArticleEditView`（先补组件测试） | 中 | 中 | 最大单文件降到 200 行内 | ⬜ 待做 |
| 10 | §3.1-2：`ArticleService` 读写分离（可选） | 中 | 中 | 读路径可独立演进 | ⬜ 待做 |

> 第 1、2、3 条加起来约半天，且**风险极低**（都有测试兜底）；第 6 条是最有长期价值的一条
> —— 它决定了三个月后会不会再写一份同样的评审报告。

## 8. 本次迭代的验证记录

| 检查 | 结果 |
|---|---|
| `ruff check` / `ruff format --check` | 通过 / 77 文件已格式化 |
| `pytest` | **183 passed**（基线 171 → 183：新增 `test_slug.py` 10 个 + 跨端契约 2 个） |
| 覆盖率 | 保持 81% 档位（新增模块均带测试） |
| `vue-tsc --noEmit` | 0 报错 |
| `vitest run` | **48 passed**（+8：状态元数据 / 分页工具边界） |
| `vite build` | 通过 |
| `smoke-check.mjs` | **34/34** |
| `interaction-check.mjs` | **22/22**（新增预热挂载、评论空值拦截、评论提交、测试数据清理，共 6 项） |

