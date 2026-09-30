"""解析器测试。

两个层次：

- **单元**：构造最小输入，逐条钉死行为（尤其是容易被"顺手优化"改坏的地方）。
- **真实仓库**：拿旧站 ``D:\\Projects\\01_个人项目\\blog`` 跑一遍，断言
  预检报告里的关键事实（图片数量、缺失清单、代码块未被改写）。
  旧站不在时自动 skip，不让测试依赖某台机器的目录结构。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from parse_hexo import (
    content_hash,
    extract_images,
    mask_inline_code,
    normalize_asset_key,
    parse_frontmatter,
    parse_post,
    parse_repository,
    render_body,
    resolve_asset,
    scan_lines,
    split_frontmatter,
    split_taxonomy,
    to_bool,
    to_int,
)

REAL_HEXO = Path(os.environ.get("HEXO_REPO", r"D:\Projects\01_个人项目\blog"))
real_repo = pytest.mark.skipif(
    not (REAL_HEXO / "source" / "_posts").is_dir(),
    reason="旧站仓库不在本机（设 HEXO_REPO 环境变量可指定）",
)


# ---------------------------------------------------------------- frontmatter


def test_split_frontmatter_only_at_file_start():
    """只认文件开头的 ``---``。

    正文里常有 ``---`` 分隔线（旧站的 AI 时代那篇就有），
    用 ``search`` 会把正文从中间腰斩，而且切出来的"frontmatter"还能解析出键值，
    错得非常安静。
    """
    text = "---\ntitle: 甲\n---\n正文\n\n---\n\n后半段\n"
    fm, body = split_frontmatter(text)
    assert fm == "title: 甲"
    assert body == "正文\n\n---\n\n后半段\n"


def test_split_frontmatter_absent():
    fm, body = split_frontmatter("# 没有 frontmatter\n")
    assert fm == ""
    assert body == "# 没有 frontmatter\n"


@pytest.mark.parametrize(
    ("raw", "key", "expected"),
    [
        ("title: 前端学习路线(从入门到入土)", "title", "前端学习路线(从入门到入土)"),
        ("abbrlink: d097ebe", "abbrlink", "d097ebe"),
        ("sticky: 12", "sticky", "12"),
        # 值里有冒号：只按第一个冒号切分
        ("title: FastAPI: 快速入门", "title", "FastAPI: 快速入门"),
        ("tags: 'Python, 人工智能'", "tags", "Python, 人工智能"),
        ('index_img: "/img/p15.webp"', "index_img", "/img/p15.webp"),
        ("index_img: img/p13.webp", "index_img", "img/p13.webp"),
    ],
)
def test_parse_frontmatter_scalars(raw, key, expected):
    assert parse_frontmatter(raw)[key] == expected


def test_parse_frontmatter_ignores_comment_and_blank_lines():
    fm = parse_frontmatter("#index_img: FastAPI框架/1.jpg\n\ntitle: 甲\n")
    assert fm == {"title": "甲"}


def test_parse_frontmatter_list_form():
    """YAML 列表形状（langchain 那篇就是）。"""
    fm = parse_frontmatter("tags:\n  - langchain\n  - rag\ncategories:\n  - langchain\n")
    assert fm["tags"] == ["langchain", "rag"]
    assert fm["categories"] == ["langchain"]


def test_parse_frontmatter_indented_keys_become_plain_keys():
    """带缩进的键也要能读出来（permalink_defaults 那种写法）。"""
    fm = parse_frontmatter("    title: 缩进标题\n")
    assert fm["title"] == "缩进标题"


def test_unquote_only_strips_matched_quotes():
    fm = parse_frontmatter('a: "甲\nb: 乙\n')
    # a 的值是未闭合的引号，不能把引号吃掉
    assert fm["a"] == '"甲'


# ---------------------------------------------------------------- 分类标签


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # 空格分隔（旧站最常见）
        ("杂项 马拉松", ["杂项", "马拉松"]),
        # 逗号分隔
        ("C++, STL, 语法", ["C++", "STL", "语法"]),
        # 引号包着的逗号列表
        ("Python, 人工智能", ["Python", "人工智能"]),
        # 中文逗号
        ("算法，快速幂", ["算法", "快速幂"]),
        # YAML 列表
        (["langchain", "rag"], ["langchain", "rag"]),
        # 去重保序
        ("算法 算法 二分查找", ["算法", "二分查找"]),
        # 引号形式
        ("'Python'", ["Python"]),
        # 尾随空格（FastAPI框架 那篇的 categories 就带尾空格）
        ("python ", ["python"]),
        (None, []),
        ("", []),
    ],
)
def test_split_taxonomy(value, expected):
    assert split_taxonomy(value) == expected


def test_split_taxonomy_comma_wins_over_whitespace():
    """含逗号就必须按逗号切。

    这条钉的是真实踩过的坑：``C++, STL, 语法`` 若先按空白切，
    会得到 ``C++,`` ``STL,`` 两个带标点的碎片，
    标签库里就会多出永远对不上的脏标签。
    """
    assert split_taxonomy("C++, STL, 语法") == ["C++", "STL", "语法"]


def test_to_int_and_to_bool():
    assert to_int("12") == 12
    assert to_int(12) == 12
    assert to_int("abc") is None
    assert to_int(True) is None, "bool 是 int 的子类，不能把 sticky: true 当成 1"
    assert to_bool("true") is True
    assert to_bool("True") is True
    assert to_bool("false") is False
    assert to_bool(None) is False


# ---------------------------------------------------------------- 路径归一


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("农大迷你马拉松/9.jpg", "农大迷你马拉松/9.jpg"),
        ("/img/p15.webp", "img/p15.webp"),
        # 少前导斜杠的站点级路径（站点更新啦那篇的 index_img 就是这样）
        ("img/p13.webp", "img/p13.webp"),
        ("./后端/1.jpg", "后端/1.jpg"),
        ("a//b/./c.png", "a/b/c.png"),
        ("a\\b.png", "a/b.png"),
        # 百分号编码
        ("%E5%86%9C%E5%A4%A7/1.jpg", "农大/1.jpg"),
        # 查询串与锚点不属于路径
        ("1.jpg?x=1#y", "1.jpg"),
    ],
)
def test_normalize_asset_key(raw, expected):
    assert normalize_asset_key(raw) == expected


def test_normalize_asset_key_is_unicode_nfc():
    """中文文件名必须 NFC 归一。

    同一个文件名在 macOS（NFD）与 Windows（NFC）上是不同的字节序列，
    不归一的话「同一张图」会算出两个键，去重与查找全部失效。
    """
    nfd = "农大迷你马拉松/9.jpg"
    import unicodedata

    decomposed = unicodedata.normalize("NFD", nfd)
    assert normalize_asset_key(decomposed) == normalize_asset_key(nfd)


def test_resolve_asset_rejects_escape(tmp_path):
    (tmp_path / "_posts").mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    assert resolve_asset(tmp_path / "_posts", "../secret.txt") is None


def test_resolve_asset_finds_both_roots(tmp_path):
    posts = tmp_path / "_posts"
    (posts / "甲").mkdir(parents=True)
    (posts / "甲" / "1.jpg").write_bytes(b"a")
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "p1.webp").write_bytes(b"b")

    assert resolve_asset(posts, "甲/1.jpg") is not None, "页面包里的图"
    assert resolve_asset(posts, "img/p1.webp") is not None, "站点级 source/img 里的图"
    assert resolve_asset(posts, "不存在/1.jpg") is None


# ---------------------------------------------------------------- 围栏扫描


def test_scan_lines_marks_fence_lines_themselves():
    body = "正文\n```\n代码\n```\n又正文\n"
    flags = [(line, in_fence) for _, line, in_fence in scan_lines(body)]
    assert flags == [
        ("正文", False),
        ("```", True),
        ("代码", True),
        ("```", True),
        ("又正文", False),
    ]


def test_scan_lines_handles_longer_fence_containing_shorter():
    """`` ```` `` 里包着的 ``` 不构成闭合。"""
    body = "````\n```\n还在代码块里\n````\n出了\n"
    inside = [line for _, line, in_fence in scan_lines(body) if in_fence]
    assert "出了" not in inside
    assert "还在代码块里" in inside


def test_scan_lines_backtick_and_tilde_are_independent():
    body = "~~~\n还在\n```\n还是代码\n~~~\n出来\n"
    flags = {line: in_fence for _, line, in_fence in scan_lines(body)}
    assert flags["还是代码"] is True
    assert flags["出来"] is False


def test_scan_lines_unclosed_fence_runs_to_end():
    """未闭合的围栏吃到文件末尾——这是 CommonMark 的行为，不是 bug。

    旧站确实有一篇结尾少闭合（FastAPI框架的 6.2 示例后面直接接了 ```python），
    必须保证这种情况下**后面所有内容都不被当作正文改写**。
    """
    flags = [in_fence for _, _, in_fence in scan_lines("```\n不闭合\n后面全是代码\n")]
    assert flags == [True, True, True]


# ---------------------------------------------------------------- 行内代码掩码


def test_mask_inline_code_is_length_preserving():
    line = '前 `code` 后 <img src="x.png">'
    masked = mask_inline_code(line)
    assert len(masked) == len(line)
    assert "code" not in masked
    assert "<img" in masked, "掩码只吃行内代码，不该动别的"


def test_mask_inline_code_handles_double_backticks():
    line = "``含 ` 的代码`` 与 <img src='a.png'>"
    masked = mask_inline_code(line)
    assert "含" not in masked
    assert "<img" in masked


# ---------------------------------------------------------------- 图片提取


def _post_with(
    body: str,
    tmp_path: Path,
    name: str = "甲.md",
    frontmatter: str = "title: 甲\ndate: 2025-11-14 10:36:19\n",
):
    """写一篇最小文章并解析。

    默认带上 ``date``，否则每篇都会多出一条 ``no-date`` warn，
    让「诊断清单」类断言要为噪声让路（等于放过了真的诊断回归）。
    """
    posts = tmp_path / "_posts"
    posts.mkdir(exist_ok=True)
    path = posts / name
    path.write_text(f"---\n{frontmatter}---\n" + body, encoding="utf-8")
    post = parse_post(path)
    post.images = extract_images(post, posts)
    return post


def _diag_codes(post) -> list[str]:
    return [d.code for d in post.diagnostics]


def test_extract_html_and_markdown_images(tmp_path):
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    post = _post_with(
        '<img src="甲/1.jpg" alt="标题" style="zoom:50%;" />\n\n![另一张](甲/1.jpg)\n',
        tmp_path,
    )
    assert len(post.images) == 2
    assert [ref.origin for ref in post.images] == ["html", "markdown"]
    assert post.images[0].alt == "标题"
    assert post.images[0].exists is True
    # matched_text 必须包含完整原文（含内联样式）：替换时按它精确定位，
    # 少一个字符就可能定位到别处或漏掉
    assert 'style="zoom:50%;"' in post.images[0].matched_text


def test_extract_skips_images_inside_fenced_code(tmp_path):
    """**这是整个迁移最重要的一条断言。**

    旧站 FastAPI框架 那篇的 ```html 示例里写着另一个 ``<img>``；
    如果它被当成真图片去替换，示例代码就被改坏了——
    而数据库和页面都不会报错，损伤是静默的。
    """
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    body = (
        '<img src="甲/1.jpg" alt="真的" />\n'
        "\n"
        "```html\n"
        '<img src="甲/1.jpg" alt="代码示例" />\n'
        "```\n"
        "\n"
        '`<img src="甲/1.jpg">`\n'
    )
    post = _post_with(body, tmp_path)
    assert len(post.images) == 1, "围栏内与行内代码里的引用都不得被提取"
    assert post.images[0].alt == "真的"


def test_extract_reports_missing_asset(tmp_path):
    post = _post_with('<img src="甲/丢了.png" />\n', tmp_path)
    assert len(post.images) == 1
    assert post.images[0].exists is False
    assert "missing-asset" in _diag_codes(post)
    assert any(d.level == "error" for d in post.diagnostics)


def test_extract_marks_remote_as_existing_passthrough(tmp_path):
    """外链图片不报缺失：它本来就不该在本地找到。

    旧站的封面和正文目前没有外链图，但迁移脚本要能安全处理——
    把 ``http://`` 当成本地路径去找文件，会得到一条假的 error，
    而假 error 会让预检报告失去可信度。
    """
    post = _post_with('<img src="https://cdn.example.com/a.png" />\n', tmp_path)
    assert post.images[0].is_remote is True
    assert post.images[0].exists is False
    assert _diag_codes(post) == [], "外链不该报 missing-asset"


def test_extract_does_not_match_image_element_or_img_prefixed_tag(tmp_path):
    """``<image>``（SVG）与 ``<img-x>`` 都不是 ``<img>``。"""
    post = _post_with('<image href="a.png"></image>\n<img-x src="b.png">\n', tmp_path)
    assert post.images == []
    assert _diag_codes(post) == []


def test_extract_reports_reference_style_image(tmp_path):
    post = _post_with("![标题][id]\n\n[id]: 甲/1.jpg\n", tmp_path)
    assert _diag_codes(post) == ["markdown-ref-image"]


# ---------------------------------------------------------------- 正文重写


def test_render_body_replaces_and_preserves_alt(tmp_path):
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    post = _post_with('<img src="甲/1.jpg" alt="最好的长跑队" style="width:100%" />\n', tmp_path)
    out = render_body(post, {"甲/1.jpg": "/media/migrated/abc.jpg"})
    assert out == "![最好的长跑队](/media/migrated/abc.jpg)\n"


def test_render_body_keeps_fenced_code_byte_identical(tmp_path):
    """围栏内容必须逐字节不变——包括样式、引号、缩进。"""
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    fenced = '<img src="甲/1.jpg"  style="zoom:50%;"  alt="别动我">\n'
    body = f'<img src="甲/1.jpg" alt="换我" />\n\n```html\n{fenced}```\n'
    post = _post_with(body, tmp_path)
    out = render_body(post, {"甲/1.jpg": "/media/migrated/abc.jpg"})

    assert "![换我](/media/migrated/abc.jpg)" in out
    assert fenced in out, "围栏内的示例必须原样保留"
    assert out.count("media/migrated") == 1


def test_render_body_keeps_inline_code_untouched(tmp_path):
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    body = '写法是 `<img src="甲/1.jpg">`，实例：<img src="甲/1.jpg" alt="实" />\n'
    post = _post_with(body, tmp_path)
    out = render_body(post, {"甲/1.jpg": "/m/a.jpg"})
    assert '`<img src="甲/1.jpg">`' in out, "行内代码里的写法说明不该被替换"
    assert out.endswith("![实](/m/a.jpg)\n")


def test_render_body_handles_multiple_images_on_one_line(tmp_path):
    """旧站确实有同一行连着两个 ``<img>``，且长度不同——右侧替换不能错位。"""
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")
    (tmp_path / "_posts" / "甲" / "2.jpg").write_bytes(b"b")

    body = '<img src="甲/1.jpg" alt="一"><img src="甲/2.jpg" alt="很长很长的二" />\n'
    post = _post_with(body, tmp_path)
    out = render_body(post, {"甲/1.jpg": "/m/1.jpg", "甲/2.jpg": "/m/2.jpg"})
    assert out == "![一](/m/1.jpg)![很长很长的二](/m/2.jpg)\n"


def test_render_body_same_image_twice_gets_distinct_fallback_alt(tmp_path):
    """同一张图重复出现且无 alt 时，生成可区分的占位 alt。"""
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    post = _post_with('<img src="甲/1.jpg" />\n<img src="甲/1.jpg" />\n', tmp_path)
    out = render_body(post, {"甲/1.jpg": "/m/1.jpg"})
    assert out == "![图1](/m/1.jpg)\n![图2](/m/1.jpg)\n"


def test_render_body_escapes_brackets_in_alt(tmp_path):
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    post = _post_with('<img src="甲/1.jpg" alt="图[1] 说明(附)" />\n', tmp_path)
    out = render_body(post, {"甲/1.jpg": "/m/1.jpg"})
    assert out == "![图\\[1\\] 说明\\(附\\)](/m/1.jpg)\n"


def test_render_body_leaves_unknown_asset_untouched(tmp_path):
    """替换表里没有的引用保持原样。

    宁可留一个坏引用让预检报告列出来，也不要把线索抹成空字符串——
    「这里曾经有张图」本身就是信息。
    """
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    original = '<img src="甲/1.jpg" alt="在" />\n<img src="甲/缺.png" alt="不在" />\n'
    post = _post_with(original, tmp_path)
    out = render_body(post, {"甲/1.jpg": "/m/1.jpg"})
    assert "![在](/m/1.jpg)" in out
    assert '<img src="甲/缺.png" alt="不在" />' in out


def test_render_body_preserves_html_comments(tmp_path):
    (tmp_path / "_posts" / "甲").mkdir(parents=True)
    (tmp_path / "_posts" / "甲" / "1.jpg").write_bytes(b"a")

    body = '<!-- 注意：图片文件名要写对 -->\n<img src="甲/1.jpg" alt="x" />\n'
    post = _post_with(body, tmp_path)
    out = render_body(post, {"甲/1.jpg": "/m/1.jpg"})
    assert "<!-- 注意：图片文件名要写对 -->" in out


def test_render_body_transforms_nothing_when_no_images(tmp_path):
    body = "# 标题\n\n纯文字段落。\n"
    post = _post_with(body, tmp_path)
    assert render_body(post, {}) == body


# ---------------------------------------------------------------- 真实旧站


@real_repo
def test_real_repository_finds_all_posts():
    posts = parse_repository(REAL_HEXO / "source" / "_posts")
    assert len(posts) == 23, f"旧站应有 23 篇，实际 {len(posts)}"
    assert all(post.title for post in posts)
    assert all(post.date_raw for post in posts), "每篇都该有 date"


@real_repo
def test_real_repository_image_counts_are_exact():
    """把预检结论钉死在测试里：54 处引用，其中 50 处能找到文件。

    这个数字有**独立来源**，不是解析器的自我复述：旧站线上页面（Hexo 构建产物）
    正文里一共渲染出 50 张图，与「54 处引用 − 4 张已被 Hexo 丢弃的坏图」完全吻合，
    且逐篇一致（婺源 19 / FastAPI 12+4坏 / 农大 10 / 后端 4 / 嵌入式 3 / 前端 1 / 考核题 1）。

    数字被改动时这条会失败，强迫后来者重新核对而不是"顺手调大"。
    """
    posts = parse_repository(REAL_HEXO / "source" / "_posts")
    refs = [ref for post in posts for ref in post.images]
    assert len(refs) == 54, f"应提取到 54 处图片引用，实际 {len(refs)}"

    missing = [ref for ref in refs if not ref.exists]
    assert len(missing) == 4, f"应有 4 处缺失，实际 {len(missing)}"
    assert {ref.raw for ref in missing} == {
        "FastAPI框架/截屏2022-08-18 12.09.40.png",
        "FastAPI框架/截屏2022-08-18 13.22.24.png",
        "FastAPI框架/截屏2022-08-29 16.31.21.png",
        "FastAPI框架/截屏2022-08-28 20.18.31.png",
    }, "缺失清单变了：必须重新核对是修好了还是又丢了图"

    assert len([ref for ref in refs if ref.exists]) == 50


@real_repo
def test_real_repository_per_post_image_counts():
    """逐篇数量固定：任何一篇的多算/少算都会在这里暴露。

    单独钉这一条是因为「总数对得上」可能是两篇互相抵消（一多一少），
    而分配到篇级之后，抵消就不可能了。
    """
    posts = parse_repository(REAL_HEXO / "source" / "_posts")
    counts = {post.stem: len(post.images) for post in posts}
    expected = {
        "婺源马拉松": 19,
        "FastAPI框架": 16,
        "农大迷你马拉松": 10,
        "后端学习路线": 4,
        "嵌入式学习路线": 3,
        "前端学习路线": 1,
        "关于考核题": 1,
    }
    assert {k: v for k, v in counts.items() if v} == expected
    assert sum(counts.values()) == 54


@real_repo
def test_real_repository_missing_assets_are_absent_from_every_known_location():
    """这 4 张图在旧站本地仓库里**从来就不存在**。

    三处都查一遍：``source/_posts``（页面包）、``source``（站点级资源）、
    ``public``（已构建产物）。结论：它们不是迁移弄丢的，Hexo 渲染时就把它们
    丢掉了——所以旧站线上这几张早就是坏的（实测 404）。
    这条测试把该结论固化下来，免得以后有人翻到预检报告又去 git 历史里找一遍。
    """
    posts = parse_repository(REAL_HEXO / "source" / "_posts")
    missing = [ref for post in posts for ref in post.images if not ref.exists]
    assert missing, "缺失清单为空的话这条测试就没有意义了"

    for ref in missing:
        for candidate in (
            REAL_HEXO / "source" / ref.asset_key,
            REAL_HEXO / "source" / "_posts" / ref.asset_key,
            REAL_HEXO / "public" / ref.asset_key,
        ):
            assert not candidate.exists(), f"其实存在：{candidate}"
        # public 是按 abbrlink 分目录的，最后的文件名在整棵树下也不该出现
        name = Path(ref.asset_key).name
        assert not any(p.name == name for p in (REAL_HEXO / "public").rglob("*")), (
            f"public 里居然找到了 {name}"
        )


@real_repo
def test_real_repository_fences_are_never_rewritten():
    """端到端校验：全部 23 篇替换后，所有围栏内容仍逐字节存在。

    这是"没有篡改内容"的机器化证据，比人眼看一遍可靠。
    """
    posts_dir = REAL_HEXO / "source" / "_posts"
    posts = parse_repository(posts_dir)
    for post in posts:
        # 用假 URL 替换全部引用，只为触发重写路径
        replacements = {ref.asset_key: f"/m/{i}.jpg" for i, ref in enumerate(post.images)}
        out = render_body(post, replacements)
        for fence_line in post.fences:
            assert fence_line in out, f"{post.stem}：围栏行被改写了：{fence_line!r}"


@real_repo
def test_real_repository_only_images_change_in_body():
    """把替换结果里的图片语法全部换成占位符后，必须与原正文（同样抹掉引用）完全一致。

    这等价于断言「除图片那一部分外，正文一个字都没被改动」——
    比人眼比对 23 篇正文可靠得多，也是整个迁移最核心的安全网。
    """
    posts_dir = REAL_HEXO / "source" / "_posts"
    img_pattern = re.compile(r"<img\b[^>]*>|!\[[^\]]*\]\([^)\s]+(?:\s+[\"'(][^)\"']*[\"')])?\)")
    for post in parse_repository(posts_dir):
        replacements = {ref.asset_key: f"/m/{i}.jpg" for i, ref in enumerate(post.images)}
        out = render_body(post, replacements)

        # 原正文里把这 52 处引用逐处抹掉（用 replace(..., 1)：同一处引用可能
        # 重复出现，必须逐处抹，且顺序与提取顺序一致）
        before = post.body
        for ref in post.images:
            before = before.replace(ref.matched_text, "", 1)
        after = img_pattern.sub("", out)
        assert before == after, f"{post.stem}：除图片外还有内容被改动"


@real_repo
def test_real_repository_hidden_and_sticky():
    """hide/sticky 的业务读数固定下来（hide -> 草稿、sticky -> 置顶的判定依据）。"""
    posts = parse_repository(REAL_HEXO / "source" / "_posts")
    hidden = [post.stem for post in posts if post.hidden]
    assert sorted(hidden) == sorted(
        ["AI时代下，我们该怎么办", "农大迷你马拉松", "勤工助学培训ddd", "婺源马拉松"]
    )
    assert sum(1 for post in posts if post.sticky is not None) == 16


@real_repo
def test_real_repository_all_images_within_upload_limit():
    """全部图片都小得上限，导入不会因体积被拒。"""
    from parse_hexo import resolve_asset

    posts_dir = REAL_HEXO / "source" / "_posts"
    limit = 10 * 1024 * 1024
    seen: dict[str, int] = {}
    for post in parse_repository(posts_dir):
        for ref in post.images:
            if not ref.exists or ref.asset_key in seen:
                continue
            path = resolve_asset(posts_dir, ref.asset_key)
            assert path is not None
            seen[ref.asset_key] = path.stat().st_size
    assert seen, "没有找到任何图片"
    biggest = max(seen.values())
    assert biggest < limit, f"有图片超过上传上限：{biggest} 字节"


@real_repo
def test_real_repository_cover_images_resolve():
    """封面（index_img）指向的文件全部存在——除被注释掉的那个。"""
    posts_dir = REAL_HEXO / "source" / "_posts"
    missing = []
    for post in parse_repository(posts_dir):
        if not post.cover_raw:
            continue
        if resolve_asset(posts_dir, normalize_asset_key(post.cover_raw)) is None:
            missing.append((post.stem, post.cover_raw))
    # FastAPI框架 的 index_img 在 frontmatter 里被 # 注释掉，解析器不该读到它
    assert missing == [], f"有封面图找不到：{missing}"


@real_repo
def test_real_repository_content_hash_is_stable():
    posts_dir = REAL_HEXO / "source" / "_posts"
    data = (posts_dir / "第一篇文章.md").read_bytes()
    assert content_hash(data) == content_hash(data)
    assert len(content_hash(data)) == 64
