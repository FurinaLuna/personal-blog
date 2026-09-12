# 项目全面评估

> 评估时间：2026-09-12 · 基线提交 `d850661`
> 方法：依赖新鲜度实测（pip index / npm outdated / npm audit）、覆盖率实测
> （pytest-cov 逐文件）、代码扫描（规模 / 类型注解 / 空捕获 / TODO）、
> 关键链路源码复核（认证、索引、分页、缓存头）。

## 0. 体检数据

| 维度 | 实测值 |
|---|---|
| 规模 | 后端 67 个 Python 文件、前端 38 个 Vue + 23 个 TS 文件，约 12,600 行（不含测试） |
| 后端测试 | **171 passed**，覆盖率 **81%**（2281 语句 / 未覆盖 424） |
| 前端测试 | **40 passed**（3/5 composables、1/3 utils，**组件层 0 测试**） |
| 端到端 | 冒烟 34 项 + 交互 16 项（均需本地起服务，**未接入 CI**） |
| 依赖漏洞 | 前端生产依赖 **0 漏洞**（官方 registry 实测）；Python 侧未跑 pip-audit |
| 后端依赖 | 基本全为最新（仅 alembic 1.19.2→1.20.0、pyjwt 2.13→2.14 两个 minor） |
| 前端依赖 | **8 个包落后一个主版本**（见 §2） |
| 代码卫生 | `TODO/FIXME` 0 处、`any` 0 处、无返回类型注解的 service 方法 0 处 |
| 构建产物 | vendor gzip 43KB / markdown gzip 31KB + 30KB / 主包 gzip 30KB / CSS gzip 9.6KB |

**结论先行**：工程底子扎实（分层清晰、类型完整、测试有量、无 TODO 积压），
主要短板集中在**上线安全门禁**、**依赖主版本落后**、**测试覆盖分布不均**、
**部分链路未经验证**这四块。

---

## 1. 现存问题与风险

### P0 — 上线前必须处理

| # | 问题 | 证据 | 影响 | 成本 |
|---|---|---|---|---|
| 1.1 | **没有生产启动门禁**：`is_production` 只用来开关 `/docs`，用默认 JWT 密钥、默认管理员密码、`DB_AUTO_CREATE=true` 照样能启动 | `config.py:115`、`main.py:124-125`，全仓仅此两处使用 | 一个 `.env` 没改就上线的实例，等于把管理员账号和签名密钥公开在 GitHub 上 | 低 |
| 1.2 | **refresh token 不可吊销、不轮换**：`refresh()` 只验签 + 查用户后直接签发新 token，旧 refresh token 在 7 天内一直可用；改密码也不会让已泄露的 token 失效 | `auth_service.py:79-91`、`change_password` 未触碰任何吊销状态 | Token 泄露后无法止损；改密码这个"应急动作"实际无效 | 中 |
| 1.3 | **Docker 部署链路从未实测**：`docker-compose.yml` / `Dockerfile × 2` / `nginx.conf` 写好了但没在本机跑过一次 | 本会话内所有验证都是本地 uvicorn + vite；`docker compose up` 未执行 | 上线当天才发现镜像构建失败或 nginx 路由不对，属于"把风险留到最后一刻" | 低（只需跑一次） |

### P1 — 应尽快处理

| # | 问题 | 证据 | 影响 | 成本 |
|---|---|---|---|---|
| 1.4 | **前端 8 个依赖落后一个主版本**（vite 6→8、tailwind 3→4、pinia 2→4、vue-router 4→5、typescript 5.7→7、marked 15→18、vue-tsc 2→3、plugin-vue 5→6） | `npm outdated` 实测 | 拖得越久迁移面越大；且安全补丁只回补近期版本线 | 中（分批） |
| 1.5 | **CI 不跑端到端**：CI 只有单测/类型/构建，没有 smoke 与 interaction | `.github/workflows/ci.yml` 无相关 job | 项目里最值钱的两个脚本（抓出过"登录被弹回"这类 bug）在流水线上是空白 | 中 |
| 1.6 | **无 ESLint / Prettier**：前端唯一守门人是 `vue-tsc` | `frontend/package.json` 无相关依赖 | 类型正确但风格漂移、易踩 `no-floating-promises` 之类的坑，review 靠人眼 | 低 |
| 1.7 | **评论接口无分页**：`list_for_article()` 一次返回整棵评论树 | `comment_service.py:34` 返回 `list[CommentRead]` | 单篇热门文章评论上千条时，响应体与渲染时间同时失控 | 中 |
| 1.8 | **公开 GET 接口无缓存头**：除 `feed.xml`/`sitemap.xml` 外全站无 `ETag`/`Cache-Control` | 全仓 grep 仅命中 feed | 每次访问都全量查询与传输；CDN/浏览器无法复用 | 低 |

### P2 — 可排期处理

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1.9 | **覆盖率分布不均**：`feed_service` **45%**、`comment_service` 46%、`taxonomy_service` 52%、`article_service` 60%、`taxonomy_repository` 63%、`auth_service` 63%、`db/seed` 59% | pytest-cov 逐文件 | 高风险模块（认证、评论审核）恰是覆盖较薄的地方 |
| 1.10 | **搜索是三个字段 `ilike`，其中 `content_md` 无索引** | `article_repository.py:129-131` | 文章上百篇后搜索退化为全表扫描；且无法按相关度排序 |
| 1.11 | **图片无显式 `width`/`height`**：5 个 `<img>` 只有 1 处用 `aspect-*` 占位 | 前端 grep | 图片到达时仍有布局跳动（CLS） |
| 1.12 | **限流是进程内的**：多 worker 部署时每个 worker 各算一份配额 | `utils/ratelimit.py`（已在 SECURITY.md 声明） | 水平扩容后限流形同放宽 N 倍 |
| 1.13 | **无审计日志**：谁在什么时候改了哪篇文章/删了哪个用户，数据库里看不出来 | 无相关表 | 多作者协作时无法追责；运营上也无从复盘 |
| 1.14 | **Python 侧供应链未检查**：没有 pip-audit，Dependabot 刚加上但未验证生效 | CI 无审计步骤 | Python 依赖漏洞盲区 |

---

## 2. 版本与技术升级

### 2.1 前端（本次评估中唯一"落后一个主版本"的地方）

| 包 | 当前 | 最新 | 破坏性 | 迁移要点 |
|---|---|---|---|---|
| tailwindcss | 3.4.19 | 4.3.3 | **高** | v4 改为 CSS-first 配置，`tailwind.config.js` 与 `postcss` 链路要重写；语义色变量方案需重新表达 |
| vite | 6.4.3 | 8.3.0 | 中 | 要求更高 Node 版本；`manualChunks` 写法与部分插件 API 有变 |
| @vitejs/plugin-vue | 5.2.4 | 6.0.8 | 中 | 跟随 vite 主版本，需同批升级 |
| typescript | 5.7.3 | 7.0.2 | **高** | 跨两个主版本，严格模式默认值变化，可能新增大量类型报错 |
| vue-tsc | 2.2.12 | 3.3.11 | 中 | 与 TS 版本强绑定，必须与 TS 同批 |
| pinia | 2.3.1 | 4.0.3 | 中 | store 定义 API 有调整（本项目 3 个 store，量小可控） |
| vue-router | 4.6.4 | 5.3.1 | 中 | 守卫返回值语义收紧过，需复核 `beforeEach` |
| marked | 15.0.12 | 18.0.12 | 中 | 跨 3 个主版本；本项目只用了 `parse` + 选项，影响面小 |

**建议顺序**（每步单独一个 PR，跑完 `make check` + `make build` + `make smoke` 再进下一步）：

1. `marked` → 18（改动面最小，先热身）
2. `vue-router` → 5 + `pinia` → 4（应用内改动可控，测试能兜住）
3. `vue-tsc` → 3 + `typescript` → 7（**一次性做完**，两者强绑定）
4. `vite` → 8 + `plugin-vue` → 6
5. `tailwind` → 4（**放最后**：它影响全站每一个组件，前四步稳定后再动它）

### 2.2 后端

只差两个 minor，建议直接升级（无破坏性）：

- `alembic` 1.19.2 → 1.20.0
- `pyjwt` 2.13.0 → 2.14.0

同时建议补 `pip-audit` 到 CI，与前端 `npm audit` 对称。

### 2.3 技术选型层面的建议

| 现状 | 建议 | 理由 |
|---|---|---|
| SQLite 默认 + 可选 PostgreSQL | 保持 | 个人博客的数据量级用不上更重的东西；迁移路径已铺好 |
| 限流在进程内 | 单实例部署保持；确定要水平扩容时再换 Redis | 现在换是"为想象中的规模付费" |
| 搜索用 `LIKE` | 换成 **SQLite FTS5 虚拟表**（Postgres 则用 `tsvector`） | FTS5 是 SQLite 内置能力，成本远低于引入 Meilisearch |
| 前端无 lint | 加 **ESLint（flat config）+ Prettier** | 配合 `eslint-plugin-vue` 的 `vue3-recommended`，成本半小时 |
| 无 Python 依赖审计 | CI 加 `pip-audit` | 与前端对称，覆盖供应链 |

---

## 3. 值得新增的功能

按「对个人博客的实际价值 ÷ 实现成本」排序：

| # | 功能 | 价值 | 成本 | 备注 |
|---|---|---|---|---|
| 3.1 | **全站导出/备份**：一键导出全部文章为 Markdown + 附件 zip | ★★★★★ | 低 | 防供应商锁定，也是唯一可靠的灾备手段；数据在自己手里才是自己的博客 |
| 3.2 | **定时发布**：`published_at` 设为未来时间，到点自动可见 | ★★★★☆ | 低 | 模型已有 `published_at`，只需查询条件加 `published_at <= now`；写作节奏立刻自由 |
| 3.3 | **图片多尺寸 + AVIF** | ★★★★☆ | 中 | 上传时用 Pillow 生成 480/800/1600 三档，列表与详情走 `srcset`；移动端流量立省一半 |
| 3.4 | **全文检索（FTS5）** | ★★★★☆ | 中 | 先解决 §1.10 的性能问题，顺带获得相关度排序与高亮 |
| 3.5 | **文章系列/合集** | ★★★☆☆ | 中 | 技术连载的完读率；详情页多一条系列导航 |
| 3.6 | **审计日志** | ★★★☆☆ | 中 | 解决 §1.13；后台多一个「操作记录」页 |
| 3.7 | **评论邮件通知**（含退订） | ★★★☆☆ | 中 | 评论区从死胡同变成对话；需 SMTP 配置与退订链接 |
| 3.8 | **统计趋势**：后台出近 30 天阅读/评论曲线 | ★★★☆☆ | 中 | 现在只有累计计数，看不出趋势 |
| 3.9 | **阅读量去重**（按 IP/会话日粒度） | ★★☆☆☆ | 低 | 当前计数可被刷新刷高，数字参考价值有限 |
| 3.10 | **友链/Webmention** | ★★☆☆☆ | 中 | 独立博客生态；有闲再做 |

**明确不建议做**：

- **SSR / 预渲染重写**（Astro/Nuxt）—— 唯一能彻底解决 OG 抓取与首屏 SEO 的方案，
  但等于重写前台。除非 SEO 是核心目标，否则不划算
- **引入 Redis / 消息队列 / 微服务** —— 单实例博客没有这些要解决的问题
- **多语言 / 多租户 / 复杂权限** —— 与"个人博客"的定位相悖

---

## 4. 性能与代码优化

### 4.1 性能（按收益排序）

| # | 优化 | 预期收益 | 成本 |
|---|---|---|---|
| 4.1.1 | **详情页代码高亮懒加载**：hljs（约 60KB）改为 `IntersectionObserver` 触发后再动态 `import`，首屏无代码块时不加载 | 详情页首屏少约 60KB 阻塞资源 | 中 |
| 4.1.2 | **公开 GET 加 `ETag` + `Cache-Control: public, max-age=60`**（文章列表/详情/标签） | 二次访问走 304；CDN 可命中 | 低 |
| 4.1.3 | **评论分页**：先返回前 20 条 + 游标加载更多（同时解决 §1.7） | 热门文章响应体积与渲染时间可控 | 中 |
| 4.1.4 | **图片多尺寸 + `srcset`**（同 §3.3） | 移动端下载量减半；消除 CLS | 中 |
| 4.1.5 | **首页三请求合并**：`articles` + `tags` + `categories` → 一个聚合接口（或至少并行） | 首屏往返次数 3 → 1~2 | 低 |
| 4.1.6 | **长列表 `content-visibility: auto`**：首页与后台列表 | 首屏渲染开销下降（大列表越明显） | 低 |
| 4.1.7 | **CSS 瘦身**：`@tailwindcss/typography` 是全量引入，可裁剪未用的 prose 变体 | 可能省 10–20KB | 低 |
| 4.1.8 | **确认 SQLite 是否开启 WAL**（`PRAGMA journal_mode`），读写并发更好 | 写入期间读不被阻塞 | 低 |

### 4.2 代码与工程

| # | 优化 | 说明 | 成本 |
|---|---|---|---|
| 4.2.1 | **补前端组件层测试**：`ArticleCard`、`Pagination`、`ConfirmDialog` 等纯展示组件用 `@vue/test-utils` 快照 + 交互断言 | 现在 40 个用例全在 composables 与 utils，组件层完全裸奔 | 中 |
| 4.2.2 | **提覆盖薄点**：`feed_service` 45% / `comment_service` 46% / `taxonomy_service` 52% / `auth_service` 63% | 恰是业务风险最高的几块；建议先补 auth 与 comment 的失败路径 | 中 |
| 4.2.3 | **拆 `ArticleEditView.vue`（452 行）**：抽出 `useArticleForm`（表单状态与校验）与 `CoverUploader` 子组件 | 当前全项目最大的单文件，且承载了草稿、上传、标签、保存四条逻辑 | 中 |
| 4.2.4 | **`article_service.py` 读写分离**（412 行 / 22 方法） | 读路径与写路径可独立演进；注意测试只增不改 | 中 |
| 4.2.5 | **抽公共分页构造**：`Page.build` 在 4 处重复调用，参数拼装逻辑相似 | 减少一处改动要改 N 个文件的情况 | 低 |
| 4.2.6 | **拆 `types/index.ts`（278 行）** 为 `article.ts` / `user.ts` / `site.ts` | 与后端 schema 一一对应，改动定位更快 | 低 |
| 4.2.7 | **图片补 `width`/`height` 属性**（同 §1.11） | 消除 CLS，且是 Web 规范的硬要求 | 低 |
| 4.2.8 | **给 `except Exception`（5 处）逐个复核**：确认每处都是"确实要宽捕获"，并补注释 | 现在是 5 处，语义上都合理，但要留下判断依据 | 低 |

---

## 5. 优先级排序（推荐执行顺序）

### 第一优先：上线阻断项（半天 ~ 1 天）

1. **§1.1 生产启动门禁** —— 在 `create_app()` 里加校验：`APP_ENV=production` 时若
   `JWT_SECRET_KEY` 仍是默认值 / `ADMIN_PASSWORD` 仍是默认值 / `DB_AUTO_CREATE=true`
   → **拒绝启动并给出明确修复指引**（fail fast 优于带病运行）
2. **§1.3 实测 Docker 部署链路** —— 跑一次 `docker compose up -d --build` + 迁移 +
   冒烟，把"从未验证"变成"已验证"
3. **§1.2 refresh token 轮换与吊销** —— 加 `token_version`（用户表）或
   `revoked_tokens` 表；刷新时轮换、改密码时递增版本

### 第二优先：工程护栏（1 ~ 2 天）

4. **§1.5 CI 补端到端 job**（用 docker compose 起服务后跑 smoke + interaction）
5. **§1.6 加 ESLint + Prettier**，同时 **§2.2 后端两个 minor 升级 + pip-audit**
6. **§4.2.2 补 auth_service / comment_service 的失败路径测试**（覆盖率薄点 + 高风险区）
7. **§4.1.2 ETag + §4.1.5 首页请求合并**（低成本的性能收益）

### 第三优先：依赖升级（2 ~ 3 天，分批）

8. §2.1 的 `marked` → `vue-router` + `pinia` → `vue-tsc` + `typescript`
   → `vite` + `plugin-vue` → 最后 `tailwind 4`

### 第四优先：功能与深度优化（按需排期）

9. §3.1 全站导出/备份（**强烈建议优先于其他新功能**）
10. §3.2 定时发布 → §3.3 图片多尺寸 → §3.4 FTS5 检索
11. §4.2.1 组件层测试 → §4.2.3 拆编辑器 → §4.2.4 服务层读写分离
12. 其余功能（系列文章 / 审计日志 / 评论通知 / 统计趋势）

---

## 6. 本次评估的未验证项（如实披露）

| 项 | 状态 |
|---|---|
| Python 依赖漏洞 | **未验证**（未安装 pip-audit，属于 §1.14 的待办） |
| Docker 部署链路 | **未验证**（§1.3） |
| 生产环境实际运行表现（多 worker、反代、CDN） | 未验证，本机为单进程 uvicorn |
| 前端组件渲染测试 | 暂无（§4.2.1） |
| 移动端真机（iOS Safari / Android Chrome） | 仅用 CDP 模拟视口验证，未上真机 |

> 这些不是"应该没问题"，而是**确实没验证过**。上面的排序里，
> 前三条正好对应其中风险最高的三项。
