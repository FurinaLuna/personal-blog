# 部署指南

> 从零到线上，照顺序做即可。全部命令都可直接复制。
> 每条命令下面写了**应该看到什么**，对不上就是这一步没成，别往下走。

**你不用自己做的事**（compose 已经处理好了，列出来免得你去配）：

| 事情 | 谁做 |
|---|---|
| 建表 / 升级表结构 | backend 容器入口自动跑 `alembic upgrade head` |
| 创建管理员账号 | 首次启动时按 `.env` 的 `ADMIN_PASSWORD` 自动建 |
| HTTPS 之外的安全响应头（CSP / nosniff / X-Frame-Options…） | nginx 已配好 |
| 每天的数据库备份 | `backup` 容器，默认每 24h 一份，保留 14 份 |
| API 反代（`/api`、`/media`、`/feed.xml`、`/sitemap.xml`） | nginx 已配好 |
| 后端端口对外暴露 | **刻意不暴露**，只经 nginx 走内网 |

---

## 0. 先决条件

| 项 | 要求 | 说明 |
|---|---|---|
| 服务器 | 1 核 2G 起 | 个人博客足够；磁盘 ≥ 20G（要放图片） |
| 系统 | 任何能装 Docker 的 Linux | 下面以 Ubuntu 为例 |
| Docker | 24+ 与 compose v2 | `docker compose version` 有输出即可 |
| 端口 | 80（+ 443 若要 HTTPS） | 后端 8000 **不**对外 |
| 域名 | 可选 | 有域名才能上 HTTPS |

没装 Docker：

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER      # 之后重新登录一次，否则每条 docker 命令都要 sudo
docker compose version             # → Docker Compose version v2.x
```

---

## 1. 部署（三步）

```bash
# ① 拉代码
git clone https://github.com/FurinaLuna/personal-blog.git
cd personal-blog

# ② 生成配置，然后编辑
cp .env.example .env

# ③ 构建并启动（首次约 3~5 分钟）
docker compose up -d --build
```

第 ② 步要改的是 `.env` 里**三处必填**，其余保持默认：

| 变量 | 填什么 | 怎么生成 |
|---|---|---|
| `POSTGRES_PASSWORD` | 数据库口令 | 随便一串强口令（别用引号包起来） |
| `JWT_SECRET_KEY` | 登录态签名密钥 | `openssl rand -hex 32` |
| `ADMIN_PASSWORD` | 站长账号**初始**口令 | 自己想一个强的，首次启动后自动建号 |

> 这三处留空，`docker compose up` 会**直接拒绝启动**并告诉你缺哪个——
> 不会带着仓库里的公开默认值跑起来。这是故意的。
>
> `.env` 必须在**项目根目录**（不是 `backend/.env`）：compose 的变量插值只读这一份。

第 ③ 步之后确认：

```bash
docker compose ps
```

应该看到 4 个服务，`db` / `backend` / `frontend` / `backup`，状态都是 `Up`（healthy）。

```bash
curl -fsS http://localhost:8080/health && echo
# → {"status":"ok"} 之类
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/v1/articles
# → 200
```

打开 `http://<服务器IP>:8080` 就是你的博客。后台在 `/admin`，用 `admin` + 你设的 `ADMIN_PASSWORD` 登录。

**8080 被占用了？** 在 `.env` 里加一行 `APP_PORT=18080`，然后
`docker compose up -d`。改完记得同步改 `SITE_BASE_URL`（RSS 和 sitemap 里的绝对地址靠它拼）。

---

## 2. 域名与 HTTPS

### 最省事：用 Cloudflare（推荐）

1. 域名 DNS 解析到服务器 IP，打开 Cloudflare 的橙色小云朵（代理开启）；
2. Cloudflare → SSL/TLS → 模式选 **Flexible**（回源走 HTTP 8080）；
3. 在 `.env` 里把 `SITE_BASE_URL` 和 `CORS_ORIGINS` 改成 `https://你的域名`；
4. `docker compose up -d`。

Cloudflare 会自己带 `X-Forwarded-Proto: https`，站点会自动发出 HSTS。
证书不用你管。

不用 Cloudflare 的话，Caddy / 云负载均衡回源到 `127.0.0.1:8080` 也一样，
**只要它设置 `X-Forwarded-Proto`**。

### 或者：用仓库自带的证书方案

仓库带了 `deploy/nginx.https.conf` 与 `docker-compose.tls.yml`，四步：

```bash
# ① 先按纯 HTTP 起一次（80 端口要用来做 ACME 挑战）
docker compose up -d --build

# ② 签发证书（把 example.com 换成你的域名和邮箱）
docker run --rm \
  -v "$PWD/deploy/certs:/etc/letsencrypt" \
  -v "$PWD/certbot-webroot:/var/www/certbot" \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d example.com --email you@example.com --agree-tos --no-eff-email

# ③ 把 deploy/nginx.https.conf 里的 example.com 全换成你的域名
sed -i 's/example.com/你的域名/g' deploy/nginx.https.conf

# ④ 叠上 TLS 覆盖文件重启
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
```

`https://你的域名` 能打开就成了。

**续期**：证书 90 天到期，在宿主 cron 里加一条（cron 里没有 `$PWD`，写绝对路径）：

```cron
0 3 * * * cd /绝对路径/personal-blog && docker run --rm -v "$PWD/deploy/certs:/etc/letsencrypt" -v "$PWD/certbot-webroot:/var/www/certbot" certbot/certbot renew --webroot -w /var/www/certbot --quiet && docker compose -f docker-compose.yml -f docker-compose.tls.yml exec frontend nginx -s reload
```

---

## 3. 备份与恢复

### 自动备份（默认就开着）

`backup` 容器每 24 小时往宿主机的 `./backups/` 落一份 dump，保留最近 14 份。

```bash
docker compose logs -f backup              # 看备份有没有在跑
BACKUP_ONCE=1 docker compose run --rm backup   # 立刻备份一次
```

几个刻意的设计，知道一下就行：

- 备份落在**宿主机目录**而不是 docker 卷——放同一个卷里等于跟库一起丢，也没法 scp 走；
- 每份 dump 落盘前先写 `.part`、再用 `pg_restore -l` 校验可读，**通过了才改名**。
  宁可什么都没有，也不要一个"看起来像备份"的坏文件；
- 容器重启会立刻补一次备份，不会出现"重启之后再也没备份过"。

想换盘：`.env` 里设 `BACKUP_HOST_DIR=/mnt/backup/blog`。

### 恢复

```bash
# ① 先停掉写入方
docker compose stop backend

# ② 恢复到一个空库（--clean --if-exists 会先删同名对象）
docker compose exec -T db pg_restore --clean --if-exists -U blog -d blog \
  < backups/blog-<时间戳>.dump

# ③ 起回来并确认
docker compose start backend
curl -fsS http://localhost:8080/api/v1/articles >/dev/null && echo OK
```

**建议上线后第一周挑一份备份真跑一遍这三条**。没验证过的备份不算备份——
这条演练仓库自己也固化在 CI 里（`tools/deploy-check.mjs` 每次都会把备份恢复到空库再核对表数量）。

---

## 4. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `required variable POSTGRES_PASSWORD is missing` | `.env` 不在**项目根目录**，或三处必填没填 |
| 8080 打不开 | `docker compose ps` 看 `frontend` 是不是 Up；`docker compose logs frontend` |
| 改了 `.env` 没生效 | compose 的变量在容器创建时注入，改完要 `docker compose up -d` 重建容器 |
| 登录 500 / 页面起不来 | 先看 `docker compose logs backend`。多半是数据库没起来或迁移失败 |
| 想看接口文档 `/docs` | 生产默认关闭。临时在 `.env` 设 `APP_ENV=development` 重启；看完改回来 |
| 想直连后端调试 | 后端**不发布** 8000。用 `docker compose exec backend curl -s 127.0.0.1:8000/health`，或临时在 compose 里加 `ports: ["127.0.0.1:8000:8000"]`（只绑回环，别写 `0.0.0.0`） |
| 忘记管理员密码 | 删掉库里的用户重来太重；正确做法是进后台改，或 `docker compose exec backend alembic` 不用——直接用库改：`docker compose exec -T db psql -U blog -d blog -c "select username,role from users"` 看账号再说 |
| 图片上传失败 | 检查 `backend` 容器的 `/app/storage` 卷是否可写、磁盘是否满 |
| 每天备份文件越来越大想清理 | `.env` 里调小 `BACKUP_KEEP`（默认 14） |

出问题时的万能三连：

```bash
docker compose ps                    # 谁没起来
docker compose logs --tail=100 backend
docker compose logs --tail=100 frontend
```

---

## 5. 更新与回滚

### 更新到最新代码

```bash
cd personal-blog
git pull
docker compose up -d --build      # 容器入口会自动跑 alembic upgrade head
```

更新前**先备份一次**（`BACKUP_ONCE=1 docker compose run --rm backup`）就够了。

### 回滚

代码回滚用 git，别用 `alembic downgrade`：

```bash
git log --oneline -5
git checkout <上一个正常运行的提交>
docker compose up -d --build
```

**不要盲目跑 `alembic downgrade`**——有些迁移是不可逆的，会丢数据。
真要降级表结构，先确认那条迁移的 `downgrade()` 写了什么。

什么情况下应该立刻回滚，别临场决定：**5xx 比例持续 3 分钟超过 1%**，或**登录不可用**，
或 `/health` 连续失败。

---

## 6. 上线前检查清单

逐项打勾，都确认过就可以对外了。

- [ ] `JWT_SECRET_KEY` 是 `openssl rand -hex 32` 生成的随机值（不是仓库默认值）
- [ ] `ADMIN_PASSWORD` 不是弱口令；上线后**第一时间登录后台改掉**
- [ ] `SITE_BASE_URL` 是正式域名且带协议（`https://`），不带尾斜杠
- [ ] `CORS_ORIGINS` 只含真实域名
- [ ] `.env` 里没有 `APP_ENV=development` / `DEBUG=true` / `SEED_DEMO_DATA=true`
- [ ] 域名解析正确，HTTPS 能打开且证书有效（自管证书的话续期 cron 已加）
- [ ] `BACKUP_HOST_DIR` 指向一块够用的盘，或确认默认 `./backups` 所在盘够用
- [ ] **真的做过一次恢复演练**（第 3 节那三条命令）
- [ ] 数据库没有对公网开放（compose 默认就不开放，别自己加 `ports`）
- [ ] 知道回滚命令，且知道"不要盲目 `alembic downgrade`"

---

## 7. 想在本机先试一遍

```bash
cp .env.example .env
# 三处必填照填，SITE_BASE_URL 保持 http://localhost:8080
docker compose up -d --build
open http://localhost:8080          # macOS；Linux 用 xdg-open，Windows 直接浏览器打开
```

不想要演示数据：`SEED_DEMO_DATA` 保持默认（`false`）。想灌 4 篇演示文章来看效果，
在 `.env` 里设 `SEED_DEMO_DATA=true` 再重建容器即可。

**想改数据库结构再上线**（不推荐、也基本不需要）：直接改库会让迁移版本对不上。
正确顺序是本地改模型 → `make migration m="描述"` 生成迁移 → 把迁移文件提交 →
线上 `git pull && docker compose up -d --build`。

---

## 附：这套东西为什么这么配

不是必须读，但有几个决定是踩过坑才定下来的，改之前建议看一眼：

| 决定 | 原因 |
|---|---|
| 后端不发布 8000 端口 | 映射到宿主就等于绕开 nginx 那层的限流、CSP、安全头 |
| `--workers 1` | 限流器是进程内的，多 worker 等于配额翻倍；SQLite 上并发建表还会直接启动失败 |
| 容器入口跑迁移 | 否则"忘了跑迁移"表现为应用起不来，而 `docker exec` 那时候根本进不去 |
| HSTS 是条件式的 | 无条件发 HSTS 会把"只能用 HTTPS"写进访客浏览器一年且服务端撤不掉，纯 HTTP 部署会把自己锁死 |
| `backup` 等 backend 健康 | 只等 db 的话，全新部署的第一份备份会赶在迁移之前落盘——一个 0.8KB、恢复出来 0 张表的"合法"dump |
| 三处必填用 `:?` 硬失败 | compose 对未定义变量默认静默换空串，而数据库口令不能是空串 |

更多设计细节见 [`DESIGN.md`](DESIGN.md) 第 5 章，安全注意事项见 [`../SECURITY.md`](../SECURITY.md)。
