"""把旧站内容导入新站。

## 为什么走 HTTP API 而不是直接写数据库

1. **复用整套服务层校验**：图片要过 Pillow 真解码（不是看扩展名）、像素炸弹上限、
   体积上限、正文长度、slug 唯一化、标签自动创建——直插 DB 等于把这些全部绕过，
   而这个仓库最忌讳的就是绕过服务层。
2. **顺带拿到衍生数据**：缩略图、多尺寸变体（AVIF/WEBP）、附件记录都由
   ``AttachmentService`` 生成。直插 DB 只能得到"一个能显示的原图"。
3. **可重跑、可对任何环境用**：本地、预发、线上只是 ``--api`` 不同；
   而且它天然是幂等的（下详），出问题不会有"半截数据 + 不知道从哪继续"的状态。
4. **留痕**：附件与文章都带真实的创建记录，事后能查、能删、能回滚。

代价是慢（50 张图要真编码变体）和依赖服务在跑。对一次性迁移都值得。

## 幂等性怎么保证

- **图片**：先按内容 SHA-256 查本地缓存（``--cache``）；没有再上传。
  服务端文件名由我们指定为 ``{hash[:16]}{ext}``，同一份内容重复上传也只会
  得到同一个 URL（覆盖写），不会产生垃圾文件。
- **文章**：导入前先拉一次全站文章列表（后台接口，含草稿），拿到已有 slug 集合。
  默认 ``--skip-existing``：slug 已存在就跳过；``--update``：改用 ``PATCH`` 覆盖正文。
  两种模式下重复跑都收敛到同一个结果。

## 安全

- 默认 **dry-run**：只打印将要发生什么，不发任何写请求。要真导入必须显式 ``--apply``。
- 凭据从环境变量取（``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``），不从命令行取——
  命令行参数会进 shell 历史与进程列表。
- 图片缺失时**不中断**：如实记进尾注，继续处理下一篇。迁移最怕"跑到第 17 篇崩了"。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parse_hexo import (
    ParsedPost,
    content_hash,
    normalize_asset_key,
    parse_repository,
    resolve_asset,
)
from transform import ORIGIN, expected_asset_name, prepare_post, upload_subdir

DEFAULT_HEXO = Path(r"D:\Projects\01_个人项目\blog")
DEFAULT_API = "http://127.0.0.1:8000/api/v1"
DEFAULT_CACHE = Path(__file__).resolve().parent / ".import-cache.json"

#: 每次 HTTP 请求的超时。图片编码变体是秒级 CPU 活，给宽一点。
REQUEST_TIMEOUT = 120

#: 目录的分类与标签数上限（仓库 schema 里 tags 上限 10，分类只有一个）
MAX_TAGS = 10

#: 后台列表接口的单页上限。服务端 ``PageParams`` 把 ``page_size`` 限制在 50 以内，
#: 写 100 会直接 422（这个值是从真实报错里量出来的，不是猜的）。
PAGE_SIZE = 50

#: slug 归一规则，**必须与 ``app/utils/text.py`` 的 ``_SLUG_STRIP`` / ``_SLUG_COLLAPSE``
#: 逐字符一致**。这里复制一份而不是 import 后端模块：迁移工具要能独立运行
#: （预检就不需要后端在场），而且它不进生产镜像。
#: 一致性由 ``tests/test_import_hexo.py`` 的交叉校验测试守住。
_SLUG_STRIP = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_SLUG_COLLAPSE = re.compile(r"-{2,}")

#: ``sticky`` 保留为置顶的阈值。``None`` 表示「有 sticky 就置顶」。
#:
#: 这个值不是随便定的，它由**新站列表的排序语义**倒推出来：
#: 前台列表默认 ``top_first=True``（``Article.is_top`` 优先），而首页把第一篇渲染成
#: **featured 头条**、其余进「更多文章」区，每页默认 10 条。
#: 所以置顶数必须远小于每页条数，否则第一页全是置顶，头条机制与
#: 「更多文章」区一起失效——实测「有 sticky 就置顶」时 16 篇置顶，
#: 首页第一页 10 张卡片**全部**挂「置顶」徽标，这个信号等于失效。
#:
#: 旧站 sticky 从 27 递减到 5；取 24 留下 3 篇长篇教程，与「首页头条」的意图匹配。
DEFAULT_TOP_THRESHOLD: int | None = 24


# ---------------------------------------------------------------- 结果类型


@dataclass
class UploadedAsset:
    """一张已上传（或已在缓存里）的图片。"""

    asset_key: str
    digest: str
    url: str
    size: int


@dataclass
class PostOutcome:
    """一篇的导入结果。"""

    stem: str
    title: str
    status: str  # created | updated | skipped | failed
    article_id: int | None = None
    slug: str | None = None
    detail: str = ""
    images_uploaded: int = 0
    images_missing: int = 0
    body_bytes: int = 0


@dataclass
class ImportOptions:
    """一次导入的全部可调项。"""

    hexo_root: Path = DEFAULT_HEXO
    api: str = DEFAULT_API
    username: str = "admin"
    password: str = ""
    apply: bool = False
    update: bool = False
    only: list[str] = field(default_factory=list)
    limit: int | None = None
    cache_path: Path = DEFAULT_CACHE
    report_path: Path | None = None
    #: dry-run 时也要把有封面的图片算进去，所以默认处理
    timeout: int = REQUEST_TIMEOUT
    #: ``sticky`` 保留为置顶的阈值（见 :data:`DEFAULT_TOP_THRESHOLD`）。
    #: ``None`` 表示「有 sticky 就置顶」。
    top_threshold: int | None = DEFAULT_TOP_THRESHOLD


# ---------------------------------------------------------------- HTTP 客户端


class ApiClient:
    """最小 HTTP 客户端。

    只依赖标准库 ``urllib``（仓库里 ``scripts/backup_db.py`` 等也是这个口径）：
    迁移工具不该为几个请求往生产依赖里加一个 HTTP 库。
    """

    def __init__(self, base: str, timeout: int = REQUEST_TIMEOUT) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    # ---------------------------------------------------------- 基础请求

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        multipart: tuple[str, str, bytes] | None = None,
        auth: bool = True,
        allow: Iterable[int] = (200, 201, 204),
    ) -> tuple[int, Any]:
        """发一次请求，返回 ``(状态码, 解析后的响应体)``。

        非预期状态码**抛异常**而不是返回错误码：调用方基本上都要么成功、
        要么就该带着上下文失败，让每个调用点自己判断状态码只会到处漏判。
        """
        url = f"{self.base}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        data: bytes | None = None

        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif multipart is not None:
            filename, content_type, payload = multipart
            boundary = f"----hexoimport{uuid.uuid4().hex}"
            data = _encode_multipart(boundary, filename, content_type, payload)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

        if auth:
            if not self.token:
                raise RuntimeError("尚未登录，无法调用需要认证的接口")
            headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            body = _decode(raw)
            if exc.code in allow:
                return exc.code, body
            raise ApiError(method, path, exc.code, body) from exc
        except urllib.error.URLError as exc:
            raise ApiError(method, path, 0, str(exc.reason)) from exc

        body = _decode(raw)
        if status not in allow:
            raise ApiError(method, path, status, body)
        return status, body

    # ---------------------------------------------------------- 具体接口

    def login(self, username: str, password: str) -> None:
        status, body = self.request(
            "POST",
            "/auth/login",
            json_body={"username": username, "password": password},
            auth=False,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
            raise RuntimeError(f"登录失败：{status} {body}")
        self.token = str(body["access_token"])

    def existing_articles(self) -> dict[str, dict[str, Any]]:
        """拉全站文章（含草稿），返回 ``slug -> 文章``。

        用后台接口 ``/articles/manage/list``：前台接口只返回已发布，
        草稿会看不见，于是「重跑时又建了一份草稿」这种重复就拦不住。

        分页上限是 **50**（``PageParams`` 的 ``page_size`` 上限），不是常见的 100。
        按 50 翻页直到收满 ``total``。
        """
        found: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            _, body = self.request(
                "GET",
                f"/articles/manage/list?page={page}&page_size={PAGE_SIZE}&sort=updated",
            )
            items = body.get("items", []) if isinstance(body, dict) else []
            for item in items:
                found[str(item["slug"])] = item
            total = body.get("total", 0) if isinstance(body, dict) else 0
            if not items or page * PAGE_SIZE >= total:
                break
            page += 1
        return found

    def upload_image(self, *, filename: str, content_type: str, payload: bytes) -> dict[str, Any]:
        _, body = self.request(
            "POST",
            "/attachments/upload",
            multipart=(filename, content_type, payload),
            allow=(201,),
        )
        if not isinstance(body, dict) or not body.get("url"):
            raise RuntimeError(f"上传响应异常：{body}")
        return body

    def create_article(self, payload: dict[str, Any]) -> dict[str, Any]:
        _, body = self.request("POST", "/articles", json_body=payload, allow=(201, 200))
        return body if isinstance(body, dict) else {}

    def update_article(self, article_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        _, body = self.request("PATCH", f"/articles/{article_id}", json_body=payload)
        return body if isinstance(body, dict) else {}

    def create_category(self, name: str) -> dict[str, Any]:
        _, body = self.request("POST", "/categories", json_body={"name": name}, allow=(201, 200))
        return body if isinstance(body, dict) else {}


class ApiError(RuntimeError):
    """接口调用失败，带上方法、路径与响应体（排错全靠这三个）。"""

    def __init__(self, method: str, path: str, status: int, body: Any) -> None:
        detail = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
        super().__init__(f"{method} {path} -> HTTP {status}: {detail[:400]}")
        self.status = status
        self.body = body


def _encode_multipart(boundary: str, filename: str, content_type: str, payload: bytes) -> bytes:
    """手写 multipart/form-data 编码。

    字段名固定为 ``file``（见 ``api/v1/attachments.py`` 的 ``File(...)``）。
    文件名一定要用 ``filename*=`` 之外的普通 ``filename`` 并做 ASCII 兜底：
    ``httpx``/``requests`` 会按 RFC 5987 处理中文名，但服务端只把
    ``filename`` 存进 ``original_name`` 字段用于展示，不参与路径计算，
    所以这里给一个安全的英文名即可。
    """
    safe_name = filename.encode("ascii", "ignore").decode("ascii") or "image"
    lines = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"'.encode(),
        f"Content-Type: {content_type}".encode(),
        b"",
        payload,
        f"--{boundary}--".encode(),
        b"",
    ]
    return b"\r\n".join(lines)


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- 导入器


class HexoImporter:
    """一次迁移的执行者。"""

    def __init__(self, options: ImportOptions, client: ApiClient | None = None) -> None:
        self.options = options
        self.client = client or ApiClient(options.api, timeout=options.timeout)
        self.posts_dir = options.hexo_root / "source" / "_posts"
        #: digest -> url，跨文章共享，也持久化到磁盘
        self.uploads: dict[str, str] = {}
        self.outcomes: list[PostOutcome] = []
        self.existing: dict[str, dict[str, Any]] = {}
        #: 分类名 -> id（含 None 表示确实取不到，避免对同一名字反复重试）
        self._category_ids: dict[str, int | None] = {}
        self._cache_dirty = False

    # ---------------------------------------------------------- 缓存

    def load_cache(self) -> None:
        """读图片上传缓存。

        缓存的意义是**重跑不重复上传**：50 张图含 AVIF 变体编码，一轮要几十秒；
        迁移过程中途失败、改完再跑是常态，每次都从头传一遍纯属浪费。
        缓存键是内容哈希，所以图片被改过就自然重新上传。
        """
        path = self.options.cache_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"  {_ICON_WARN} 缓存文件损坏，忽略：{path}", file=sys.stderr)
            return
        if isinstance(data, dict):
            self.uploads = {str(k): str(v) for k, v in data.items()}
            print(f"  已载入上传缓存：{len(self.uploads)} 条")

    def save_cache(self) -> None:
        if not self._cache_dirty or not self.options.apply:
            return
        path = self.options.cache_path
        path.write_text(
            json.dumps(self.uploads, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )

    # ---------------------------------------------------------- 主流程

    def run(self) -> list[PostOutcome]:
        posts = parse_repository(self.posts_dir)
        if self.options.only:
            wanted = {name.lower() for name in self.options.only}
            posts = [
                post
                for post in posts
                if post.stem.lower() in wanted or post.source.name.lower() in wanted
            ]
        if self.options.limit:
            posts = posts[: self.options.limit]

        mode = "应用" if self.options.apply else "干跑（不发写请求）"
        print(f"模式：{mode}")
        print(f"旧站：{self.options.hexo_root}")
        print(f"接口：{self.options.api}")
        print(f"文章：{len(posts)} 篇")
        print()

        self.load_cache()
        if self.options.apply:
            self.client.login(self.options.username, self.options.password)
            print("  登录成功")
            self.existing = self.client.existing_articles()
            print(f"  目标站现有文章：{len(self.existing)} 篇")
        print()

        for index, post in enumerate(posts, start=1):
            outcome = self._import_one(index, len(posts), post)
            self.outcomes.append(outcome)

        self.save_cache()
        return self.outcomes

    # ---------------------------------------------------------- 单篇

    def _import_one(self, index: int, total: int, post: ParsedPost) -> PostOutcome:
        prefix = f"[{index}/{total}] {post.stem}"
        missing = [ref for ref in post.images if not ref.exists and not ref.is_remote]
        try:
            asset_urls, pending = self._ensure_assets(post)
        except ApiError as exc:
            print(f"{prefix}  {_ICON_FAILED} 图片上传失败：{exc}")
            return PostOutcome(
                stem=post.stem,
                title=post.title,
                status="failed",
                detail=str(exc)[:300],
                images_missing=len(missing),
            )

        _origin, body = prepare_post(post, asset_urls)

        payload = self._build_payload(post, body)
        uploaded = len(asset_urls)

        if not self.options.apply:
            print(
                f"{prefix}  [干跑] {len(body) / 1024:.1f} KB｜图片 {uploaded} 张"
                f"（待传 {pending}）｜缺失 {len(missing)}｜{payload['status']}"
            )
            return PostOutcome(
                stem=post.stem,
                title=post.title,
                status="dry-run",
                images_uploaded=0,
                images_missing=len(missing),
                body_bytes=len(body.encode("utf-8")),
            )

        slug = self._slug_for(post)
        # 幂等：先用服务端算好的 slug 查，再用「标题完全相同」兜底。
        #
        # 为什么需要兜底：服务端的 unique_slug 在重名时会加 -2 后缀，
        # 所以重跑时算出来的 slug 可能变成 `foo-2`，而库里那篇叫 `foo`。
        # 只看 slug 就会认为"不存在"，于是每次都新建一份——
        # 这正是第一次试跑时暴露出来的重复问题。
        existing = self.existing.get(slug) or self._find_by_title(post.title)

        if existing is not None and not self.options.update:
            print(f"{prefix}  {_ICON_SKIPPED}  已存在（slug={existing['slug']}），跳过")
            return PostOutcome(
                stem=post.stem,
                title=post.title,
                status="skipped",
                article_id=int(existing["id"]),
                slug=str(existing["slug"]),
                images_missing=len(missing),
                body_bytes=len(body.encode("utf-8")),
            )

        try:
            if existing is not None:
                article = self.client.update_article(int(existing["id"]), payload)
                outcome_status, article_id = "updated", int(existing["id"])
                slug = str(existing["slug"])
            else:
                article = self.client.create_article(payload)
                outcome_status, article_id = (
                    "created",
                    int(article.get("id", 0)) or None,
                )
                slug = str(article.get("slug") or slug)
                self.existing[slug] = {
                    "id": article_id,
                    "slug": slug,
                    "title": post.title,
                }
        except ApiError as exc:
            print(f"{prefix}  {_ICON_FAILED} 创建失败：{exc}")
            return PostOutcome(
                stem=post.stem,
                title=post.title,
                status="failed",
                detail=str(exc)[:300],
                images_missing=len(missing),
            )

        icon = _ICON_CREATED if outcome_status == "created" else _ICON_UPDATED
        print(
            f"{prefix}  {icon} {outcome_status} id={article_id} slug={slug}"
            f"｜{len(body) / 1024:.1f} KB｜图片 {uploaded} 张｜缺失 {len(missing)}"
        )
        return PostOutcome(
            stem=post.stem,
            title=post.title,
            status=outcome_status,
            article_id=article_id,
            slug=slug,
            images_uploaded=uploaded,
            images_missing=len(missing),
            body_bytes=len(body.encode("utf-8")),
        )

    def _find_by_title(self, title: str) -> dict[str, Any] | None:
        for item in self.existing.values():
            if str(item.get("title", "")).strip() == title.strip():
                return item
        return None

    def _slug_for(self, post: ParsedPost) -> str:
        """本地估算 slug，用于**预判是否已存在**。

        必须与服务端 ``app.utils.text.slugify`` **逐字符一致**，否则重跑时
        会把已存在的文章误判为"不存在"，于是每次都新建一份重复。

        实测踩过：最初写成"用正则删掉所有非字母数字汉字"，而服务端是
        "把连续的非词字符替换成 **一个连字符**"——`算法竞赛C++STL常用用法`
        在服务端是 `算法竞赛c-stl常用用法`，我这边却算成 `算法竞赛cstl常用用法`，
        23 篇里有 5 篇对不上。

        ``tests/test_import_hexo.py`` 里有一条测试会在后端代码可读时
        直接调用真正的 ``slugify`` 做交叉校验，防它再次漂移。

        **已知且不可避免的差异**：标题里一个有效字符都没有时（如 ``！！！``），
        服务端返回 ``article-{随机 8 位}``——随机的，本地不可能算出来。
        这类文章靠 :meth:`_find_by_title` 的标题匹配兜底，不影响幂等性。
        旧站 23 篇没有这种标题（有测试断言每篇标题都能算出可读 slug）。
        """
        normalized = unicodedata.normalize("NFKC", post.title or "").strip().lower()
        normalized = _SLUG_STRIP.sub("-", normalized)
        normalized = _SLUG_COLLAPSE.sub("-", normalized).strip("-")
        if not normalized:
            return f"article-{uuid.uuid4().hex[:8]}"
        return normalized[:80].strip("-")

    # ---------------------------------------------------------- 图片

    def _ensure_assets(self, post: ParsedPost) -> tuple[dict[str, str], int]:
        """确保本篇用到的图片都已上传。

        返回 ``(asset_key -> url, 本轮还需要新上传的张数)``。

        **URL 一律来自上传响应**（或缓存），绝不自己拼：服务端按
        ``uploads/<YYYYMM>/`` 分目录落盘，月份取决于上传那一刻的时间。
        自己拼 ``/media/uploads/{name}`` 会少一层月份目录，结果是
        **全站图片 404 而导入过程毫无报错**——这个坑已经真实踩过一次，
        所以干跑时也用同一套规则构造 URL，保证干跑与真跑的形态完全一致。

        去重按**内容哈希**：同一张图出现在多篇文章里只上传一次，
        重跑时命中缓存直接复用 URL。
        """
        urls: dict[str, str] = {}
        pending = 0
        subdir = upload_subdir()
        for ref in post.images:
            if not ref.exists:
                continue
            path = resolve_asset(self.posts_dir, ref.asset_key)
            if path is None:  # pragma: no cover - exists=True 时不应发生
                continue

            payload = path.read_bytes()
            digest = content_hash(payload)
            cached = self.uploads.get(digest)

            if cached:
                urls[ref.asset_key] = cached
                continue

            if not self.options.apply:
                # 干跑不联网，但 URL 形状要与真跑一致（含月份目录）
                name = expected_asset_name(ref.asset_key, digest)
                urls[ref.asset_key] = f"/media/uploads/{subdir}/{name}"
                pending += 1
                continue

            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            uploaded = self.client.upload_image(
                filename=expected_asset_name(ref.asset_key, digest),
                content_type=content_type,
                payload=payload,
            )
            url = str(uploaded["url"])
            self.uploads[digest] = url
            urls[ref.asset_key] = url
            self._cache_dirty = True

        return urls, pending

    # ---------------------------------------------------------- 组装 payload

    def _build_payload(self, post: ParsedPost, body: str) -> dict[str, Any]:
        """组装 ``POST /articles`` 的请求体。

        字段映射逐条说明：

        - ``title``：直接用旧站标题（含 ``C++``、``(从入门到入土)`` 这类字符）。
        - ``slug``：留空，交给服务端按标题生成 —— 新站的 slugify 保留中文
          （现有文章就是 ``/articles/为什么我把博客的数据层重写了一遍``），
          所以「按标题生成」本身就得到可读 URL。
        - ``status``：``hide: true`` → ``draft``（用户决定：由作者在后台决定是否发布）。
        - ``published_at``：旧站的 ``date``。旧站是 UTC+8 写的，这里按时区的
          UTC 时刻交出去，展示层再按东八区渲染，日期与旧站一致。
        - ``is_top``：旧站 ``sticky`` 达阈值即置顶（见 :data:`DEFAULT_TOP_THRESHOLD`）。
          新站只有布尔值、没有排序权重，所以**名次无法保留**；
          原始 ``sticky`` 写进尾注留档（见 ``transform.build_disclaimer``）。
        - ``category_id``：这里**不传**——分类是"不存在就创建"，但要先反查 id。
          交给 ``_resolve_category`` 在真实导入时做（干跑不查库）。
        - ``cover_image``：``index_img`` 指向的图也当附件传一遍，拿到站内 URL，
          避免新站仍然依赖旧站域名（旧站哪天关掉，封面就全挂）。
        """
        payload: dict[str, Any] = {
            "title": post.title,
            "content_md": body,
            "status": "draft" if post.hidden else "published",
            "is_top": self._is_top(post),
            "allow_comment": True,
            "tags": post.tags[:MAX_TAGS],
        }
        published_at = _parse_hexo_date(post.date_raw)
        if published_at:
            payload["published_at"] = published_at

        # 封面：index_img 指向的图也当附件传一遍，拿到站内 URL。
        # 不沿用旧站的绝对/相对路径——旧站哪天关掉，封面就全挂。
        if post.cover_raw:
            cover_url = self._ensure_cover(post)
            if cover_url:
                payload["cover_image"] = cover_url

        if post.categories and self.options.apply:
            category_id = self._resolve_category(post.categories[0])
            if category_id:
                payload["category_id"] = category_id
        return payload

    def _is_top(self, post: ParsedPost) -> bool:
        """旧站 ``sticky`` 是否应保留为置顶。

        阈值的存在理由见 :data:`DEFAULT_TOP_THRESHOLD`：新站的置顶是**首页头条**语义，
        数量必须远小于每页条数。``top_threshold=None``（CLI ``--top-threshold 0``）
        表示「有 sticky 就置顶」，用于将来想全部保留时。
        """
        if post.sticky is None:
            return False
        threshold = self.options.top_threshold
        return True if threshold is None else post.sticky >= threshold

    def _ensure_cover(self, post: ParsedPost) -> str | None:
        """上传并返回封面 URL；取不到就返回 ``None``（不阻断文章导入）。

        封面与正文图片走同一套去重缓存（按内容哈希），所以同一张图既是封面
        又出现在正文时只会存一份。
        """
        key = normalize_asset_key(post.cover_raw or "")
        if not key:
            return None
        path = resolve_asset(self.posts_dir, key)
        if path is None:
            print(
                f"  {_ICON_WARN} {post.stem}：封面在磁盘上不存在（{post.cover_raw}），跳过",
                file=sys.stderr,
            )
            return None

        payload = path.read_bytes()
        digest = content_hash(payload)
        cached = self.uploads.get(digest)
        if cached:
            return cached
        if not self.options.apply:
            name = expected_asset_name(key, digest)
            return f"/media/uploads/{upload_subdir()}/{name}"

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        uploaded = self.client.upload_image(
            filename=expected_asset_name(key, digest),
            content_type=content_type,
            payload=payload,
        )
        url = str(uploaded["url"])
        self.uploads[digest] = url
        self._cache_dirty = True
        return url

    def _resolve_category(self, name: str) -> int | None:
        """按名取分类 id；不存在就**创建**。

        先查 ``GET /categories``、查不到再 ``POST /categories``。
        不用「查不到就留空」的降级：分类是归档页的分组骨架，23 篇文章
        丢掉 17 个分类会让归档页几乎空掉；而创建分类本来就有公开接口
        （``POST /categories``，作者权限），没有理由降级。

        结果按名字缓存：23 篇里「算法」出现 4 次，不该查 4 次。
        """
        key = name.strip()
        if not key:
            return None
        if key in self._category_ids:
            return self._category_ids[key]

        try:
            _, body = self.client.request("GET", "/categories")
        except ApiError as exc:
            print(f"  {_ICON_WARN} 读取分类失败（本文将不带分类）：{exc}", file=sys.stderr)
            self._category_ids[key] = None
            return None

        for item in body if isinstance(body, list) else []:
            if str(item.get("name", "")).strip() == key:
                self._category_ids[key] = int(item["id"])
                return self._category_ids[key]

        try:
            created = self.client.create_category(key)
        except ApiError as exc:
            print(f"  {_ICON_WARN} 创建分类「{key}」失败：{exc}", file=sys.stderr)
            self._category_ids[key] = None
            return None

        category_id = int(created.get("id", 0)) or None
        self._category_ids[key] = category_id
        return category_id

    # ---------------------------------------------------------- 报告

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        parts = [f"{status} {count}" for status, count in sorted(counts.items())]
        images = sum(item.images_uploaded for item in self.outcomes)
        missing = sum(item.images_missing for item in self.outcomes)
        return f"结果：{'｜'.join(parts) or '无'}｜图片上传 {images} 次｜缺失引用 {missing} 处"

    def write_report(self, path: Path) -> None:
        data = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "origin": ORIGIN,
            "hexo_root": str(self.options.hexo_root),
            "api": self.options.api,
            "apply": self.options.apply,
            "outcomes": [outcome.__dict__ for outcome in self.outcomes],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 控制台输出

#: 状态图标。非 UTF-8 控制台上会被 make_output_utf8_safe 降级成 ASCII。
_ICON_CREATED = "✅"
_ICON_UPDATED = "♻️"
_ICON_SKIPPED = "⏭️"
_ICON_FAILED = "❌"
_ICON_WARN = "⚠️"


def make_output_utf8_safe() -> None:
    """让输出在非 UTF-8 控制台上也不炸。

    中文 Windows 的默认控制台编码是 GBK，打印 ✅ 这类符号会抛
    ``UnicodeEncodeError`` 并**中断整个导入**——实测踩过：文章已经建好了，
    进程却死在"打印成功消息"这一步，看起来像导入失败。

    两步处理：
    1. 先把 stdout/stderr 切成 UTF-8（``errors="replace"`` 兜底）；
    2. 切不动的环境（老版本、被重定向到奇怪的对象）就把状态图标降级成 ASCII，
       宁可少个勾也不能因为一行日志把迁移中断。
    """
    global _ICON_CREATED, _ICON_UPDATED, _ICON_SKIPPED

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # 切不动就继续（老版本 / 被重定向到奇怪对象）；下一段的 ASCII 降级会兜住
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")

    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    if "utf" not in encoding:  # pragma: no cover - 只在非 UTF-8 控制台走到
        _ICON_CREATED, _ICON_UPDATED, _ICON_SKIPPED = "[new]", "[upd]", "[skip]"


# ---------------------------------------------------------------- 日期


def _parse_hexo_date(raw: str | None) -> str | None:
    """把 Hexo 的 ``2025-11-14 10:36:19`` 转成 ISO 8601（带东八区偏移）。

    为什么要显式带时区：``ArticleCreate.published_at`` 是带时区的 datetime，
    如果发一个**裸**字符串，FastAPI 会按"无时区"解析，服务端再当 UTC 存，
    于是所有文章的日期都会往前错 8 小时——跨天的（旧站多在深夜写）日期直接变前一天。

    旧站是中文博客、作者在国内，按 UTC+8 解释是唯一合理的假设。
    """
    if not raw:
        return None
    text = raw.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = time.strptime(text, fmt)
        except ValueError:
            continue
        return time.strftime("%Y-%m-%dT%H:%M:%S", parsed) + "+08:00"
    return None


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 Hexo 旧站内容导入新站（默认干跑）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  # 干跑：只看看会发生什么\n"
            "  python tools/hexo_import/import_hexo.py\n\n"
            "  # 先导几篇试验田（按文件名或 stem 指定）\n"
            "  python tools/hexo_import/import_hexo.py --apply \\\n"
            "      --only 后端学习路线 --only 农大迷你马拉松\n\n"
            "  # 全量导入\n"
            "  python tools/hexo_import/import_hexo.py --apply\n"
        ),
    )
    parser.add_argument("--hexo", type=Path, default=DEFAULT_HEXO, help="旧站仓库根目录")
    parser.add_argument("--api", default=DEFAULT_API, help="新站 API 基地址")
    parser.add_argument("--apply", action="store_true", help="真的写数据（默认只干跑）")
    parser.add_argument("--update", action="store_true", help="已存在的文章改用 PATCH 覆盖")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="名字",
        help="只处理这些文章（按文件名或 stem，可重复）",
    )
    parser.add_argument("--limit", type=int, help="最多处理前 N 篇")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="上传缓存文件")
    parser.add_argument("--report", type=Path, help="把逐篇结果写成 JSON")
    parser.add_argument(
        "--top-threshold",
        type=int,
        default=DEFAULT_TOP_THRESHOLD,
        metavar="N",
        help=(
            f"旧站 sticky >= N 的文章保留为置顶（默认 {DEFAULT_TOP_THRESHOLD}）。"
            "传 0 表示「有 sticky 就置顶」——注意那会让首页第一页几乎全是置顶，"
            "头条机制失效（见 DEFAULT_TOP_THRESHOLD 的说明）"
        ),
    )
    return parser


def normalize_top_threshold(value: int | None) -> int | None:
    """把 CLI 的阈值转成内部表示：**0 表示「不设阈值」**。

    单独抽出来是为了可测。直接把它当数字用会得到「sticky >= 0」——
    对旧站数据结果恰好相同，但意图完全不同：一个是"我要全部保留"，
    另一个是"阈值恰好是 0"。哪天旧站出现 ``sticky: -1`` 就会分叉。
    """
    return None if value == 0 else value


def main(argv: list[str] | None = None) -> int:
    make_output_utf8_safe()
    args = build_parser().parse_args(argv)

    password = os.environ.get("ADMIN_PASSWORD", "")
    username = os.environ.get("ADMIN_USERNAME", "admin")
    if args.apply and not password:
        print(
            "缺少 ADMIN_PASSWORD 环境变量。\n"
            "凭据刻意只从环境变量读：命令行参数会进 shell 历史与进程列表。",
            file=sys.stderr,
        )
        return 2

    options = ImportOptions(
        hexo_root=args.hexo,
        api=args.api,
        username=username,
        password=password,
        apply=args.apply,
        update=args.update,
        only=args.only,
        limit=args.limit,
        cache_path=args.cache,
        report_path=args.report,
        top_threshold=normalize_top_threshold(args.top_threshold),
    )

    importer = HexoImporter(options)
    try:
        importer.run()
    except ApiError as exc:
        print(f"接口调用失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "\n已被中断。已完成的文章保留在目标站；重跑会自动跳过它们。",
            file=sys.stderr,
        )
        importer.save_cache()
        return 130

    print()
    print(importer.summary())
    if not options.apply:
        print("（这是干跑。要真正导入请加 --apply，并设置 ADMIN_PASSWORD）")
    if options.report_path:
        importer.write_report(options.report_path)
        print(f"逐篇报告：{options.report_path}")
    return 0 if all(item.status != "failed" for item in importer.outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
