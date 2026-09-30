"""导入器测试。

这里钉的都是「不会报错、只会悄悄做错」的地方：

- **slug 估算规则**：与后端不一致时，重跑会把已存在的文章当成新文章再建一份；
- **multipart 编码**：手工拼的请求体少一个 CRLF，服务端就收不到文件；
- **日期解析**：时区丢了会让所有文章的时间平移 8 小时，跨天的直接变前一天；
- **文件名规则**：与 ``AttachmentService`` 不一致时，重跑会重新上传一遍。

最要紧的是 ``test_slug_rules_match_backend``：它直接调用**后端真正的**
``slugify`` 做交叉校验，是防"两边悄悄漂移"的唯一手段。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from import_hexo import (
    PAGE_SIZE,
    ApiClient,
    HexoImporter,
    ImportOptions,
    _encode_multipart,
    _parse_hexo_date,
    normalize_top_threshold,
)
from parse_hexo import (
    ParsedPost,
    extract_images,
    normalize_asset_key,
    parse_post,
    parse_repository,
    resolve_asset,
)
from transform import expected_asset_name, upload_subdir

REAL_HEXO = Path(r"D:\Projects\01_个人项目\blog")
REPO_ROOT = Path(__file__).resolve().parents[3]
real_repo = pytest.mark.skipif(
    not (REAL_HEXO / "source" / "_posts").is_dir(),
    reason="旧站仓库不在本机（设 HEXO_REPO 环境变量可指定）",
)


def _post(title: str) -> ParsedPost:
    return ParsedPost(
        source=Path("x.md"),
        frontmatter={},
        body="",
        title=title,
        date_raw=None,
        tags=[],
        categories=[],
        cover_raw=None,
        abbrlink=None,
        hidden=False,
        sticky=None,
    )


# ---------------------------------------------------------------- slug


def test_slug_matches_backend_for_tricky_titles():
    """旧站真实标题里的坑：``++``、括号、中文标点、全角。"""
    importer = HexoImporter(ImportOptions())
    cases = {
        "C++竞赛代码模板": "c-竞赛代码模板",
        "算法竞赛C++STL常用用法": "算法竞赛c-stl常用用法",
        "二分查找、三分查找": "二分查找-三分查找",
        "前端学习路线(从入门到入土)": "前端学习路线-从入门到入土",
        "AI时代下，我们该怎么办": "ai时代下-我们该怎么办",
        "半月工作室站点迁移啦!": "半月工作室站点迁移啦",
        "FastAPI框架": "fastapi框架",
        "前端转python ai全栈开发": "前端转python-ai全栈开发",
    }
    for title, expected in cases.items():
        assert importer._slug_for(_post(title)) == expected, title


def test_slug_never_empty_and_never_leaves_dangling_hyphen():
    importer = HexoImporter(ImportOptions())
    for title in ("+++", "  ", "（（））", "!!!C++!!!"):
        slug = importer._slug_for(_post(title))
        assert slug, f"{title!r} 算出了空 slug"
        assert not slug.startswith("-") and not slug.endswith("-")
    # 完全没有有效字符时走随机兜底，形状是 article-<8 位>
    fallback = importer._slug_for(_post("！！！"))
    assert fallback.startswith("article-") and len(fallback) == 16


def test_slug_rules_match_backend():
    """**跨仓一致性校验**：本地规则必须与后端 ``app.utils.text.slugify`` 完全一致。

    这条测试的价值全在"跨"字上：它 import 的是**后端真正的实现**，
    所以后端改规则而这里没跟着改时，测试立刻红。
    没有它的话，两边漂移的后果是静默的——重跑导入会多出一批重复文章。

    后端不可 import（例如只带了迁移工具的独立环境）时跳过。
    """
    backend_src = REPO_ROOT / "backend" / "src"
    if not (backend_src / "app" / "utils" / "text.py").is_file():
        pytest.skip("后端源码不在本机")
    sys.path.insert(0, str(backend_src))
    try:
        from app.utils.text import slugify  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - 依赖缺失时不该让迁移测试变红
        pytest.skip("后端依赖未安装，无法 import slugify")

    importer = HexoImporter(ImportOptions())
    titles = [
        "第一篇文章",
        "C++竞赛代码模板",
        "算法竞赛C++STL常用用法",
        "二分查找、三分查找",
        "前端学习路线(从入门到入土)",
        "AI时代下，我们该怎么办",
        "半月工作室站点迁移啦!",
        "FastAPI框架",
        "前端转python ai全栈开发",
        "《置身事内》读后：理解一件事的「钱从哪来」",
        "a" * 200,  # 超长：两边都要截断到 80
    ]
    for title in titles:
        assert importer._slug_for(_post(title)) == slugify(title), f"slug 规则漂移：{title!r}"


def test_slug_fallback_shape_matches_backend():
    """标题没有有效字符时，两边都返回 ``<prefix>-<8 位随机>``。

    只比**形状**不比具体值：服务端用的是随机串，本地不可能算出一模一样的。
    这类文章靠标题匹配兜底（见 ``HexoImporter._slug_for`` 的说明）。
    """
    backend_src = REPO_ROOT / "backend" / "src"
    if not (backend_src / "app" / "utils" / "text.py").is_file():
        pytest.skip("后端源码不在本机")
    sys.path.insert(0, str(backend_src))
    try:
        from app.utils.text import slugify  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        pytest.skip("后端依赖未安装，无法 import slugify")

    importer = HexoImporter(ImportOptions())
    local = importer._slug_for(_post("！！！"))
    server = slugify("！！！")
    assert local.split("-")[0] == "article"
    assert server.split("-")[0] == "item"
    assert len(local.split("-")[1]) == 8
    assert len(server.split("-")[1]) == 8


@real_repo
def test_all_real_titles_produce_sane_slugs():
    importer = HexoImporter(ImportOptions())
    posts = parse_repository(REAL_HEXO / "source" / "_posts")
    for post in posts:
        slug = importer._slug_for(post)
        assert slug and slug == slug.strip()
        assert " " not in slug and "/" not in slug
        assert len(slug) <= 80


# ---------------------------------------------------------------- multipart


def test_multipart_body_is_well_formed():
    body = _encode_multipart("BOUNDARY", "abc.jpg", "image/jpeg", b"\xff\xd8data")
    assert body.startswith(b"--BOUNDARY\r\n")
    assert body.endswith(b"--BOUNDARY--\r\n")
    assert b'name="file"' in body
    assert b'filename="abc.jpg"' in body
    assert b"Content-Type: image/jpeg" in body
    assert b"\xff\xd8data" in body
    # 头部与负载之间必须恰好一个空行
    assert b"\r\n\r\n" in body


def test_multipart_filename_is_ascii_safe():
    """中文文件名必须降级成 ASCII。

    服务端只把 ``filename`` 存进 ``original_name`` 用于展示、不参与路径计算，
    但把原始 UTF-8 字节塞进 multipart 头在部分实现下会被拒绝或乱码。
    """
    body = _encode_multipart("B", "截屏2022-08-29.png", "image/png", b"x")
    head = body.split(b"\r\n\r\n", 1)[0]
    assert head.decode("ascii", "strict"), "multipart 头里出现了非 ASCII 字节"
    assert b"filename=" in head


def test_multipart_empty_filename_falls_back():
    body = _encode_multipart("B", "中文名", "image/png", b"x")
    assert b'filename="image"\n' in body or b'filename="image"' in body


# ---------------------------------------------------------------- 日期


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2025-11-14 10:36:19", "2025-11-14T10:36:19+08:00"),
        ("2025-11-14 10:36", "2025-11-14T10:36:00+08:00"),
        ("2025-11-14", "2025-11-14T00:00:00+08:00"),
        ("2025/11/14 10:36:19", "2025-11-14T10:36:19+08:00"),
        ("  2025-11-14 10:36:19  ", "2025-11-14T10:36:19+08:00"),
    ],
)
def test_parse_hexo_date(raw, expected):
    assert _parse_hexo_date(raw) == expected


def test_parse_hexo_date_keeps_timezone_offset():
    """**必须带时区偏移**。

    旧站是国内博客，``date`` 是东八区墙上时间。发一个裸字符串的话服务端会当
    无时区解析、再当 UTC 存，于是所有文章时间平移 8 小时——旧站多在深夜发文，
    跨天的直接显示成前一天。
    """
    parsed = _parse_hexo_date("2025-11-14 10:36:19")
    assert parsed is not None
    assert parsed.endswith("+08:00")


def test_parse_hexo_date_bad_input_returns_none():
    for raw in (None, "", "不是日期", "2025-13-45"):
        assert _parse_hexo_date(raw) is None


# ---------------------------------------------------------------- 资产命名


def test_expected_asset_name_uses_content_hash_prefix():
    name = expected_asset_name("甲/1.JPG", "a" * 64)
    assert name == "a" * 16 + ".jpg", "扩展名要小写，否则同一文件会落成两个名字"


def test_asset_name_and_subdir_match_server_conventions():
    """文件名与子目录必须与服务端一致，否则重跑会重复上传。"""
    assert upload_subdir().isdigit() and len(upload_subdir()) == 6
    name = expected_asset_name("x.png", "b" * 64)
    assert len(name.rsplit(".", 1)[0]) == 16


# ---------------------------------------------------------------- API 客户端


def test_api_client_requires_login_before_authenticated_calls():
    client = ApiClient("http://127.0.0.1:1")
    with pytest.raises(RuntimeError, match="尚未登录"):
        client.request("GET", "/articles")


def test_page_size_respects_server_cap():
    """服务端把 ``page_size`` 限制在 50；写大了会 422（已踩过）。"""
    assert PAGE_SIZE <= 50


# ---------------------------------------------------------------- 封面


def _write_post(tmp_path: Path, frontmatter: str, body: str = "正文\n") -> ParsedPost:
    """按旧站真实布局建一篇：``<hexo_root>/source/_posts/``。

    目录层级必须与 ``HexoImporter.posts_dir``（``hexo_root / "source" / "_posts"``）
    一致——否则封面会「在磁盘上找不到」，而那是**测试布局错了**，不是代码错了。
    """
    posts = tmp_path / "source" / "_posts"
    posts.mkdir(parents=True, exist_ok=True)
    path = posts / "甲.md"
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")
    post = parse_post(path)
    post.images = extract_images(post, posts)
    return post


def test_cover_shares_dedup_cache_with_body_images(tmp_path):
    """封面与正文引用同一张图时，应得到**同一个 URL**（只上传一次）。

    共用缓存不只是省一次上传：它保证「封面 URL == 正文图片 URL」，
    前端 `cover_variants` 的映射（按 URL 查附件）才能对上。
    """
    (tmp_path / "source" / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "source" / "_posts" / "甲" / "1.jpg").write_bytes(b"cover-bytes")
    post = _write_post(
        tmp_path,
        "title: 甲\ndate: 2025-11-14 10:36:19\nindex_img: 甲/1.jpg\n",
        '<img src="甲/1.jpg" alt="图" />\n',
    )

    importer = HexoImporter(ImportOptions(hexo_root=tmp_path, apply=False))
    body_urls, _pending = importer._ensure_assets(post)
    cover_url = importer._ensure_cover(post)

    assert cover_url is not None
    assert cover_url == body_urls["甲/1.jpg"], "同一张图必须得到同一个 URL"
    assert cover_url.startswith("/media/uploads/"), "干跑也要给出与真跑同形状的 URL"


def test_cover_missing_file_returns_none_without_aborting(tmp_path):
    """封面文件不存在时返回 None，**不阻断**文章导入。"""
    post = _write_post(tmp_path, "title: 甲\ndate: 2025-11-14 10:36:19\nindex_img: 甲/丢了.jpg\n")
    importer = HexoImporter(ImportOptions(hexo_root=tmp_path, apply=False))
    assert importer._ensure_cover(post) is None


def test_cover_absent_returns_none(tmp_path):
    post = _write_post(tmp_path, "title: 甲\ndate: 2025-11-14 10:36:19\n")
    importer = HexoImporter(ImportOptions(hexo_root=tmp_path, apply=False))
    assert post.cover_raw is None
    assert importer._ensure_cover(post) is None


# ---------------------------------------------------------------- 置顶阈值


@pytest.mark.parametrize(
    ("sticky", "threshold", "expected"),
    [
        # 默认阈值 24：只有最高的几篇被保留为置顶
        (27, 24, True),
        (24, 24, True),
        (23, 24, False),
        (5, 24, False),
        # None = 不设阈值（CLI 的 --top-threshold 0 会被转成 None）
        (5, None, True),
        (1, None, True),
    ],
)
def test_is_top_threshold(sticky, threshold, expected):
    importer = HexoImporter(ImportOptions(top_threshold=threshold))

    class _WithSticky:
        pass

    probe = _WithSticky()
    probe.sticky = sticky  # type: ignore[attr-defined]
    assert importer._is_top(probe) is expected  # type: ignore[arg-type]


def test_is_top_false_when_no_sticky():
    importer = HexoImporter(ImportOptions())
    assert importer._is_top(_post("甲")) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, None), (1, 1), (24, 24), (None, None), (100, 100)],
)
def test_normalize_top_threshold_treats_zero_as_no_threshold(raw, expected):
    """CLI 的 ``--top-threshold 0`` = 「不设阈值」，不是「sticky >= 0」。

    对旧站现有数据两者结果恰好相同，但意图不同——哪天出现 ``sticky: -1``
    就会分叉。这条钉住转换本身。
    """
    assert normalize_top_threshold(raw) == expected


def test_cli_exposes_top_threshold():
    """CLI 必须能调阈值，并且默认值与常量一致。"""
    from import_hexo import DEFAULT_TOP_THRESHOLD, build_parser

    args = build_parser().parse_args([])
    assert args.top_threshold == DEFAULT_TOP_THRESHOLD
    assert build_parser().parse_args(["--top-threshold", "0"]).top_threshold == 0


@real_repo
def test_real_repo_covers_all_resolve():
    """旧站带的 9 个 `index_img` 全都能在磁盘上找到（否则封面会静默丢失）。"""
    posts = parse_repository(REAL_HEXO / "source" / "_posts")
    with_cover = [p for p in posts if p.cover_raw]
    assert len(with_cover) == 9, f"应有 9 篇带封面，实际 {len(with_cover)}"
    for post in with_cover:
        key = normalize_asset_key(post.cover_raw or "")
        assert resolve_asset(REAL_HEXO / "source" / "_posts", key) is not None, (
            f"{post.stem} 的封面找不到：{post.cover_raw}"
        )


def test_login_failure_raises_runtime_error(monkeypatch):
    client = ApiClient("http://127.0.0.1:1")

    def fake_request(*_args, **_kwargs):
        return 401, {"detail": "bad credentials"}

    monkeypatch.setattr(client, "request", fake_request)
    with pytest.raises(RuntimeError, match="登录失败"):
        client.login("admin", "wrong")
