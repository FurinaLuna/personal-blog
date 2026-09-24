# 安全响应头（server 级与 location 级共用一份）。
#
# ── 为什么必须做成 include，而不是写在 server 里就完事 ────────────────
# nginx 的 `add_header` **不继承**：只要某一层自己写了哪怕一条 add_header，
# 父层的**全部** add_header 在该层就失效（官方文档原话："inherited ... if and
# only if there are no add_header directives defined on the current level"）。
#
# 这不是理论风险，是本仓库真实存在的缺陷：拆分前 `location = /index.html`
# 为了加 `Cache-Control: no-cache` 写了一条 add_header，于是**首页 HTML 文档
# 丢掉了整个 CSP** —— 而 index.html 恰好是唯一会被浏览器当文档解析、
# 也是 XSS 唯一有意义的落点。`/assets/`、`/media/` 同样丢过 CSP 与 nosniff。
# 实测（拆分前）：`curl -sI http://localhost:8080/` 没有任何 CSP 头。
#
# 所以规则是：**任何自己写了 add_header 的 location，都必须 include 这个文件**。
# 只写了 `expires` 的 location 不受影响（expires 属于另一个模块，不参与这条继承规则）。
#
# 变量 $hsts_header 由各自的入口配置定义（纯 HTTP 版是条件式的，
# HTTPS 版才是无条件的）—— 见 deploy/nginx.conf 与 deploy/nginx.https.conf 里的 map。

add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
# 只有外层是 HTTPS 时才非空 ⇒ 只有 HTTPS 部署才会发出（见入口配置里的 map）
add_header Strict-Transport-Security $hsts_header always;
# CSP 刻意留得比较宽松：正文里可能出现任意外链图片。
# 但 script-src 只允许同源，这是防 XSS 的最后一道防线。
#
# `'sha256-Fb60WNSuZOWLArK7qYmXbb+/2exiew+HE1W0FhJVQdw='` 是
# frontend/index.html 里那段**内联**主题引导脚本的哈希。
# 少了它，`script-src 'self'` 会把那段脚本拦掉 —— 而它正是用来在样式加载前
# 给 <html> 打 dark 类、避免暗色模式白闪的；被拦掉的症状是"白闪回来了 +
# 控制台报 CSP 违规"，但页面其余部分完全正常，很容易被当成 CSS 问题去查。
#
# ⚠️ 改那段内联脚本后必须重算哈希（Vite 不会改写它，所以源文件与产物一致）：
#     node tools/deploy-check.mjs   # 断言失败时会把该填的哈希直接打出来
add_header Content-Security-Policy
    "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self' 'sha256-Fb60WNSuZOWLArK7qYmXbb+/2exiew+HE1W0FhJVQdw='; font-src 'self' data:; connect-src 'self'; frame-ancestors 'self'"
    always;
