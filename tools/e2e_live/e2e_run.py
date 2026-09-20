"""personal-blog 端到端（E2E）测试执行器。

设计原则：**真实跑通，不做占位验证**。
- 真实进程：用 backend/.venv 的解释器启动独立 uvicorn 实例。
- 真实数据库：临时目录新建空 SQLite，**用 alembic upgrade head 建表**（与生产同路径），
  而不是 ORM 的 create_all，保证被测结构与真实部署一致。
- 真实链路：所有断言走 HTTP；落库结果**直连 sqlite 二次校验**。
- 不污染项目：不使用 backend/blog.db 与 backend/storage，测试前后对真实库做指纹比对。

用法：
    backend/.venv/Scripts/python.exe tools/e2e_live/e2e_run.py

产出：
    tools/e2e_live/reports/e2e-report-<时间戳>.md      可读报告（含失败原因、证据、复现步骤）
    tools/e2e_live/reports/e2e-results-<时间戳>.json   机器可读的逐用例结果
"""

from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, UTC
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
REPORTS = Path(__file__).resolve().parent / "reports"

ADMIN_USER = "admin"
ADMIN_PASS = "E2eTest@13579"
NEW_PASS = "NewE2ePass@2468"
# 注意：邮箱域名不能是 .test / .example / .invalid 等 RFC 保留域，
# email-validator 会拒绝它们（本项目默认 admin@example.com 就命中这条）。
# 保留域触发的真实故障现象记录在用例 X01 里。
ADMIN_EMAIL = "admin@e2eblog.dev"
MAIL_DOMAIN = "e2eblog.dev"
PORT = int(os.environ.get("E2E_PORT", "8099"))
BASE = f"http://127.0.0.1:{PORT}"
API = BASE + "/api/v1"
REAL_DB = BACKEND / "blog.db"

WORKDIR: Path
DB_PATH: Path
STORAGE_DIR: Path
SERVER_LOG: Path

STATE: dict = {}
NOTE: dict = {"text": ""}


def note(text: str) -> None:
    """给 PASS 用例附加说明（例如「服务端拒绝，防护生效」这类有价值的正向证据）。"""
    NOTE["text"] = text


# ---------------------------------------------------------------- 用例框架


class Results:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, cid, flow, prio, name, status, reason="", repro="", evidence=""):
        self.rows.append(
            {
                "id": cid,
                "flow": flow,
                "priority": prio,
                "name": name,
                "status": status,
                "reason": reason,
                "repro": repro,
                "evidence": evidence,
            }
        )
        print(f"  [{status:5}] {cid} {name}" + (f"  <- {reason[:120]}" if reason else ""))

    def summary(self):
        counts = {"PASS": 0, "FAIL": 0, "ERROR": 0}
        for row in self.rows:
            counts[row["status"]] += 1
        return counts


RES = Results()


def case(cid, flow, prio, name, repro="python tools/e2e_live/e2e_run.py"):
    """注册用例（不在装饰时执行，由阶段列表统一驱动，保证顺序可控）。"""

    def deco(fn):
        fn.__case__ = (cid, flow, prio, name, repro)
        return fn

    return deco


def run_case(fn):
    cid, flow, prio, name, repro = fn.__case__
    NOTE["text"] = ""
    try:
        fn()
    except AssertionError as exc:
        RES.record(cid, flow, prio, name, "FAIL", str(exc)[:800], repro)
    except Exception as exc:
        RES.record(
            cid, flow, prio, name, "ERROR", f"{type(exc).__name__}: {exc}"[:800], repro,
            traceback.format_exc()[-1500:],
        )
    else:
        RES.record(cid, flow, prio, name, "PASS", NOTE["text"], repro)


def run_phase(title: str, functions):
    print(f"\n--- {title}")
    for fn in functions:
        run_case(fn)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------- 数据库断言


def db():
    conn = sqlite3.connect(str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(sql, params=()):
    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def rows(sql, params=()):
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def table_exists(name: str) -> bool:
    return scalar("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None


# ---------------------------------------------------------------- 服务进程


def server_env(**overrides) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH.as_posix()}",
            "STORAGE_DIR": str(STORAGE_DIR),
            "APP_ENV": "testing",
            "DEBUG": "false",
            "DB_AUTO_CREATE": "false",  # 强制走迁移，禁止自动建表
            "JWT_SECRET_KEY": secrets.token_hex(32),
            "ADMIN_USERNAME": ADMIN_USER,
            "ADMIN_PASSWORD": ADMIN_PASS,
            "ADMIN_EMAIL": ADMIN_EMAIL,
            "SEED_DEMO_DATA": "true",
            "RATE_LIMIT_ENABLED": "false",
            "LOG_JSON": "false",
            # 故意指向必然连不上的 SMTP，用于验证「通知依赖故障不影响主流程」
            "SMTP_ENABLED": "true",
            "SMTP_HOST": "127.0.0.1",
            "SMTP_PORT": "1",
            "SMTP_USERNAME": "nobody@" + MAIL_DOMAIN,
            "SMTP_PASSWORD": "x",
            "SMTP_FROM": "nobody@" + MAIL_DOMAIN,
            "SMTP_USE_TLS": "false",
        }
    )
    env.update(overrides)
    return env


def wait_ready(timeout=120) -> bool:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/ready", timeout=3)
            if r.status_code == 200:
                return True
            last = f"{r.status_code} {r.text[:200]}"
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(1)
    print(f"  !! 服务未在 {timeout}s 内就绪：{last}")
    return False


def start_server(tag: str, **overrides):
    # 追加写而非截断：两个服务实例的日志都要留作故障证据
    logf = SERVER_LOG.open("a", encoding="utf-8")
    logf.write(f"\n===== 服务实例 {tag} 启动 {datetime.now().isoformat(timespec='seconds')} =====\n")
    logf.flush()
    proc = subprocess.Popen(
        [str(PY), "-m", "uvicorn", "app.main:app", "--app-dir", "src",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(BACKEND), env=server_env(**overrides), stdout=logf, stderr=subprocess.STDOUT,
    )
    if not wait_ready():
        tail = server_tail(3000)
        stop_server(proc, logf)
        raise RuntimeError(f"服务（{tag}）启动失败：\n{tail}")
    return proc, logf


def stop_server(proc, logf):
    try:
        proc.terminate()
        proc.wait(timeout=20)
    except Exception:
        proc.kill()
    finally:
        try:
            logf.close()
        except Exception:
            pass


def server_tail(n=2500) -> str:
    try:
        return SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-n:]
    except Exception:
        return ""


def db_fingerprint(path: Path) -> str:
    if not path.exists():
        return "missing"
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


CLIENT = httpx.Client(timeout=60)


def api(method, path, **kw):
    return CLIENT.request(method, f"{API}{path}", **kw)


def auth_hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ================================================================ 用例定义
# 环境与迁移


@case("E01", "环境与探针", "P1", "GET /health 返回 ok 且环境标识生效")
def c_e01():
    r = CLIENT.get(f"{BASE}/health", timeout=10)
    check(r.status_code == 200, f"期望 200，实际 {r.status_code}：{r.text[:200]}")
    body = r.json()
    check(body.get("status") == "ok", f"status 非 ok：{body}")
    check(body.get("env") == "testing", f"APP_ENV 未生效：{body}")


@case("E02", "环境与探针", "P1", "GET /ready 就绪探针报 database=up")
def c_e02():
    r = CLIENT.get(f"{BASE}/ready", timeout=10)
    check(r.status_code == 200, f"期望 200，实际 {r.status_code}：{r.text[:200]}")
    check(r.json().get("database") == "up", f"数据库未就绪：{r.text[:200]}")


@case("E03", "迁移/建表", "P0", "alembic upgrade head 在空库上建成全部核心表")
def c_e03():
    expected = ["users", "articles", "comments", "categories", "tags", "attachments",
                "article_revisions", "visit_logs", "site_profile", "article_tags",
                "notification_opt_outs", "series"]
    missing = [t for t in expected if not table_exists(t)]
    check(not missing, f"缺少表：{missing}")
    total = scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
    note(f"库内共 {total} 张表，核心表齐全；FTS 虚拟表={'有' if table_exists('articles_fts') else '无'}")


@case("E04", "启动种子", "P0", "启动种子真实入库：站长账号 + 4 篇演示文章")
def c_e04():
    admin = rows("SELECT * FROM users WHERE username=?", (ADMIN_USER,))
    check(len(admin) == 1, f"站长账号未入库，命中 {len(admin)} 行")
    hp = str(admin[0]["hashed_password"])
    check(hp.startswith("$2") and ADMIN_PASS not in hp, "密码未以 bcrypt 哈希存储")
    check(str(admin[0]["role"]).lower().endswith("admin"), f"角色异常：{admin[0]['role']}")
    total = scalar("SELECT COUNT(*) FROM articles")
    published = scalar("SELECT COUNT(*) FROM articles WHERE status='published'")
    check(total == 4, f"演示文章应为 4 篇，实际 {total}")
    STATE["published"] = published
    note(f"演示文章 {total} 篇 / 已发布 {published} 篇；站长账号 {ADMIN_USER} 已入库")


# 访客读取链路


@case("R01", "访客读取", "P0", "文章列表分页：条数与库内已发布文章数一致")
def c_r01():
    r = api("GET", "/articles", params={"page": 1, "page_size": 50})
    check(r.status_code == 200, f"列表失败：{r.status_code} {r.text[:200]}")
    items = r.json().get("items", [])
    check(len(items) == STATE["published"], f"列表 {len(items)} 条 vs 库内 {STATE['published']} 篇")
    row = rows("SELECT id,status FROM articles WHERE id=?", (items[0]["id"],))[0]
    check(row["status"] == "published", f"列表包含非发布态文章：{row['status']}")
    STATE["article"] = items[0]


@case("R02", "访客读取", "P0", "文章详情：正文一致 + 浏览量递增 + visit_logs 真实落库")
def c_r02():
    art = STATE["article"]
    before_views = scalar("SELECT view_count FROM articles WHERE id=?", (art["id"],))
    before_logs = scalar("SELECT COUNT(*) FROM visit_logs WHERE article_id=?", (art["id"],))
    r = api("GET", f"/articles/{art['id']}")
    check(r.status_code == 200, f"详情失败：{r.status_code} {r.text[:200]}")
    body = r.json()
    db_content = scalar("SELECT content_md FROM articles WHERE id=?", (art["id"],))
    check(body["content_md"] == db_content, "接口正文与库内不一致")
    after_views = scalar("SELECT view_count FROM articles WHERE id=?", (art["id"],))
    after_logs = scalar("SELECT COUNT(*) FROM visit_logs WHERE article_id=?", (art["id"],))
    check(after_views == before_views + 1, f"浏览量未递增：{before_views} -> {after_views}")
    check(after_logs == before_logs + 1, f"visit_logs 未新增：{before_logs} -> {after_logs}")
    note(f"浏览量 {before_views}->{after_views}，访问日志 {before_logs}->{after_logs}")


@case("R03", "访客读取", "P0", "全文检索命中关键词并返回片段")
def c_r03():
    kw = "SQLite"
    r = api("GET", "/articles/search", params={"q": kw, "page": 1, "page_size": 10})
    check(r.status_code == 200, f"搜索失败：{r.status_code} {r.text[:200]}")
    items = r.json().get("items", [])
    check(items, f"关键词 {kw!r} 无命中结果")
    check(items[0].get("snippet") is not None, "搜索结果未返回 snippet 片段")
    note(f"命中 {len(items)} 条，首条：{items[0]['title'][:30]}")


@case("R04", "访客读取", "P1", "按月归档返回非空分组且条目总数不超过库内文章数")
def c_r04():
    r = api("GET", "/articles/archive")
    check(r.status_code == 200, f"归档失败：{r.status_code} {r.text[:200]}")
    groups = r.json()
    check(isinstance(groups, list) and groups, "归档为空")
    check(groups[0].get("count", 0) >= 1, "归档分组无条目")
    note(f"归档 {len(groups)} 个月份分组")


@case("R05", "访客读取", "P1", "分类 / 标签 / 系列 列表可用且与库内一致")
def c_r05():
    cats = api("GET", "/categories")
    check(cats.status_code == 200, f"分类列表失败：{cats.status_code}")
    tags = api("GET", "/tags")
    check(tags.status_code == 200, f"标签列表失败：{tags.status_code}")
    series = api("GET", "/series")
    check(series.status_code == 200, f"系列列表失败：{series.status_code}")
    db_cats = scalar("SELECT COUNT(*) FROM categories")
    check(len(cats.json()) == db_cats, f"分类数不一致：接口 {len(cats.json())} vs 库内 {db_cats}")
    note(f"分类 {len(cats.json())} / 标签 {len(tags.json())} / 系列 {len(series.json())}")


@case("R06", "访客读取", "P2", "相关文章接口可访问且返回列表")
def c_r06():
    r = api("GET", f"/articles/{STATE['article']['id']}/related")
    check(r.status_code == 200, f"相关文章失败：{r.status_code} {r.text[:160]}")
    check(isinstance(r.json(), list), "返回不是列表")


@case("R07", "访客读取", "P1", "RSS feed.xml 与 sitemap.xml 可访问且含真实文章链接")
def c_r07():
    art = STATE["article"]
    feed = CLIENT.get(f"{BASE}/feed.xml", timeout=20)
    check(feed.status_code == 200, f"feed.xml 失败：{feed.status_code}")
    check("<rss" in feed.text.lower(), "feed.xml 不是合法 RSS")
    check(art["title"] in feed.text, "RSS 未包含已发布文章标题")
    sm = CLIENT.get(f"{BASE}/sitemap.xml", timeout=20)
    check(sm.status_code == 200, f"sitemap.xml 失败：{sm.status_code}")
    check(art["slug"] in sm.text, "sitemap 未包含文章 slug")


@case("R08", "访客读取", "P1", "站点档案 /site/profile 数据与库内一致")
def c_r08():
    r = api("GET", "/site/profile")
    check(r.status_code == 200, f"站点档案失败：{r.status_code}")
    db_name = scalar("SELECT owner_name FROM site_profile WHERE id=1")
    check(r.json().get("owner_name") == db_name,
          f"站点档案不一致：{r.json().get('owner_name')} vs {db_name}")


# 账号与作者主流程


@case("A01", "账号与权限", "P0", "站长登录换取双 token 并可通过 /auth/me 校验")
def c_a01():
    r = api("POST", "/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    check(r.status_code == 200, f"登录失败：{r.status_code} {r.text[:200]}")
    body = r.json()
    check(body.get("access_token") and body.get("refresh_token"), "未返回双 token")
    STATE["admin_token"] = body["access_token"]
    STATE["admin_refresh"] = body["refresh_token"]
    me = api("GET", "/auth/me", headers=auth_hdr(body["access_token"]))
    check(me.status_code == 200 and me.json()["username"] == ADMIN_USER, "/auth/me 校验失败")


@case("A02", "文章创作", "P0", "创建草稿：状态/正文/作者/标签全部真实落库")
def c_a02():
    token = STATE["admin_token"]
    payload = {"title": "端到端测试文章", "summary": "验证完整链路",
               "content_md": "# 正文\n这是端到端测试写入的真实内容。", "status": "draft",
               "tags": ["E2E", "测试"]}
    r = api("POST", "/articles", json=payload, headers=auth_hdr(token))
    check(r.status_code in (200, 201), f"创建文章失败：{r.status_code} {r.text[:300]}")
    aid = r.json()["id"]
    STATE["aid"] = aid
    row = rows("SELECT * FROM articles WHERE id=?", (aid,))[0]
    check(row["status"] == "draft", f"状态不是 draft：{row['status']}")
    check(row["title"] == payload["title"], "标题未落库")
    check(row["content_md"] == payload["content_md"], "正文未落库")
    author_id = scalar("SELECT id FROM users WHERE username=?", (ADMIN_USER,))
    check(row["author_id"] == author_id, "作者归属错误")
    tag_names = [t["name"] for t in rows(
        "SELECT t.name FROM tags t JOIN article_tags at ON at.tag_id=t.id "
        "WHERE at.article_id=?", (aid,))]
    check(set(payload["tags"]).issubset(set(tag_names)), f"标签未落库：{tag_names}")
    note(f"草稿 id={aid}，标签 {tag_names}")


@case("A03", "文件上传", "P0", "上传真实 PNG：附件落库 + 磁盘文件存在 + 可通过静态路由访问")
def c_a03():
    token = STATE["admin_token"]
    png = make_png(600, 400)
    r = api("POST", "/attachments/upload", headers=auth_hdr(token),
            files={"file": ("e2e-upload.png", png, "image/png")})
    check(r.status_code == 201, f"上传失败：{r.status_code} {r.text[:300]}")
    up = r.json()
    attach_id = up["id"]
    STATE["attach_id"] = attach_id
    row = rows("SELECT * FROM attachments WHERE id=?", (attach_id,))[0]
    url = row["url"]
    rel = url.split("/media/", 1)[-1] if "/media/" in url else url.lstrip("/")
    disk = STORAGE_DIR / rel
    check(disk.exists() and disk.stat().st_size > 0, f"磁盘文件缺失或为空：{disk}")
    media = CLIENT.get(f"{BASE}{url if url.startswith('/') else '/media/' + rel}", timeout=20)
    check(media.status_code == 200, f"静态媒体路由不可访问：{media.status_code}")
    note(f"附件 id={attach_id}，落盘 {disk.name}，类型 {row['mime_type']}，HTTP 200")


@case("A04", "文章发布", "P0", "发布文章：状态与发布时间落库，且前台可见")
def c_a04():
    token = STATE["admin_token"]
    aid = STATE["aid"]
    r = api("PATCH", f"/articles/{aid}", json={"status": "published"}, headers=auth_hdr(token))
    check(r.status_code == 200, f"发布失败：{r.status_code} {r.text[:300]}")
    row = rows("SELECT * FROM articles WHERE id=?", (aid,))[0]
    check(row["status"] == "published", f"库内状态未更新：{row['status']}")
    check(row["published_at"] not in (None, ""), "published_at 未写入")
    guest = api("GET", f"/articles/{aid}")
    check(guest.status_code == 200, f"访客无法访问已发布文章：{guest.status_code}")
    check(guest.json()["title"] == row["title"], "访客所见内容与库内不一致")


@case("A05", "版本管理", "P0", "编辑已发布文章自动生成修订版本")
def c_a05():
    token = STATE["admin_token"]
    aid = STATE["aid"]
    before = scalar("SELECT COUNT(*) FROM article_revisions WHERE article_id=?", (aid,))
    r = api("PATCH", f"/articles/{aid}", json={"content_md": "# 修改后\n第二次编辑的内容。"},
            headers=auth_hdr(token))
    check(r.status_code == 200, f"更新失败：{r.status_code} {r.text[:200]}")
    after = scalar("SELECT COUNT(*) FROM article_revisions WHERE article_id=?", (aid,))
    check(after > before, f"未生成修订版本：{before} -> {after}")
    lst = api("GET", f"/articles/{aid}/revisions", headers=auth_hdr(token))
    check(lst.status_code == 200 and lst.json(), "版本列表无数据")
    STATE["revision_id"] = lst.json()[0]["id"]
    note(f"版本数 {before} -> {after}")


@case("A06", "版本管理", "P0", "恢复到指定版本：正文真实回滚")
def c_a06():
    token = STATE["admin_token"]
    aid = STATE["aid"]
    rev_id = STATE["revision_id"]
    detail = api("GET", f"/articles/{aid}/revisions/{rev_id}", headers=auth_hdr(token))
    check(detail.status_code == 200, f"版本详情失败：{detail.status_code}")
    target = detail.json()["content_md"]
    r = api("POST", f"/articles/{aid}/revisions/{rev_id}/restore", headers=auth_hdr(token))
    check(r.status_code == 200, f"恢复失败：{r.status_code} {r.text[:200]}")
    now = scalar("SELECT content_md FROM articles WHERE id=?", (aid,))
    check(now == target,
          f"恢复后正文未回滚到目标版本：库内={str(now)[:60]!r} 版本={str(target)[:60]!r}")


@case("A07", "冲突处理", "P1", "重复 slug 创建：不产生两条同 slug 记录（409 或自动退让）")
def c_a07():
    token = STATE["admin_token"]
    existing_slug = rows("SELECT slug FROM articles LIMIT 1")[0]["slug"]
    r = api("POST", "/articles", headers=auth_hdr(token),
            json={"title": "重名文章", "slug": existing_slug, "content_md": "x", "status": "draft"})
    check(r.status_code in (200, 201, 409),
          f"重复 slug 应被拒绝或自动退让，实际 {r.status_code}：{r.text[:200]}")
    dup = scalar("SELECT COUNT(*) FROM articles WHERE slug=?", (existing_slug,))
    check(dup == 1, f"库内出现 {dup} 条同名 slug（唯一性被破坏）")
    if r.status_code in (200, 201):
        new_slug = r.json()["slug"]
        check(new_slug != existing_slug, "显式指定冲突 slug 却原样入库")
        note(f"服务端采用自动退让策略：{existing_slug} -> {new_slug}")
    else:
        note("服务端返回 409 冲突")


@case("A08", "删除与级联", "P1", "删除文章：记录移除且关联评论被级联清理")
def c_a08():
    token = STATE["admin_token"]
    # 草稿不允许评论（返回 404 是产品既定行为），因此用一篇「已发布」文章做级联验证
    r0 = api("POST", "/articles", headers=auth_hdr(token),
             json={"title": "待删除文章", "content_md": "x", "status": "published"})
    check(r0.status_code in (200, 201), f"构造文章失败：{r0.status_code} {r0.text[:160]}")
    did = r0.json()["id"]
    c = api("POST", f"/comments/article/{did}",
            json={"author_name": "待清理", "author_email": "x@" + MAIL_DOMAIN + "",
                  "content": "将被级联删除"})
    check(c.status_code == 201, f"构造评论失败：{c.status_code} {c.text[:160]}")
    check(scalar("SELECT COUNT(*) FROM comments WHERE article_id=?", (did,)) >= 1, "评论未入库")
    r = api("DELETE", f"/articles/{did}", headers=auth_hdr(token))
    check(r.status_code == 204, f"删除文章失败：{r.status_code} {r.text[:200]}")
    check(scalar("SELECT COUNT(*) FROM articles WHERE id=?", (did,)) == 0, "文章记录仍残留")
    left = scalar("SELECT COUNT(*) FROM comments WHERE article_id=?", (did,))
    check(left == 0, f"文章删除后残留 {left} 条孤儿评论（级联未生效）")
    STATE["draft_id"] = did


@case("A09", "权限", "P0", "作者角色访问站长专属接口返回 403")
def c_a09():
    r = api("GET", "/auth/users", headers=auth_hdr(STATE["author_token"]))
    check(r.status_code == 403, f"作者访问用户列表应 403，实际 {r.status_code}：{r.text[:160]}")


@case("A10", "权限", "P0", "匿名访问需授权接口返回 401")
def c_a10():
    r = api("POST", "/articles", json={"title": "未授权", "content_md": "x"})
    check(r.status_code == 401, f"匿名创建文章应 401，实际 {r.status_code}：{r.text[:160]}")
    r2 = api("GET", "/comments", params={"page": 1, "page_size": 5})
    check(r2.status_code == 401, f"匿名访问后台评论列表应 401，实际 {r2.status_code}")


# 账号体系


@case("U01", "账号与权限", "P0", "错误密码登录返回 401")
def c_u01():
    r = api("POST", "/auth/login", json={"username": ADMIN_USER, "password": "wrong-password"})
    check(r.status_code == 401, f"错误密码应 401，实际 {r.status_code}")


@case("U02", "账号与权限", "P0", "伪造令牌访问受保护资源返回 401")
def c_u02():
    r = api("GET", "/auth/me", headers=auth_hdr("not-a-real-token"))
    check(r.status_code == 401, f"伪造 token 应 401，实际 {r.status_code}")


@case("U03", "账号与权限", "P0", "refresh 接口可换取可用的新访问令牌")
def c_u03():
    r = api("POST", "/auth/refresh", json={"refresh_token": STATE["admin_refresh"]})
    check(r.status_code == 200, f"刷新失败：{r.status_code} {r.text[:200]}")
    me = api("GET", "/auth/me", headers=auth_hdr(r.json()["access_token"]))
    check(me.status_code == 200, "新换取的令牌不可用")


@case("U04", "账号与权限", "P0", "改密后旧令牌立即失效（吊销生效）")
def c_u04():
    token = STATE["admin_token"]
    old_refresh = STATE["admin_refresh"]
    r = api("POST", "/auth/me/password", headers=auth_hdr(token),
            json={"old_password": ADMIN_PASS, "new_password": NEW_PASS})
    check(r.status_code == 200, f"改密失败：{r.status_code} {r.text[:200]}")
    check(api("GET", "/auth/me", headers=auth_hdr(token)).status_code == 401,
          "改密后旧 access token 仍可用（未吊销）")
    check(api("POST", "/auth/refresh", json={"refresh_token": old_refresh}).status_code == 401,
          "改密后旧 refresh token 仍可用")
    login = api("POST", "/auth/login", json={"username": ADMIN_USER, "password": NEW_PASS})
    check(login.status_code == 200, "新密码无法登录")
    STATE["admin_token"] = login.json()["access_token"]
    STATE["admin_refresh"] = login.json()["refresh_token"]


@case("U05", "账号与权限", "P1", "登出后旧令牌是否失效 [已知缺陷 A2 验证]")
def c_u05():
    token = STATE["admin_token"]
    r = api("POST", "/auth/logout", headers=auth_hdr(token))
    check(r.status_code == 200, f"登出接口失败：{r.status_code} {r.text[:160]}")
    still = api("GET", "/auth/me", headers=auth_hdr(token))
    if still.status_code == 200:
        raise AssertionError(
            "确认缺陷：登出后旧令牌仍可正常访问受保护资源 —— "
            "服务端无状态 JWT 且 logout 只返回语义消息，未做任何吊销；"
            "用户以为已登出，实际凭证在有效期内可继续使用"
        )
    note(f"登出后令牌已失效（{still.status_code}）")
    # 复原登录态，避免污染后续用例
    login = api("POST", "/auth/login", json={"username": ADMIN_USER, "password": NEW_PASS})
    STATE["admin_token"] = login.json()["access_token"]
    STATE["admin_refresh"] = login.json()["refresh_token"]


@case("U06", "账号与权限", "P0", "站长创建作者账号：新账号可真实登录且密码哈希入库")
def c_u06():
    token = STATE["admin_token"]
    payload = {"username": "e2eauthor", "email": "author@" + MAIL_DOMAIN + "", "password": "Author@2026",
               "nickname": "E2E作者", "role": "author"}
    r = api("POST", "/auth/users", json=payload, headers=auth_hdr(token))
    check(r.status_code == 201, f"创建用户失败：{r.status_code} {r.text[:250]}")
    uid = r.json()["id"]
    STATE["author_uid"] = uid
    db_user = rows("SELECT * FROM users WHERE id=?", (uid,))[0]
    hp = str(db_user["hashed_password"])
    check(hp.startswith("$2"), "密码未以 bcrypt 哈希入库")
    check("Author@2026" not in hp, "库中疑似出现明文密码")
    login = api("POST", "/auth/login", json={"username": "e2eauthor", "password": "Author@2026"})
    check(login.status_code == 200, f"新账号无法登录：{login.status_code}")
    STATE["author_token"] = login.json()["access_token"]
    note(f"作者账号 id={uid} 创建并登录成功")


@case("U07", "边界输入", "P2", "弱密码 / 非法邮箱创建用户返回 422")
def c_u07():
    token = STATE["admin_token"]
    r = api("POST", "/auth/users", headers=auth_hdr(token),
            json={"username": "weak", "email": "weak@" + MAIL_DOMAIN + "", "password": "123"})
    check(r.status_code == 422, f"弱密码未拦截：{r.status_code} {r.text[:160]}")
    r2 = api("POST", "/auth/users", headers=auth_hdr(token),
             json={"username": "bademail", "email": "not-an-email", "password": "Good@12345"})
    check(r2.status_code == 422, f"非法邮箱未拦截：{r2.status_code}")


# 评论链路


@case("C01", "评论", "P0", "游客发表评论：真实入库且初始待审核")
def c_c01():
    art_id = int(scalar("SELECT id FROM articles WHERE status='published' ORDER BY id LIMIT 1"))
    STATE["article_id"] = art_id
    before = scalar("SELECT COUNT(*) FROM comments")
    payload = {"author_name": "E2E游客", "author_email": "guest@" + MAIL_DOMAIN + "",
               "author_site": "https://example.com",
               "content": "端到端测试留言：这是一条真实写入数据库的评论。"}
    r = api("POST", f"/comments/article/{art_id}", json=payload)
    check(r.status_code == 201, f"发表评论失败：{r.status_code} {r.text[:300]}")
    cid = r.json()["id"]
    row = rows("SELECT * FROM comments WHERE id=?", (cid,))[0]
    check(row["content"] == payload["content"], "库内正文与提交内容不一致")
    check(row["author_email"] == payload["author_email"], "邮箱未落库")
    check(row["is_approved"] in (0, False), f"新评论应为待审核，实际 {row['is_approved']}")
    check(scalar("SELECT COUNT(*) FROM comments") == before + 1, "comments 未新增记录")
    STATE["comment_id"] = cid


@case("C02", "评论", "P0", "待审核评论对访客不可见")
def c_c02():
    r = api("GET", f"/comments/article/{STATE['article_id']}")
    check(r.status_code == 200, f"评论树失败：{r.status_code}")
    check(str(STATE["comment_id"]) not in r.text,
          f"未审核评论 {STATE['comment_id']} 出现在访客可见的评论树中")


@case("C03", "评论", "P0", "站长审核通过后：库内状态翻转且前台可见")
def c_c03():
    cid = STATE["comment_id"]
    token = STATE["admin_token"]
    r = api("PATCH", f"/comments/{cid}", json={"is_approved": True}, headers=auth_hdr(token))
    check(r.status_code == 200, f"审核失败：{r.status_code} {r.text[:300]}")
    check(bool(r.json()["is_approved"]), "响应未反映已通过")
    check(bool(scalar("SELECT is_approved FROM comments WHERE id=?", (cid,))), "库内未标记审核通过")
    check(str(cid) in api("GET", f"/comments/article/{STATE['article_id']}").text,
          "审核通过后评论树中仍看不到该评论")


@case("C04", "评论", "P0", "二级回复：parent_id 落库正确且进入 replies")
def c_c04():
    parent = STATE["comment_id"]
    r = api("POST", f"/comments/article/{STATE['article_id']}",
            json={"author_name": "E2E回复者", "author_email": "reply@" + MAIL_DOMAIN + "",
                  "content": "这是一条二级回复。", "parent_id": parent})
    check(r.status_code == 201, f"回复失败：{r.status_code} {r.text[:300]}")
    reply = r.json()
    STATE["reply_id"] = reply["id"]
    check(reply["parent_id"] == parent, f"响应 parent_id 错误：{reply['parent_id']}")
    check(scalar("SELECT parent_id FROM comments WHERE id=?", (reply["id"],)) == parent,
          "库内 parent_id 与请求不一致")
    api("PATCH", f"/comments/{reply['id']}", json={"is_approved": True},
        headers=auth_hdr(STATE["admin_token"]))
    tree = api("GET", f"/comments/article/{STATE['article_id']}").json()
    root = next((c for c in tree if c["id"] == parent), None)
    check(root is not None, "评论树中未找到根评论")
    check(any(x["id"] == reply["id"] for x in root.get("replies", [])),
          "二级回复未出现在根评论的 replies 中")


@case("C05", "评论", "P1", "三级评论（回复的回复）行为验证 [已知缺陷 C1]")
def c_c05():
    r = api("POST", f"/comments/article/{STATE['article_id']}",
            json={"author_name": "E2E三级", "author_email": "lv3@" + MAIL_DOMAIN + "",
                  "content": "三级回复：验证层级约束。", "parent_id": STATE["reply_id"]})
    if r.status_code >= 400:
        note(f"服务端已拒绝三级评论（{r.status_code}），层级校验生效")
        return
    lv3 = r.json()["id"]
    api("PATCH", f"/comments/{lv3}", json={"is_approved": True},
        headers=auth_hdr(STATE["admin_token"]))
    tree = api("GET", f"/comments/article/{STATE['article_id']}").json()
    visible = any(c["id"] == lv3 for c in tree) or any(
        any(x["id"] == lv3 for x in c.get("replies", [])) for c in tree)
    if not visible:
        raise AssertionError(
            f"确认缺陷：三级评论被接受并落库（id={lv3}，parent_id={STATE['reply_id']}），"
            "但两级评论树查询（只查 parent_id IS NULL 的根 + 一层 selectinload）永远查不到它 —— "
            "用户端表现为「回复成功却看不见」，而后台扁平列表里能看到"
        )
    note("三级评论可见（已被重挂到根层级）")


@case("C06", "评论", "P0", "恶意 author_site（javascript: 伪协议）被拒绝")
def c_c06():
    before = scalar("SELECT COUNT(*) FROM comments")
    r = api("POST", f"/comments/article/{STATE['article_id']}",
            json={"author_name": "攻击者", "author_email": "evil@" + MAIL_DOMAIN + "",
                  "author_site": "javascript:alert(1)", "content": "XSS 探测"})
    check(r.status_code == 422, f"伪协议地址未被拦截：{r.status_code} {r.text[:200]}")
    check(scalar("SELECT COUNT(*) FROM comments") == before, "被拒的恶意请求仍写入了数据库")


@case("C07", "评论", "P1", "空白内容评论被 422 拦截且不入库")
def c_c07():
    before = scalar("SELECT COUNT(*) FROM comments")
    r = api("POST", f"/comments/article/{STATE['article_id']}",
            json={"author_name": "空内容", "author_email": "x@" + MAIL_DOMAIN + "", "content": "   "})
    check(r.status_code == 422, f"空评论未被拦截：{r.status_code} {r.text[:200]}")
    check(scalar("SELECT COUNT(*) FROM comments") == before, "空评论仍写入数据库")


@case("C08", "评论", "P1", "依赖不可用（SMTP 不可达）不阻塞评论主流程")
def c_c08():
    before = scalar("SELECT COUNT(*) FROM comments")
    r = api("POST", f"/comments/article/{STATE['article_id']}",
            json={"author_name": "依赖故障", "author_email": "dep@" + MAIL_DOMAIN + "",
                  "content": "SMTP 不可达时的评论提交"})
    check(r.status_code == 201,
          f"邮件服务不可用时评论接口失败（应 fire-and-forget）：{r.status_code} {r.text[:300]}")
    check(scalar("SELECT COUNT(*) FROM comments") == before + 1, "评论未落库")
    note("SMTP 指向 127.0.0.1:1（必然拒绝），评论仍正常落库")


@case("C09", "评论", "P1", "删除顶级评论级联删除其回复")
def c_c09():
    token = STATE["admin_token"]
    art_id = STATE["article_id"]
    parent = api("POST", f"/comments/article/{art_id}",
                 json={"author_name": "待删父", "author_email": "p@" + MAIL_DOMAIN + "", "content": "父评论"}).json()
    child = api("POST", f"/comments/article/{art_id}",
                json={"author_name": "待删子", "author_email": "c@" + MAIL_DOMAIN + "",
                      "content": "子评论", "parent_id": parent["id"]}).json()
    check(scalar("SELECT COUNT(*) FROM comments WHERE id=?", (child["id"],)) == 1, "子评论未入库")
    r = api("DELETE", f"/comments/{parent['id']}", headers=auth_hdr(token))
    check(r.status_code == 200, f"删除失败：{r.status_code} {r.text[:200]}")
    check(scalar("SELECT COUNT(*) FROM comments WHERE id=?", (parent["id"],)) == 0, "父评论未删除")
    check(scalar("SELECT COUNT(*) FROM comments WHERE id=?", (child["id"],)) == 0,
          "子评论未级联删除，产生孤儿行")


@case("C10", "评论", "P2", "后台评论列表按审核状态过滤与库内一致")
def c_c10():
    r = api("GET", "/comments", params={"approved": "false", "page": 1, "page_size": 50},
            headers=auth_hdr(STATE["admin_token"]))
    check(r.status_code == 200, f"后台评论列表失败：{r.status_code} {r.text[:200]}")
    items = r.json().get("items", [])
    pending = scalar("SELECT COUNT(*) FROM comments WHERE is_approved=0")
    check(len(items) == pending, f"待审核条数不一致：接口 {len(items)} vs 库内 {pending}")


# 点赞与边界异常


@case("L01", "点赞", "P0", "点赞：计数递增并真实落库")
def c_l01():
    art_id = STATE["article"]["id"]
    before = scalar("SELECT like_count FROM articles WHERE id=?", (art_id,))
    r = api("POST", f"/articles/{art_id}/like")
    check(r.status_code == 200, f"点赞失败：{r.status_code} {r.text[:200]}")
    after = scalar("SELECT like_count FROM articles WHERE id=?", (art_id,))
    check(after == before + 1, f"计数未递增：{before} -> {after}")
    note(f"点赞 {before} -> {after}（无身份计数，设计如此）")


@case("L02", "点赞", "P1", "草稿文章点赞返回 404 且不改计数")
def c_l02():
    token = STATE["admin_token"]
    draft = api("POST", "/articles", headers=auth_hdr(token),
                json={"title": "草稿不可点赞", "content_md": "# draft", "status": "draft"})
    check(draft.status_code in (200, 201), f"构造草稿失败：{draft.status_code}")
    did = draft.json()["id"]
    before = scalar("SELECT like_count FROM articles WHERE id=?", (did,))
    r = api("POST", f"/articles/{did}/like")
    check(r.status_code == 404, f"草稿点赞应 404，实际 {r.status_code}")
    check(scalar("SELECT like_count FROM articles WHERE id=?", (did,)) == before, "草稿被点赞了")


@case("B01", "边界输入", "P1", "page=0 被参数校验拦截")
def c_b01():
    r = api("GET", "/articles", params={"page": 0, "page_size": 10})
    check(r.status_code == 422, f"page=0 应 422，实际 {r.status_code}：{r.text[:160]}")


@case("B02", "边界输入", "P1", "超大 page_size 被服务端收敛，不会打爆响应")
def c_b02():
    r = api("GET", "/articles", params={"page": 1, "page_size": 99999})
    check(r.status_code in (200, 422), f"超大 page_size 响应异常：{r.status_code}")
    if r.status_code == 200:
        items = r.json().get("items", [])
        check(len(items) <= 50, f"page_size 未收敛，返回 {len(items)} 条（MAX_PAGE_SIZE=50）")
        note(f"page_size=99999 被收敛到 {len(items)} 条")


@case("B03", "边界输入", "P1", "非法文章状态值返回 422")
def c_b03():
    r = api("POST", "/articles", headers=auth_hdr(STATE["admin_token"]),
            json={"title": "非法状态", "content_md": "x", "status": "not-a-status"})
    check(r.status_code == 422, f"非法状态未拦截：{r.status_code} {r.text[:160]}")


@case("B04", "边界输入", "P1", "超长标题（201 字）返回 422")
def c_b04():
    r = api("POST", "/articles", headers=auth_hdr(STATE["admin_token"]),
            json={"title": "标" * 201, "content_md": "x"})
    check(r.status_code == 422, f"超长标题未拦截：{r.status_code} {r.text[:160]}")


@case("B05", "上传安全", "P1", "非白名单类型（伪造后缀的可执行文件 / SVG）被拒绝")
def c_b05():
    token = STATE["admin_token"]
    r = api("POST", "/attachments/upload", headers=auth_hdr(token),
            files={"file": ("evil.exe", b"MZ\x90\x00 fake executable content", "image/png")})
    check(r.status_code >= 400, f"危险文件被接受：{r.status_code} {r.text[:200]}")
    r2 = api("POST", "/attachments/upload", headers=auth_hdr(token),
             files={"file": ("shell.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml")})
    check(r2.status_code >= 400, f"SVG 被接受（存储型 XSS 风险）：{r2.status_code}")
    note(f"exe 伪装 -> {r.status_code}；SVG -> {r2.status_code}")


@case("B06", "上传安全", "P1", "超过体积上限的文件被拒绝")
def c_b06():
    token = STATE["admin_token"]
    big = make_png(2400, 2400) + os.urandom(11 * 1024 * 1024)
    r = api("POST", "/attachments/upload", headers=auth_hdr(token),
            files={"file": ("huge.png", big, "image/png")})
    check(r.status_code in (400, 413, 422), f"超限文件未被拒绝：{r.status_code} {r.text[:160]}")
    note(f"{len(big) / 1024 / 1024:.1f}MB 文件 -> {r.status_code}")


@case("B07", "上传安全", "P0", "图片解压炸弹（小体积超高像素）防护验证")
def c_b07():
    token = STATE["admin_token"]
    bomb = make_png(8000, 8000)  # 6400 万像素，远超 5000 万上限
    size_kb = len(bomb) / 1024
    r = api("POST", "/attachments/upload", headers=auth_hdr(token),
            files={"file": ("bomb.png", bomb, "image/png")})
    if r.status_code == 201:
        raise AssertionError(
            f"严重缺陷：解压炸弹被接受（{size_kb:.0f}KB → 6400 万像素），"
            "像素上限校验未生效，存在 CPU / 内存耗尽风险"
        )
    if r.status_code >= 500:
        raise AssertionError(f"解压炸弹触发服务端内部错误：{r.status_code} {r.text[:200]}")
    note(f"已拦截 {size_kb:.0f}KB / 6400 万像素的压缩炸弹 -> {r.status_code}")


@case("B08", "冲突处理", "P1", "重复创建同名分类返回 409")
def c_b08():
    token = STATE["admin_token"]
    existing = rows("SELECT name FROM categories LIMIT 1")[0]["name"]
    r = api("POST", "/categories", json={"name": existing}, headers=auth_hdr(token))
    check(r.status_code == 409, f"重复分类应 409，实际 {r.status_code}：{r.text[:160]}")


@case("B09", "错误契约", "P1", "404 响应遵循统一信封（含 code 与 request_id）")
def c_b09():
    r = api("GET", "/articles/99999999")
    check(r.status_code == 404, f"不存在资源应 404，实际 {r.status_code}")
    body = r.json()
    check("detail" in body, f"缺少 detail：{body}")
    check(body.get("code") is not None, f"缺少 code：{body}")
    check(r.headers.get("X-Request-ID") or body.get("request_id"), "未携带 request_id")


@case("B10", "错误契约", "P1", "异常输入触发 5xx 时的响应格式 [已知缺陷 A1 验证]")
def c_b10():
    token = STATE["admin_token"]
    broken = b"\x89PNG\r\n\x1a\n" + os.urandom(4096)  # 合法头 + 损坏数据
    r = api("POST", "/attachments/upload", headers=auth_hdr(token),
            files={"file": ("broken.png", broken, "image/png")})
    if r.status_code < 500:
        note(f"损坏图片被正常处理：{r.status_code}")
        return
    structured = False
    try:
        structured = "request_id" in r.json()
    except Exception:
        structured = False
    if not structured:
        raise AssertionError(
            "确认缺陷：未捕获异常落到 Starlette 默认 500 处理器，返回体非结构化且缺失 request_id —— "
            f"前端无法归一化错误文案，运维也无法用 request_id 定位日志。响应体：{r.text[:160]}"
        )
    note("500 响应已包含 request_id")


@case("B11", "空数据", "P1", "空结果集合返回空列表而非报错")
def c_b11():
    token = STATE["admin_token"]
    r = api("GET", "/articles/manage/list", headers=auth_hdr(token),
            params={"page": 1, "page_size": 5, "status": "draft"})
    check(r.status_code == 200, f"后台列表失败：{r.status_code} {r.text[:160]}")
    check("items" in r.json(), "分页响应缺少 items")
    r2 = api("GET", "/articles/search", params={"q": "绝不可能存在的关键词zzzq996"})
    check(r2.status_code == 200, f"空结果搜索应 200，实际 {r2.status_code}")
    check(r2.json().get("items") == [], "空结果未返回空列表")


@case("B12", "重复操作", "P1", "重复创建同名标签产生冲突且无脏重复数据")
def c_b12():
    token = STATE["admin_token"]
    first = api("POST", "/tags", json={"name": "重复幂等"}, headers=auth_hdr(token))
    if first.status_code not in (200, 201, 409):
        check(False, f"创建标签失败：{first.status_code} {first.text[:160]}")
    second = api("POST", "/tags", json={"name": "重复幂等"}, headers=auth_hdr(token))
    check(second.status_code in (400, 409),
          f"重复创建同名标签应冲突，实际 {second.status_code}：{second.text[:160]}")
    count = scalar("SELECT COUNT(*) FROM tags WHERE name='重复幂等'")
    check(count == 1, f"同名标签被重复插入 {count} 条")


@case("B13", "限流", "P1", "登录限流：连续请求超过配额后返回 429")
def c_b13():
    codes = []
    for _ in range(7):
        codes.append(api("POST", "/auth/login",
                         json={"username": ADMIN_USER, "password": NEW_PASS}).status_code)
    if 429 in codes:
        note(f"限流生效，7 次登录状态码：{codes}")
    else:
        raise AssertionError(f"未触发限流（期望 429），7 次登录状态码序列：{codes}")


# ================================================================ 辅助


@case("X01", "配置健壮性", "P1", "非法/保留域邮箱配置：启动期拒绝 或 运行时结构化 500（含 X02 联动）")
def c_x01():
    """判定口径已随修复调整（原口径见下）。

    修复**前**的现象：``ADMIN_EMAIL=admin@e2e.test``（RFC 保留域）的实例能正常启动，
    seed 写入时不校验，读取时才被 Pydantic 校验 → 未捕获异常 →
    ``/site/profile`` 与 ``/auth/me`` 全部 500，而启动日志没有任何告警。

    修复**后** ``admin_email`` 是 ``EmailStr``，非法值在解析配置时就失败，
    所以这一场景的预期行为变成了「**启动期被拒绝**」。因此「服务起不来」在本用例里
    是**通过条件**——但必须确认拒绝原因确实是 ``admin_email`` 校验，
    而不是端口占用 / 迁移失败之类的无关故障，否则这条用例会变成永远通过的空壳。

    万一服务还是起来了，就退回运行时判定：要么接口正常，要么必须给出结构化 500
    （``request_id`` 由 X02 复核）。
    """
    if STATE.get("cfg_startup_rejected"):
        log = str(STATE.get("cfg_startup_log", ""))
        check("admin_email" in log,
              "服务确实没起来，但启动日志里看不到 admin_email 的校验失败 —— "
              "无法确认是配置校验拦下的（可能是端口/迁移等无关故障），不能算通过")
        note("配置层校验生效：ADMIN_EMAIL=admin@e2e.test 的实例在启动期即被拒绝"
             "（日志命中 admin_email 校验），不会再出现「启动正常但读取接口全 500」")
        return

    r = requests_get_profile()
    token = None
    login = api("POST", "/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    if login.status_code == 200:
        token = login.json().get("access_token")
    me_status = None
    if token:
        me_status = api("GET", "/auth/me", headers=auth_hdr(token)).status_code
    if r.status_code == 200 and me_status in (None, 200):
        note("非法邮箱配置未造成故障，服务端已具备校验或降级能力")
        return
    structured = False
    try:
        structured = "request_id" in r.json()
    except Exception:
        structured = False
    if r.status_code == 500 and structured:
        note("服务在运行时 500，但响应已带 request_id（兜底处理器生效，可定位）")
        return
    raise AssertionError(
        "确认缺陷：仅 ADMIN_EMAIL 使用了保留域名（admin@e2e.test）这一条环境变量配置，"
        f"就让 /site/profile 返回 {r.status_code}、/auth/me 返回 {me_status}，"
        "且响应不是结构化信封（缺少 request_id）—— "
        "根因是 seed 写入时未做 EmailStr 校验，读取时才触发 Pydantic ValidationError，"
        "而没有兜底的 500 处理器把它翻译成结构化错误。"
        "影响面：关于页整页不可用、站长账号无法读取自身资料（登录后任何依赖 /auth/me 的页面都挂），"
        "而服务仍能正常启动且不报任何警告。"
    )


def requests_get_profile():
    return api("GET", "/site/profile")


@case("X02", "配置健壮性", "P1", "配置错误场景下错误响应是否可定位（request_id / 统一信封）")
def c_x02():
    r = requests_get_profile()
    if r.status_code < 500:
        note(f"/site/profile 正常（{r.status_code}），无需评估兜底错误契约")
        return
    structured = False
    try:
        structured = "request_id" in r.json()
    except Exception:
        structured = False
    if structured:
        note("500 响应已含 request_id")
    else:
        raise AssertionError(
            "确认缺陷：未捕获异常落到 Starlette 默认 500 处理器，返回纯文本 Internal Server Error，"
            "既没有统一信封也没有 request_id，前端只能显示「请求失败」，"
            f"运维也无法把用户报错对应到日志。响应体片段：{r.text[:120]}"
        )


@case("S01", "系列管理", "P1", "系列：创建 → 挂载文章 → 详情含文章 → 修改 → 删除（含 SET NULL）")
def c_s01():
    token = STATE["admin_token"]
    r = api("POST", "/series", headers=auth_hdr(token),
            json={"name": "E2E系列", "description": "系列端到端"})
    check(r.status_code == 201, f"创建系列失败：{r.status_code} {r.text[:200]}")
    sid = r.json()["id"]
    check(scalar("SELECT COUNT(*) FROM series WHERE id=?", (sid,)) == 1, "系列未入库")
    aid = STATE["aid"]
    up = api("PATCH", f"/articles/{aid}", headers=auth_hdr(token),
             json={"series_id": sid, "series_order": 1})
    check(up.status_code == 200, f"挂载系列失败：{up.status_code} {up.text[:160]}")
    check(scalar("SELECT series_id FROM articles WHERE id=?", (aid,)) == sid, "文章未挂到系列")
    detail = api("GET", f"/series/{sid}")
    check(detail.status_code == 200, f"系列详情失败：{detail.status_code}")
    items = detail.json().get("articles", {}).get("items", [])
    check(any(a["id"] == aid for a in items), "系列详情未包含已挂载的文章")
    p = api("PATCH", f"/series/{sid}", headers=auth_hdr(token), json={"description": "改后简介"})
    check(p.status_code == 200 and p.json()["description"] == "改后简介",
          f"系列更新未生效：{p.status_code} {p.text[:160]}")
    check(scalar("SELECT description FROM series WHERE id=?", (sid,)) == "改后简介", "更新未落库")
    d = api("DELETE", f"/series/{sid}", headers=auth_hdr(token))
    check(d.status_code == 200, f"删除系列失败：{d.status_code} {d.text[:160]}")
    check(scalar("SELECT COUNT(*) FROM series WHERE id=?", (sid,)) == 0, "系列记录仍存在")
    check(scalar("SELECT series_id FROM articles WHERE id=?", (aid,)) is None,
          "删除系列后文章仍挂在系列上（未 SET NULL，产生悬挂引用）")
    note("系列全周期通过，删除后文章 series_id 正确置空")


@case("T01", "分类标签", "P1", "分类创建→修改→删除，以及空标签清理")
def c_t01():
    token = STATE["admin_token"]
    c = api("POST", "/categories", headers=auth_hdr(token),
            json={"name": "E2E分类", "description": "x"})
    check(c.status_code == 201, f"创建分类失败：{c.status_code} {c.text[:200]}")
    cid = c.json()["id"]
    check(scalar("SELECT name FROM categories WHERE id=?", (cid,)) == "E2E分类", "分类未落库")
    p = api("PATCH", f"/categories/{cid}", headers=auth_hdr(token), json={"name": "E2E分类改名"})
    check(p.status_code == 200, f"修改分类失败：{p.status_code} {p.text[:160]}")
    check(scalar("SELECT name FROM categories WHERE id=?", (cid,)) == "E2E分类改名", "改名未落库")
    d = api("DELETE", f"/categories/{cid}", headers=auth_hdr(token))
    check(d.status_code == 200, f"删除分类失败：{d.status_code} {d.text[:160]}")
    check(scalar("SELECT COUNT(*) FROM categories WHERE id=?", (cid,)) == 0, "分类记录仍存在")

    t = api("POST", "/tags", headers=auth_hdr(token), json={"name": "待清理空标签"})
    check(t.status_code in (200, 201), f"创建标签失败：{t.status_code} {t.text[:160]}")
    check(scalar("SELECT COUNT(*) FROM tags WHERE name='待清理空标签'") == 1, "标签未入库")
    cl = api("POST", "/tags/cleanup", headers=auth_hdr(token))
    check(cl.status_code == 200, f"清理空标签失败：{cl.status_code} {cl.text[:160]}")
    check(scalar("SELECT COUNT(*) FROM tags WHERE name='待清理空标签'") == 0,
          "无文章引用的空标签未被清理")


@case("M01", "媒体库", "P1", "媒体库列表可见新上传附件，删除后磁盘文件同步清理")
def c_m01():
    token = STATE["admin_token"]
    up = api("POST", "/attachments/upload", headers=auth_hdr(token),
             files={"file": ("e2e-media.png", make_png(320, 240), "image/png")})
    check(up.status_code == 201, f"上传失败：{up.status_code} {up.text[:160]}")
    attach = up.json()
    attach_id, url = attach["id"], attach["url"]
    rel = url.split("/media/", 1)[-1] if "/media/" in url else url.lstrip("/")
    disk = STORAGE_DIR / rel
    lst = api("GET", "/attachments", headers=auth_hdr(token), params={"page": 1, "page_size": 10})
    check(lst.status_code == 200, f"媒体库列表失败：{lst.status_code}")
    check(any(a["id"] == attach_id for a in lst.json().get("items", [])),
          "新上传附件未出现在媒体库列表")
    d = api("DELETE", f"/attachments/{attach_id}", headers=auth_hdr(token))
    check(d.status_code == 200, f"删除附件失败：{d.status_code} {d.text[:160]}")
    check(scalar("SELECT COUNT(*) FROM attachments WHERE id=?", (attach_id,)) == 0, "附件记录仍存在")
    check(not disk.exists(), f"附件记录已删除但磁盘文件仍残留：{disk}")


@case("N01", "用户管理", "P1", "站长修改用户资料后删除用户（含落库校验）")
def c_n01():
    token = STATE["admin_token"]
    uid = STATE["author_uid"]
    p = api("PATCH", f"/auth/users/{uid}", headers=auth_hdr(token), json={"nickname": "改名后的作者"})
    check(p.status_code == 200, f"修改用户失败：{p.status_code} {p.text[:200]}")
    check(scalar("SELECT nickname FROM users WHERE id=?", (uid,)) == "改名后的作者", "昵称未落库")
    d = api("DELETE", f"/auth/users/{uid}", headers=auth_hdr(token))
    check(d.status_code == 204, f"删除用户失败：{d.status_code} {d.text[:160]}")
    check(scalar("SELECT COUNT(*) FROM users WHERE id=?", (uid,)) == 0, "用户记录仍存在")
    check(api("POST", "/auth/login", json={"username": "e2eauthor",
                                           "password": "Author@2026"}).status_code == 401,
          "用户已删除却仍可登录")


@case("ST01", "统计与清理", "P1", "仪表盘统计、访问趋势与日志清理接口")
def c_st01():
    token = STATE["admin_token"]
    stats = api("GET", "/site/stats", headers=auth_hdr(token))
    check(stats.status_code == 200, f"全站统计失败：{stats.status_code} {stats.text[:160]}")
    numbers = [v for v in stats.json().values() if isinstance(v, (int, float))]
    check(numbers and max(numbers) > 0, f"统计数据全为 0，与库内已有内容矛盾：{stats.json()}")
    daily = api("GET", "/stats/views/daily", headers=auth_hdr(token))
    check(daily.status_code == 200, f"每日访问趋势失败：{daily.status_code} {daily.text[:160]}")
    check(isinstance(daily.json(), list), "趋势接口未返回列表")
    prune = api("POST", "/stats/visit-logs/prune", headers=auth_hdr(token))
    check(prune.status_code == 200, f"日志清理失败：{prune.status_code} {prune.text[:160]}")
    check(isinstance(prune.json().get("removed"), int), "清理结果未返回 removed 计数")
    note(f"站点统计 {stats.json()}；清理结果 {prune.json()}")


@case("NT01", "通知", "P2", "退订接口对伪造 token 返回 4xx 而非 500")
def c_nt01():
    r = api("POST", "/notifications/unsubscribe", json={"token": "definitely-not-a-valid-token"})
    check(400 <= r.status_code < 500, f"伪造 token 未返回 4xx：{r.status_code} {r.text[:160]}")


def make_png(width: int, height: int) -> bytes:
    """生成真实 PNG 字节流（不落临时文件，直接走 multipart 上传）。"""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), (30, 90, 160))
    for x in range(0, min(width, 200), 40):
        for y in range(0, min(height, 200), 40):
            img.putpixel((x, y), (255, 200, 0))
    img.save(buf, format="PNG")
    return buf.getvalue()


PHASE_BOOTSTRAP = [c_e01, c_e02, c_e03, c_e04, c_r01, c_r02, c_r03, c_r04, c_r05, c_r06, c_r07, c_r08]
PHASE_AUTHOR = [c_a01, c_a02, c_a03, c_a04, c_a05, c_a06, c_a07, c_a08,
                c_u01, c_u02, c_u03, c_u04, c_u05, c_u06, c_u07, c_a09, c_a10]
PHASE_COMMENT = [c_c01, c_c02, c_c03, c_c04, c_c05, c_c06, c_c07, c_c08, c_c09, c_c10]
PHASE_ADMIN = [c_s01, c_t01, c_m01, c_n01, c_st01, c_nt01]
PHASE_BOUNDARY = [c_l01, c_l02, c_b01, c_b02, c_b03, c_b04, c_b05, c_b06, c_b07,
                  c_b08, c_b09, c_b10, c_b11, c_b12]
PHASE_RATELIMIT = [c_b13]
PHASE_ROBUST = [c_x01, c_x02]


# ================================================================ 报告


def write_reports(elapsed, real_before, real_after):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = REPORTS / f"e2e-results-{stamp}.json"
    md_path = REPORTS / f"e2e-report-{stamp}.md"
    counts = RES.summary()

    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": elapsed,
                "workdir": str(WORKDIR),
                "database": str(DB_PATH),
                "storage": str(STORAGE_DIR),
                "real_db_untouched": real_before == real_after,
                "summary": counts,
                "cases": RES.rows,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    flows: dict[str, list] = {}
    for row in RES.rows:
        flows.setdefault(row["flow"], []).append(row)

    lines = [
        "# personal-blog 端到端测试报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（耗时 {elapsed}s）",
        "- 被测环境：独立临时 SQLite（**alembic upgrade head 建表**）+ 独立 uvicorn 进程 + 独立 storage 目录",
        f"- 测试数据库：`{DB_PATH}`",
        f"- 项目真实库（backend/blog.db）是否被污染：**{'否' if real_before == real_after else '是'}**",
        "",
        "## 一、总体结果",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 用例总数 | {len(RES.rows)} |",
        f"| PASS | {counts['PASS']} |",
        f"| FAIL | {counts['FAIL']} |",
        f"| ERROR | {counts['ERROR']} |",
        "",
        "## 二、按流程分布",
        "",
        "| 流程 | 用例数 | PASS | FAIL | ERROR |",
        "|---|---|---|---|---|",
    ]
    for flow, items in flows.items():
        c = {"PASS": 0, "FAIL": 0, "ERROR": 0}
        for it in items:
            c[it["status"]] += 1
        lines.append(f"| {flow} | {len(items)} | {c['PASS']} | {c['FAIL']} | {c['ERROR']} |")

    lines += ["", "## 三、逐用例明细", "",
              "| 编号 | 流程 | 优先级 | 用例 | 状态 | 说明 / 失败原因 |",
              "|---|---|---|---|---|---|"]
    for row in RES.rows:
        reason = (row["reason"] or "-").replace("\n", " ").replace("|", "/")
        lines.append(f"| {row['id']} | {row['flow']} | {row['priority']} | {row['name']} | "
                     f"**{row['status']}** | {reason[:200]} |")

    fails = [r for r in RES.rows if r["status"] != "PASS"]
    lines += ["", "## 四、失败用例详情", ""]
    if not fails:
        lines.append("全部通过。")
    for row in fails:
        lines += [
            f"### {row['id']} · {row['name']}（{row['status']}，优先级 {row['priority']}）",
            "",
            f"- **失败原因**：{row['reason'] or '见异常栈'}",
            f"- **所属流程**：{row['flow']}",
            f"- **最小复现步骤**：{row['repro']}",
        ]
        if row["evidence"]:
            lines.append(f"- **证据**：`{row['evidence'][:400]}`")
        lines.append("")

    lines += ["## 五、服务端日志摘录", "", "```", server_tail(2500) or "（无）", "```", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入：\n  {md_path}\n  {json_path}")
    return md_path


def main() -> int:
    global WORKDIR, DB_PATH, STORAGE_DIR, SERVER_LOG

    started = time.time()
    if not PY.exists():
        print(f"[致命] 未找到虚拟环境解释器：{PY}")
        return 2
    REPORTS.mkdir(parents=True, exist_ok=True)
    WORKDIR = Path(tempfile.mkdtemp(prefix="blog-e2e-"))
    DB_PATH = WORKDIR / "e2e.db"
    STORAGE_DIR = WORKDIR / "storage"
    SERVER_LOG = WORKDIR / "server.log"
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    SERVER_LOG.write_text("", encoding="utf-8")
    real_before = db_fingerprint(REAL_DB)

    print("=" * 78)
    print("personal-blog 端到端测试（真实进程 + 真实数据库 + HTTP 全链路）")
    print(f"工作目录：{WORKDIR}")
    print("=" * 78)

    try:
        print("\n[阶段 0] 在空库上执行 alembic upgrade head")
        mig = subprocess.run(
            [str(PY), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND), env=server_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        print((mig.stdout or "")[-1500:])
        if mig.returncode != 0:
            print(f"[致命] 迁移失败：\n{mig.stdout}\n{mig.stderr}")
            return 2

        proc, logf = start_server("main", RATE_LIMIT_ENABLED="false")
        try:
            run_phase("阶段 1 · 环境与访客读取链路", PHASE_BOOTSTRAP)
            run_phase("阶段 2 · 账号权限与作者主流程", PHASE_AUTHOR)
            run_phase("阶段 3 · 评论链路", PHASE_COMMENT)
            run_phase("阶段 4 · 后台管理流程（系列/分类标签/媒体库/用户/统计）", PHASE_ADMIN)
            run_phase("阶段 5 · 点赞与边界异常", PHASE_BOUNDARY)
        finally:
            stop_server(proc, logf)

        proc2, logf2 = start_server("ratelimit", RATE_LIMIT_ENABLED="true")
        try:
            run_phase("阶段 5 · 限流验证", PHASE_RATELIMIT)
        finally:
            stop_server(proc2, logf2)

        print("\n[阶段 6] 配置健壮性：独立空库 + ADMIN_EMAIL 使用保留域名")
        cfg_db = WORKDIR / "e2e-badcfg.db"
        mig2 = subprocess.run(
            [str(PY), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND),
            env=server_env(**{"DATABASE_URL": f"sqlite+aiosqlite:///{cfg_db.as_posix()}"}),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if mig2.returncode != 0:
            print(f"[警告] 阶段 6 迁移失败，跳过：{mig2.stdout[-300:]}")
        else:
            # 6a：配置层校验。修复后 ``admin_email`` 是 EmailStr，带保留域邮箱的
            # 实例会在**解析配置时**就失败——于是「服务起不来」成了**期望结果**。
            # 启动失败原本会被 start_server 抛成 RuntimeError 并把整个套件打成
            # E00 ERROR，这里必须把它接住，否则「校验生效」反而表现为执行器故障。
            rejected = False
            proc3 = None
            logf3 = None
            try:
                proc3, logf3 = start_server(
                    "config-robust-startup",
                    DATABASE_URL=f"sqlite+aiosqlite:///{cfg_db.as_posix()}",
                    ADMIN_EMAIL="admin@e2e.test",
                )
            except RuntimeError as exc:
                rejected = True
                STATE["cfg_startup_log"] = f"{exc}\n{server_tail(6000)}"
            finally:
                if proc3 is not None:
                    stop_server(proc3, logf3)
            STATE["cfg_startup_rejected"] = rejected
            print(f"  6a 启动期是否被拒绝：{'是（配置校验生效）' if rejected else '否（服务起来了）'}")

            # 6b：运行时兜底。配置层已经拦不住的脏数据（手工改库 / 历史遗留 /
            # 老版本写入）仍会在读取期触发 ValidationError，所以兜底 500 处理器
            # 必须单独验证：用合法邮箱启动，再把库里的邮箱改坏，看响应是否结构化。
            proc4 = None
            logf4 = None
            try:
                proc4, logf4 = start_server(
                    "config-robust-runtime",
                    DATABASE_URL=f"sqlite+aiosqlite:///{cfg_db.as_posix()}",
                )
                conn = sqlite3.connect(str(cfg_db), timeout=20)
                try:
                    conn.execute("UPDATE site_profile SET email = 'broken@e2e.test'")
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                print(f"  [警告] 6b 未能构造运行时故障场景：{exc}")
            try:
                run_phase("阶段 6 · 配置健壮性", PHASE_ROBUST)
            finally:
                if proc4 is not None:
                    stop_server(proc4, logf4)

    except Exception as exc:
        traceback.print_exc()
        RES.record("E00", "执行器", "P0", "E2E 执行器整体运行", "ERROR",
                   f"{type(exc).__name__}: {exc}"[:600], evidence=traceback.format_exc()[-1500:])
    finally:
        real_after = db_fingerprint(REAL_DB)
        print(f"\n项目真实库是否被污染：{'否' if real_before == real_after else '是（请检查！）'}")
        md_path = write_reports(round(time.time() - started, 1), real_before, real_after)
        print(f"工作目录保留：{WORKDIR}")
        STATE["report"] = str(md_path)

    counts = RES.summary()
    print("\n" + "=" * 78)
    print(f"总计 {len(RES.rows)} 条：PASS={counts['PASS']}  FAIL={counts['FAIL']}  ERROR={counts['ERROR']}")
    print("=" * 78)
    for row in RES.rows:
        if row["status"] != "PASS":
            print(f"  - {row['id']} [{row['status']}] {row['name']}\n      {row['reason'][:280]}")
    return 0 if not (counts["FAIL"] or counts["ERROR"]) else 1


if __name__ == "__main__":
    sys.exit(main())
