# 安全策略

## 支持范围

这是一个个人博客项目，只维护 `main` 分支上的最新版本。

| 版本 | 是否维护 |
|---|---|
| `main`（最新提交） | ✅ |
| 历史提交 / fork | ❌ |

## 报告漏洞

**请不要开公开 issue。** 公开披露会让漏洞在被修复前就暴露给所有人。

推荐通过 GitHub 的私密渠道报告：

1. 打开仓库的 **Security** 标签页 → **Report a vulnerability**
2. 或者直接给维护者发邮件（见 GitHub 个人主页）

请尽量包含：

- 影响范围（哪个接口 / 哪个页面 / 需要什么权限）
- 复现步骤或 PoC
- 你判断的严重程度，以及是否有已知的缓解方式

我会在 **7 天内**回复确认，并在修复发布后致谢（如果你希望署名）。

## 部署者需要注意的安全项

这些不是漏洞，而是**上线前必须自己确认**的配置 —— 默认值是为了开发方便，
直接照搬到公网会出问题：

| 项 | 要求 |
|---|---|
| `JWT_SECRET_KEY` | 必须换成 `openssl rand -hex 32` 生成的值。默认值是公开的 |
| `ADMIN_PASSWORD` | 首次启动后立刻修改默认的 `admin123456` |
| `DB_AUTO_CREATE` | 生产必须设为 `false`，改由 `alembic upgrade head` 管理表结构 |
| `SEED_DEMO_DATA` | 生产设为 `false`，否则会写入演示文章 |
| `CORS_ORIGINS` | 只填真实前端域名，不要留 `*` 或 localhost |
| `TRUST_PROXY_HEADERS` | 只有确实部署在可信反代之后才设为 `true`。该请求头可被客户端伪造，盲信会让限流可被绕过，并让伪造 IP 被当作评论者地址写入数据库 |
| `APP_ENV` | 设为 `production` 后 `/docs` 与 `/redoc` 会自动关闭 |

## 已内置的防护

- 上传靠 Pillow 真实解码判定类型，不信任客户端声明的 `Content-Type`；扩展名白名单，默认禁用 SVG / HTML（避免存储型 XSS）
- Markdown 渲染走「marked → DOMPurify 白名单消毒 → 再增强」流水线
- 文件名由服务端生成，根除路径穿越
- JWT 双 Token（access 120 分钟 / refresh 7 天），401 静默续期带单飞锁
- 登录 / 评论 / 点赞有限流（429 带 `Retry-After`）
- 请求 ID 做字符集与长度校验后才写入日志与响应头，防日志注入
- 每个请求带 `X-Request-ID`，错误响应体也带 `request_id`，便于定位

## 已知的取舍

诚实列出当前的边界，避免产生错误的安全预期：

- **限流是进程内的**：多 worker 部署时每个 worker 各算一份配额，实际放行量会乘以 worker 数。需要精确限流应换 Redis 实现（接口已隔离在 `app/utils/ratelimit.py`）
- **Token 存在 localStorage**：有 XSS 就有盗号风险。权衡与升级路径写在 `docs/DESIGN.md` 第 4.7 节
- **无 CSRF Token**：API 靠 `Authorization` 头鉴权而非 Cookie，天然不受经典 CSRF 影响；但如果将来改用 Cookie 承载凭证，必须补上 CSRF 防护
