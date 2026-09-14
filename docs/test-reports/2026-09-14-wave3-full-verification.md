# 功能第三波全量测试报告

- **报告日期**：2026-09-14
- **被测功能**：功能第三波三项（1.6 图片多尺寸变体 / 1.4 访问统计趋势 / 1.5 评论邮件通知）
- **被测版本**：`2d93700`（图片多尺寸、统计趋势已提交）+ 工作区未提交的 1.5 评论邮件通知改动
- **测试目标**：正常流程、边界条件、异常场景与错误处理、模块间集成兼容性
- **结论**：**除 1 项回填收敛性缺陷（F1）外全部通过**；累计执行 **469 个自动化用例/检查项**（275 后端 + 86 前端 + 34 冒烟 + 22 交互 + 41 全功能 + 11 UI 专项），未发现数据损坏、越权、崩溃类问题

---

## 1. 测试环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows（PowerShell 5.1） |
| 后端 | Python 3.13.14 / FastAPI / SQLAlchemy 2.0 异步 / SQLite(WAL，`PRAGMA foreign_keys=ON`) |
| 图像库 | Pillow 12.3.0（**本机带 AVIF 编码器**，`PIL.features.check("avif") == True`） |
| 邮件库 | aiosmtplib 5.1.3 |
| 前端 | Node v24.20.0 / Vite 6.4.3 / Vue 3 / vitest 5.0.0 |
| 浏览器 | Chrome 153.0.8010.36（CDP 驱动，未使用 Playwright——本机未安装其浏览器） |
| 服务 | 后端 `127.0.0.1:8000`、前端 `127.0.0.1:5173`（dev 库已 `alembic upgrade head`） |

## 2. 结果总览

| # | 测试层 | 规模 | 结果 |
|---|---|---|---|
| A | 后端 pytest 基线（既有用例） | 239 | ✅ 239 passed |
| B | 后端 pytest 补充边界/异常用例（本次新增） | 36 | ✅ 36 passed |
| C | 后端 pytest 全量回归（A+B 一起） | 275 | ✅ 275 passed |
| D | 前端 vitest | 11 文件 / 86 用例 | ✅ 86 passed |
| E | 静态检查：ruff check + ruff format --check | 全量 | ✅ All checks passed |
| F | 分层契约 import-linter | 77 文件 / 241 依赖 | ✅ 2 contracts KEPT, 0 broken |
| G | 类型检查 vue-tsc --noEmit | 全量 | ✅ 0 error |
| H | 生产构建 vite build | 全量 | ✅ built in 4.38s |
| I | Alembic 迁移往返（隔离临时库） | 5 迁移 / 3 态 | ✅ 升级 → 降级 base → 再升级，无 `_alembic_tmp_*` 残留 |
| J | 浏览器冒烟 smoke-check | 34 项 | ✅ 34 / 34 |
| K | 浏览器交互 interaction-check | 22 项 | ✅ 22 / 22 |
| L | 浏览器全功能 full-check | 41 项 | ✅ 41 / 41，**数据基线：一致 ✅** |
| M | 新功能 UI 专项（CDP 临时脚本） | 11 项 | ✅ 11 / 11 |
| N | dev 库残留核查 | — | ✅ 临时文章/附件/标签全部回收，测试退订记录已清理 |

> 说明：工具脚本在收尾时被沙箱拦下 Chrome 字典文件与 stdlib `.pyc` 的写入，导致进程退出码非 0；**这与断言结果无关**（每个脚本的通过数均已单独打印）。

---

## 3. 1.6 图片多尺寸变体（AVIF/WEBP）

### 3.1 既有用例（`tests/test_attachments.py`）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_wide_image_generates_ascending_variants` | 1920px → 480/800/1600 升序、文件真实可访问 | ✅ |
| `test_small_image_has_no_variants` | 120px 不放大、无变体 | ✅ |
| `test_delete_removes_variant_files` | 删除后变体文件 404 | ✅ |
| `test_backfill_regenerates_for_legacy_image` | 存量图回填成功且列表可见 | ✅ |
| `test_backfill_is_idempotent` | 第二轮 `processed == 0`（无小图/GIF 场景） | ✅ |
| `test_backfill_requires_admin` / `test_guest_cannot_backfill` | 403 / 401 | ✅ |

### 3.2 本次补充用例

| 用例 | 输入 / 场景 | 期望 | 结果 |
|---|---|---|---|
| `test_gif_skips_variants_entirely` | 1920×1080 GIF | 动图不做静态变体，`variants == []` | ✅ |
| `test_middle_tiers_only_for_1000px` | 1000px 宽 | 只出 `[480, 800]`（1600 会放大，跳过） | ✅ |
| `test_width_equal_to_tier_is_not_upscaled` | 800px 宽（**等于档位**） | 只剩 `[480]`（`width <= target` 边界） | ✅ |
| `test_variant_naming_shares_one_stem` | 1920px | 命名为 `{8位hex}-{宽度}.{ext}`，三档同 stem、宽度后缀与 `width` 一致 | ✅ |
| `test_variant_prefers_avif_when_encoder_available` | 有 AVIF 编码器 | 扩展名 `.avif`，且下载后能被 Pillow 真实解码、宽度匹配 | ✅ |
| `test_variant_falls_back_to_webp_without_avif_encoder` | 强制 `features.check("avif")=False` | 降级 WEBP（三档齐全、文件可解码为 WEBP） | ✅ |
| `test_delete_also_removes_thumbnail` | 上传后删除 | 缩略图 URL 由 200 → 404 | ✅ |
| `test_list_api_exposes_ascending_variant_urls` | 媒体库列表 | `variants` 升序、URL 前缀 `/media/uploads/`、逐个可访问 | ✅ |
| `test_limit_validation_bounds` | `limit=0 / 101 / 1` | 0 与 101 → 422；1 → 200 | ✅ |
| `test_skips_when_original_file_missing` | 原文件被删（存储损坏） | `skipped ≥ 1`、不 500、`variants` 保持 NULL | ✅ |
| `test_skips_when_original_file_corrupted` | 原文件被写入垃圾字节（**数据错误**） | 同上，且不打断整轮回填 | ✅ |
| `test_gif_is_selected_then_skipped` | GIF 上传（`variants` 落库为 NULL） | 必然被回填命中 → `processed ≥ 1` 且 `skipped ≥ 1` | ✅ |
| `test_backfill_never_converges_while_small_images_exist` | 库里存在 120px 小图 | 第二轮 `processed` 仍 ≥ 1 → **回填不收敛** | ✅（发现 F1） |
| `test_fixed_limit_can_starve_real_legacy_image` | 小图 id 更小 + 存量大图，`limit=1` | 批额度被小图占满，大图永远轮不到 → **饿死** | ✅（发现 F1） |

### 3.3 集成（封面变体装配）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_article_list_and_detail_carry_cover_variants` | 文章**列表**与**详情**都返回 `cover_variants` 三档且可访问（列表一次 IN 查询，无 N+1 查询报错） | ✅ |
| `test_external_cover_image_has_no_variants` | 外链封面不查库、`cover_variants == []`，不报错 | ✅ |
| `test_backfill_after_variant_column_reset_keeps_urls_consistent` | 回填出的变体与文章 `cover_variants` 指向同一份文件、URL 空间一致 | ✅ |

---

## 4. 1.4 访问统计趋势

### 4.1 既有用例（`tests/test_stats.py`）

详情访问 3 次记 3 条 / 同 IP 的 UV=1 / 草稿预览不记 / 归档文章不记（与 view_count 同口径）/ 列表接口不记 / `days=7` 缺日补零且日期不重不漏 / `days=0|91` → 422 / author 403 / 游客 401 —— **全部 ✅**

### 4.2 本次补充用例

| 用例 | 输入 / 场景 | 期望 | 结果 |
|---|---|---|---|
| `test_days_lower_and_upper_bounds_are_valid` | `days=1` / `days=90` | 恰好 1 条 / 90 条（边界值合法） | ✅ |
| `test_days_non_integer_rejected` | `days=abc` / `1.5` / 空 | 全部 422 | ✅ |
| `test_missing_article_is_not_recorded` | 访问不存在的 slug（404）后再访问真实文章 | 只记 1 条（404 不计入） | ✅ |
| `test_multiple_articles_aggregate_into_one_day` | 两篇文章共 3 次访问 | 当日 `views == 3`、`uv == 1` | ✅ |
| `test_distinct_ips_count_distinct_uv` | 打开 `trust_proxy_headers`，注入 2 个不同 XFF（`...7` 两次、`...9` 一次） | `views == 3`、`uv == 2`（distinct 真实生效） | ✅ |
| `test_visit_logs_cascade_when_article_deleted` | 记 1 次访问后删除文章 | 当日 `views` 回到 0（FK CASCADE 真生效） | ✅ |
| `test_series_is_iso_dates_ascending_ending_today` | `days=30` | 30 条、日期 `YYYY-MM-DD`、升序、末条为今天(UTC) | ✅ |

> 说明：`test_distinct_ips_count_distinct_uv` 是本轮唯一需要打开 `TRUST_PROXY_HEADERS` 的用例——该开关默认关闭（防伪造），正因如此才需要显式注入来源 IP 来验证 UV 去重。

---

## 5. 1.5 评论邮件通知

### 5.1 既有用例（`tests/test_notifications.py`，mock 发信器）

退订 token 往返与小写归一 / 退订幂等 / 垃圾 token 400 / access token 冒充退订 token 400 / 待审回复只通知站长 / 过审后才通知被回复者 / 退订后不再发复信通知 / 站长回复不给自己发信 / 自己回自己不重复发 / SMTP 关闭零调用 / 复信含文章链接 / 发信器抛异常评论仍 201 —— **全部 ✅**

### 5.2 本次新增：**本地真实 SMTP 服务器收信**（真实 aiosmtplib 网络传输）

用一个零依赖的极简 SMTP 收信服务器（支持 `EHLO` / `AUTH PLAIN` / `AUTH LOGIN` / `MAIL FROM` / `RCPT TO` / `DATA` / `QUIT`）承接真实发信，并用 `email` 模块解析原始 RFC822 报文。

| 用例 | 场景 | 期望 | 结果 |
|---|---|---|---|
| `test_full_notification_journey_over_real_smtp` | 完整闭环 5 步 | ① 顶级待审评论 → 站长收信（含 `/admin/comments?approved=false`、正文含「待审核」）；② 过审顶级评论 → 不再发信；③ 回复待审 → 只提醒站长；④ 过审回复 → 被回复者收到「您的评论收到了新回复」（含回复人昵称、文章链接）；⑤ **从真实邮件正文正则抠出退订 token 并真的调用退订接口** → 落库 `notification_opt_out`；再发新回复过审 → 该邮箱不再收信（站长提醒照常） | ✅ |
| `test_chinese_subject_is_rfc2047_encoded` | 中文主题 | 原始报文头为 `=?utf-8?b?...?=`（RFC2047 编码），解码后为「新评论提醒：<文章标题>」 | ✅ |
| `test_connection_refused_does_not_break_comment` | SMTP 端口无人监听（**网络异常**） | 评论仍 201，后台任务静默失败 | ✅ |
| `test_auth_failure_does_not_break_comment` | 服务器 `535` 拒绝认证 | 评论仍 201；**一封信也发不出去**（不重试、不串号） | ✅ |
| `test_server_drops_connection_does_not_break_comment` | 服务端接受连接后立刻断开 | 评论仍 201，客户端失败得干净 | ✅ |
| `test_notify_for_missing_comment_is_noop` | 评论已被删（极端时序） | 任务按 id 查不到时安静退出，不产生「Task exception was never retrieved」噪音 | ✅ |
| `test_reply_to_admin_comment_sends_nothing_to_admin` | 游客回复**站长**的评论 | 不发复信通知（站长评论无 email），仅发「新评论提醒」 | ✅ |

### 5.3 退订接口边界与异常

| 用例 | 输入 | 期望 | 结果 |
|---|---|---|---|
| `test_empty_token_rejected` | `token=""` | 422（`min_length=1`） | ✅ |
| `test_expired_token_rejected` | 已过期签名 token（`exp` 为昨天） | 400 | ✅ |
| `test_token_signed_with_other_secret_rejected` | 换密钥伪造签名 | 400 | ✅ |
| `test_tampered_signature_rejected` | 篡改签名首字符 | 400 | ✅ |
| `test_case_variants_share_one_opt_out_row` | 同一邮箱 `UPPER` + `lower` 两次退订 | 两次都 200，库中**只有 1 行**且为小写 | ✅ |
| `test_wrong_token_type_rejected`（既有） | 用登录 access token 冒充 | 400 | ✅ |

---

## 6. 前端与浏览器端

| 项 | 内容 | 结果 |
|---|---|---|
| 单元测试 | `TrendChart.spec.ts`（点数 / 空态 / 全零不 NaN / 峰值归一）+ 既有 11 文件 | ✅ 86 passed |
| 冒烟 34 项 | 首页、排序、详情、归档、标签、关于、404、登录、后台 6 页、暗色、移动端、零脚本错误 | ✅ 34/34 |
| 交互 22 项 | 登录失败内联提示、审核 toast、状态切换、窄屏卡片、草稿自动保存/恢复/放弃、评论全链路 | ✅ 22/22 |
| 全功能 41 项 | 后台写操作生命周期、列表筛选边界、详情交互、认证与主题、站点元信息 | ✅ 41/41，数据基线一致 |
| **1.6 UI 专项** | 首页封面 `srcset` 三档 + `sizes="(min-width: 640px) 160px, 100vw"`、`src` 仍指向原图兜底、媒体库「回填变体」按钮（仅站长，含说明 title）、媒体库「变体 480/…」文案、点击按钮 toast 反馈、**相关阅读缩略图 `srcset` + `sizes="80px"`** | ✅ 6/6 |
| **1.4 UI 专项** | 仪表盘「访问趋势（近 30 天）」标题、`svg[aria-label="访问趋势图"]`、30 个数据点、UV 虚线、合计阅读数显示 | ✅ 2/2 |
| **1.5 UI 专项** | `/unsubscribe` 三态：无 token →「退订链接不完整」/ 合法 token →「已退订」/ 非法 token →「退订没有成功」 | ✅ 3/3 |

页面脚本错误：全流程 **0 条**。

---

## 7. 发现的问题

### F1（中低危 · 缺陷）回填变体永不收敛，且固定批额度会让真正的存量图饿死

**现象**
1. 只要媒体库里存在**任何小图（宽 ≤ 480）或 GIF**，`POST /attachments/backfill-variants` 的 `processed` 永远不会归零，每轮都会重复「处理」同一批记录；
2. 当这类**永远回填不了**的记录条数 ≥ 批大小（默认 100）且 id 小于真正的存量图时，后者永远拿不到批额度。

**根因**
- 上传时 `variants = variants or None`（[attachment_service.py](file:///d:/Projects/01_个人项目/personal-blog/backend/src/app/services/attachment_service.py#L129)）：小图/GIF 的 `made` 为空 dict，落库即 **NULL**；
- 而回填的候选筛选条件恰是 `variants IS NULL`（[attachment_repository.py](file:///d:/Projects/01_个人项目/personal-blog/backend/src/app/repositories/attachment_repository.py#L52-L61)），`ORDER BY id LIMIT n`；
- 结果：「未处理」与「处理过但无需变体」两种状态**无法区分**。

**影响**
- 前端提示与后端语义矛盾：`frontend/src/api/attachments.ts` 注释称「可反复调用直到 processed 为 0」，`MediaView.vue` toast 称「还剩待处理可再次运行」——两者都不成立；
- 每轮都做无谓的磁盘读 + 解码（性能浪费）；
- 存量图被饿死时，功能实际失效（用户点了「回填变体」却永远补不上）。

**复现步骤**
```powershell
cd d:\Projects\01_个人项目\personal-blog\backend
# 1) 造一张小图（宽 120 ≤ 最小档位 480）
#    通过上传接口：POST /api/v1/attachments/upload（author 身份，120x90 PNG）
# 2) 连点两次回填
#    POST /api/v1/attachments/backfill-variants  → processed>=1, updated=0
#    POST /api/v1/attachments/backfill-variants  → processed 仍 >=1（应为 0）
# 3) 饿死场景：小图(id 小) + 清空 variants 的 1920px 大图(id 大)
#    POST /api/v1/attachments/backfill-variants?limit=1
#    → processed=1, updated=0；大图 variants 仍为 NULL
```
自动化复现（本次新增用例，两者均通过即证明当前实现确实如此）：
```powershell
cd d:\Projects\01_个人项目\personal-blog\backend
.\.venv\Scripts\python.exe -m pytest tests/test_wave3_verify.py -v `
  -k "never_converges or starve" -p no:cacheprovider
```

**建议修复方向**（未擅自改动代码）
- 让「已处理」可表达：对「确定无需变体」的记录写一个可区分的标记（例如写 `{}` 而非 NULL，或新增 `variants_state` 列），筛选条件只取真正未处理的记录；
- 或在候选查询中直接排除不可能产生变体的记录（GIF、`width <= min(image_variant_widths)`）；
- 同时修正前端「直到 processed 为 0」的提示措辞。

### F2（低危 · 文档不一致）退订接口方法在规格文档中过期

- 规格文档 `.trae/documents/功能第三波-多尺寸图片-统计趋势-评论通知.md` 第 107 行写的是 `GET /notifications/unsubscribe?token=…`；
- 实际实现为 **`POST /notifications/unsubscribe` + JSON body**（[notifications.py](file:///d:/Projects/01_个人项目/personal-blog/backend/src/app/api/v1/notifications.py#L20-L31)、[notifications.ts](file:///d:/Projects/01_个人项目/personal-blog/frontend/src/api/notifications.ts#L12-L14)），前端 `UnsubscribeView` 也只从 query 取 token 后放进 body；
- **前后端是自洽的，功能无缺陷**，仅文档过期，会误导后续对接方。建议同步文档。

### F3（提示 · 非产品问题）工具链与环境

- 本机未安装 Playwright 浏览器（`npx playwright install` 缺失），改用项目自带 CDP 方案（Chrome 已装），零新增依赖；建议后续沿用 `tools/` 的 CDP 路线；
- 沙箱会拦截 stdlib `.pyc` 与 Chrome 字典文件的写入，导致 pytest / 工具脚本在**全部断言通过后**仍返回非 0 退出码——判读结果时应看脚本自身打印的通过数，而非退出码。

---

## 8. 已知限制与未覆盖项

| 项 | 说明 |
|---|---|
| **隐式 TLS（465）路径未验证** | 本机只能用明文 SMTP（`smtp_use_tls=False`）搭建真实收信服务器；`use_tls=True`（生产默认，QQ/163 场景）的 TLS 握手路径未在真实网络上跑通，仅静态确认 `EmailSender` 按 `settings.smtp_use_tls` 传参 |
| 真实第三方邮箱未验证 | 无 QQ/163 凭据，未做真实投递与退信处理验证 |
| 重复审核会重复发信 | 设计已知限制（approve→revoke→approve 各发一次），规格文档已声明 |
| 跨 UTC 月边界上传 | 变体磁盘子目录由上传时刻推导、URL 子目录由 `created_at` 推导，理论上在月末毫秒级窗口可能不一致（未构造出该场景） |
| 退订无恢复入口 | 设计如此（邮件文案注明「重新评论即恢复」） |
| `visit_logs` 无清理任务 | 设计取舍，个人博客量级无压力 |
| 未做并发/压测 | 未验证高并发上传、并发评论同时触发通知的竞争表现 |
| 移动端新功能专项 | 移动端仅由 smoke 覆盖（首页 + 汉堡菜单），未对趋势图/退订页做 390px 专项断言 |

---

## 9. 结论

1. **三个功能的正常流程、边界、异常与集成路径全部按预期工作**：migration 往返干净、分层契约未被破坏、类型与构建全绿、41 项全功能回归零回归且数据基线一致。
2. **评论邮件通知的真实 SMTP 链路已闭环验证**：真实 aiosmtplib 传输 → 本地 SMTP 收信 → 解析中文主题与正文 → 从邮件里抠出退订链接并真的退订成功 → 退订后不再发信；网络异常/认证失败/连接被断都只影响通知、不影响评论接口。
3. **唯一缺陷 F1（回填收敛性与批额度饿死）** 已定位到根因（`variants IS NULL` 无法区分「未处理」与「无需变体」）并给出可复现步骤与修复方向；个人博客量级下不会立刻爆炸，但会持续产生误导性提示与无谓 I/O，且当不可回填记录数 ≥ 批大小时会真正失效。
4. 建议下一步：修 F1 → 同步 F2 的文档 → 补 `use_tls=True` 的 TLS 路径验证。

---

## 附录 A：本次新增自动化用例

- 文件：`backend/tests/test_wave3_verify.py`（36 个用例，含本地真实 SMTP 服务器实现与 1 个跨功能集成用例）
- 覆盖：1.6 边界/异常 15 项、1.4 边界 7 项、1.5 真实 SMTP 与 token 边界 13 项、跨功能集成 1 项
- 注意：其中 `test_backfill_never_converges_while_small_images_exist` 与 `test_fixed_limit_can_starve_real_legacy_image`（共 2 项）是**对当前缺陷行为的特征化（characterization）用例**，F1 修复后需同步调整断言；如不需要保留本文件，直接删除即可（无其他引用）。

## 附录 B：执行命令清单

```powershell
# 后端
cd d:\Projects\01_个人项目\personal-blog\backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider                     # 275 passed
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -c "from importlinter.cli import lint_imports_command; lint_imports_command()"

# 迁移往返（隔离临时库，不动 dev 库）
$db = Join-Path $env:TEMP "wave3_mig.db"; Remove-Item $db -ErrorAction SilentlyContinue
$env:DATABASE_URL = "sqlite+aiosqlite:///" + ($db -replace '\\','/')
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic downgrade base
.\.venv\Scripts\python.exe -m alembic upgrade head

# 前端
cd ..\frontend
npm run test; npm run type-check; npm run build

# 浏览器（需先起前后端）
cd ..
.\.backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend\src --port 8000   # 另开终端
node tools/smoke-check.mjs http://127.0.0.1:5173 "$env:TEMP\wave3-shots\smoke"                  # 34/34
node tools/interaction-check.mjs "$env:TEMP\wave3-shots\interaction"                            # 22/22
node tools/full-check.mjs http://127.0.0.1:5173 "$env:TEMP\wave3-shots\full"                    # 41/41 + 基线一致
```
