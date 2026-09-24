# 站点本体（server 块的内容），由 nginx.conf（纯 HTTP）与
# nginx.https.conf（自管证书）**共用同一份**。
#
# 为什么拆出来：TLS 版与纯 HTTP 版的差别只在"外壳"（listen/证书/跳转），
# 站点的 location 与缓存策略必须一模一样。抄成两份的下场是"改了 HTTP 版、
# HTTPS 版没改"，而 nginx 不会报任何错，只是行为不同 —— 这类漂移最难发现。
#
# ⚠️ 本文件里任何自己写了 `add_header` 的 location，都必须
#    include 一份 security-headers.inc，否则会静默丢掉 CSP（原因见该文件顶部）。

# 必须与后端的 MAX_UPLOAD_SIZE 保持一致。
# 只改后端不改这里的话，大文件会在 Nginx 层就被 413 拦掉，
# 而后端日志里什么都看不到，是最容易浪费时间的一类"灵异问题"。
client_max_body_size 10m;

gzip on;
gzip_vary on;
gzip_min_length 1024;
gzip_comp_level 6;
gzip_types
    text/plain text/css text/xml
    application/json application/javascript application/xml+rss
    application/rss+xml image/svg+xml;

# ---------- 安全响应头（server 级默认）----------
# 没自己写 add_header 的 location 会继承这一份；写了的那几个各自 include。
include /etc/nginx/conf.d/security-headers.inc;

# ---------- ACME HTTP-01 挑战 ----------
# 自管证书（docker-compose.tls.yml 那条路）要用 webroot 模式签发/续期：
# certbot 把文件写进宿主的 ./certbot-webroot，由这里以纯文本吐出去。
# 必须放在任何跳转规则**之前**并且用 `^~`：否则 80 端口那条
# "全量跳 HTTPS"会把挑战请求也 301 掉，续期就永远失败（而且失败得很安静 ——
# certbot 只会说"challenge 校验失败"，不会告诉你被自己重定向了）。
location ^~ /.well-known/acme-challenge/ {
    root /var/www/certbot;
    default_type "text/plain";
    access_log off;
}

# ---------- 带指纹的静态资源：长缓存 ----------
# Vite 产物文件名里带 hash，内容变了文件名就变，可以放心 immutable
location /assets/ {
    # 显式写 Cache-Control 而不用 `expires 1y`：expires 会另发一条
    # `Cache-Control: max-age=...`，于是响应里出现两条 Cache-Control。
    # 两条虽然合法（接收方按逗号合并），但排查缓存问题时极难读。
    add_header Cache-Control "public, max-age=31536000, immutable" always;
    include /etc/nginx/conf.d/security-headers.inc;
    access_log off;
    # =404 而不是回退到 index.html：缺一个 JS 就该 404，
    # 回退只会让浏览器拿到一份 HTML 去当 JS 解析，报错信息完全指错方向。
    try_files $uri =404;
}

location = /favicon.svg {
    expires 30d;
    access_log off;
}

# ---------- 媒体文件 ----------
# 反代到后端由 StaticFiles 提供（这样本地磁盘/对象存储的切换对 Nginx 透明）
location /media/ {
    proxy_pass http://blog_backend;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # 上传的图片都是不可变的（文件名含随机串），可以长缓存
    add_header Cache-Control "public, max-age=2592000" always;
    # 防止上传的 SVG/HTML 被浏览器按脚本执行（后端已禁用这两类，这里是双保险）。
    # nosniff 本身来自下面 include 的安全头那一份 —— 别在这儿再写一条，
    # 否则响应里会出现两个 X-Content-Type-Options（实测 "nosniff, nosniff"）。
    include /etc/nginx/conf.d/security-headers.inc;
}

# ---------- API ----------
location /api/ {
    proxy_pass http://blog_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    # 后端靠这个头拿真实客户端 IP（评论记录 IP、将来做限流都要用）
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout 10s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;

    # 上传接口不要缓存
    proxy_buffering off;
}

location = /health {
    proxy_pass http://blog_backend;
    access_log off;
}

# ---------- RSS / sitemap ----------
# 两个都由后端生成（内容随发文变化），直连后端。
# 刻意**不做** nginx 层的 micro-cache：`proxy_cache_valid` 在没有定义
# proxy_cache 的情况下是空操作（此前这里就有两行这样的死配置，读起来像已经缓存了）。
# 真要开，必须同时定义 proxy_cache_path + zone，并想清楚 Vary 的处理 ——
# 本站刚刚因为 `Vary: Authorization` 吃过一次亏（匿名与登录态共用 URL，
# 共享缓存把后台的读己之写弄坏了，见 docs/devlog 与 api/cache.py）。
# 当前由应用侧的 Cache-Control + ETag 负责，语义清楚且不会串号。
location ~ ^/(feed\.xml|sitemap\.xml)$ {
    proxy_pass http://blog_backend;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    access_log off;
}

# ---------- SPA 路由回退 ----------
# Vue Router 用 history 模式，像 /article/xxx 这种路径在磁盘上并不存在，
# 必须回退到 index.html 交给前端路由处理，否则刷新页面就是 404。
location / {
    try_files $uri $uri/ /index.html;
}

# index.html 不能缓存，否则前端发版后用户仍会加载旧的 JS 引用。
# ⚠️ 这里自己写了 add_header，所以必须 include 安全头那一份：
#    漏掉它的直接后果是**首页文档没有 CSP**（拆分前就是这样，已修）。
location = /index.html {
    add_header Cache-Control "no-cache, must-revalidate" always;
    include /etc/nginx/conf.d/security-headers.inc;
}

location ~ /\. {
    deny all;
}
