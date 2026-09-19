# 个人博客网站 · 设计与实现方案

> 主题定位：**综合型个人博客**——技术笔记、生活随笔、读书观影都往这里放。
> 技术栈：**FastAPI + Vue 3 前后端分离**。
> 本文档与仓库中的可运行代码一一对应，所有路由、字段、命令都可以直接在产品里验证。

---

## 目录

1. [整体页面结构](#1-整体页面结构)
2. [前端功能模块](#2-前端功能模块)
3. [数据模型设计](#3-数据模型设计)
4. [认证方案](#4-认证方案)
5. [部署上线流程](#5-部署上线流程)
6. [附录：项目结构与验证记录](#6-附录项目结构与验证记录)

---

## 1. 整体页面结构

### 1.1 信息架构

站点分成 **前台（读者侧）** 与 **后台（作者侧）** 两个域，中间用一个登录页连接。

```
                         ┌──────────────────┐
                         │   个人博客 前台    │
                         └────────┬─────────┘
        ┌──────────────┬──────────┼──────────┬──────────────┐
        ▼              ▼          ▼          ▼              ▼
   ┌─────────┐   ┌─────────┐ ┌────────┐ ┌────────┐   ┌──────────┐
   │ 文章列表 │   │ 文章详情 │ │ 标签页 │ │ 分类页 │   │  关于页   │
   │   /     │   │/article/│ │ /tags  │ │/categor│   │  /about  │
   │         │   │  :slug  │ │        │ │  ies   │   │          │
   └────┬────┘   └────┬────┘ └───┬────┘ └───┬────┘   └──────────┘
        │             │          │          │
        │ 分页/排序/  │ 正文+目录│ 标签云   │ 分类卡片
        │ 关键词/标签 │ 上下篇   │          │
        │ /分类筛选   │ 评论+点赞│          │
        └─────────────┴──────────┴──────────┘
                         │
                  ┌──────┴───────┐
                  │  其他页(占位) │  /links、/guestbook
                  └──────┬───────┘
                         │
                  ┌──────┴───────┐
                  │   登录页      │  /login
                  └──────┬───────┘
                         ▼
                  ┌──────────────────────────────────────┐
                  │          后台 /admin（登录后）          │
                  │ 仪表盘│文章│评论│分类标签│媒体库│用户│设置│
                  └──────────────────────────────────────┘
```

### 1.2 路由表

#### 前台路由

| 路径 | 名称 | 页面 | 说明 |
|---|---|---|---|
| `/` | `home` | 文章列表页 | 承载分页、排序、关键词搜索、标签/分类筛选，**全部状态存在 URL query 里** |
| `/article/:slug` | `article-detail` | 文章详情页 | Markdown 渲染、目录、上下篇、点赞、评论 |
| `/categories` | `categories` | 分类页 | 分类卡片墙，点击回到 `/?category=slug` |
| `/tags` | `tags` | 标签页 | 标签云，字号按使用频次分档 |
| `/archive` | `archive` | 归档页 | 按年月分组、可折叠，默认展开当年 |
| `/about` | `about` | 关于页 | 数据源是站点档案（`site_profile` 表） |
| `/links` | `links` | 友情链接 | **占位页** |
| `/guestbook` | `guestbook` | 留言板 | **占位页** |
| `/login` | `login` | 登录页 | 使用空白布局，不显示导航 |
| `/:pathMatch(.*)*` | `not-found` | 404 页 | 兜底，避免出现空白页 |

#### 后台路由（统一挂在 `/admin` 前缀下）

| 路径 | 页面 | 权限 |
|---|---|---|
| `/admin` | 仪表盘（全站统计 + 快捷操作） | 登录用户 |
| `/admin/articles` | 文章列表（含草稿，可按状态/关键词筛选） | 作者：仅自己；站长：全站 |
| `/admin/articles/new` | 新建文章 | 作者 |
| `/admin/articles/:id/edit` | 编辑文章 | 文章作者或站长 |
| `/admin/comments` | 评论审核（默认只看待审） | 作者：自己文章下的；站长：全部 |
| `/admin/taxonomy` | 分类 / 标签管理 | 作者（删除分类需站长） |
| `/admin/media` | 媒体库 | 作者：仅自己上传；站长：全站 |
| `/admin/users` | 用户管理 | **站长** |
| `/admin/settings` | 站点设置 | **站长** |

**为什么后台统一用 `/admin` 前缀？** 路由守卫只需要判断前缀，不必维护一张"哪些页面算后台"的清单——清单一定会漏，前缀不会。

### 1.3 布局层级

三个布局组件，页面组件完全不关心自己外面套的是什么：

```
App.vue  ← 只做一件事：按 route.meta.layout 选择布局
├── DefaultLayout    顶部导航(SiteHeader) + <router-view> + 页脚(SiteFooter) + ToastHost
├── AdminLayout      左侧导航 + 顶栏(用户名/主题/退出) + <router-view> + ToastHost
└── BlankLayout      居中卡片 + 主题切换（登录页用）
```

好处：页面组件保持纯粹的内容职责。新增一个页面时，不需要重复写导航和页脚。

### 1.4 导航方式

| 位置 | 内容 | 行为 |
|---|---|---|
| 顶部导航 | 首页 / 分类 / 标签 / 归档 / 友链 / 关于 | 当前项高亮；移动端折叠成抽屉，路由变化自动收起 |
| 顶栏右侧 | 主题切换、后台 / 登录入口 | 未登录显示"登录"，已登录显示"后台" |
| 列表页侧边栏 | 分类列表、标签云、快捷入口 | 点击即改 URL query，不做组件内状态切换 |
| 页脚 | 归档、留言板、社交链接、备案号 | 数据来自站点档案 |
| 文章详情 | 目录（xl 断点常驻右侧）、上下篇卡片 | 目录支持 IntersectionObserver 高亮当前章节 |
| 后台 | 左侧导航（按角色过滤可见项）、顶栏"回到前台" | 用户/设置菜单只对站长可见 |

> ⚠️ 前端隐藏菜单只是**体验优化**，真正的权限判定全部在后端。见第 4 章。

---

## 2. 前端功能模块

### 2.1 文章列表页：分页 + 排序

#### 分页

后端统一返回分页信封，前端只需要写一份分页组件：

```jsonc
// GET /api/v1/articles?page=2&page_size=8
{
  "items": [ /* ArticleSummary[] */ ],
  "total": 37,
  "page": 2,
  "page_size": 8,
  "pages": 5
}
```

实现要点：

- **服务端分页**，`page_size` 上限硬约束为 50（`app/api/pagination.py`）。不加上限的话，一个 `?page_size=100000` 就能把进程内存打满。
- **先 `count` 再取数据**，这样即使用户传了超范围的页码，也能拿到正确的 `total`，前端据此把用户弹回最后一页，而不是显示成"没有文章"。
- **列表接口绝不返回正文**。`ArticleSummary` 里刻意没有 `content_md` —— 一次返回 20 条带正文的记录，响应体轻松上兆，这是分页接口最常见的性能事故。
- 页码算法支持首尾页常驻 + 中间省略号（`frontend/src/components/Pagination.vue`）。

#### 排序

排序走**枚举白名单**，用户输入永远只会在预置选项里选，不存在把任意字符串拼进 `ORDER BY` 的可能：

| 枚举值 | 界面文案 | 实际 ORDER BY |
|---|---|---|
| `latest` | 最新发布 | `is_top DESC, COALESCE(published_at, created_at) DESC, id DESC` |
| `oldest` | 最早发布 | `is_top DESC, COALESCE(...) ASC, id ASC` |
| `hottest` | 最多阅读 | `is_top DESC, view_count DESC, COALESCE(...) DESC` |
| `updated` | 最近更新 | `updated_at DESC, id DESC` |
| `title` | 标题排序 | `title ASC` |

两个细节：

1. 用 `COALESCE(published_at, created_at)` 作为"有效发布时间"。草稿没有 `published_at`，直接按它排序会踩到 **SQLite 与 PostgreSQL 对 NULL 排序位置不一致**的坑（SQLite 认为 NULL 最小，PG 的 `DESC` 把 NULL 排最前）。用 COALESCE 一次性绕开。后台列表另有关掉 `is_top` 优先的开关（`top_first=False`）。
2. 置顶文章（`is_top`）在任何排序下都排在第一位——否则"置顶"这个功能没有意义。

#### 筛选

| 参数 | 说明 |
|---|---|
| `keyword` | 标题 / 摘要 / 正文三字段模糊匹配 |
| `category` | 分类 **slug 或数字 id** 皆可，前端不必关心自己手上是哪种 |
| `tag` | 标签 slug |
| `author_id` | 按作者过滤 |
| `status` | 仅后台接口，按草稿/已发布/已归档过滤 |

关键词搜索有一个容易被忽略的安全细节：`%` 和 `_` 是 LIKE 的通配符，不转义的话用户搜 `100%` 会变成"以 100 开头"的模糊匹配，搜 `%` 会匹配到全部文章。代码里做了转义（`_like_pattern()`）。

### 2.2 文章详情页：Markdown 渲染

#### 存储策略：存原文，不存 HTML

正文以 **Markdown 原文**存在数据库（`articles.content_md`），渲染完全在前端完成。

这样选的理由：

- 后端保持纯粹的"内容源"角色，将来换渲染器（marked → markdown-it → 服务端渲染）不用动数据库；
- 避免了"存进去时消毒不干净"的隐患——后端根本不产生 HTML；
- 原文是可移植资产，导出到任何 Markdown 工具都能直接用。

#### 渲染流水线

```
content_md
   │
   ├─▶ marked.parse()          Markdown → HTML（开启 GFM：表格/删除线/任务列表）
   │
   ├─▶ DOMPurify.sanitize()    ★ 安全边界：白名单标签 + 白名单属性 + 协议白名单
   │
   ├─▶ 给 h1~h4 加锚点 id，顺便收集目录数据
   │
   ├─▶ highlight.js           代码块高亮（直接操作 DOM，比自己拼字符串安全）
   │
   ├─▶ 外链补 target="_blank" + rel="noopener noreferrer"
   │
   └─▶ <img> 加 loading="lazy" decoding="async"
```

**关键顺序：消毒必须排在所有增强步骤之前。** 因为增强步骤只会 `setAttribute` 服务端生成的 id、自己拼的 `rel` 值，不引入任何新的不可信内容；反过来先增强再消毒，就可能把增强逻辑本身变成注入点。

> **为什么必须消毒？** Markdown 语法**允许内联 HTML**，也就是说 `content_md` 里直接写 `<img src=x onerror=alert(1)>` 是完全合法的 Markdown。认为"用了 Markdown 就安全"是很常见的错觉。
> 另外在 DOMPurify 配置里显式 **禁掉了 `style` 属性**——内联样式可以做覆盖式钓鱼（伪造一个全屏的假登录框）。

#### 目录（TOC）

- 渲染时顺手收集 `h2~h4` 的 `{id, text, depth}`；
- `IntersectionObserver` 监听标题进入视口，高亮当前章节——比监听 `scroll` 事件性能好得多（前者在合成线程算，后者会频繁触发 JS）；
- 用 `rootMargin: '-80px 0px -70% 0px'` 把"当前阅读位置"锚定在视口上半部分，符合直觉；
- `scroll-padding-top` 保证锚点跳转时标题不被固定顶栏盖住。

#### 渲染缓存

`renderMarkdown()` 内部有一个 30 条的 LRU 缓存（`frontend/src/utils/markdown.ts`）。同一篇文章的正文会被目录组件和正文组件同时消费，缓存能避免重复解析。

### 2.3 图片与附件的上传、存储与展示

#### 全链路

```
浏览器                      FastAPI                     存储
   │                          │                          │
   │  POST /attachments/upload│                          │
   │  multipart/form-data     │                          │
   ├─────────────────────────▶│                          │
   │                          │ 1. 流式读 + 限流          │
   │                          │ 2. Pillow 真实解码判类型   │
   │                          │ 3. 扩展名白名单校验        │
   │                          │ 4. UUID 生成文件名 ───────▶ storage/uploads/YYYYMM/xxx.png
   │                          │ 5. 生成 WEBP 缩略图 ──────▶ storage/uploads/YYYYMM/thumb-xxx.webp
   │                          │ 6. 元数据落库             │
   │  201 { url, thumbnail_url, markdown }
   │◀─────────────────────────│
   │                          │
   │  GET /media/uploads/...  │  StaticFiles 直出静态文件
   ├─────────────────────────▶│
```

#### 存储设计

| 决策 | 做法 | 理由 |
|---|---|---|
| 存哪儿 | 库里只存**元数据 + 相对 URL**，物理文件落在磁盘/对象存储 | 将来从本地磁盘切 S3/COS 只需换一个 `StorageBackend` 实现（`app/utils/storage.py` 已定义协议），服务层一行不用改 |
| 目录结构 | `storage/uploads/<YYYYMM>/<8位随机串>.<ext>` | 按月分目录，单目录文件数不会失控；文件名纯 ASCII 随机串 |
| 为什么不用原名 | 用户原始文件名只存进数据库用于展示 | 从根上消除路径穿越（`../../etc/passwd`）与重名覆盖；也避开中文文件名在不同系统上 URL 编码行为不一致的问题 |
| 缩略图 | Pillow 生成 WEBP（质量 82，最长边 480） | WEBP 同时支持透明通道与高压缩率，比"PNG 太大 / JPEG 丢透明"更合适 |
| URL 前缀 | `/media` | 与 API 前缀 `/api/v1` 分离，方便反向代理层单独给静态资源加缓存头 |

#### 安全校验（这部分逐条都是实际踩过的坑）

1. **不信任客户端声明的类型。** `Content-Type` 和文件名都能随便伪造，所以"是不是图片"靠 **Pillow 真解一遍**来判定。把一段文本改名成 `.png` 上传会被正确拒绝（415）。
2. **流式限流读。** 边读边累加字节数，超过 `MAX_UPLOAD_SIZE`（默认 10MB）立刻中断，不会等整个文件进内存/落盘才发现太大。
3. **扩展名白名单，且默认禁 SVG。** SVG 是 XML，可以内嵌 `<script>`；从本站同源直出的话，攻击者上传一个 SVG 就等于拿到**存储型 XSS**，能盗取所有访客的登录态。需要 SVG 时应放到独立域名托管。
4. **普通附件只允许** `pdf / zip / txt / md / csv / json / docx / xlsx / pptx / epub`，**禁掉 html / js / svg** —— 这些文件被浏览器当同源脚本执行就是 XSS。
5. **像素炸弹防护。** 拒绝超过 5000 万像素的图片，避免 Pillow 解码时把内存吃光。
6. **空文件直接拒绝。**

#### 展示

- 详情页图片走 `loading="lazy"`，列表卡片用 `thumbnail_url` 而不是原图；
- 上传接口返回体里带 `markdown` 字段（`![name](url)` / `[name](url)`），编辑器据此实现"上传即插入"；
- 编辑器支持**直接粘贴剪贴板里的图片**，自动走上传（比"先存到本地再选文件"顺手得多）；
- 媒体库支持多选上传，并**逐个汇报结果**——批量上传里"部分失败"是常态，一句笼统的"上传失败"会让用户完全不知道是哪个文件出了问题。

### 2.4 状态管理与数据流

```
组件 (views/)
   │  只调用 api/ 暴露的方法，不直接碰 axios
   ▼
API 层 (api/)
   │  统一附加 Token、401 自动续期、异常归一化为 ApiError
   ▼
Store (stores/)          跨页面共享的状态：登录态 / 主题 / 站点档案
   │
   ▼
后端
```

- **列表页的筛选、排序、分页状态全部放在 URL query 里**，组件不额外维护一份。这样刷新、分享链接、浏览器前进后退都能还原到同一个视图，也杜绝了"URL 和界面对不上"的经典 bug。
- `useAsyncData` 统一封装 loading / error / data，并用自增 requestId **丢弃过期响应**——用户快速切页时，先发的请求可能后返回，会把新页面的数据覆盖成旧内容。
- 主题切换在 `index.html` 里用一段内联脚本提前给 `<html>` 打上 `.dark`，避免暗色模式用户看到一次白屏闪烁。

---

## 3. 数据模型设计

### 3.1 实体关系总览

```
                   ┌────────────────┐
                   │     users      │
                   │────────────────│
                   │ id (PK)        │
                   │ username  UQ   │
                   │ email     UQ   │
                   │ hashed_password│
                   │ role           │  admin | author
                   │ is_active      │
                   └───┬────────┬───┘
          author_id    │        │   uploader_id
        (1:N, CASCADE) │        │  (1:N, CASCADE)
                       ▼        ▼
        ┌──────────────────┐  ┌─────────────────┐
        │    articles      │  │   attachments   │
        │──────────────────│  │─────────────────│
        │ id (PK)          │  │ id (PK)         │
        │ title            │  │ stored_name UQ  │
        │ slug        UQ   │  │ original_name   │
        │ summary          │  │ mime_type       │
        │ content_md       │  │ size / kind     │
        │ cover_image      │  │ width / height  │
        │ status           │  │ url / thumb_url │
        │ is_top           │  └─────────────────┘
        │ allow_comment    │
        │ view_count       │
        │ like_count       │
        │ reading_time     │
        │ published_at     │
        │ author_id   FK   │
        │ category_id FK   │──┐
        └───┬──────────┬───┘  │ (N:1, SET NULL)
            │          │      ▼
            │          │  ┌────────────────┐
            │          │  │  categories    │
            │          │  │────────────────│
            │          │  │ id (PK)        │
            │          │  │ name      UQ   │
            │          │  │ slug      UQ   │
            │          │  │ description    │
            │          │  │ sort_order     │
            │          │  └────────────────┘
            │          │
            │          │  ┌────────────────┐
            │          └─▶│    comments    │  (1:N, CASCADE)
            │   article_id │────────────────│
            │              │ id (PK)        │
            │              │ parent_id FK ──┼──┐ 自关联（两级回复）
            │              │ user_id   FK   │  │
            │              │ author_name    │  │
            │              │ author_email   │  │
            │              │ content        │  │
            │              │ is_approved    │  │
            │              │ is_admin_reply │  │
            │              │ ip / user_agent│  │
            │              └────────────────┘  │
            │                                  │
            │  ┌───────────────────────┐       │
            └─▶│    article_tags       │◀──────┘
               │───────────────────────│
               │ article_id  PK/FK     │   纯连接表：
               │ tag_id      PK/FK     │   该关系无额外属性
               └───────────┬───────────┘
                           ▼
                   ┌────────────────┐
                   │      tags      │
                   │────────────────│
                   │ id (PK)        │
                   │ name      UQ   │
                   │ slug      UQ   │
                   └────────────────┘

                   ┌────────────────┐
                   │  site_profile  │  单行表 (id 恒为 1)
                   │────────────────│
                   │ owner_name     │
                   │ headline       │
                   │ avatar_url     │
                   │ bio_md         │
                   │ about_md       │
                   │ social_links[] │  JSON
                   │ skills[]       │  JSON
                   │ comment_need_  │
                   │   approval     │
                   │ allow_guest_   │
                   │   comment      │
                   └────────────────┘
```

### 3.2 字段定义

#### users

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | int | PK | |
| `username` | varchar(50) | UQ, IDX, NOT NULL | 登录名，至少 3 字符 |
| `email` | varchar(255) | UQ, IDX, NOT NULL | 可用于登录；不公开 |
| `hashed_password` | varchar(255) | NOT NULL | bcrypt（cost 12）；**从不返回给客户端** |
| `role` | enum | NOT NULL, default `author` | `admin` / `author`，以字符串存储 |
| `nickname` | varchar(50) | | 展示名，缺省回落到 username |
| `avatar_url` | varchar(500) | | |
| `bio` | text | | |
| `is_active` | bool | NOT NULL, default true | 停用后无法登录 |
| `created_at` / `updated_at` | timestamptz | NOT NULL, IDX | 统一由 `TimestampMixin` 提供 |

#### categories

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | int | PK | |
| `name` | varchar(50) | UQ, IDX, NOT NULL | |
| `slug` | varchar(80) | UQ, IDX, NOT NULL | 留空按名称自动生成；中文直接保留汉字 |
| `description` | text | | |
| `sort_order` | int | NOT NULL, default 0 | 越小越靠前 |

#### tags

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | int | PK | |
| `name` | varchar(50) | UQ, IDX, NOT NULL | |
| `slug` | varchar(80) | UQ, IDX, NOT NULL | |

#### articles

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | int | PK | |
| `title` | varchar(200) | NOT NULL, IDX | |
| `slug` | varchar(220) | UQ, IDX, NOT NULL | URL 别名；重名自动追加 `-2` / `-3` |
| `summary` | varchar(500) | | 留空从正文自动提取 |
| `content_md` | text | NOT NULL | Markdown **原文** |
| `cover_image` | varchar(500) | | |
| `status` | enum | NOT NULL, IDX, default `draft` | `draft` / `published` / `archived` |
| `is_top` | bool | NOT NULL, default false | 置顶 |
| `allow_comment` | bool | NOT NULL, default true | |
| `view_count` | int | NOT NULL, default 0 | 详情页访问时**原子自增** |
| `like_count` | int | NOT NULL, default 0 | |
| `reading_time` | int | NOT NULL, default 1 | 预估阅读分钟数，按正文字符数估算 |
| `published_at` | timestamptz | IDX | 首次发布时写入；反复切状态**不会**覆盖 |
| `author_id` | int | FK users, NOT NULL, IDX, CASCADE | |
| `category_id` | int | FK categories, IDX, **SET NULL** | 可空 = 未分类 |

索引：`ix_articles_status_published_at(status, published_at)`、`ix_articles_is_top_published_at(is_top, published_at)` —— 对应列表页最高频的两种查询组合。

#### attachments

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | int | PK | |
| `stored_name` | varchar(255) | UQ, NOT NULL | 磁盘上的文件名（纯 ASCII 随机串） |
| `original_name` | varchar(255) | NOT NULL | 用户原始文件名，仅用于展示/下载 |
| `mime_type` | varchar(120) | NOT NULL | |
| `size` | bigint | NOT NULL | 字节数 |
| `kind` | varchar(20) | NOT NULL, default `image` | `image` / `file`，由 Pillow 解码结果决定 |
| `width` / `height` | int | | 仅图片有值 |
| `thumbnail_name` | varchar(255) | | 缩略图文件名 |
| `url` / `thumbnail_url` | varchar(500) | NOT NULL / 可空 | 可直出浏览器的相对地址 |
| `uploader_id` | int | FK users, NOT NULL, IDX, CASCADE | |

#### comments

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | int | PK | |
| `article_id` | int | FK articles, NOT NULL, IDX, CASCADE | |
| `parent_id` | int | FK comments, IDX, CASCADE | 自关联，**只做两级** |
| `user_id` | int | FK users, SET NULL | 登录用户才有 |
| `author_name` | varchar(50) | NOT NULL | 游客必填；登录用户自动取昵称 |
| `author_email` | varchar(255) | | **不公开**，仅站长可见 |
| `author_site` | varchar(255) | | |
| `content` | text | NOT NULL | 纯文本，最多 2000 字 |
| `is_approved` | bool | NOT NULL, IDX, default false | 先审后发 |
| `is_admin_reply` | bool | NOT NULL, default false | 站长/作者回复自动过审 |
| `ip_address` / `user_agent` | varchar | | 反垃圾排查用 |

#### site_profile（单行表）

`owner_name`、`headline`、`avatar_url`、`bio_md`、`about_md`、`email`、`location`、`icp`、`social_links`(JSON)、`skills`(JSON)、`comment_need_approval`(bool)、`allow_guest_comment`(bool)。

**为什么用单行表而不是 key-value 配置表？** 这些字段有明确的结构（数组、布尔），KV 表存进去就得手动序列化/反序列化，还会丢掉类型校验。单行表的查询也更简单（`WHERE id = 1`）。

### 3.3 关联关系一览

| 关系 | 类型 | 级联策略 | 说明 |
|---|---|---|---|
| User → Article | 1:N | `ON DELETE CASCADE` | 删除用户会删除其文章 |
| User → Attachment | 1:N | `ON DELETE CASCADE` | |
| Category → Article | 1:N | `ON DELETE SET NULL` | **删分类不删文章**，它们变成"未分类" |
| Article ↔ Tag | M:N | `ON DELETE CASCADE`（连接表） | 纯连接表，无额外属性 |
| Article → Comment | 1:N | `ON DELETE CASCADE` | 删文章连带删评论 |
| Comment → Comment | 1:N（自关联） | `ON DELETE CASCADE` | 删父评论连带删回复 |

### 3.4 三个实现层面的关键决策

#### ① 所有时间字段统一为 UTC + 带时区

SQLite 底层把时间存成字符串，取出来是 **naive**（无时区）datetime；PostgreSQL 的 `timestamptz` 取出来是 **aware** 的；而应用里新建对象用的是 `datetime.now(UTC)`，也是 aware。

结果是同一个字段在"刚创建"和"重新查询"两条路径下序列化出的 JSON **不一样**（`...T14:33:07Z` vs `...T14:33:07`），前端比较时间、做缓存去重时会莫名其妙失败。

解法是自定义字段类型 `UTCDateTime`（`app/db/types.py`）：读出来时统一补上 UTC。全站时间口径只有一种——**UTC，带时区**，展示时由前端按用户本地时区格式化。

#### ② 时间戳的 Python 侧默认值

`created_at` / `updated_at` 同时给了 Python 侧 `lambda` 和数据库侧 `server_default`：

- Python 侧的 lambda 让 ORM **明确知道**写入的是什么值，flush 之后不需要再回查；
- 数据库侧的 `server_default` 保证裸 SQL 插入也拿得到时间戳。

**为什么不用 `onupdate=func.now()` 让数据库算？** 因为 SQLAlchemy 无法预知数据库函数的结果，只能在 UPDATE 之后把这些列标记为过期、等下次访问再回查。而异步会话里的这种惰性回查会在事件循环中发起同步 IO，直接抛 `MissingGreenlet` —— 表现为"改完数据序列化响应时报 500"，排查起来非常隐蔽。

#### ③ 必须显式打开 SQLite 的外键约束

SQLite 的 `PRAGMA foreign_keys` **默认是 OFF**。这意味着写了 `ON DELETE CASCADE` 数据库根本不会执行——删掉一篇文章，评论会全部变成指向不存在文章的孤儿行。

必须在每个连接建立时打开（`connect` 事件里，而不是启动时执行一次，因为连接池里的连接是复用的），同时开启 WAL 提升并发读写。

---

## 4. 认证方案

### 4.1 整体设计

**JWT 双 Token + RBAC 三角色**。

```
                    ┌──────────────────────────────────────┐
                    │  访客（未认证）                        │
                    │  · 读已发布文章 / 标签 / 分类 / 关于页   │
                    │  · 发表评论（可匿名，需审核）            │
                    │  · 点赞                               │
                    │  · 访问草稿 → 404（不是 403）           │
                    └────────────────┬─────────────────────┘
                                     │ POST /auth/login
                                     ▼
                    ┌──────────────────────────────────────┐
                    │  access_token  有效期 120 分钟         │
                    │  refresh_token 有效期 7 天            │
                    │  payload: sub / role / type / iat /   │
                    │           exp / jti                   │
                    └────────────────┬─────────────────────┘
                                     │
        ┌────────────────────────────┴────────────────────────────┐
        ▼                                                          ▼
┌──────────────────┐                                     ┌──────────────────┐
│  author（作者）   │                                     │  admin（站长）    │
│ · 建/改/删自己的   │                                     │ · 作者的权限      │
│   文章与附件      │                                     │ · 改删任何人的    │
│ · 管理分类标签    │  ← 包含 →                           │   文章/附件       │
│ · 审核自己文章下   │                                     │ · 删分类          │
│   的评论          │                                     │ · 管理用户        │
│ · 后台只看自己的   │                                     │ · 站点设置/统计   │
│   草稿            │                                     │ · 全站评论审核    │
└──────────────────┘                                     └──────────────────┘
```

### 4.2 Token 设计

| 项 | 值 | 说明 |
|---|---|---|
| 算法 | HS256 | 对称签名，单服务部署足够；密钥长度要求 ≥ 32 字节 |
| access_token TTL | 120 分钟 | 短有效期，泄露窗口小 |
| refresh_token TTL | 7 天 | 用于静默续期 |
| payload | `sub` / `role` / `type` / `iat` / `exp` / `jti` | **刻意不含邮箱、密码哈希等敏感字段** |

**`type` 字段的用途**：防止 refresh token 被当成 access token 直接访问业务接口。`decode_token(token, expected_type="access")` 会校验类型，不符就抛错。少了这一层，短期凭证和长期凭证的隔离就白做了。

**关于载荷是明文可读的**：JWT 只签名不加密。所以 token 里绝不能放密码哈希、邮箱这类信息，测试里有一条专门断言 token 的字段集合。`jti` 为将来做 token 黑名单/单点登出留了钩子。

### 4.3 前端续期：单飞锁

页面加载时经常几个请求同时收到 401。如果不加锁，就会并发发出 N 个 refresh，而滚动刷新（refresh 后旧 refresh 立即作废）会让其中 N-1 个失败，用户体验就是"莫名其妙被登出"。

```ts
// frontend/src/api/http.ts
let refreshPromise: Promise<Token> | null = null

refreshPromise = refreshPromise ?? refreshTokens()
const tokens = await refreshPromise
// 用新 token 重放原请求
```

续期失败则清空本地凭证并跳转登录页（用 `location.replace` 而不是 `push`，登出后不该还能按返回键回到需要登录的页面）。

### 4.4 访问控制：三层落地

#### 第一层：依赖注入门禁（`app/api/deps.py`）

```python
CurrentUser  = Annotated[User, Depends(get_current_user)]     # 认证：你是谁
OptionalUser = Annotated[User | None, Depends(get_optional_user)]  # 可选登录态
AdminUser    = Annotated[User, Depends(require_admin)]        # 授权：仅站长
AuthorUser   = Annotated[User, Depends(require_author)]       # 授权：作者或站长
```

**认证与授权分开**是关键：路由上按需组合，不在每个 handler 里手写 `if role == ...`。

可选登录态（`get_optional_user`）用于"登录用户能看到自己的草稿、访客只能看已发布"这类接口。注意它**对无效 token 静默降级为访客**而不是报错——不应该因为一个坏 token 就把公开页面挡在门外。

#### 第二层：资源级归属校验（服务层）

角色门禁解决不了"两个作者之间"的问题，必须逐个资源判断：

```python
@staticmethod
def _can_edit(article: Article, user: User) -> bool:
    """作者只能改自己的文章；站长可以改任何人的。"""
    return user.role is UserRole.ADMIN or article.author_id == user.id
```

同类校验还有：附件只能删自己上传的、评论只能管理自己文章下的、用户只能改自己的资料。

#### 第三层：Schema 层防提权

修改自己的资料用 `UserSelfUpdate`，这个 schema **根本不包含 `role` 和 `is_active` 字段**：

```python
class UserSelfUpdate(BaseModel):
    nickname: str | None = None
    email: EmailStr | None = None
    avatar_url: str | None = None
    bio: str | None = None
    # 没有 role，没有 is_active
```

从数据契约层面就杜绝了普通用户给自己提权的可能，比在业务代码里加判断更不容易被绕过。

### 4.5 权限矩阵

| 操作 | 访客 | author | admin |
|---|:---:|:---:|:---:|
| 读已发布 / 已归档文章 | ✅ | ✅ | ✅ |
| 读草稿 | ❌ 404 | 仅自己的 | 全部 |
| 发表评论 | ✅（需审核） | ✅ | ✅（自动过审） |
| 点赞 | ✅ | ✅ | ✅ |
| 新建文章 | ❌ | ✅ | ✅ |
| 改 / 删文章 | ❌ | 仅自己的 | 任意 |
| 上传附件 | ❌ | ✅ | ✅ |
| 删附件 | ❌ | 仅自己的 | 任意 |
| 建 / 改分类、标签 | ❌ | ✅ | ✅ |
| 删分类 | ❌ | ❌ | ✅ |
| 清理空标签 | ❌ | ❌ | ✅ |
| 审核 / 删评论 | ❌ | 自己文章下的 | 全部 |
| 管理用户 | ❌ | ❌ | ✅ |
| 站点设置 / 看统计 | ❌ | ❌ | ✅ |

### 4.6 若干安全细节

| 风险 | 处理 |
|---|---|
| **账号枚举** | 用户不存在时也跑一次 bcrypt 校验（`_dummy_hash()`），让"账号不存在"与"密码错误"响应耗时接近；且两者返回**完全相同**的文案 |
| **草稿信息泄露** | 访问无权查看的草稿返回 **404 而非 403**。403 等于承认"这里确实有一篇未发布的文章" |
| **bcrypt 72 字节上限** | bcrypt 对超过 72 **字节**的输入会直接抛错。在 schema 层显式校验字节长度（不是字符数——1 个汉字算 3 字节），而不是静默截断（截断会让"前 72 字节相同"的两个不同密码互相能登录） |
| **站点自锁** | 不允许停用/降级最后一个可用管理员，不允许删除自己 |
| **暴力破解** | access token 120 分钟 + refresh 7 天，显著小于"永不过期"的凭证；生产环境建议在反向代理层再加一层 IP 限流 |
| **生产密钥** | `jwt_secret_key` 有明确的开发默认值，生产必须用 `openssl rand -hex 32` 重新生成 |

### 4.7 关于 Token 存储位置的说明

前端把 token 放在 `localStorage`。这带来一个已知的权衡：

- **优点**：实现简单，无 CSRF 风险（不会自动随请求携带）；
- **缺点**：一旦发生 XSS，token 可被读取。

之所以接受这个权衡，是因为本站已经**从架构上大幅压缩了 XSS 面**：正文经 DOMPurify 白名单消毒、评论用纯文本渲染（Vue 自动转义）、上传禁用 SVG/HTML、DOMPurify 禁掉内联 `style`。

如果将来要上线对安全性要求更高的形态，推荐升级为：`refresh_token` 放 **HttpOnly + Secure + SameSite=Lax 的 Cookie**，`access_token` 只保存在内存里（刷新页面就重新静默续期一次），并补上 CSRF Token。这是一个独立的改造点，不影响现有分层。

---

## 5. 部署上线流程

### 5.1 架构

```
                    Internet
                       │
                  HTTPS 443
                       ▼
            ┌──────────────────────┐
            │   Nginx（反向代理）    │
            │ · TLS 终止            │
            │ · 静态资源直出 + 缓存  │
            │ · /api 转发到后端      │
            └───────┬──────────────┘
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
┌───────────────┐        ┌────────────────────┐
│ Nginx 容器     │        │  backend 容器       │
│ 托管前端 dist  │        │  uvicorn (FastAPI) │
└───────────────┘        └─────────┬──────────┘
                                   │
                        ┌──────────┴──────────┐
                        ▼                     ▼
              ┌──────────────────┐  ┌──────────────────┐
              │  PostgreSQL      │  │  文件存储卷        │
              │  (postgres:16)   │  │  /app/storage     │
              └──────────────────┘  └──────────────────┘
```

### 5.2 环境要求

| 组件 | 版本 | 备注 |
|---|---|---|
| Python | ≥ 3.11 | 用到 `StrEnum`（3.11+）与 `X \| None` 语法 |
| Node.js | ≥ 20 | 仅构建前端时需要 |
| PostgreSQL | ≥ 14 | 生产推荐；开发可继续用 SQLite |
| Docker / Compose | ≥ 24 / v2 | 容器化部署需要 |
| Nginx | ≥ 1.24 | 或使用云厂商的负载均衡 |

### 5.3 环境变量清单

后端（`backend/.env`）：

| 变量 | 必填 | 默认 | 说明 |
|---|:---:|---|---|
| `APP_ENV` | ✅ | `development` | 生产必须设为 `production`（会关闭 `/docs`） |
| `DEBUG` | | `true` | 生产设 `false` |
| `JWT_SECRET_KEY` | ✅ | 开发占位值 | **生产必须换成 `openssl rand -hex 32`** |
| `DATABASE_URL` | ✅ | SQLite | 生产：`postgresql+asyncpg://user:pass@db:5432/blog` |
| `DB_AUTO_CREATE` | | `true` | **生产必须设 `false`**，改由 Alembic 管理表结构 |
| `CORS_ORIGINS` | ✅ | localhost | 生产填真实前端域名，逗号分隔 |
| `STORAGE_DIR` | | `./storage` | 生产指向挂载卷 |
| `MAX_UPLOAD_SIZE` | | `10485760` | 10MB |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | ✅ | | 首次启动创建的管理员；**上线后请立即改密码** |
| `SEED_DEMO_DATA` | | `true` | 正式站设为 `false`，避免写入演示文章 |

前端（构建期，`frontend/.env.production`）：

| 变量 | 说明 |
|---|---|
| `VITE_API_BASE_URL` | 默认 `/api/v1`。前后端同域时不用改；跨域部署时填完整 URL |

### 5.4 依赖清单

后端（`backend/pyproject.toml`）：

```
fastapi, uvicorn[standard], sqlalchemy[asyncio], greenlet, aiosqlite,
alembic, pydantic, pydantic-settings, email-validator,
python-multipart, pyjwt, bcrypt, pillow
生产额外：asyncpg            （uv sync --extra postgres）
开发额外：httpx, pytest, pytest-asyncio, pytest-cov, ruff
```

前端（`frontend/package.json`）：

```
运行时：vue, vue-router, pinia, axios, marked, dompurify, highlight.js
构建期：vite, @vitejs/plugin-vue, typescript, vue-tsc,
        tailwindcss, postcss, autoprefixer, @tailwindcss/typography, @types/node
```

### 5.5 上线步骤

#### 方式 A：Docker Compose（推荐）

```bash
# 1. 拉取代码
git clone <你的仓库地址> personal-blog && cd personal-blog

# 2. 准备生产配置
#    ⚠️ 是**项目根目录**的 .env，不是 backend/.env：
#    compose 的变量插值只读根目录这一份，backend/.env 是裸机开发用的。
cp .env.example .env
#    编辑 .env，三处必填（缺了 compose 直接拒绝启动）：
#      POSTGRES_PASSWORD=<数据库口令>
#      JWT_SECRET_KEY=$(openssl rand -hex 32)
#      ADMIN_PASSWORD=<站长初始强口令>
#    上线还要改：SITE_BASE_URL=https://你的域名
#    APP_ENV / DEBUG / DB_AUTO_CREATE / SEED_DEMO_DATA 已由 compose 按
#    生产侧设好，一般不用动。

# 3. 构建并启动（容器入口会自动跑 alembic upgrade head）
docker compose up -d --build

# 4. 确认
curl -fsS http://localhost:8080/health
docker compose ps
```

> 后端**不发布** 8000 端口，上面这个 `/health` 是经 Nginx 反代的。
> 想看接口文档要在根 `.env` 里设 `APP_ENV=development`（生产会关掉 `/docs`），
> 或用 `docker compose exec backend curl -s 127.0.0.1:8000/health` 在容器内自测。

#### 方式 B：裸机部署

```bash
# ---------- 后端 ----------
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[postgres]"          # 或 uv sync --extra postgres
cp .env.example .env && vim .env      # 同上，改生产配置
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4

# ---------- 前端 ----------
cd ../frontend
npm ci
VITE_API_BASE_URL=/api/v1 npm run build   # 产出 frontend/dist
# 把 dist/ 交给 Nginx 托管，配置见 deploy/nginx.conf
```

#### 用 systemd 托管后端

```ini
# /etc/systemd/system/personal-blog.service
[Unit]
Description=Personal Blog API
After=network.target postgresql.service

[Service]
Type=simple
User=blog
WorkingDirectory=/srv/personal-blog/backend
EnvironmentFile=/srv/personal-blog/backend/.env
ExecStart=/srv/personal-blog/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now personal-blog
```

### 5.6 数据库演进：Alembic

开发期 `DB_AUTO_CREATE=true` 会自动建表，方便；**生产必须关掉**，改由迁移脚本管理——否则一次误改模型就会在生产悄悄改表。

```bash
cd backend
alembic revision --autogenerate -m "add xxx field"   # 生成迁移
# ⚠️ 务必人工检查生成的脚本（autogenerate 会漏掉列重命名、索引变更等）
alembic upgrade head                                  # 应用
alembic downgrade -1                                  # 回滚一步
alembic current                                       # 查看当前版本
```

### 5.7 上线检查清单（PRR）

| 维度 | 检查项 |
|---|---|
| **配置安全** | `JWT_SECRET_KEY` 已换成随机值；`APP_ENV=production`（`/docs` 已关闭）；`DEBUG=false`；`DB_AUTO_CREATE=false`；`SEED_DEMO_DATA=false`；`CORS_ORIGINS` 只含真实域名 |
| **账号** | 默认管理员密码已修改；不存在测试账号 |
| **可观测** | `/health` 已接入探活；Nginx access log 已开；应用异常日志已收集 |
| **可靠性** | 后端进程有自动重启（systemd `Restart=always` 或 Compose `restart: unless-stopped`）；数据库有定时备份 |
| **容量** | `MAX_UPLOAD_SIZE` 合理；`storage` 卷有足够空间与监控；Nginx `client_max_body_size` 与后端限制一致 |
| **安全** | TLS 证书有效且自动续期；上传目录**不可执行**；`/media` 响应加了 `X-Content-Type-Options: nosniff`；数据库不对公网开放 |
| **数据** | 已完成首次备份；`alembic upgrade head` 已执行；回滚方案已确认 |
| **回滚** | 明确回滚命令：`docker compose up -d --build <上一个镜像 tag>`；代码回滚后**不要**盲目 `alembic downgrade`（先确认迁移是否不可逆） |

### 5.8 发布与回滚

推荐**渐进式发布**：

1. 先在同环境的预发实例上跑一遍冒烟（`/health`、登录、发一篇文章、传一张图）；
2. 灰度：Nginx 按权重把少量流量打到新版本，观察 5–10 分钟的错误率与延迟；
3. 无异常后全量放量；
4. 发布后观察清单：`/health` 200、登录成功率、文章列表 P95 延迟、5xx 数量、磁盘占用。

**回滚条件（预先写死，别临场决定）**：5xx 比例 > 1% 持续 3 分钟，或登录不可用，或 `/health` 连续失败 → 立即回滚上一版本镜像。

### 5.9 备份

```bash
# 数据库（每日，保留 14 天）
docker compose exec -T db pg_dump -U blog blog | gzip > /backup/blog-$(date +%F).sql.gz
find /backup -name 'blog-*.sql.gz' -mtime +14 -delete

# 媒体文件（每周全量，或每日增量）
tar -czf /backup/storage-$(date +%F).tar.gz -C /srv/personal-blog/backend storage/
```

---

## 6. 附录：项目结构与验证记录

### 6.1 目录结构

```
personal-blog/
├── backend/
│   ├── src/app/
│   │   ├── main.py                 # 应用工厂：异常处理 / CORS / 静态挂载 / lifespan
│   │   ├── config.py               # pydantic-settings，全部配置来自环境变量
│   │   ├── api/
│   │   │   ├── deps.py             # 认证与授权依赖
│   │   │   ├── pagination.py       # 分页参数（page_size 上限硬约束）
│   │   │   └── v1/                 # auth / articles / taxonomy / attachments
│   │   │                           # comments / site
│   │   ├── models/                 # SQLAlchemy 2.0 声明式模型
│   │   ├── schemas/                # Pydantic v2 请求/响应模型
│   │   ├── services/               # 业务规则唯一所在地
│   │   ├── repositories/           # 数据访问（只 flush，不 commit）
│   │   ├── db/                     # base / session / types / seed
│   │   └── utils/                  # security / storage / text / exceptions
│   ├── tests/                      # 145 个用例
│   ├── alembic/                    # 迁移
│   ├── storage/                    # 媒体文件（生产挂载卷）
│   ├── pyproject.toml              # 依赖 + ruff + pytest 配置
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── main.ts / App.vue       # 入口与布局分发
│   │   ├── api/                    # HTTP 客户端与各模块接口
│   │   ├── components/             # 12 个可复用组件
│   │   ├── composables/            # useAsyncData / useToast
│   │   ├── layouts/                # Default / Admin / Blank
│   │   ├── router/                 # 路由表 + 守卫
│   │   ├── stores/                 # auth / site / theme
│   │   ├── utils/                  # markdown 渲染 / 格式化
│   │   └── views/                  # 9 个前台页 + 8 个后台页
│   ├── package.json
│   ├── vite.config.ts              # 开发代理 / 手动分包
│   ├── tailwind.config.js
│   └── .env.example
├── deploy/                         # Dockerfile / nginx.conf
├── tools/smoke-check.mjs           # 端到端冒烟验证（CDP 驱动真实浏览器）
├── docs/
│   ├── DESIGN.md                   # 本文档
│   └── screenshots/                # 冒烟验证产出的界面截图
├── docker-compose.yml
├── Makefile                        # make help 查看全部命令
└── README.md
```

### 6.2 分层职责（一句话版）

| 层 | 只做 | 绝不做 |
|---|---|---|
| `api/` | 解析入参、调用 service、返回 schema | 业务判断、直接操作 ORM |
| `services/` | 业务规则、事务内的多步组合 | 依赖 FastAPI、拼 SQL |
| `repositories/` | 数据存取（只 `flush`） | 业务规则、`commit` |
| `models/` | 表结构与关联 | 业务逻辑 |
| `schemas/` | 数据契约与校验 | 业务逻辑 |
| `db/session.py` | 事务边界（一请求一事务） | — |

### 6.3 验证记录

#### 后端静态检查与测试

```
$ ruff check .             → All checks passed!
$ ruff format --check .    → 65 files already formatted
$ pytest                   → 145 passed in 103.55s
```

#### 数据库迁移

```
$ alembic revision --autogenerate -m "initial schema"
  → 生成 8 张表 + 全部索引，并由 post-write hook 自动 ruff format
$ alembic upgrade head
  → alembic_version / article_tags / articles / attachments /
    categories / comments / site_profile / tags / users
$ alembic upgrade head     → 幂等，无重复执行
$ alembic downgrade base   → 全部表干净清除
```

#### 前端构建

```
$ vue-tsc --noEmit         → 无报错
$ vite build               → 成功
  index-DDFhdaLY.js        77.38 kB │ gzip: 29.17 kB
  vendor-B76VgHBK.js      110.78 kB │ gzip: 43.18 kB
  markdown-BbFTrWIB.js    233.32 kB │ gzip: 77.56 kB
  其余 22 个路由级 chunk 按需加载，合计 637 KB
```

#### 端到端冒烟（真实 Chrome，CDP 驱动）

脚本：`tools/smoke-check.mjs`（`make smoke`）。后端 `uvicorn` + 前端 Vite dev server 真实运行，
用 Chrome DevTools Protocol 逐页打开、**轮询等待条件**、读取 DOM 并截图。
**34 / 34 全部通过，页面脚本错误 0 条。**

| 检查项 | 结果 |
|---|---|
| 首页渲染文章卡片 / 站点标题 / 侧边栏 | ✅ 4 篇 |
| 排序参数（`?sort=hottest`）生效 | ✅ |
| 详情页正文渲染 + 代码高亮 + 目录生成 + 评论区挂载 | ✅ 2 个高亮块 / 6 个目录项 |
| 归档页年月分组 | ✅ 2 个月份 |
| 标签云 / 关于页（数据来自站点档案）/ 404 兜底页 | ✅ |
| 登录页渲染 → 真实登录 → 进入后台并加载统计数字 | ✅ |
| 后台文章列表（真实调 `/articles/manage/list`） | ✅ 4 行 |
| 后台编辑器 / 分类标签 / 媒体库 / 用户 / 设置 / 评论 | ✅ 全部达标 |
| 暗色主题（`<html class="dark">` 生效、配色切换） | ✅ |
| 移动端（390×844）汉堡菜单出现 | ✅ |

截图产物见 `docs/screenshots/`：

| 文件 | 内容 |
|---|---|
| `01-home.png` | 前台首页（列表 + 分页 + 排序 + 侧边栏） |
| `02-detail.png` | 文章详情（正文 / 代码高亮 / 目录 / 上下篇） |
| `04-admin-dashboard.png` | 后台仪表盘（统计数字） |
| `05-admin-articles.png` | 后台文章列表 |
| `06-admin-editor.png` | Markdown 编辑器 |
| `08-home-dark.png` | 暗色主题首页 |
| `09-home-mobile.png` | 移动端（390×844） |

> **为什么值得单独做这一层**：上面第 6.4 节记录的两个前端 bug，
> 单元测试、`vue-tsc` 类型检查、`vite build` **全部是绿的**，但真实打开页面就是坏的。
> 只有真的用浏览器跑一遍才可能发现——这也是这个脚本被纳入仓库而不是用完即弃的原因。

#### 测试覆盖范围（后端）

| 测试文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `test_auth.py` | 24 | 登录（用户名/邮箱/表单）、`me`、refresh 流程、密码修改、bcrypt 字节上限、用户管理、最后管理员保护、**token 载荷不含敏感字段** |
| `test_articles.py` | 43 | 创建（草稿/发布/slug 去重/标签自建）、分页数学、超范围页码、排序（latest/oldest/hottest/置顶）、筛选（关键词/通配符转义/标签/分类/作者）、详情可见性（草稿 404）、阅读量自增、上下篇、部分更新、清空可空字段、越权改删 |
| `test_comments.py` | 21 | 游客评论审核、登录用户自动取名、关闭评论、两级回复、跨文章回复拦截、可见性（游客看不到待审）、审核通过/撤下、级联删除、评论计数 |
| `test_taxonomy.py` | 19 | 分类/标签 CRUD、重名冲突、删除分类保留文章、计数口径一致、标签云排序、空标签清理 |
| `test_attachments.py` | 14 | PNG/JPEG 上传与尺寸、缩略图、**文件真实可访问**、伪装类型拒绝、SVG 拒绝、超限 413、空文件、可执行/HTML 扩展名拒绝、媒体库隔离、删除同时删磁盘 |
| `test_site.py` | 24 | 健康检查、OpenAPI 路由完整性、站点档案读写、布尔开关、统计数字、**跨模块级联** |

### 6.4 开发期踩到的坑（已修复，留档备查）

| 现象 | 根因 | 处理 |
|---|---|---|
| 新建文章返回 500 `MissingGreenlet` | `article.tags = tags` 时 SQLAlchemy 为算差集触发**惰性加载**，异步会话下同步 IO 会炸 | 创建时把关系对象直接传给构造函数；更新时先 `refresh(article, ["tags"])` 再赋值 |
| 详情页 500，报 `updated_at` 提取失败 | 批量 `UPDATE` 默认会话同步策略把带 `onupdate` 的列**标记为过期**，异步下回查即炸 | 加 `.execution_options(synchronize_session=False)`，并用 `set_committed_value` 把新值告知 ORM |
| 同一时间字段序列化结果有时带 `Z` 有时不带 | SQLite 读出 naive datetime，应用写入的是 aware datetime | 自定义 `UTCDateTime` 类型，读出一律补 UTC |
| 评论创建后序列化 500 | `comment.replies` 未加载就被访问 | 加 `_is_loaded()` 守卫：只序列化已加载的关系，绝不用惰性加载去补 |
| 归档接口 500 | `await f()[:n]` 的运算符优先级写错，对协程取了下标 | 先 `await` 再切片 |
| 删除标签后文章仍关联 | SQLite `PRAGMA foreign_keys` 默认 OFF，`ON DELETE CASCADE` 形同虚设 | 在 `connect` 事件里显式 `PRAGMA foreign_keys=ON` |
| `alembic` 任何命令都报 `UnicodeDecodeError: 'gbk' codec` | Alembic 用 `configparser` 读 `alembic.ini`，编码取 `locale`，在中文 Windows 上是 GBK，读不了 UTF-8 中文注释 | `alembic.ini` 全文件保持 ASCII，注释改英文（其他配置文件如 `.env` 由 pydantic-settings 显式按 UTF-8 读，不受影响） |
| `alembic revision` 报 `Can't locate timezone: Asia/Shanghai` | `timezone` 选项需要 IANA tzdata，Windows 默认 Python 不自带 | 去掉该选项，用本地时间即可 |
| `alembic upgrade head` 报 `NameError: name 'app' is not defined` | autogenerate 把自定义类型 `UTCDateTime` 渲染成 `app.db.types.UTCDateTime(...)`，但迁移脚本没有 import app | 在 `env.py` 里加 `render_item` 钩子，把它渲染成 `sa.DateTime(timezone=True)`（DDL 层面两者完全等价） |

#### 两个只有在真实浏览器里才暴露的前端 bug

这两个都是**功能全对、单元测试和构建全绿**却真实影响用户的问题，靠读代码和跑构建都发现不了，
必须用真实浏览器打开页面才能看到。它们是"端到端冒烟"这一层存在的意义。

**① 登录后访问后台，会被莫名弹回登录页（并发竞态）**

网络面板显示 `/api/v1/auth/me` 明明返回了 **200**，localStorage 里 token 也还在，
但用户就是被踢到了 `/login?redirect=/admin`。

根因是 `stores/auth.ts` 里 `restore()` 的写法：

```ts
// 错误写法
async function restore(force = false) {
  if (restoring.value) return   // ← 发现有人在做，直接返回，不等待
  ...
}
```

触发链路：

1. `main.ts` 在 `router.isReady()` 之前就 `app.mount()`，Vue 先按「起始路由」渲染出 `DefaultLayout`；
2. `DefaultLayout.onMounted` 调起 `restore()`，`/auth/me` 起飞；
3. vue-router 加载完 `/admin` 的**懒加载布局 chunk** 之后，路由守卫才运行；
4. 守卫也调 `restore()`，命中 `if (restoring.value) return` —— **直接返回，没等结果**；
5. 守卫立刻读到 `user === null` → 判定未登录 → 跳登录页。

它只在「布局 chunk 加载慢于组件挂载」时出现，所以是间歇性的，手动点很难复现。

修法（两处，都必要）：

- `restore()` 改为**并发安全**：并发调用方共享并等待同一个 Promise，而不是直接返回；
- `main.ts` 改为 `router.isReady().then(() => app.mount('#app'))`，从根上消除「挂载早于守卫」的窗口，
  顺带也解决了后台页面会先闪一下前台布局的问题。

**② 后台侧边栏渲染两遍，且子页面完全不显示**

现象很怪：`/admin/articles` 的 URL 和标题（「文章管理」）都对，但列表是空的，
而且侧边栏出现了两次 —— 而且**列表接口压根没被调用**。

根因是「布局用 meta 表达」和「布局用路由嵌套表达」两种方式被混用了：

```ts
// 错误写法
{
  path: '/admin',
  component: () => import('@/layouts/AdminLayout.vue'),  // ← 布局同时作为路由组件
  meta: { layout: 'admin' },                              // ← 又声明了 meta 布局
  children: [...]
}
```

于是渲染成了两层嵌套：`App.vue` 按 `meta.layout` 包一层 `AdminLayout`，
里面那层 `<router-view>` 又把 `AdminLayout` 当作路由组件渲染了第二遍。
而**内层实例是被 router-view 渲染的，拿不到任何 slot 内容**，
所以 `ArticleListView` 永远没被挂载 —— 接口自然也不会发。

修法：父路由**不写 `component`**，布局只由 `meta.layout` 一处表达。

> 教训：约定要么统一走 meta，要么统一走路由嵌套，两者混用必然重复且症状离奇
> （URL 对、标题对、就是内容不出来）。

### 6.5 后续可扩展方向

| 方向 | 说明 |
|---|---|
| 全文检索 | 当前用 `LIKE` 模糊匹配。文章量上万后建议上 PostgreSQL 的 `tsvector` + GIN 索引，或接入 Meilisearch |
| 图片处理 | 接入对象存储（S3/COS）+ 按需裁剪（`?w=800`），减轻服务器带宽 |
| 评论反垃圾 | 接入 Akismet，或加基于 IP + 时间窗的限流 |
| 文章版本历史 | 新增 `article_revisions` 表，支持回滚 |
| 可访问性审计 | 补齐 ARIA 标注，用 axe / Lighthouse 跑一轮 |

### 6.6 迭代记录

**2026-09-12 第三批（`c2bc4b6`）**：主题三态（亮/暗/跟随系统）、阅读进度条、
highlight.js 按需注册（markdown chunk 233KB → 90KB）、正文行高 1.85 → 1.75。

**2026-09-12 第四批（`f0e6e47`）**：SEO 与内容发现。

- `GET /feed.xml` —— RSS 2.0，最近 20 篇已发布，绝对链接（`SITE_BASE_URL` 配置项），`Cache-Control: 1h`
- `GET /sitemap.xml` —— 静态页 + 最近 1000 篇文章，含 `lastmod`
- `GET /api/v1/articles/{id}/related` —— 相关文章：同分类或共享标签，按发布时间倒序，排除自身与草稿
- 前端 `useHead` composable：每页标题 + `og:*` / `twitter:card` meta。
  与路由 afterEach 的分工是「路由标题为底、业务标题为盖」，卸载时交还路由层
- 详情页新增「相关阅读」区块；ArticleCard 封面改 `aspect-*` 占位消除布局跳动；
  页脚 RSS 链接 + `index.html` RSS autodiscovery；vite/nginx 增加 `/feed.xml`、`/sitemap.xml` 路由

> SPA 说明：per-page OG meta 是 JS 写入的，对不执行 JS 的分享卡片抓取器不可见。
> 要彻底解决需 SSR/预渲染（Astro/Nuxt），属重写级成本，暂未采纳。
